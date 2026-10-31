"""Render docs/HF_README.md with the latest cache statistics and push it
to the Hugging Face dataset repo as the root-level README.md.

This script is intentionally decoupled from data_artifacts.upload_to_huggingface:
that helper computes the remote path from the local path relative to the
project root, which would land the file at ``docs/HF_README.md`` on Hugging
Face. The HF dataset hub only renders README.md placed at the repo root, so
we call HfApi.upload_file directly with an explicit ``path_in_repo='README.md'``.

The script is safe to invoke multiple times in a single workflow run; HF
upload is idempotent (no-op when the file content is unchanged).
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_artifacts import (  # noqa: E402
    DEFAULT_CACHE_PATH,
    HF_UPLOAD_MAX_ATTEMPTS,
    HF_UPLOAD_RETRY_BACKOFF,
    _get_hf_api,
    _get_remote_commit,
    _hf_repo_id,
    _hf_repo_type,
    _is_stale_parent_commit_error,
    ensure_cache_local,
    upload_to_huggingface,
)


HF_README_TEMPLATE = ROOT / "docs" / "HF_README.md"
RECENT_UPDATE_START = "<!-- hf-recent-update-start -->"
RECENT_UPDATE_END = "<!-- hf-recent-update-end -->"
HF_README_PATH_IN_REPO = "README.md"

# Statistics SVGs that the HF README references via relative ``pics/stats/*.svg``
# paths. We push them alongside the README so the HF dataset card always
# renders the freshest charts. Order is irrelevant; upload_to_huggingface
# silently skips entries whose local file does not exist (e.g. wordcloud
# can be absent when the optional ``wordcloud`` package is not installed).
HF_README_CHARTS = (
    ROOT / "pics" / "stats" / "stats_overview.svg",
    ROOT / "pics" / "stats" / "papers_by_category.svg",
    ROOT / "pics" / "stats" / "papers_by_year.svg",
    ROOT / "pics" / "stats" / "wordcloud.svg",
)


def _compute_stats(cache_path: Path) -> Dict[str, int]:
    """Compute the headline numbers shown in the HF README banner.

    The on-disk format is a JSON-Lines file (gzipped) where each line is one
    paper record carrying a ``conf`` field such as ``"NeurIPS2024"``. We
    deliberately mirror the counting rules used by ``maintain.compute_stats``
    so the HF banner stays consistent with the GitHub README:

    * ``total``           - one per non-empty line that parses as JSON.
    * ``with_abstract``   - ``paper_abstract`` is a non-empty string.
    * ``with_code``       - ``paper_code`` is non-empty AND not the ``"#"``
                            placeholder that the collector writes when no
                            code link was discovered.
    * ``conf_series``     - number of distinct venue series, computed by
                            stripping the trailing 4-digit year from each
                            distinct ``conf`` value (so ``ACL2023`` and
                            ``ACL2024`` collapse into the single ``ACL``
                            series). Falls back to the raw key when the
                            regex does not match.
    """
    total = 0
    with_abstract = 0
    with_code = 0
    conf_keys: set = set()
    if not cache_path.exists():
        return {
            "total": 0,
            "with_abstract": 0,
            "with_code": 0,
            "conf_series": 0,
        }
    with gzip.open(cache_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            if (record.get("paper_abstract") or "").strip():
                with_abstract += 1
            code_link = str(record.get("paper_code") or "").strip()
            if code_link and code_link != "#":
                with_code += 1
            conf = (record.get("conf") or "").strip()
            if conf:
                conf_keys.add(conf)

    series: set = set()
    series_re = re.compile(r"^([A-Za-z]+)\d{4}$")
    for key in conf_keys:
        match = series_re.match(key)
        series.add(match.group(1).upper() if match else key)

    return {
        "total": total,
        "with_abstract": with_abstract,
        "with_code": with_code,
        "conf_series": len(series),
    }


def _render_recent_update_block(stats: Dict[str, int]) -> str:
    now_cn = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    size_summary_en = (
        f"{stats['total']:,} papers"
        f" / {stats['conf_series']} venue series"
        f" / {stats['with_abstract']:,} with abstract"
        f" / {stats['with_code']:,} with code"
    )
    size_summary_zh = (
        f"{stats['total']:,} 篇论文"
        f" / {stats['conf_series']} 个刊物系列"
        f" / {stats['with_abstract']:,} 篇含摘要"
        f" / {stats['with_code']:,} 篇含开源代码"
    )
    lines = [
        RECENT_UPDATE_START,
        f"- 📅 **最近更新 · Last updated**: {now_cn} (Asia/Shanghai)",
        f"- 📊 **数据库规模 · Database size**: {size_summary_zh}（{size_summary_en}）",
        RECENT_UPDATE_END,
    ]
    return "\n".join(lines)


def _render_readme(template_text: str, stats: Dict[str, int]) -> str:
    start_idx = template_text.find(RECENT_UPDATE_START)
    end_idx = template_text.find(RECENT_UPDATE_END)
    if start_idx == -1 or end_idx == -1 or end_idx < start_idx:
        # Defensive fallback: if markers are missing the template was edited
        # in a way we cannot safely patch; surface a clear error rather than
        # silently shipping an outdated HF README.
        raise RuntimeError(
            f"HF README template at {HF_README_TEMPLATE} is missing the "
            f"'{RECENT_UPDATE_START}' / '{RECENT_UPDATE_END}' markers; "
            "cannot inject the recent-update block."
        )
    end_idx_after = end_idx + len(RECENT_UPDATE_END)
    return (
        template_text[:start_idx]
        + _render_recent_update_block(stats)
        + template_text[end_idx_after:]
    )


def _upload_hf_readme(content: str, commit_message: str) -> bool:
    repo_id = _hf_repo_id()
    if not repo_id:
        print("[*] PAPERVAULT_HF_REPO_ID is not set; skipping HF README sync.")
        return False

    api = _get_hf_api()
    if api is None:
        print(
            "[!] huggingface_hub is not installed or auth failed; "
            "skipping HF README sync."
        )
        return False

    repo_type = _hf_repo_type()
    payload = content.encode("utf-8")
    parent_commit: Optional[str] = _get_remote_commit(api, repo_id, HF_README_PATH_IN_REPO)

    max_attempts = max(1, HF_UPLOAD_MAX_ATTEMPTS)
    backoff = max(0.0, HF_UPLOAD_RETRY_BACKOFF)
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        kwargs = dict(
            path_or_fileobj=payload,
            path_in_repo=HF_README_PATH_IN_REPO,
            repo_id=repo_id,
            repo_type=repo_type,
            commit_message=commit_message,
        )
        if parent_commit:
            kwargs["parent_commit"] = parent_commit
        try:
            api.upload_file(**kwargs)
            print(
                f"[+] HF README synchronised to {repo_id}/{HF_README_PATH_IN_REPO}"
            )
            return True
        except Exception as exc:
            last_exc = exc
            stale = _is_stale_parent_commit_error(exc)
            print(
                f"[!] HF README upload failed (attempt {attempt}/{max_attempts}): {exc}"
            )
            if stale and attempt < max_attempts:
                new_head = _get_remote_commit(api, repo_id, HF_README_PATH_IN_REPO)
                if new_head and new_head != parent_commit:
                    print(
                        f"[*] Stale parent_commit detected; rebasing on "
                        f"new head {new_head} and retrying."
                    )
                    parent_commit = new_head
                    continue
            if attempt < max_attempts and backoff > 0:
                sleep_for = backoff * (2 ** (attempt - 1))
                print(f"[*] Retrying in {sleep_for:.1f}s ...")
                time.sleep(sleep_for)

    print(
        f"[!] Giving up on HF README upload after {max_attempts} attempts. "
        f"Last error: {last_exc}"
    )
    return False


def _upload_stats_charts(commit_message: str) -> int:
    """Push the statistics SVGs referenced by the HF README to the dataset repo.

    Best-effort: returns the number of charts successfully uploaded. Failures
    are logged but never raised, because the README has already been
    synchronised at this point and a chart hiccup must not flip the workflow
    step to a non-zero exit code (the GitHub Action that calls this script
    treats any non-zero status as a hard failure of the data pipeline).

    Charts that do not exist locally (e.g. when the optional ``wordcloud``
    dependency was unavailable on the runner) are silently skipped by
    ``upload_to_huggingface``.
    """
    existing = [p for p in HF_README_CHARTS if p.exists()]
    missing = [p for p in HF_README_CHARTS if not p.exists()]
    for path in missing:
        # Surface a clear hint rather than dropping the chart silently;
        # the corresponding ``<img>`` tag in the HF README will appear
        # broken until the chart is generated by ``maintain.py``.
        print(f"[*] HF chart skipped (not present locally): {path.relative_to(ROOT)}")

    if not existing:
        print("[*] No stats SVGs to upload; skipping chart sync.")
        return 0

    try:
        uploaded = upload_to_huggingface(existing, commit_message)
    except Exception as exc:  # pragma: no cover - defensive guard
        print(f"[!] HF chart upload raised unexpectedly; ignoring: {exc}")
        return 0

    for remote_path in uploaded:
        print(f"[+] HF chart synchronised: {remote_path}")
    if len(uploaded) < len(existing):
        print(
            f"[!] HF chart sync partial: {len(uploaded)}/{len(existing)} uploaded "
            "(see warnings above)."
        )
    return len(uploaded)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render the README locally and print a preview without uploading.",
    )
    parser.add_argument(
        "--commit-message",
        default="chore(auto): sync HF README from PaperVault GitHub workflow",
        help="Commit message used for the Hugging Face upload.",
    )
    parser.add_argument(
        "--skip-cache-refresh",
        action="store_true",
        help=(
            "Skip the initial Hugging Face cache pull; useful when the caller "
            "already has a fresh cache.jsonl.gz on disk (e.g. right after a "
            "collect/backfill step in the same workflow)."
        ),
    )
    parser.add_argument(
        "--skip-charts",
        action="store_true",
        help=(
            "Skip uploading the pics/stats/*.svg charts that the HF README "
            "references. By default the script pushes them after the README "
            "is synchronised so the HF dataset card always renders the "
            "freshest figures."
        ),
    )
    args = parser.parse_args()

    if not HF_README_TEMPLATE.exists():
        print(f"[!] HF README template not found: {HF_README_TEMPLATE}")
        return 1

    cache_path = Path(DEFAULT_CACHE_PATH)
    if not args.skip_cache_refresh:
        try:
            cache_path, _ = ensure_cache_local(cache_path)
        except Exception as exc:
            print(
                f"[!] Failed to refresh cache from Hugging Face ({exc}); "
                "will compute stats from the local file if present."
            )

    stats = _compute_stats(cache_path)
    print(
        f"[+] cache stats: total={stats['total']} "
        f"with_abstract={stats['with_abstract']} "
        f"with_code={stats['with_code']} "
        f"conf_series={stats['conf_series']}"
    )

    template_text = HF_README_TEMPLATE.read_text(encoding="utf-8")
    rendered = _render_readme(template_text, stats)

    if args.dry_run:
        # Write to stdout via the raw buffer with UTF-8 to avoid
        # UnicodeEncodeError on Windows consoles whose default codec is GBK
        # (the README intentionally contains emoji and other non-BMP chars).
        sys.stdout.write("--- BEGIN HF README PREVIEW ---\n")
        sys.stdout.flush()
        try:
            sys.stdout.buffer.write(rendered.encode("utf-8"))
            sys.stdout.buffer.write(b"\n")
            sys.stdout.buffer.flush()
        except AttributeError:
            # Some test runners replace sys.stdout with a buffer-less stream.
            sys.stdout.write(rendered + "\n")
        sys.stdout.write("--- END HF README PREVIEW ---\n")
        if not args.skip_charts:
            existing = [p for p in HF_README_CHARTS if p.exists()]
            print(
                f"[dry-run] would upload {len(existing)} stats chart(s): "
                + ", ".join(str(p.relative_to(ROOT)) for p in existing)
            )
        return 0

    ok = _upload_hf_readme(rendered, args.commit_message)
    if ok and not args.skip_charts:
        # Run only when the README itself was uploaded successfully so we
        # never publish charts that contradict a stale README banner.
        _upload_stats_charts(args.commit_message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
