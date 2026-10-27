from __future__ import annotations

import argparse
import gzip
import json
import re
import subprocess
from pathlib import Path

MAX_BODY_CHARS = 8000
MAX_DETAIL_ITEMS = 15
MAX_DETAIL_CHARS = 120


def _number(value: int) -> str:
    return f"{value:,}"


def _signed(value: int) -> str:
    return f"{value:+,}"


def _short(value: object, limit: int = MAX_DETAIL_CHARS) -> str:
    text = " ".join(str(value or "").split()).replace("`", "'")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _inline_items(items: list[str]) -> tuple[str, int]:
    shown = ", ".join(f"`{_short(item)}`" for item in items[:MAX_DETAIL_ITEMS])
    return shown, max(0, len(items) - MAX_DETAIL_ITEMS)


def _render(
    intro: str,
    metrics: list[str],
    run_url: str,
    *,
    details: list[str] | None = None,
    note: str | None = None,
) -> str:
    lines = [intro, "", "## Run summary", *metrics]
    if details:
        lines.extend(details)
    if note:
        lines.extend(["", note])
    lines.extend(["", f"[View workflow run]({run_url})"])
    body = "\n".join(lines)
    if len(body) > MAX_BODY_CHARS:
        raise ValueError(f"PR body exceeds {MAX_BODY_CHARS} characters: {len(body)}")
    return body


def _load_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def build_discovery_body(
    changed_files: list[str], entries: list[str], run_url: str
) -> str:
    files, remaining_files = _inline_items(changed_files)
    names, remaining_names = _inline_items(entries)
    details = []
    if files:
        details.append(f"- Files: {files}")
        if remaining_files:
            details.append(f"- … and {remaining_files} more files")
    if names:
        details.append(f"- Entries: {names}")
        if remaining_names:
            details.append(f"- … and {remaining_names} more entries")
    return _render(
        "Automated conference configuration update.",
        [
            f"- Config files changed: **{len(changed_files)}**",
            f"- Pending conference/year entries: **{len(entries)}**",
        ],
        run_url,
        details=details,
        note="Review the source URLs before merging.",
    )


