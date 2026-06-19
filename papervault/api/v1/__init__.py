"""v1 API blueprints."""

from .health import bp as health_bp
from .confs import bp as confs_bp
from .papers import bp as papers_bp
from .suggest import bp as suggest_bp

__all__ = ["health_bp", "confs_bp", "papers_bp", "suggest_bp"]
