"""v1 API blueprints."""

from .ai import bp as ai_bp
from .confs import bp as confs_bp
from .health import bp as health_bp
from .papers import bp as papers_bp
from .suggest import bp as suggest_bp

__all__ = ["health_bp", "confs_bp", "papers_bp", "suggest_bp", "ai_bp"]


def register_blueprints(app):
    app.register_blueprint(health_bp, url_prefix="/api/v1")
    app.register_blueprint(confs_bp, url_prefix="/api/v1")
    app.register_blueprint(papers_bp, url_prefix="/api/v1")
    app.register_blueprint(suggest_bp, url_prefix="/api/v1")
    app.register_blueprint(ai_bp, url_prefix="/api/v1")