def _discovery_changes() -> tuple[list[str], list[str]]:
    changed = subprocess.run(
        ["git", "diff", "--name-only", "--", "conf/"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    diff = subprocess.run(
        ["git", "diff", "--unified=0", "--", "conf/"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    entries = []
    seen = set()
    for line in diff.splitlines():
        match = re.match(r'^\+\s*"name"\s*:\s*("(?:[^"\\]|\\.)*")', line)
        if not match:
            continue
        name = json.loads(match.group(1))
        if name not in seen:
            seen.add(name)
            entries.append(name)
    return changed, entries


def build_collect_body(
    *, mode: str, baseline: int, current: int, meta_path: Path, failures_path: Path, run_url: str
) -> str:
    meta = _load_json(meta_path, {})
    failures = _load_json(failures_path, [])
    new_papers = int(meta.get("new_papers", 0)) if isinstance(meta, dict) else 0
    new_conferences = int(meta.get("new_conferences", 0)) if isinstance(meta, dict) else 0
    failure_count = len(failures) if isinstance(failures, list) else 0
    return _render(
        "Automated paper collection and README/statistics refresh.",
        [
            f"- Mode: **{mode}**",
            f"- New papers: **{_number(new_papers)}**",
            f"- New conference/year entries: **{_number(new_conferences)}**",
            f"- Cache records: **{_number(baseline)} → {_number(current)}** (**{_signed(current - baseline)} net**)",
            f"- Collection failures pending retry: **{_number(failure_count)}**",
        ],
        run_url,
    )


def build_backfill_body(
    *, baseline: int, current: int, total: int, target: str, run_url: str
) -> str:
    metrics = [
        f"- Abstracts: **{_number(baseline)} → {_number(current)}** (**{_signed(current - baseline)} this run**)",
    ]
    if total:
        before = baseline / total * 100
        after = current / total * 100
        metrics.append(
            f"- Coverage: **{before:.2f}% → {after:.2f}%** of **{_number(total)}** papers"
        )
    else:
        metrics.append("- Coverage: **N/A** (empty cache)")
    if target.strip():
        metrics.append(f"- Target: `{_short(target)}`")
    return _render(
        "Automated abstract backfill and statistics refresh.", metrics, run_url
    )


def _cache_stats(path: Path) -> dict[str, int]:
    stats = {"papers": 0, "abstracts": 0, "code": 0}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                paper = json.loads(line)
            except json.JSONDecodeError:
                continue
            stats["papers"] += 1
            if str(paper.get("paper_abstract") or "").strip():
                stats["abstracts"] += 1
            code = str(paper.get("paper_code") or "").strip()
            if code and code != "#":
                stats["code"] += 1
    return stats


def build_readme_body(
    *, mode: str, cache_path: Path, meta_path: Path, run_url: str
) -> str:
    stats = _cache_stats(cache_path)
    meta = _load_json(meta_path, {})
    if not isinstance(meta, dict):
        meta = {}
    metrics = [
        f"- Mode: **{mode}**",
        f"- Papers: **{_number(stats['papers'])}**",
        f"- With abstracts: **{_number(stats['abstracts'])}**",
        f"- With code links: **{_number(stats['code'])}**",
    ]
    new_papers = int(meta.get("new_papers", 0))
    new_conferences = int(meta.get("new_conferences", 0))
    if mode == "force":
        metrics.extend(
            [
                f"- New papers this run: **{_number(new_papers)}**",
                f"- New conferences this run: **{_number(new_conferences)}**",
            ]
        )
    else:
        date = _short(meta.get("last_update") or "unknown", 40)
        metrics.append(
            f"- Latest collection snapshot: **{date}** — "
            f"**{_number(new_papers)}** new papers, **{_number(new_conferences)}** new conferences"
        )
    return _render("Automated README and statistics refresh.", metrics, run_url)


def _report_count(report: str, label: str) -> int:
    match = re.search(rf"^\s*{re.escape(label)}\s*:\s*(\d+)\s*$", report, re.MULTILINE)
    return int(match.group(1)) if match else 0


def build_wayback_body(report: str, run_url: str) -> str:
    rewritten = _report_count(report, "rewrite (in place)")
    merged = _report_count(report, "merge (dup (conf,url))")
    abstracts = _report_count(report, "abstracts backfilled")
    unchanged = _report_count(report, "unchanged")
    return _render(
        "Automated repair of Wayback-wrapped paper URLs.",
        [
            f"- Repaired URLs: **{_number(rewritten + merged)}**",
            f"- Rewritten in place: **{_number(rewritten)}**; duplicates merged: **{_number(merged)}**",
            f"- Abstracts also filled: **{_number(abstracts)}**",
            f"- Unchanged: **{_number(unchanged)}**",
        ],
        run_url,
        note="The migration log supports rollback; cache updates were synced to Hugging Face.",
    )


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-url", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate concise automatic PR descriptions")
    commands = parser.add_subparsers(dest="command", required=True)

    discovery = commands.add_parser("discovery")
    _common_arguments(discovery)

    collect = commands.add_parser("collect")
    _common_arguments(collect)
    collect.add_argument("--mode", choices=["incremental", "force"], default="incremental")
    collect.add_argument("--baseline", type=int, required=True)
    collect.add_argument("--current", type=int, required=True)
    collect.add_argument("--meta", type=Path, required=True)
    collect.add_argument("--failures", type=Path, required=True)

    backfill = commands.add_parser("backfill")
    _common_arguments(backfill)
    backfill.add_argument("--baseline", type=int, required=True)
    backfill.add_argument("--current", type=int, required=True)
    backfill.add_argument("--total", type=int, required=True)
    backfill.add_argument("--target", default="")

    readme = commands.add_parser("readme")
    _common_arguments(readme)
    readme.add_argument("--mode", choices=["readme", "force"], default="readme")
    readme.add_argument("--cache", type=Path, required=True)
    readme.add_argument("--meta", type=Path, required=True)

    wayback = commands.add_parser("wayback")
    _common_arguments(wayback)
    wayback.add_argument("--log", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "discovery":
        changed, entries = _discovery_changes()
        body = build_discovery_body(changed, entries, args.run_url)
    elif args.command == "collect":
        body = build_collect_body(
            mode=args.mode,
            baseline=args.baseline,
            current=args.current,
            meta_path=args.meta,
            failures_path=args.failures,
            run_url=args.run_url,
        )
    elif args.command == "backfill":
        body = build_backfill_body(
            baseline=args.baseline,
            current=args.current,
            total=args.total,
            target=args.target,
            run_url=args.run_url,
        )
    elif args.command == "readme":
        body = build_readme_body(
            mode=args.mode,
            cache_path=args.cache,
            meta_path=args.meta,
            run_url=args.run_url,
        )
    else:
        body = build_wayback_body(args.log.read_text(encoding="utf-8"), args.run_url)
    args.output.write_text(body + "\n", encoding="utf-8")
    print(f"[+] Wrote PR body to {args.output} ({len(body)} characters)")


if __name__ == "__main__":
    main()
