from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import maintenance_lock  # noqa: E402

LOCK_REF = "refs/heads/papervault-test-hf-lock"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _new_repositories(tmp_path: Path) -> tuple[Path, Path, Path]:
    remote = tmp_path / "remote.git"
    first = tmp_path / "first"
    second = tmp_path / "second"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(first)], check=True, capture_output=True)
    _git(first, "config", "user.name", "PaperVault Test")
    _git(first, "config", "user.email", "papervault@example.invalid")
    (first / "paper.json").write_text('{"title": "initial"}\n', encoding="utf-8")
    _git(first, "add", "paper.json")
    _git(first, "commit", "-m", "initial")
    _git(first, "remote", "add", "origin", str(remote))
    _git(first, "push", "-u", "origin", "main")
    subprocess.run(
        ["git", "clone", "--branch", "main", str(remote), str(second)],
        check=True,
        capture_output=True,
    )
    return remote, first, second


def test_lock_excludes_other_writer_and_checks_release_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    remote, first, second = _new_repositories(tmp_path)
    monkeypatch.chdir(first)
    first_lock = maintenance_lock.acquire_lock(
        ref=LOCK_REF, timeout=1, poll_interval=0.01
    )
    assert _git(remote, "rev-parse", LOCK_REF) == first_lock

    monkeypatch.chdir(second)
    with pytest.raises(TimeoutError):
        maintenance_lock.acquire_lock(ref=LOCK_REF, timeout=0.05, poll_interval=0.01)
    with pytest.raises(RuntimeError, match="another workflow"):
        maintenance_lock.release_lock("not-the-owner", ref=LOCK_REF)
    assert _git(remote, "rev-parse", LOCK_REF) == first_lock

    monkeypatch.chdir(first)
    assert maintenance_lock.release_lock(first_lock, ref=LOCK_REF)
    assert _git(remote, "for-each-ref", LOCK_REF) == ""

    monkeypatch.chdir(second)
    second_lock = maintenance_lock.acquire_lock(
        ref=LOCK_REF, timeout=1, poll_interval=0.01
    )
    assert second_lock != first_lock
    assert maintenance_lock.release_lock(second_lock, ref=LOCK_REF)


def test_stale_lock_is_replaced_with_lease_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    remote, first, second = _new_repositories(tmp_path)
    monkeypatch.chdir(first)
    first_lock = maintenance_lock.acquire_lock(
        ref=LOCK_REF, timeout=1, poll_interval=0.01
    )

    monkeypatch.chdir(second)
    monkeypatch.setattr(maintenance_lock, "_lock_timestamp", lambda *_args: 0)
    second_lock = maintenance_lock.acquire_lock(
        ref=LOCK_REF,
        stale_after=1,
        timeout=1,
        poll_interval=0.01,
    )

    assert second_lock != first_lock
    assert _git(remote, "rev-parse", LOCK_REF) == second_lock
    assert maintenance_lock.release_lock(second_lock, ref=LOCK_REF)


def test_non_main_ref_cannot_acquire_hf_writer_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    remote, first, _second = _new_repositories(tmp_path)
    _git(first, "checkout", "-b", "feature")
    (first / "branch-only.txt").write_text("not for main\n", encoding="utf-8")
    _git(first, "add", "branch-only.txt")
    _git(first, "commit", "-m", "unmerged branch commit")
    monkeypatch.chdir(first)

    with pytest.raises(RuntimeError, match="checked-out ref"):
        maintenance_lock.acquire_lock(ref=LOCK_REF, timeout=1, poll_interval=0.01)
    assert _git(remote, "for-each-ref", LOCK_REF) == ""


def test_cli_acquire_and_release_use_direct_script_entrypoint(tmp_path: Path):
    _remote, first, _second = _new_repositories(tmp_path)
    script = ROOT / "scripts" / "maintenance_lock.py"
    acquired = subprocess.run(
        [
            sys.executable,
            str(script),
            "acquire",
            "--ref",
            LOCK_REF,
            "--timeout",
            "1",
            "--poll-interval",
            "0.01",
        ],
        cwd=first,
        check=True,
        capture_output=True,
        text=True,
    )
    lock_commit = acquired.stdout.strip()
    assert lock_commit

    released = subprocess.run(
        [
            sys.executable,
            str(script),
            "release",
            "--ref",
            LOCK_REF,
            "--lock-commit",
            lock_commit,
        ],
        cwd=first,
        check=True,
        capture_output=True,
        text=True,
    )
    assert released.returncode == 0


def test_lock_release_during_timestamp_read_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    remote, first, second = _new_repositories(tmp_path)
    monkeypatch.chdir(first)
    first_lock = maintenance_lock.acquire_lock(
        ref=LOCK_REF, timeout=1, poll_interval=0.01
    )
    monkeypatch.chdir(second)

    def release_then_fail(*_args):
        monkeypatch.chdir(first)
        maintenance_lock.release_lock(first_lock, ref=LOCK_REF)
        monkeypatch.chdir(second)
        raise subprocess.CalledProcessError(1, ["git", "fetch"])

    monkeypatch.setattr(maintenance_lock, "_lock_timestamp", release_then_fail)
    second_lock = maintenance_lock.acquire_lock(
        ref=LOCK_REF, timeout=1, poll_interval=0.01
    )

    assert _git(remote, "rev-parse", LOCK_REF) == second_lock
    assert maintenance_lock.release_lock(second_lock, ref=LOCK_REF)
