"""Tests for `discovery/generate_conf.py` — merge/insertion logic and the
`--inherit-branch` machinery that prevents unmerged auto-discoveries from
being overwritten by the next workflow run.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import List, Dict, Any

import pytest

from discovery import generate_conf


def _write_conf(conf_dir: Path, filename: str, data: List[Dict[str, Any]]):
    path = conf_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")


def _read_conf(conf_dir: Path, filename: str) -> List[Dict[str, Any]]:
    path = conf_dir / filename
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. merge_conf / insertion ordering
# ---------------------------------------------------------------------------


def test_merge_conf_dedupes_by_url():
    existing = [
        {"name": "ACL2024", "url": "https://aclanthology.org/events/acl-2024/"},
    ]
    new = [
        {"name": "ACL2024", "url": "https://aclanthology.org/events/acl-2024/"},
        {"name": "ACL2025", "url": "https://aclanthology.org/events/acl-2025/"},
    ]
    merged = generate_conf.merge_conf(existing, new)
    assert len(merged) == 2
    assert merged[0]["name"] == "ACL2024"
    assert merged[1]["name"] == "ACL2025"


def test_merge_conf_inserts_after_same_name():
    existing = [
        {"name": "ICDE2018", "url": "https://dblp.org/db/conf/icde/icde2018.html"},
        {"name": "DAC2000", "url": "https://dblp.org/db/conf/dac/dac2000.html"},
    ]
    new = [
        {"name": "ICDE2026", "url": "https://dblp.org/db/conf/icde/icde2026.html"},
    ]
    merged = generate_conf.merge_conf(existing, new)
    # ICDE2026 should be inserted right after ICDE2018.
    assert [m["name"] for m in merged] == ["ICDE2018", "ICDE2026", "DAC2000"]


def test_merge_conf_orders_by_year_within_same_prefix():
    existing = [
        {"name": "ICDE2018", "url": "https://dblp.org/db/conf/icde/icde2018.html"},
    ]
    new = [
        {"name": "ICDE2020", "url": "https://dblp.org/db/conf/icde/icde2020.html"},
        {"name": "ICDE2019", "url": "https://dblp.org/db/conf/icde/icde2019.html"},
    ]
    merged = generate_conf.merge_conf(existing, new)
    assert [m["name"] for m in merged] == ["ICDE2018", "ICDE2019", "ICDE2020"]


# ---------------------------------------------------------------------------
# 2. Loading conf from a git ref
# ---------------------------------------------------------------------------


def _init_git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    # 显式创建 main 分支，避免不同 git 默认分支不一致导致测试歧义
    subprocess.run(
        ["git", "checkout", "-b", "main"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    return tmp_path


def test_load_conf_from_ref_reads_branch_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = _init_git_repo(tmp_path)
    conf_dir = repo / "conf"
    monkeypatch.setattr(generate_conf, "CONF_DIR", conf_dir)

    _write_conf(conf_dir, "dblp_conf.json", [
        {"name": "ICDE2026", "url": "https://dblp.org/db/conf/icde/icde2026.html"},
    ])

    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "auto-discover-confs"], cwd=repo, check=True, capture_output=True)

    data = generate_conf._load_conf_from_ref("auto-discover-confs", "dblp_conf.json")
    assert data == [
        {"name": "ICDE2026", "url": "https://dblp.org/db/conf/icde/icde2026.html"},
    ]


def test_load_conf_from_ref_returns_empty_when_ref_missing(tmp_path: Path):
    repo = _init_git_repo(tmp_path)
    # No conf file committed, no branch.
    assert generate_conf._load_conf_from_ref("nonexistent-branch", "dblp_conf.json") == []


# ---------------------------------------------------------------------------
# 3. inherit_from_branch merges unmerged discoveries into the working tree
# ---------------------------------------------------------------------------


def test_inherit_from_branch_merges_unmerged_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = _init_git_repo(tmp_path)
    conf_dir = repo / "conf"
    monkeypatch.setattr(generate_conf, "CONF_DIR", conf_dir)

    # main 状态
    _write_conf(conf_dir, "dblp_conf.json", [
        {"name": "ICDE2018", "url": "https://dblp.org/db/conf/icde/icde2018.html"},
    ])
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "main"], cwd=repo, check=True, capture_output=True)

    # 模拟 auto-discover-confs 分支：包含未合并的 ICDE2026
    subprocess.run(["git", "checkout", "-b", "auto-discover-confs"], cwd=repo, check=True, capture_output=True)
    _write_conf(conf_dir, "dblp_conf.json", [
        {"name": "ICDE2018", "url": "https://dblp.org/db/conf/icde/icde2018.html"},
        {"name": "ICDE2026", "url": "https://dblp.org/db/conf/icde/icde2026.html"},
    ])
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "discover ICDE2026"], cwd=repo, check=True, capture_output=True)

    # 切回 main（工作区会被 checkout 重置为 main 状态）
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
    assert _read_conf(conf_dir, "dblp_conf.json") == [
        {"name": "ICDE2018", "url": "https://dblp.org/db/conf/icde/icde2018.html"},
    ]

    # 继承未合并分支
    generate_conf.inherit_from_branch("auto-discover-confs")

    merged = _read_conf(conf_dir, "dblp_conf.json")
    assert len(merged) == 2
    assert merged[1]["name"] == "ICDE2026"


def test_inherit_from_branch_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = _init_git_repo(tmp_path)
    conf_dir = repo / "conf"
    monkeypatch.setattr(generate_conf, "CONF_DIR", conf_dir)

    _write_conf(conf_dir, "dblp_conf.json", [
        {"name": "ICDE2018", "url": "https://dblp.org/db/conf/icde/icde2018.html"},
        {"name": "ICDE2026", "url": "https://dblp.org/db/conf/icde/icde2026.html"},
    ])
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "main already has it"], cwd=repo, check=True, capture_output=True)

    subprocess.run(["git", "checkout", "-b", "auto-discover-confs"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "empty"], cwd=repo, check=True, capture_output=True)

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)

    generate_conf.inherit_from_branch("auto-discover-confs")
    # Nothing new to inherit; file should stay identical.
    merged = _read_conf(conf_dir, "dblp_conf.json")
    assert len(merged) == 2


def test_inherit_from_branch_silently_skips_missing_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = _init_git_repo(tmp_path)
    conf_dir = repo / "conf"
    monkeypatch.setattr(generate_conf, "CONF_DIR", conf_dir)

    _write_conf(conf_dir, "dblp_conf.json", [
        {"name": "ICDE2018", "url": "https://dblp.org/db/conf/icde/icde2018.html"},
    ])
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "main"], cwd=repo, check=True, capture_output=True)

    # 不抛异常、不破坏现有文件
    generate_conf.inherit_from_branch("origin/does-not-exist")
    assert _read_conf(conf_dir, "dblp_conf.json") == [
        {"name": "ICDE2018", "url": "https://dblp.org/db/conf/icde/icde2018.html"},
    ]


def test_inherit_from_branch_dry_run_does_not_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = _init_git_repo(tmp_path)
    conf_dir = repo / "conf"
    monkeypatch.setattr(generate_conf, "CONF_DIR", conf_dir)

    # main 状态
    _write_conf(conf_dir, "dblp_conf.json", [
        {"name": "ICDE2018", "url": "https://dblp.org/db/conf/icde/icde2018.html"},
    ])
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "main"], cwd=repo, check=True, capture_output=True)

    # 模拟未合并分支
    subprocess.run(["git", "checkout", "-b", "auto-discover-confs"], cwd=repo, check=True, capture_output=True)
    _write_conf(conf_dir, "dblp_conf.json", [
        {"name": "ICDE2018", "url": "https://dblp.org/db/conf/icde/icde2018.html"},
        {"name": "ICDE2026", "url": "https://dblp.org/db/conf/icde/icde2026.html"},
    ])
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "discover"], cwd=repo, check=True, capture_output=True)

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)

    # dry-run 模式下不应写入文件
    generate_conf.inherit_from_branch("auto-discover-confs", dry_run=True)
    assert _read_conf(conf_dir, "dblp_conf.json") == [
        {"name": "ICDE2018", "url": "https://dblp.org/db/conf/icde/icde2018.html"},
    ]


# ---------------------------------------------------------------------------
# 4. Resolving refs with origin remote (CI path) and fetch failures
# ---------------------------------------------------------------------------


def _add_origin_remote(repo: Path, bare_remote: Path):
    """把 bare_remote 作为 origin 远程配置到 repo。"""
    subprocess.run(
        ["git", "remote", "add", "origin", str(bare_remote)],
        cwd=repo, check=True, capture_output=True,
    )


def test_resolve_inherit_refs_prefers_local_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """本地分支存在时，不尝试 fetch origin，直接返回本地引用。"""
    repo = _init_git_repo(tmp_path)
    conf_dir = repo / "conf"
    conf_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(generate_conf, "CONF_DIR", conf_dir)

    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "auto-discover-confs"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)

    refs = generate_conf._resolve_inherit_refs("auto-discover-confs")
    assert refs == ["auto-discover-confs"]


def test_resolve_inherit_refs_fetches_from_origin_in_ci(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """模拟 CI 环境：本地无目标分支，但 origin 远程有，应 fetch 后返回 origin/<branch>。"""
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    remote.mkdir()
    repo.mkdir()

    # 初始化 bare remote
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)

    # 在 repo 中创建 main + auto-discover-confs，并推送到 origin
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=repo, check=True, capture_output=True)

    conf_dir = repo / "conf"
    monkeypatch.setattr(generate_conf, "CONF_DIR", conf_dir)
    _write_conf(conf_dir, "dblp_conf.json", [
        {"name": "ICDE2018", "url": "https://dblp.org/db/conf/icde/icde2018.html"},
    ])
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "main"], cwd=repo, check=True, capture_output=True)

    subprocess.run(["git", "checkout", "-b", "auto-discover-confs"], cwd=repo, check=True, capture_output=True)
    _write_conf(conf_dir, "dblp_conf.json", [
        {"name": "ICDE2018", "url": "https://dblp.org/db/conf/icde/icde2018.html"},
        {"name": "ICDE2026", "url": "https://dblp.org/db/conf/icde/icde2026.html"},
    ])
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "discover"], cwd=repo, check=True, capture_output=True)

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", str(remote), "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", str(remote), "auto-discover-confs"], cwd=repo, check=True, capture_output=True)

    # 删除本地 auto-discover-confs，模拟 CI fresh checkout
    subprocess.run(["git", "branch", "-D", "auto-discover-confs"], cwd=repo, check=True, capture_output=True)

    _add_origin_remote(repo, remote)

    refs = generate_conf._resolve_inherit_refs("auto-discover-confs")
    assert "origin/auto-discover-confs" in refs

    # 验证从 origin 读取并合并成功
    generate_conf.inherit_from_branch("auto-discover-confs")
    merged = _read_conf(conf_dir, "dblp_conf.json")
    assert any(item["name"] == "ICDE2026" for item in merged)


def test_resolve_inherit_refs_degrades_when_remote_ref_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """origin 存在但远程无该分支时，应优雅降级而不是抛异常。"""
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    remote.mkdir()
    repo.mkdir()

    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=repo, check=True, capture_output=True)

    conf_dir = repo / "conf"
    monkeypatch.setattr(generate_conf, "CONF_DIR", conf_dir)
    conf_dir.mkdir(parents=True, exist_ok=True)
    _write_conf(conf_dir, "dblp_conf.json", [
        {"name": "ICDE2018", "url": "https://dblp.org/db/conf/icde/icde2018.html"},
    ])
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", str(remote), "main"], cwd=repo, check=True, capture_output=True)

    _add_origin_remote(repo, remote)

    # 远程只有 main，没有 auto-discover-confs；不应抛异常
    refs = generate_conf._resolve_inherit_refs("auto-discover-confs")
    assert refs == ["origin/auto-discover-confs", "auto-discover-confs"]

    # 继承流程也应安全跳过，不破坏现有文件
    generate_conf.inherit_from_branch("auto-discover-confs")
    assert _read_conf(conf_dir, "dblp_conf.json") == [
        {"name": "ICDE2018", "url": "https://dblp.org/db/conf/icde/icde2018.html"},
    ]


def test_resolve_inherit_refs_fails_fast_on_fetch_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """origin 存在但 fetch 失败时，应抛出 InheritBranchError 而不是静默降级。"""
    repo = _init_git_repo(tmp_path)
    conf_dir = repo / "conf"
    conf_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(generate_conf, "CONF_DIR", conf_dir)

    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo, check=True, capture_output=True)

    # 配置一个指向不存在路径的 origin，使 fetch 必然失败
    fake_remote = tmp_path / "nonexistent-remote.git"
    _add_origin_remote(repo, fake_remote)

    with pytest.raises(generate_conf.InheritBranchError):
        generate_conf._resolve_inherit_refs("auto-discover-confs")


def test_main_exits_on_inherit_branch_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """main() 入口在遇到 InheritBranchError 时应以非零码退出。"""
    repo = _init_git_repo(tmp_path)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo, check=True, capture_output=True)
    fake_remote = tmp_path / "nonexistent-remote.git"
    _add_origin_remote(repo, fake_remote)

    monkeypatch.setattr(generate_conf, "CONF_DIR", repo / "conf")

    with pytest.raises(SystemExit) as exc_info:
        generate_conf.main(["--inherit-branch", "auto-discover-confs"])

    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# 5. CLI argument wiring
# ---------------------------------------------------------------------------


def test_cli_parses_inherit_branch():
    """命令行能正确解析 --inherit-branch。"""
    parser = generate_conf._build_parser()
    args = parser.parse_args([
        "--start-year", "2024",
        "--end-year", "2025",
        "--dry-run",
        "--inherit-branch", "auto-discover-confs",
    ])
    assert args.start_year == 2024
    assert args.end_year == 2025
    assert args.dry_run is True
    assert getattr(args, "inherit_branch") == "auto-discover-confs"


def test_cli_inherit_branch_defaults_to_none():
    """--inherit-branch 不传时默认值为 None。"""
    parser = generate_conf._build_parser()
    args = parser.parse_args([])
    assert getattr(args, "inherit_branch") is None
