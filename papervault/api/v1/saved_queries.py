"""Saved search queries (favorites) endpoints.

Logged-in users (OAuth profile or admin session) can persist named
advanced-search DSL strings together with a last-seen result count.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, Dict, TypeVar

from flask import Blueprint, current_app, jsonify, request, session
from pydantic import BaseModel, ValidationError

from ...errors import ApiError, NotFoundError
from ...schemas import SavedQueryCreateIn, SavedQueryOut, SavedQueryUpdateIn
from ...services.user_store import get_user_store

bp = Blueprint("saved_queries_v1", __name__)

F = TypeVar("F", bound=Callable[..., Any])
T = TypeVar("T", bound=BaseModel)


def _current_user_key() -> str:
    """Resolve the storage key for the logged-in identity, or 401."""

    user = session.get("papervault_user")
    if isinstance(user, dict) and user.get("provider") and user.get("id"):
        return f"{user['provider']}:{user['id']}"
    if session.get("papervault_admin"):
        # The admin session only stores a boolean flag; the username is
        # sourced from settings (same as ``/api/v1/auth/me``).
        username = current_app.extensions["settings"].admin_username
        return f"admin:{username}"
    raise ApiError("login required", status_code=401, code="UNAUTHORIZED")


def require_user(fn: F) -> F:
    """Guard a view behind a logged-in identity and inject ``user_key``."""

    @wraps(fn)
    def wrapped(*args: Any, **kwargs: Any):
        kwargs["user_key"] = _current_user_key()
        return fn(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


def _parse(model: type[T], message: str) -> T:
    payload = request.get_json(silent=True) or {}
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ApiError(
            message,
            status_code=400,
            code="BAD_REQUEST",
            details=exc.errors(include_url=False, include_context=False),
        )


def _out(row: Dict[str, Any]) -> Dict[str, Any]:
    return SavedQueryOut(**row).model_dump()


@bp.get("/saved_queries")
@require_user
def list_saved_queries(user_key: str):
    rows = get_user_store().list_queries(user_key)
    items = [_out(row) for row in rows]
    return jsonify({"items": items, "total": len(items)})


@bp.post("/saved_queries")
@require_user
def create_saved_query(user_key: str):
    req = _parse(SavedQueryCreateIn, "Invalid saved query request.")

    settings = current_app.extensions["settings"]
    # Count-check and insert run under the store lock so two concurrent
    # requests cannot both pass the quota check.
    row = get_user_store().create_query(
        user_key,
        req.name,
        req.dsl,
        req.last_count,
        max_per_user=settings.saved_queries_max_per_user,
    )
    if row is None:
        raise ApiError(
            "saved query quota exceeded",
            status_code=403,
            code="QUOTA_EXCEEDED",
        )
    return jsonify(_out(row)), 201


@bp.patch("/saved_queries/<int:query_id>")
@require_user
def update_saved_query(user_key: str, query_id: int):
    req = _parse(SavedQueryUpdateIn, "Invalid saved query update.")

    changes: Dict[str, Any] = {}
    if req.name is not None:
        changes["name"] = req.name
    if req.dsl is not None:
        changes["dsl"] = req.dsl
    if "last_count" in req.model_fields_set:
        changes["last_count"] = req.last_count
    if not changes:
        # e.g. ``{"name": null}`` — a field was provided but nothing about
        # the row would actually change.
        raise ApiError(
            "no updatable fields provided",
            status_code=400,
            code="BAD_REQUEST",
        )

    row = get_user_store().update_query(user_key, query_id, **changes)
    if row is None:
        raise NotFoundError("saved query not found")
    return jsonify(_out(row))


@bp.delete("/saved_queries/<int:query_id>")
@require_user
def delete_saved_query(user_key: str, query_id: int):
    if not get_user_store().delete_query(user_key, query_id):
        raise NotFoundError("saved query not found")
    return jsonify({"deleted": True})
