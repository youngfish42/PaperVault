"""Structured logging setup."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from flask import Flask, g, request


_CONFIGURED: bool = False
_REQUEST_ID_MARKER = "_papervault_request_id_factory"


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging exactly once.

    Guarded with a module-level flag so repeated ``create_app`` calls (notably
    in tests) do not stack new LogRecord factories on top of the previous ones,
    which would create a self-referential factory chain.
    """

    global _CONFIGURED
    if _CONFIGURED:
        # Allow log level to be updated on subsequent calls without rebuilding
        # the factory chain.
        logging.getLogger().setLevel(level)
        return

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] [%(request_id)s] %(message)s",
    )

    old_factory = logging.getLogRecordFactory()

    # Defensive: if a previous import already installed our factory, bail out.
    if getattr(old_factory, _REQUEST_ID_MARKER, False):
        _CONFIGURED = True
        return

    def factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.request_id = _current_request_id() or "-"
        return record

    setattr(factory, _REQUEST_ID_MARKER, True)
    logging.setLogRecordFactory(factory)
    _CONFIGURED = True


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
