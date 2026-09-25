"""Tests for the saved search queries (favorites) API."""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import _build_app

BASE = "/api/v1/saved_queries"


@pytest.fixture(autouse=True)
def _user_db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Redirect the user DB into the per-test tmp dir. This must run before
    # any fixture builds the app, because ``Settings`` reads the env var at
    # construction time.
    monkeypatch.setenv("PAPERVAULT_USER_DB_PATH", str(tmp_path / "users.sqlite3"))


@pytest.fixture
def quota_client(tmp_path: Path, sample_cache_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAPERVAULT_SAVED_QUERIES_MAX_PER_USER", "2")
    app = _build_app(tmp_path, sample_cache_path, monkeypatch)
    return app.test_client()


def _login(client, user_id: str = "u1", name: str = "User One"):
    with client.session_transaction() as sess:
        sess["papervault_user"] = {
            "provider": "github",
            "id": user_id,
            "name": name,
        }


def _logout(client):
    with client.session_transaction() as sess:
        sess.pop("papervault_user", None)
        sess.pop("papervault_admin", None)


def _create(client, name: str = "My Query", dsl: str = 'TI="attention"', last_count=None):
    payload = {"name": name, "dsl": dsl}
    if last_count is not None:
        payload["last_count"] = last_count
    return client.post(BASE, json=payload)


def test_unauthenticated_requests_return_401(client_with_sample):
    client = client_with_sample

    for resp in (
        client.get(BASE),
        client.post(BASE, json={"name": "x", "dsl": "y"}),
        client.patch(f"{BASE}/1", json={"name": "x"}),
        client.delete(f"{BASE}/1"),
    ):
        assert resp.status_code == 401
        assert resp.get_json()["error"]["code"] == "UNAUTHORIZED"


def test_full_crud_flow(client_with_sample):
    client = client_with_sample
    _login(client)

    resp = _create(client, last_count=7)
    assert resp.status_code == 201
    created = resp.get_json()
    assert created["name"] == "My Query"
    assert created["dsl"] == 'TI="attention"'
    assert created["last_count"] == 7
    assert created["created_at"]
    assert created["updated_at"]

    resp = client.get(BASE)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == created["id"]

    resp = client.patch(
        f"{BASE}/{created['id']}",
        json={"name": "Renamed", "dsl": 'AU="Alice Adams"', "last_count": 12},
    )
    assert resp.status_code == 200
    updated = resp.get_json()
    assert updated["name"] == "Renamed"
    assert updated["dsl"] == 'AU="Alice Adams"'
    assert updated["last_count"] == 12
    assert updated["created_at"] == created["created_at"]

    resp = client.get(BASE)
    item = resp.get_json()["items"][0]
    assert item["name"] == "Renamed"

    resp = client.delete(f"{BASE}/{created['id']}")
    assert resp.status_code == 200
    assert resp.get_json() == {"deleted": True}

    resp = client.get(BASE)
    assert resp.get_json()["total"] == 0


def test_user_isolation(client_with_sample):
    client = client_with_sample
    _login(client, user_id="u1", name="User One")
    created = _create(client).get_json()
    query_id = created["id"]

    _login(client, user_id="u2", name="User Two")

    resp = client.get(BASE)
    assert resp.get_json()["total"] == 0

    resp = client.patch(f"{BASE}/{query_id}", json={"name": "Hijack"})
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "NOT_FOUND"

    resp = client.delete(f"{BASE}/{query_id}")
    assert resp.status_code == 404

    # User A's entry is untouched.
    _login(client, user_id="u1", name="User One")
    resp = client.get(BASE)
    body = resp.get_json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "My Query"


def test_admin_session_gets_own_user_key(client_with_sample):
    client = client_with_sample
    with client.session_transaction() as sess:
        sess["papervault_admin"] = True

    resp = _create(client, name="Admin Query")
    assert resp.status_code == 201
    resp = client.get(BASE)
    assert resp.get_json()["total"] == 1


def test_quota_exceeded(quota_client):
    client = quota_client
    _login(client)

    assert _create(client, name="q1").status_code == 201
    assert _create(client, name="q2").status_code == 201

    resp = _create(client, name="q3")
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == "QUOTA_EXCEEDED"

    # The quota is per-user: another user is unaffected by user A's cap.
    _login(client, user_id="u2", name="User Two")
    assert _create(client, name="other-user").status_code == 201


def test_validation_errors(client_with_sample):
    client = client_with_sample
    _login(client)

    resp = client.post(BASE, json={"name": "", "dsl": "x"})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "BAD_REQUEST"

    resp = client.post(BASE, json={"name": "   ", "dsl": "x"})
    assert resp.status_code == 400

    resp = client.post(BASE, json={"name": "ok", "dsl": "a" * 2001})
    assert resp.status_code == 400

    resp = client.post(BASE, json={"name": "ok", "dsl": "x", "last_count": -1})
    assert resp.status_code == 400

    created = _create(client).get_json()
    resp = client.patch(f"{BASE}/{created['id']}", json={})
    assert resp.status_code == 400

    # Explicit-null name/dsl pass the schema's "at least one field" check but
    # would change nothing — the endpoint must reject them as a no-op update
    # instead of silently bumping ``updated_at``.
    resp = client.patch(f"{BASE}/{created['id']}", json={"name": None})
    assert resp.status_code == 400
    resp = client.patch(f"{BASE}/{created['id']}", json={"dsl": None})
    assert resp.status_code == 400


def test_last_count_set_and_cleared(client_with_sample):
    client = client_with_sample
    _login(client)

    created = _create(client).get_json()
    assert created["last_count"] is None

    resp = client.patch(f"{BASE}/{created['id']}", json={"last_count": 42})
    assert resp.status_code == 200
    assert resp.get_json()["last_count"] == 42

    resp = client.patch(f"{BASE}/{created['id']}", json={"last_count": None})
    assert resp.status_code == 200
    assert resp.get_json()["last_count"] is None

    # Omitting last_count leaves the (cleared) value untouched.
    resp = client.patch(f"{BASE}/{created['id']}", json={"name": "Other"})
    assert resp.status_code == 200
    assert resp.get_json()["last_count"] is None
