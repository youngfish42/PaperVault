"""Unified error responses for the v1 API.

Response shape:

    { "error": { "code": "BAD_REQUEST", "message": "...", "details": {...} } }
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

logger = logging.getLogger("papervault.errors")


class ApiError(Exception):
    status_code: int = 400
    code: str = "BAD_REQUEST"

    def __init__(self, message: str, *, status_code: Optional[int] = None,
                 code: Optional[str] = None, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        self.details = details


class NotFoundError(ApiError):
    status_code = 404
    code = "NOT_FOUND"


class UpstreamError(ApiError):
    status_code = 502
    code = "UPSTREAM_ERROR"


def _envelope(code: str, message: str, details: Any = None) -> dict:
    body: dict = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return body


def _handle_api_error(err: ApiError):
    return jsonify(_envelope(err.code, err.message, err.details)), err.status_code


def _handle_http_exception(err: HTTPException):
    return (
        jsonify(_envelope(err.name.upper().replace(" ", "_"), err.description or err.name)),
        err.code or 500,
    )


def _handle_unexpected(err: Exception):
    logger.exception("Unhandled exception: %s", err)
    return jsonify(_envelope("INTERNAL_ERROR", "Internal server error")), 500


def register_error_handlers(app: Flask) -> None:
    app.register_error_handler(ApiError, _handle_api_error)
    app.register_error_handler(HTTPException, _handle_http_exception)
    app.register_error_handler(Exception, _handle_unexpected)
