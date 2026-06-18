import gzip
import json
import os
import shutil
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union


try:
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass


HF_UPLOAD_MAX_ATTEMPTS = int(os.getenv("PAPERVAULT_HF_UPLOAD_MAX_ATTEMPTS", "3"))
HF_UPLOAD_RETRY_BACKOFF = float(os.getenv("PAPERVAULT_HF_UPLOAD_RETRY_BACKOFF", "5"))


ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE_PATH = ROOT / "cache" / "cache.jsonl.gz"
DEFAULT_PARQUET_PATH = ROOT / "cache" / "papers.parquet"
PathLike = Union[str, Path]


_HF_PARENT_COMMITS: dict = {}


def _hf_repo_id() -> Optional[str]:
    return os.getenv("PAPERVAULT_HF_REPO_ID") or None


def _hf_repo_type() -> str:
    return os.getenv("PAPERVAULT_HF_REPO_TYPE", "dataset")


def _hf_token() -> Optional[str]:
    return os.getenv("HF_TOKEN") or None


def _offline_mode() -> bool:
    return os.getenv("PAPERVAULT_OFFLINE", "").lower() in ("1", "true", "yes")


def _get_hf_api():
    try:
        from huggingface_hub import HfApi
    except ImportError:
        return None
    try:
        return HfApi(token=_hf_token())
    except Exception as exc:
        print(f"[!] Failed to initialize Hugging Face client: {exc}")
        return None


def _path_in_repo(local_path: Path) -> str:
    local_path = local_path.resolve()
    try:
        return local_path.relative_to(ROOT).as_posix()
    except ValueError:
        return local_path.name


def _get_remote_commit(api, repo_id: str, path_in_repo: str) -> Optional[str]:
    """Return the most recent commit oid that touched ``path_in_repo``.

    The HF Hub does not expose per-file commit metadata in a single call, so we
    fall back to the dataset head revision. This is acceptable for our optimistic
    lock: any concurrent writer that pushed in the meantime will move the head
    forward and our parent_commit check will reject the stale upload.
    """
    try:
        info = api.dataset_info(repo_id=repo_id, revision="main")
        return getattr(info, "sha", None) or info.sha  # type: ignore[attr-defined]
    except Exception:
        try:
            refs = api.list_repo_refs(repo_id=repo_id, repo_type=_hf_repo_type())
            for branch in getattr(refs, "branches", []):
                if branch.name == "main":
                    return branch.target_commit
        except Exception:
            return None
    return None


def ensure_cache_local(
    cache_path: PathLike = DEFAULT_CACHE_PATH,
    refresh: bool = True,
    allow_missing_remote: bool = True,
) -> Tuple[Path, Optional[str]]:
    """Make sure the local cache file mirrors the latest HF dataset revision.

    Always returns the resolved local path. When refresh is True (default) and
    HF is reachable, the file is overwritten with the remote copy and the
    head commit is recorded for later parent_commit optimistic-lock pushes.

    Failure modes (logged, not fatal when a local copy already exists):
      * huggingface_hub missing / network down / token invalid
      * PAPERVAULT_HF_REPO_ID unset
      * remote file does not exist yet
    """
    cache_path = Path(cache_path).resolve()
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if _offline_mode():
        print("[*] PAPERVAULT_OFFLINE=1; skipping HF cache refresh.")
        return cache_path, None

    repo_id = _hf_repo_id()
    if not repo_id:
        if not cache_path.exists():
            print(
                "[!] PAPERVAULT_HF_REPO_ID is not set and no local cache exists; "
                "operations that read the cache will fail."
            )
        return cache_path, None

    api = _get_hf_api()
    if api is None:
        if not cache_path.exists():
            raise RuntimeError(
                "huggingface_hub is unavailable and no local cache exists. "
                "Install requirements.txt and set HF_TOKEN to bootstrap the cache."
            )
        print("[!] huggingface_hub unavailable; using existing local cache as-is.")
        return cache_path, None

    if not refresh and cache_path.exists():
        commit = _get_remote_commit(api, repo_id, _path_in_repo(cache_path))
        if commit:
            _HF_PARENT_COMMITS[_path_in_repo(cache_path)] = commit
        return cache_path, commit

    try:
        from huggingface_hub import hf_hub_download
        from huggingface_hub.utils import EntryNotFoundError
    except ImportError:
        print("[!] huggingface_hub missing; cannot refresh cache from HF.")
        return cache_path, None

    path_in_repo = _path_in_repo(cache_path)
    try:
        downloaded = hf_hub_download(
            repo_id=repo_id,
            filename=path_in_repo,
            repo_type=_hf_repo_type(),
            token=_hf_token(),
            revision="main",
        )
    except EntryNotFoundError:
        if allow_missing_remote:
            print(
                f"[*] Remote cache {repo_id}/{path_in_repo} does not exist yet; "
                "this run will create it on the next upload."
            )
            return cache_path, None
        raise
    except Exception as exc:
        if cache_path.exists():
            print(
                f"[!] Failed to refresh cache from HF ({exc}); "
                "falling back to existing local copy."
            )
            return cache_path, None
        raise RuntimeError(
            f"Failed to fetch cache from Hugging Face ({repo_id}/{path_in_repo}): {exc}"
        )

    downloaded_path = Path(downloaded).resolve()
    if downloaded_path != cache_path:
        shutil.copyfile(downloaded_path, cache_path)
    parent_commit = _get_remote_commit(api, repo_id, path_in_repo)
    if parent_commit:
        _HF_PARENT_COMMITS[path_in_repo] = parent_commit
    print(
        f"[+] Cache synchronised from Hugging Face: {repo_id}/{path_in_repo} "
        f"(commit={parent_commit or 'unknown'})"
    )
    return cache_path, parent_commit


