"""Shared pytest fixtures for PaperVault backend tests.

Why generate the sample cache at runtime instead of committing a binary:
- Keeps the repo free of synthetic gzip blobs that humans can't diff.
- Guarantees the on-disk format always matches whatever
  ``collector.save_cache`` would write today (single source of truth).
- Avoids any risk of accidentally shipping copyrighted upstream abstracts.

All tests run with ``PAPERVAULT_OFFLINE=1`` so the Hugging Face refresh
path is short-circuited and no network is needed.
"""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
from typing import Dict, List

import pytest

os.environ.setdefault("PAPERVAULT_OFFLINE", "1")


SAMPLE_PAPERS: Dict[str, List[dict]] = {
    "ACL 2023": [
        {
            "paper_name": "Attention Is All You Need Revisited",
            "paper_authors": ["Alice Adams", "Bob Brown"],
            "paper_url": "https://example.org/acl2023/p1",
            "paper_abstract": "A revisit of attention mechanisms.",
            "paper_code": None,
        },
    ],
    "ACL 2024": [
        {
            "paper_name": "Retrieval Augmented Generation at Scale",
            "paper_authors": ["Alice Adams"],
            "paper_url": "https://example.org/acl2024/p1",
            "paper_abstract": "Scaling RAG.",
            "paper_code": "https://github.com/example/rag",
        },
    ],
    "EMNLP 2023": [
        {
            "paper_name": "Diffusion Models for Text",
            "paper_authors": ["Eve Edwards", "Grace Green"],
            "paper_url": "https://example.org/emnlp2023/p1",
            "paper_abstract": "Diffusion applied to text generation.",
            "paper_code": None,
        },
    ],
    "CVPR 2024": [
        {
            "paper_name": "Vision Transformers Revisited",
            "paper_authors": ["Ivy Ito"],
            "paper_url": "https://example.org/cvpr2024/p1",
            "paper_abstract": "A study of ViT scaling.",
            "paper_code": None,
        },
    ],
    "NeurIPS 2022": [
        {
            "paper_name": "Graph Neural Networks Survey",
            "paper_authors": ["Kai Lin", "Mia Nakamura"],
            "paper_url": "https://example.org/neurips2022/p1",
            "paper_abstract": "A comprehensive GNN survey.",
            "paper_code": None,
        },
    ],
    "NIPS 2021": [
        {
            "paper_name": "Legacy Title with-Hyphen Inside",
            "paper_authors": ["Oscar Park"],
            "paper_url": "https://example.org/nips2021/p1",
            "paper_abstract": "Testing hyphen normalization.",
            "paper_code": None,
        },
    ],
    "Workshop W00 2000": [
        {
            "paper_name": "Edge Case Ancient Paper",
            "paper_authors": [],
            "paper_url": "https://example.org/w00/p1",
            "paper_abstract": None,
            "paper_code": None,
        },
    ],
    # Negative sample: conf key without a 4-digit year.
    # PaperRepository's startup materialiser should log a warning and skip it.
    "WorkshopNoYear": [
        {
            "paper_name": "Should Be Skipped",
            "paper_authors": ["Z Z"],
            "paper_url": "https://example.org/skipped/p1",
            "paper_abstract": None,
            "paper_code": None,
        },
    ],
}


def _write_sample_cache(gz_path: Path) -> None:
    gz_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(gz_path, "wt", encoding="utf-8") as f:
        for conf, papers in SAMPLE_PAPERS.items():
            for paper in papers:
                record = dict(paper)
                record["conf"] = conf
                f.write(json.dumps(record, ensure_ascii=False) + "\n")


@pytest.fixture
def sample_cache_path(tmp_path: Path) -> Path:
    cache_path = tmp_path / "cache" / "cache.jsonl.gz"
    _write_sample_cache(cache_path)
    return cache_path


def _build_app(tmp_path: Path, cache_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAPERVAULT_OFFLINE", "1")
    monkeypatch.setenv("HF_TOKEN", "")
    monkeypatch.setenv("PAPERVAULT_HF_REPO_ID", "")

    from papervault import create_app
    from papervault.config import Settings

    static_dir = tmp_path / "static" / "dist"
    static_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        base_dir=tmp_path,
        cache_path=cache_path,
        static_folder=static_dir,
    )
    app = create_app(settings, eager_load=True)
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def app_with_sample(tmp_path: Path, sample_cache_path: Path, monkeypatch: pytest.MonkeyPatch):
    return _build_app(tmp_path, sample_cache_path, monkeypatch)


@pytest.fixture
def client_with_sample(app_with_sample):
    return app_with_sample.test_client()


@pytest.fixture
def repository_with_sample(sample_cache_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAPERVAULT_OFFLINE", "1")
    from papervault.services.papers import PaperRepository

    repo = PaperRepository(cache_path=sample_cache_path, refresh_on_load=False)
    repo.ensure_loaded()
    return repo
