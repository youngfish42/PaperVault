"""Tests for the Windows-safe ``hf_hub_download`` wrapper.

The default HF cache layout uses symlinks, which fails on Windows with
``OSError: [WinError 14007]`` on many developer boxes. ``_download_with_fallback``
must therefore pass ``local_dir=<repo_root>`` on Windows / when the
``PAPERVAULT_HF_LOCAL_DIR=1`` override is set, and leave the classic
cache-dir call intact everywhere else so Linux CI stays in lockstep with
GitHub Actions behaviour. Corresponds to spec ``AC-6`` / TR-1.1 / TR-1.2.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import data_artifacts


def _fake_hf_hub_download(**kwargs):
    return "/tmp/fake-download"


def test_local_dir_branch_forced_via_env(monkeypatch):
    monkeypatch.setenv("PAPERVAULT_HF_LOCAL_DIR", "1")

    with patch(
        "huggingface_hub.hf_hub_download", side_effect=_fake_hf_hub_download
    ) as mock_dl:
        data_artifacts._download_with_fallback(
            repo_id="fake/repo",
            filename="cache/x.jsonl.gz",
            repo_type="dataset",
            token=None,
            revision="main",
            local_dir=data_artifacts.ROOT,
        )

    assert mock_dl.call_count == 1
    _, kwargs = mock_dl.call_args
    assert "local_dir" in kwargs
    assert str(kwargs["local_dir"]) == str(data_artifacts.ROOT)


def test_default_branch_disabled_via_env(monkeypatch):
    monkeypatch.setenv("PAPERVAULT_HF_LOCAL_DIR", "0")

    with patch(
        "huggingface_hub.hf_hub_download", side_effect=_fake_hf_hub_download
    ) as mock_dl:
        data_artifacts._download_with_fallback(
            repo_id="fake/repo",
            filename="cache/x.jsonl.gz",
            repo_type="dataset",
            token=None,
            revision="main",
            local_dir=data_artifacts.ROOT,
        )

    assert mock_dl.call_count == 1
    _, kwargs = mock_dl.call_args
    assert "local_dir" not in kwargs


def test_hf_local_dir_mode_env_precedence(monkeypatch):
    monkeypatch.setenv("PAPERVAULT_HF_LOCAL_DIR", "1")
    assert data_artifacts._hf_local_dir_mode() is True

    monkeypatch.setenv("PAPERVAULT_HF_LOCAL_DIR", "0")
    assert data_artifacts._hf_local_dir_mode() is False


def test_hf_local_dir_mode_defaults_to_os(monkeypatch):
    monkeypatch.delenv("PAPERVAULT_HF_LOCAL_DIR", raising=False)
    expected = os.name == "nt"
    assert data_artifacts._hf_local_dir_mode() is expected


def test_cache_sync_preserves_download_mtime(tmp_path: Path, monkeypatch):
    """An unchanged HF blob must not invalidate the derived SQLite index."""

    downloaded = tmp_path / "hf-cache" / "cache.jsonl.gz"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"stable-cache-content")
    stable_mtime_ns = 1_700_000_000_123_456_789
    os.utime(downloaded, ns=(stable_mtime_ns, stable_mtime_ns))
    local = tmp_path / "checkout" / "cache.jsonl.gz"

    monkeypatch.delenv("PAPERVAULT_OFFLINE", raising=False)
    monkeypatch.setenv("PAPERVAULT_HF_REPO_ID", "example/papervault")
    monkeypatch.setattr(data_artifacts, "_get_hf_api", lambda: object())
    monkeypatch.setattr(
        data_artifacts,
        "_download_with_fallback",
        lambda **_kwargs: str(downloaded),
    )
    monkeypatch.setattr(
        data_artifacts,
        "_get_remote_commit",
        lambda *_args, **_kwargs: "abc123",
    )

    data_artifacts.ensure_cache_local(local)

    assert local.read_bytes() == downloaded.read_bytes()
    assert local.stat().st_mtime_ns == stable_mtime_ns
