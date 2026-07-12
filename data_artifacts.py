import gzip
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
DEFAULT_PROGRESS_PATH = ROOT / "cache" / "abstract_backfill_progress.jsonl.gz"
PathLike = Union[str, Path]


_HF_PARENT_COMMITS: dict = {}
# TODO(review #3, fourth pass): ``_HF_PARENT_COMMITS`` is a plain module
# global and its readers/writers currently run only from the main thread
# (Flask request handlers touch it serially, GH Actions jobs are
# single-process). If we ever expose the upload path to a
# ``ThreadPoolExecutor`` (e.g. parallel per-file HF pushes) this dict
# needs a ``threading.Lock`` — otherwise a racy read-then-write can
# stamp an older ``parent_commit`` back over a newer one and re-trigger
# the 412 rebase loop unnecessarily. Not a bug today; noting it here so
# the invariant is explicit for the next reader.
#
# TODO(review #7, fourth pass): the dict is keyed by ``path_in_repo``
# (cache vs. progress) but the underlying HF API's ``list_repo_commits``
# only returns the *dataset-wide* HEAD — so the values for two
# different keys always resolve to the same commit id. That's fine for
# correctness (any commit that touched *any* file in the dataset
# invalidates the local snapshot of *every* file) but it means the
# per-key granularity is cosmetic. If HF ever exposes per-path
# revisions we can tighten the invalidation set.


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


def _is_stale_parent_commit_error(exc: Exception) -> bool:
    """Detect HF Hub's "parent_commit is stale" rejection.

    Preference order:
      1. Structured HTTP status (412 Precondition Failed) if the exception
         exposes a ``response`` attribute (HfHubHTTPError and subclasses).
      2. Tightened substring matches: only treat the error as stale when the
         message clearly references parent commits, optimistic locking or
         precondition failures. Plain "conflict" or a bare "412" appearing
         inside an unrelated message will no longer trigger a false rebase.
    """
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code == 412:
        return True

    message = str(exc).lower()
    keywords = (
        "parent_commit",
        "parent commit",
        "stale parent",
        "precondition failed",
    )
    return any(keyword in message for keyword in keywords)


def _get_remote_commit(api, repo_id: str, path_in_repo: str) -> Optional[str]:
    """Return the most recent commit oid that touched ``path_in_repo``.

    The HF Hub does not expose per-file commit metadata in a single call, so we
    fall back to the dataset head revision. This is acceptable for our optimistic
    lock: any concurrent writer that pushed in the meantime will move the head
    forward and our parent_commit check will reject the stale upload.
    """
    try:
        info = api.dataset_info(repo_id=repo_id, revision="main")
        return getattr(info, "sha", None)
    except Exception:
        try:
            refs = api.list_repo_refs(repo_id=repo_id, repo_type=_hf_repo_type())
            for branch in getattr(refs, "branches", []):
                if getattr(branch, "name", None) == "main":
                    return getattr(branch, "target_commit", None)
        except Exception:
            return None
    return None


def _hf_local_dir_mode() -> bool:
    """Return True if hf_hub_download should be forced into local_dir mode.

    Motivation: on Windows the default HF cache stores blobs as symlinks,
    which fails with ``OSError: [WinError 14007]`` on many developer boxes
    (privilege not held, non-NTFS mount, etc.). Passing ``local_dir=<root>``
    switches ``huggingface_hub`` to a copy-based layout that has no symlinks
    at all. We keep the classic cache-dir path on Linux CI to stay in sync
    with GitHub Actions behaviour.
    """
    override = os.getenv("PAPERVAULT_HF_LOCAL_DIR")
    if override is not None:
        return override.lower() in ("1", "true", "yes")
    return os.name == "nt"


