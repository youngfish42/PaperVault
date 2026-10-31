from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.commit_automated_changes import ensure_main_base  # noqa: E402

DEFAULT_REF = "refs/heads/papervault-hf-writer-lock"
DEFAULT_STALE_AFTER = 43200
DEFAULT_TIMEOUT = 19800
DEFAULT_POLL_INTERVAL = 30


def _git(
    args: Sequence[str], *, input_text: str | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        input=input_text,
        capture_output=True,
        text=True,
        check=check,
    )


def _create_lock_commit() -> str:
    message = (
        "PaperVault HF writer lock\n"
        f"run_id={os.getenv('GITHUB_RUN_ID', 'local')}\n"
        f"run_attempt={os.getenv('GITHUB_RUN_ATTEMPT', '1')}\n"
        f"created_ns={time.time_ns()}\n"
    )
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "PaperVault Automation")
    env.setdefault("GIT_AUTHOR_EMAIL", "papervault@users.noreply.github.com")
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
    result = subprocess.run(
        ["git", "commit-tree", "HEAD^{tree}"],
        input=message,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return result.stdout.strip()


def _remote_lock(ref: str) -> str | None:
    output = _git(["ls-remote", "origin", ref]).stdout.strip()
    if not output:
        return None
    return output.split()[0]


def _delete_lock(ref: str, expected_commit: str) -> None:
    _git(
        [
            "push",
            f"--force-with-lease={ref}:{expected_commit}",
            "origin",
            f":{ref}",
        ]
    )


def _lock_timestamp(ref: str, commit: str) -> int:
    _git(["fetch", "--no-tags", "origin", ref])
    return int(_git(["show", "-s", "--format=%ct", commit]).stdout.strip())


def acquire_lock(
    *,
    ref: str = DEFAULT_REF,
    stale_after: int = DEFAULT_STALE_AFTER,
    timeout: int = DEFAULT_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> str:
    ensure_main_base()
    lock_commit = _create_lock_commit()
    deadline = time.monotonic() + timeout

    while True:
        remote_commit = _remote_lock(ref)
        if remote_commit == lock_commit:
            return lock_commit
        if remote_commit is None:
            result = _git(
                ["push", "origin", f"{lock_commit}:{ref}"], check=False
            )
            if result.returncode == 0:
                print(f"[+] Acquired HF writer lock {ref}", file=sys.stderr)
                return lock_commit
        else:
            try:
                age = time.time() - _lock_timestamp(ref, remote_commit)
            except subprocess.CalledProcessError:
                print(
                    "[*] HF writer lock changed while reading it; retrying.",
                    file=sys.stderr,
                )
            else:
                if age > stale_after:
                    print(
                        f"[!] Removing stale HF writer lock {remote_commit}",
                        file=sys.stderr,
                    )
                    try:
                        _delete_lock(ref, remote_commit)
                    except subprocess.CalledProcessError:
                        pass
                    continue

        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for HF writer lock {ref}")
        print(f"[*] Waiting for HF writer lock {ref}", file=sys.stderr)
        time.sleep(poll_interval)


def release_lock(
    lock_commit: str, *, ref: str = DEFAULT_REF
) -> bool:
    remote_commit = _remote_lock(ref)
    if remote_commit is None:
        return False
    if remote_commit != lock_commit:
        raise RuntimeError(
            "Refusing to release an HF writer lock owned by another workflow run"
        )
    _delete_lock(ref, lock_commit)
    print(f"[+] Released HF writer lock {ref}", file=sys.stderr)
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Serialize HF writers with an atomic, lease-protected Git ref lock."
    )
    parser.add_argument("command", choices=["acquire", "release"])
    parser.add_argument("--ref", default=DEFAULT_REF)
    parser.add_argument("--lock-commit")
    parser.add_argument("--stale-after", type=int, default=DEFAULT_STALE_AFTER)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "acquire":
            print(
                acquire_lock(
                    ref=args.ref,
                    stale_after=args.stale_after,
                    timeout=args.timeout,
                    poll_interval=args.poll_interval,
                )
            )
        else:
            if not args.lock_commit:
                raise ValueError("--lock-commit is required for release")
            release_lock(args.lock_commit, ref=args.ref)
    except (RuntimeError, TimeoutError, ValueError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr.strip(), file=sys.stderr)
        print(f"[!] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
