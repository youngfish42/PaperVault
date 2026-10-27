from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import generate_pr_body as pr  # noqa: E402

RUN_URL = "https://github.com/example/PaperVault/actions/runs/123"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_discovery_changes_reads_only_new_unique_names(monkeypatch):
    outputs = iter(
        [
            "conf/acl_conf.json\nconf/dblp_conf.json\n",
            '''+++ b/conf/acl_conf.json
+    "name": "ACL2027",
+    "name": "ACL2027",
-    "name": "ACL2025",
+    "name": "A \\"quoted\\" venue",
''',
        ]
    )

    class Result:
        def __init__(self, stdout):
            self.stdout = stdout

    monkeypatch.setattr(pr.subprocess, "run", lambda *args, **kwargs: Result(next(outputs)))

    changed, entries = pr._discovery_changes()

    assert changed == ["conf/acl_conf.json", "conf/dblp_conf.json"]
    assert entries == ["ACL2027", 'A "quoted" venue']


def test_discovery_body_bounds_optional_details():
    files = [f"conf/source_{index}.json" for index in range(20)]
    entries = [f"CONF{index}{'X' * 200}" for index in range(20)]

    body = pr.build_discovery_body(files, entries, RUN_URL)

    assert len(body) <= pr.MAX_BODY_CHARS
    assert "Config files changed: **20**" in body
    assert "Pending conference/year entries: **20**" in body
    assert "… and 5 more files" in body
    assert "… and 5 more entries" in body
    assert "CONF15" not in body
    assert "X" * 121 not in body
    assert RUN_URL in body


def test_collect_body_uses_meta_and_signed_net_delta(tmp_path):
    meta = tmp_path / "readme_meta.json"
    failures = tmp_path / "collect_failures.json"
    _write_json(meta, {"new_papers": 12, "new_conferences": 2})
    _write_json(failures, [{"url": "https://example.org"}])

    body = pr.build_collect_body(
        mode="force",
        baseline=110,
        current=100,
        meta_path=meta,
        failures_path=failures,
        run_url=RUN_URL,
    )

    assert "Mode: **force**" in body
    assert "New papers: **12**" in body
    assert "110 → 100" in body
    assert "**-10 net**" in body
    assert "Collection failures pending retry: **1**" in body


def test_backfill_body_reports_zero_delta_and_bounds_target():
    body = pr.build_backfill_body(
        baseline=50,
        current=50,
        total=100,
        target="A" * 500,
        run_url=RUN_URL,
    )

    assert "**+0 this run**" in body
    assert "Coverage: **50.00% → 50.00%**" in body
    assert "A" * 121 not in body
    assert "A" * 119 + "…" in body


def test_readme_body_counts_cache_and_distinguishes_modes(tmp_path):
    cache = tmp_path / "cache.jsonl.gz"
    records = [
        {"paper_abstract": "abstract", "paper_code": "https://github.com/example/repo"},
        {"paper_abstract": "", "paper_code": "#"},
        {"paper_abstract": None, "paper_code": ""},
    ]
    with gzip.open(cache, "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    meta = tmp_path / "readme_meta.json"
    _write_json(
        meta,
        {"last_update": "2026-10-27", "new_papers": 7, "new_conferences": 1},
    )

    force = pr.build_readme_body(
        mode="force", cache_path=cache, meta_path=meta, run_url=RUN_URL
    )
    readme = pr.build_readme_body(
        mode="readme", cache_path=cache, meta_path=meta, run_url=RUN_URL
    )

    assert "Papers: **3**" in force
    assert "With abstracts: **1**" in force
    assert "With code links: **1**" in force
    assert "New papers this run: **7**" in force
    assert "Latest collection snapshot: **2026-10-27**" in readme
    assert "New papers this run" not in readme


def test_wayback_body_parses_report_counts():
    report = """
===== wayback repair report [WRITE] =====
processed this run       : 5
  rewrite (in place)     : 3
  merge (dup (conf,url)) : 2
  unchanged              : 9
  abstracts backfilled   : 4
"""

    body = pr.build_wayback_body(report, RUN_URL)

    assert "Repaired URLs: **5**" in body
    assert "Rewritten in place: **3**; duplicates merged: **2**" in body
    assert "Abstracts also filled: **4**" in body
    assert "Unchanged: **9**" in body


def test_main_writes_pr_body(tmp_path):
    meta = tmp_path / "readme_meta.json"
    failures = tmp_path / "collect_failures.json"
    output = tmp_path / "pr_body.md"
    _write_json(meta, {"new_papers": 1, "new_conferences": 0})
    _write_json(failures, [])

    pr.main(
        [
            "collect",
            "--baseline",
            "10",
            "--current",
            "11",
            "--meta",
            str(meta),
            "--failures",
            str(failures),
            "--output",
            str(output),
            "--run-url",
            RUN_URL,
        ]
    )

    body = output.read_text(encoding="utf-8")
    assert body.startswith("Automated paper collection")
    assert body.rstrip().endswith(f"[View workflow run]({RUN_URL})")


def test_workflows_use_generated_pr_bodies_and_bounded_titles():
    import yaml

    workflow_paths = [
        ROOT / ".github/workflows/discover_and_update.yml",
        ROOT / ".github/workflows/collect_papers.yml",
        ROOT / ".github/workflows/backfill_abstracts.yml",
        ROOT / ".github/workflows/update_readme.yml",
        ROOT / ".github/workflows/wayback_repair.yml",
    ]
    pr_steps = []
    for path in workflow_paths:
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in workflow["jobs"].values():
            steps = job.get("steps", [])
            for index, step in enumerate(steps):
                if not str(step.get("uses", "")).startswith(
                    "peter-evans/create-pull-request@"
                ):
                    continue
                config = step["with"]
                assert "body" not in config
                assert config["body-path"] == "${{ runner.temp }}/pr_body.md"
                assert len(config["title"]) <= 200
                assert any(
                    previous.get("name") == "Generate PR summary"
                    for previous in steps[:index]
                )
                pr_steps.append((path, step["name"]))

    assert len(pr_steps) == 6
