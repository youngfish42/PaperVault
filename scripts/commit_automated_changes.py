from __future__ import annotations

import argparse
import subprocess
import time
from collections.abc import Sequence

DEFAULT_MAX_ATTEMPTS = 5
MAIN_REF = "refs/heads/main"


def _run(args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], check=check)


def _rebase_on_main() -> None:
    _run(["fetch", "origin", "main"])
    result = _run(["rebase", "origin/main"], check=False)
    if result.returncode != 0:
        _run(["rebase", "--abort"], check=False)
        raise RuntimeError(
            "Failed to rebase automated changes onto origin/main; "
            "the conflicting commit was not pushed."
        )


def _ensure_main_base() -> None:
    _run(["fetch", "origin", "main"])
    result = _run(
        ["merge-base", "--is-ancestor", "HEAD", "origin/main"], check=False
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Refusing to push automated changes: the checked-out ref contains "
            "commits that are not on origin/main. Run this workflow from main."
        )


def commit_and_push(
    *,
    message: str,
    paths: Sequence[str],
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> bool:
    if not paths:
        raise ValueError("At least one artifact path is required.")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1.")

    _ensure_main_base()
    _run(["reset"])
    _run(["add", "-A", "--", *paths])
    diff = _run(["diff", "--cached", "--quiet"], check=False)
    if diff.returncode == 0:
        print("[*] No automated artifact changes; skipping commit and push.")
        return False
    if diff.returncode != 1:
        raise RuntimeError("Unable to inspect staged automated changes.")

    _run(["commit", "-m", message])
    for attempt in range(1, max_attempts + 1):
        _rebase_on_main()
        result = _run(["push", "origin", f"HEAD:{MAIN_REF}"], check=False)
        if result.returncode == 0:
            print(f"[+] Pushed automated changes to {MAIN_REF}.")
            return True
        if attempt < max_attempts:
            delay = min(2 ** (attempt - 1), 10)
            print(
                f"[!] Push attempt {attempt}/{max_attempts} failed; "
                f"rebasing and retrying in {delay}s."
            )
            time.sleep(delay)

    raise RuntimeError(
        f"Failed to push automated changes after {max_attempts} attempts; "
        "the remote main branch was not force-updated."
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Commit whitelisted automated artifacts and push them to main safely."
    )
    parser.add_argument("--message", required=True, help="Commit message")
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help="Maximum fetch/rebase/push attempts",
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        required=True,
        help="Artifact paths allowed in the automated commit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        commit_and_push(
            message=args.message,
            paths=args.paths,
            max_attempts=args.max_attempts,
        )
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"[!] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
