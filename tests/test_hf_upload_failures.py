from __future__ import annotations

from pathlib import Path

import pytest

import data_artifacts


class _FailingApi:
    def __init__(self):
        self.calls = []

    def upload_file(self, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError("network unavailable")


def _configure_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[_FailingApi, Path, Path]:
    cache = tmp_path / "cache.jsonl.gz"
    progress = tmp_path / "abstract_backfill_progress.jsonl.gz"
    cache.write_bytes(b"cache")
    progress.write_bytes(b"progress")
    api = _FailingApi()
    monkeypatch.setenv("PAPERVAULT_HF_REPO_ID", "example/papervault")
    monkeypatch.setattr(data_artifacts, "_get_hf_api", lambda: api)
    monkeypatch.setattr(data_artifacts, "HF_UPLOAD_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(data_artifacts, "HF_UPLOAD_RETRY_BACKOFF", 0)
    return api, cache, progress


def test_upload_failure_stops_batch_before_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    api, cache, progress = _configure_failure(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="cache.jsonl.gz"):
        data_artifacts.upload_to_huggingface(
            [cache, progress], commit_message="test"
        )

    assert len(api.calls) == 1
    assert api.calls[0]["path_in_repo"].endswith("cache.jsonl.gz")


def test_sync_cache_artifacts_propagates_upload_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _api, cache, progress = _configure_failure(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="cache.jsonl.gz"):
        data_artifacts.sync_cache_artifacts(
            cache_path=cache,
            progress_path=progress,
            commit_message="test",
        )
