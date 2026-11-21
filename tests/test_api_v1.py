"""Smoke tests for the /api/v1 surface.

These tests run fully offline (``PAPERVAULT_OFFLINE=1``) against an empty
cache directory, so they require no network and no Hugging Face credentials.
The goal is to lock in the response contract (status codes, top-level keys),
not to exercise real data.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("PAPERVAULT_OFFLINE", "1")


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAPERVAULT_OFFLINE", "1")
    monkeypatch.setenv("HF_TOKEN", "")
    monkeypatch.setenv("PAPERVAULT_HF_REPO_ID", "")

    from papervault import create_app
    from papervault.config import Settings

    settings = Settings(
        base_dir=tmp_path,
        cache_path=tmp_path / "cache" / "cache.jsonl.gz",
        static_folder=tmp_path / "static" / "dist",
    )
    (tmp_path / "static" / "dist").mkdir(parents=True, exist_ok=True)

    flask_app = create_app(settings, eager_load=False)
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def test_healthz_returns_ok(client):
    resp = client.get("/api/v1/healthz")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert "papers" in body
    assert "confs" in body


def test_confs_returns_empty_list(client):
    resp = client.get("/api/v1/confs")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["items"] == []
    assert body["total"] == 0


def test_papers_returns_paginated_empty(client):
    resp = client.get("/api/v1/papers?size=1")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["items"] == []
    assert body["meta"] == {"page": 1, "size": 1, "total": 0}


def test_papers_rejects_invalid_since(client):
    resp = client.get("/api/v1/papers?since=abc")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"]["code"] == "BAD_REQUEST"
    assert "details" in body["error"]


def test_papers_rejects_oversize_page(client):
    resp = client.get("/api/v1/papers?size=99999")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"]["code"] == "BAD_REQUEST"


def test_papers_accepts_multiple_conf_filters(client):
    resp = client.get("/api/v1/papers?conf=ACL&conf=EMNLP&size=10")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["meta"]["size"] == 10
    assert body["items"] == []
