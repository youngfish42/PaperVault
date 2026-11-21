"""Tests for ``data_artifacts.upload_progress_only``.

The function is the single sanctioned entry point for pushing a locally
edited ``cache/abstract_backfill_progress.jsonl.gz`` to Hugging Face. It
MUST refuse every other path so a developer script cannot smuggle
``cache.jsonl.gz`` (or anything else) onto HF from a laptop.

Covers spec ``AC-13`` and re-checks the "no cache uploads from local"
side of ``AC-11``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import data_artifacts


def test_upload_progress_only_accepts_progress_path():
    target = data_artifacts.ROOT / "cache" / "abstract_backfill_progress.jsonl.gz"

    with patch.object(
        data_artifacts, "upload_to_huggingface", return_value=["cache/abstract_backfill_progress.jsonl.gz"]
    ) as mock_upload:
        returned = data_artifacts.upload_progress_only(target)

    assert mock_upload.call_count == 1
    args, kwargs = mock_upload.call_args
    paths_arg = args[0] if args else kwargs.get("paths") or kwargs["paths"]
    paths_list = list(paths_arg)
    assert len(paths_list) == 1
    assert Path(paths_list[0]).resolve() == target.resolve()
    commit_message = kwargs.get("commit_message")
    if commit_message is None and len(args) > 1:
        commit_message = args[1]
    assert commit_message and "progress" in commit_message.lower()
    assert returned == ["cache/abstract_backfill_progress.jsonl.gz"]


def test_upload_progress_only_rejects_cache_jsonl_gz():
    forbidden = data_artifacts.ROOT / "cache" / "cache.jsonl.gz"

    with patch.object(data_artifacts, "upload_to_huggingface") as mock_upload:
        with pytest.raises(RuntimeError) as exc_info:
            data_artifacts.upload_progress_only(forbidden)

    assert "cache.jsonl.gz" in str(exc_info.value)
    assert mock_upload.call_count == 0


def test_upload_progress_only_rejects_arbitrary_path(tmp_path):
    other = tmp_path / "some" / "other.gz"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_bytes(b"")

    with patch.object(data_artifacts, "upload_to_huggingface") as mock_upload:
        with pytest.raises(RuntimeError):
            data_artifacts.upload_progress_only(other)

    assert mock_upload.call_count == 0


def test_upload_progress_only_rejects_spoofed_basename(tmp_path):
    """Review issue #5: an out-of-tree file whose basename happens to be
    ``abstract_backfill_progress.jsonl.gz`` must NOT bypass the whitelist.
    """
    spoofed = tmp_path / "abstract_backfill_progress.jsonl.gz"
    spoofed.write_bytes(b"")

    with patch.object(data_artifacts, "upload_to_huggingface") as mock_upload:
        with pytest.raises(RuntimeError):
            data_artifacts.upload_progress_only(spoofed)

    assert mock_upload.call_count == 0