def _download_with_fallback(
    *,
    repo_id: str,
    filename: str,
    repo_type: str,
    token: Optional[str],
    revision: str,
    local_dir: Path,
) -> str:
    """Thin wrapper around ``hf_hub_download`` with a Windows-safe branch.

    Isolated so tests can assert the exact kwargs we pass. Do NOT inline
    this back into ``ensure_cache_local`` -- ``tests/test_data_artifacts_download.py``
    relies on this seam.
    """
    from huggingface_hub import hf_hub_download

    kwargs = dict(
        repo_id=repo_id,
        filename=filename,
        repo_type=repo_type,
        token=token,
        revision=revision,
    )
    if _hf_local_dir_mode():
        # local_dir lays the file out as <local_dir>/<filename> with a plain
        # copy instead of a symlink into the shared HF blob cache. That
        # sidesteps WinError 14007 without needing admin/Developer Mode.
        kwargs["local_dir"] = str(local_dir)
    return hf_hub_download(**kwargs)


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
        from huggingface_hub import hf_hub_download  # noqa: F401  (imported for ImportError signalling)
        from huggingface_hub.utils import EntryNotFoundError
    except ImportError:
        print("[!] huggingface_hub missing; cannot refresh cache from HF.")
        return cache_path, None

    path_in_repo = _path_in_repo(cache_path)
    try:
        downloaded = _download_with_fallback(
            repo_id=repo_id,
            filename=path_in_repo,
            repo_type=_hf_repo_type(),
            token=_hf_token(),
            revision="main",
            local_dir=ROOT,
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
        # Write through a sibling .tmp file so a half-finished copy (process
        # killed, soft-timeout, Ctrl-C) cannot leave a corrupt gzip behind.
        tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        try:
            shutil.copyfile(downloaded_path, tmp_path)
            os.replace(tmp_path, cache_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
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


def ensure_progress_local(
    progress_path: PathLike = DEFAULT_PROGRESS_PATH,
    refresh: bool = True,
    allow_missing_remote: bool = True,
) -> Tuple[Path, Optional[str]]:
    """与 ``ensure_cache_local`` 同语义，但作用于进度文件。

    复用同一套 HF 同步 + 乐观锁机制，使 ``abstract_backfill_progress.jsonl.gz``
    与 ``cache.jsonl.gz`` 在远端 dataset 仓库里并列管理。多机协作时也能通过
    parent_commit 机制避免互相覆盖。
    """
    return ensure_cache_local(
        cache_path=progress_path,
        refresh=refresh,
        allow_missing_remote=allow_missing_remote,
    )


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

    # Materialise so we can tell whether more uploads follow the current one;
    # this lets us skip an otherwise-wasted ``dataset_info`` round-trip after
    # the final push (parent_commit is only useful for subsequent uploads).
    path_list = [Path(p) for p in paths]

    for index, path in enumerate(path_list):
        path = path.resolve()
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
                # Only refresh parent_commit if more uploads follow in this
                # batch; on the last path no one will read the value and we
                # save one HF round-trip (helpful under HF rate limiting).
                if index < len(path_list) - 1:
                    new_head = _get_remote_commit(api, repo_id, path_in_repo)
                    if new_head:
                        _HF_PARENT_COMMITS[path_in_repo] = new_head
                uploaded.append(path_in_repo)
                print(f"[+] Uploaded to Hugging Face: {repo_id}/{path_in_repo}")
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                stale = _is_stale_parent_commit_error(exc)
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


def upload_progress_only(
    path: PathLike = DEFAULT_PROGRESS_PATH,
    *,
    commit_message: str = "Update abstract backfill progress (local)",
) -> List[str]:
    """Whitelisted local uploader for the backfill progress file only.

    This is the single sanctioned entry point for pushing a locally-edited
    ``cache/abstract_backfill_progress.jsonl.gz`` back to Hugging Face from a
    developer machine (see AGENTS.md / spec.md ``FR-13`` / ``AC-13``).

    Any other path -- most importantly ``cache/cache.jsonl.gz`` -- is rejected
    with ``RuntimeError`` so a fat-fingered developer script cannot bypass the
    "no cache uploads from local" policy. Uploads to the main cache must go
    through the GitHub Actions workflow.
    """
    resolved = Path(path).resolve()
    # Strict whitelist: the path *must* live inside the repo root at the
    # canonical location. This closes review issue #5 (a
    # ``ValueError`` from ``relative_to`` used to fall back to
    # ``resolved.name``, letting any file named
    # ``abstract_backfill_progress.jsonl.gz`` bypass the check).
    expected = (ROOT / "cache" / "abstract_backfill_progress.jsonl.gz").resolve()
    if resolved != expected:
        raise RuntimeError(
            "upload_progress_only refuses to upload "
            f"{resolved!s}: only "
            f"{expected!s} is allowed from a local machine. "
            "cache.jsonl.gz uploads must go through the GitHub Actions "
            "workflow."
        )

    print(
        "[*] progress-only upload: pushing "
        "cache/abstract_backfill_progress.jsonl.gz to Hugging Face "
        "(cache.jsonl.gz will NOT be touched)."
    )
    return upload_to_huggingface([resolved], commit_message=commit_message)


def sync_cache_artifacts(
    cache_path: PathLike = DEFAULT_CACHE_PATH,
    upload: bool = True,
    commit_message: str = "Update PaperVault data artifacts",
    progress_path: Optional[PathLike] = DEFAULT_PROGRESS_PATH,
) -> None:
    """同步缓存制品到 HF。

    ``cache_path`` 一定上传；``progress_path`` 若存在则一并上传，使
    abstract backfill 进度文件与主缓存保持同一次提交语义。传入 ``None``
    可显式关闭进度文件上传（例如只想推主缓存）。
    """
    cache_path = Path(cache_path)
    targets: List[Path] = [cache_path]
    if progress_path is not None:
        progress_path = Path(progress_path)
        if progress_path.exists():
            targets.append(progress_path)

    if upload:
        try:
            upload_to_huggingface(
                targets,
                commit_message=commit_message,
            )
        except Exception as exc:
            print(
                "[!] Hugging Face sync raised an unexpected error; continuing workflow. "
                f"Error: {exc}"
            )
            traceback.print_exc()
