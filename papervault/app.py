"""Flask application factory for PaperVault."""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from flask import Flask, send_from_directory

from .api.v1 import confs_bp, health_bp, papers_bp, suggest_bp
from .config import Settings, get_settings
from .errors import register_error_handlers
from .logging import configure_logging, install_request_id
from .services.papers import PaperRepository

logger = logging.getLogger("papervault.app")


def create_app(settings: Settings | None = None, *, eager_load: bool = True) -> Flask:
    load_dotenv()
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = Flask(
        __name__.split(".")[0],
        static_folder=str(settings.static_folder),
        static_url_path="",
    )
    app.config["JSON_SORT_KEYS"] = False
    app.config["TEMPLATES_AUTO_RELOAD"] = False

    repository = PaperRepository(cache_path=settings.cache_path)
    if eager_load:
        try:
            repository.ensure_loaded()
        except Exception:  # pragma: no cover - defensive, do not block boot
            logger.exception("Initial cache load failed; will retry lazily on first request.")

    app.extensions["paper_repository"] = repository
    app.extensions["settings"] = settings

    install_request_id(app)
    register_error_handlers(app)

    app.register_blueprint(health_bp, url_prefix="/api/v1")
    app.register_blueprint(confs_bp, url_prefix="/api/v1")
    app.register_blueprint(papers_bp, url_prefix="/api/v1")
    app.register_blueprint(suggest_bp, url_prefix="/api/v1")

    @app.get("/")
    def _root():
        return send_from_directory(app.static_folder, "index.html")

    @app.errorhandler(404)
    def _spa_fallback(_err):  # type: ignore[override]
        return send_from_directory(app.static_folder, "index.html")

    return app
