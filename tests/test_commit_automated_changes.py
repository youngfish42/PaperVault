from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.commit_automated_changes import commit_and_push  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _configure_user(repo: Path) -> None:
    _git(repo, "config", "user.name", "PaperVault Test")
    _git(repo, "config", "user.email", "papervault@example.invalid")


def _new_clone(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "working"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    _configure_user(repo)
    artifact = repo / "artifacts" / "progress.json"
    artifact.parent.mkdir()
    artifact.write_text('{"count": 0}\n', encoding="utf-8")
    _git(repo, "add", "artifacts/progress.json")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "main")
    return remote, repo


def _push_upstream_change(
    remote: Path,
    tmp_path: Path,
    *,
    path: str,
    content: str,
    message: str,
) -> None:
    upstream = tmp_path / "upstream"
    subprocess.run(
        ["git", "clone", "--branch", "main", str(remote), str(upstream)],
        check=True,
        capture_output=True,
    )
    _configure_user(upstream)
    target = upstream / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(upstream, "add", path)
    _git(upstream, "commit", "-m", message)
    _git(upstream, "push", "origin", "main")


def test_commit_whitelists_paths_and_skips_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    remote, repo = _new_clone(tmp_path)
    (repo / "artifacts" / "progress.json").write_text('{"count": 1}\n', encoding="utf-8")
    (repo / "unrelated.txt").write_text("do not commit\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    assert commit_and_push(message="automated update", paths=["artifacts"])
    assert _git(remote, "show", "main:artifacts/progress.json") == '{"count": 1}'
    assert "unrelated.txt" not in _git(remote, "ls-tree", "-r", "--name-only", "main")
    assert "unrelated.txt" in _git(repo, "status", "--short")

    head = _git(repo, "rev-parse", "HEAD")
    assert not commit_and_push(message="no-op", paths=["artifacts"])
    assert _git(repo, "rev-parse", "HEAD") == head


def test_rebases_onto_nonconflicting_upstream_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    remote, repo = _new_clone(tmp_path)
    (repo / "artifacts" / "progress.json").write_text('{"count": 2}\n', encoding="utf-8")
    _push_upstream_change(
        remote,
        tmp_path,
        path="upstream.txt",
        content="upstream\n",
        message="human upstream update",
    )
    monkeypatch.chdir(repo)

    assert commit_and_push(message="automated update", paths=["artifacts"])
    log = _git(remote, "log", "--format=%s", "main")
    assert "automated update" in log
    assert "human upstream update" in log
    assert _git(remote, "show", "main:upstream.txt") == "upstream"
    assert _git(remote, "show", "main:artifacts/progress.json") == '{"count": 2}'


def test_conflict_fails_without_overwriting_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    remote, repo = _new_clone(tmp_path)
    (repo / "artifacts" / "progress.json").write_text('{"count": "local"}\n', encoding="utf-8")
    _push_upstream_change(
        remote,
        tmp_path,
        path="artifacts/progress.json",
        content='{"count": "upstream"}\n',
        message="conflicting upstream update",
    )
    monkeypatch.chdir(repo)

    with pytest.raises(RuntimeError, match="Failed to rebase"):
        commit_and_push(message="automated update", paths=["artifacts"])

    assert _git(remote, "show", "main:artifacts/progress.json") == '{"count": "upstream"}'
    assert "conflicting upstream update" in _git(remote, "log", "-1", "--format=%s", "main")
    assert not (repo / ".git" / "rebase-merge").exists()
    assert not (repo / ".git" / "rebase-apply").exists()
