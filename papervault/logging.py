"""Structured logging setup."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from flask import Flask, g, request


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] [%(request_id)s] %(message)s",
    )

    old_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.request_id = _current_request_id() or "-"
        return record

    logging.setLogRecordFactory(factory)


def _current_request_id() -> Optional[str]:
    try:
        return getattr(g, "request_id", None)
    except RuntimeError:
        return None


def install_request_id(app: Flask) -> None:
    @app.before_request
    def _assign_request_id() -> None:
        g.request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex

    @app.after_request
    def _emit_request_id(response):  # type: ignore[override]
        rid = getattr(g, "request_id", None)
        if rid:
            response.headers["X-Request-Id"] = rid
        return response