@contextmanager
def open_cache(cache_path: Path):
    if cache_path.suffix == ".gz":
        with gzip.open(cache_path, "rt", encoding="utf-8") as handle:
            yield handle
    else:
        with cache_path.open("r", encoding="utf-8") as handle:
            yield handle


def iter_cache_records(cache_path: PathLike) -> Iterable[dict]:
    cache_path = Path(cache_path)
    with open_cache(cache_path) as handle:
        for line_num, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            conf = record.get("conf")
            if not conf:
                raise ValueError(f"Missing conf on line {line_num} of {cache_path}")
            authors = record.get("paper_authors") or []
            if not isinstance(authors, list):
                authors = [str(authors)]
            yield {
                "conf": str(conf),
                "paper_name": record.get("paper_name") or "",
                "paper_url": record.get("paper_url") or "",
                "paper_authors": [str(author) for author in authors],
                "paper_abstract": record.get("paper_abstract") or "",
                "paper_code": record.get("paper_code") or "#",
            }


def build_parquet(
    cache_path: PathLike = DEFAULT_CACHE_PATH,
    output_path: PathLike = DEFAULT_PARQUET_PATH,
    batch_size: int = 10000,
) -> Tuple[Path, int]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "Parquet export requires pyarrow. Install requirements.txt first."
        ) from exc

    cache_path = Path(cache_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    schema = pa.schema(
        [
            ("conf", pa.string()),
            ("paper_name", pa.string()),
            ("paper_url", pa.string()),
            ("paper_authors", pa.list_(pa.string())),
            ("paper_abstract", pa.string()),
            ("paper_code", pa.string()),
        ]
    )

    writer = None
    batch = []
    total = 0
    try:
        for record in iter_cache_records(cache_path):
            batch.append(record)
            if len(batch) >= batch_size:
                writer = _write_parquet_batch(tmp_path, schema, writer, batch)
                total += len(batch)
                batch.clear()
        if batch:
            writer = _write_parquet_batch(tmp_path, schema, writer, batch)
            total += len(batch)
        if writer is None:
            writer = pq.ParquetWriter(tmp_path, schema, compression="zstd")
    finally:
        if writer is not None:
            writer.close()

    os.replace(tmp_path, output_path)
    return output_path, total


def _write_parquet_batch(tmp_path: Path, schema, writer, batch: List[dict]):
    import pyarrow as pa
    import pyarrow.parquet as pq

    if writer is None:
        writer = pq.ParquetWriter(tmp_path, schema, compression="zstd")
    table = pa.Table.from_pylist(batch, schema=schema)
    writer.write_table(table)
    return writer


def upload_to_huggingface(paths: Iterable[PathLike], commit_message: str) -> List[str]:
    repo_id = _hf_repo_id()
    if not repo_id:
        print("[*] PAPERVAULT_HF_REPO_ID is not set; skipping Hugging Face upload.")
        return []

    api = _get_hf_api()
    if api is None:
        print(
            "[!] huggingface_hub is not installed or auth failed; skipping upload. "
            "Install requirements.txt and configure HF_TOKEN to enable artifact sync."
        )
        return []

    repo_type = _hf_repo_type()

    uploaded: List[str] = []
    failed: List[str] = []
    max_attempts = max(1, HF_UPLOAD_MAX_ATTEMPTS)
    backoff = max(0.0, HF_UPLOAD_RETRY_BACKOFF)

    for path in paths:
        path = Path(path).resolve()
        if not path.exists():
            continue
        path_in_repo = _path_in_repo(path)
        # Optimistic-lock parent commit: rely on the head we observed when we
        # last refreshed this file. Concurrent writers will bump the head and
        # cause HF to reject our stale push, which we then recover from by
        # re-downloading and retrying once with the new parent.
        parent_commit = _HF_PARENT_COMMITS.get(path_in_repo)

        last_exc: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            kwargs = dict(
                path_or_fileobj=str(path),
                path_in_repo=path_in_repo,
                repo_id=repo_id,
                repo_type=repo_type,
                commit_message=commit_message,
            )
            if parent_commit:
                kwargs["parent_commit"] = parent_commit
            try:
                api.upload_file(**kwargs)
                # Refresh parent commit to the just-pushed head for the next call
                new_head = _get_remote_commit(api, repo_id, path_in_repo)
                if new_head:
                    _HF_PARENT_COMMITS[path_in_repo] = new_head
                uploaded.append(path_in_repo)
                print(f"[+] Uploaded to Hugging Face: {repo_id}/{path_in_repo}")
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                message = str(exc).lower()
                stale = (
                    "parent_commit" in message
                    or "stale" in message
                    or "412" in message
                    or "conflict" in message
                )
                print(
                    f"[!] Hugging Face upload failed for {path_in_repo} "
                    f"(attempt {attempt}/{max_attempts}): {exc}"
                )
                if stale and attempt < max_attempts:
                    # Another writer raced us; pull the latest head so we
                    # rebase on top of their commit. The local file is still
                    # the source of truth for this writer's pending changes.
                    new_head = _get_remote_commit(api, repo_id, path_in_repo)
                    if new_head and new_head != parent_commit:
                        print(
                            f"[*] Stale parent_commit detected; rebasing on "
                            f"new head {new_head} and retrying."
                        )
                        parent_commit = new_head
                        _HF_PARENT_COMMITS[path_in_repo] = new_head
                        continue
                if attempt < max_attempts and backoff > 0:
                    sleep_for = backoff * (2 ** (attempt - 1))
                    print(f"[*] Retrying in {sleep_for:.1f}s ...")
                    time.sleep(sleep_for)

        if last_exc is not None:
            failed.append(path_in_repo)
            print(
                f"[!] Giving up on Hugging Face upload for {path_in_repo} "
                f"after {max_attempts} attempts."
            )

    if failed:
        print(
            f"[!] Hugging Face upload completed with failures: {failed}. "
            "Workflow will continue without aborting."
        )
    return uploaded


def sync_cache_artifacts(
    cache_path: PathLike = DEFAULT_CACHE_PATH,
    parquet_path: PathLike = DEFAULT_PARQUET_PATH,
    upload: bool = True,
    commit_message: str = "Update PaperVault data artifacts",
) -> None:
    cache_path = Path(cache_path)
    parquet_path = Path(parquet_path)

    parquet_file, count = build_parquet(cache_path, parquet_path)
    print(f"[+] Parquet generated: {parquet_file} ({count} papers)")

    if upload:
        try:
            upload_to_huggingface(
                [cache_path, parquet_file],
                commit_message=commit_message,
            )
        except Exception as exc:
            print(
                "[!] Hugging Face sync raised an unexpected error; continuing workflow. "
                f"Error: {exc}"
            )
            traceback.print_exc()
