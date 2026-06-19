"""Runtime configuration for the PaperVault web service.

All env-driven fields use ``field(default_factory=...)`` so that ``Settings``
is reconstructed from the *current* environment every time ``get_settings()``
is invoked. This keeps the dataclass ``frozen=True`` (cheap hashable value
object) while avoiding the classic "defaults captured at import time" trap
that would otherwise make environment overrides in tests ineffective.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


_BASE_DIR = Path(__file__).resolve().parent.parent


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    base_dir: Path = _BASE_DIR
    cache_path: Path = _BASE_DIR / "cache" / "cache.jsonl.gz"
    static_folder: Path = _BASE_DIR / "static" / "dist"

    host: str = field(default_factory=lambda: _env_str("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("PORT", 5001))
    debug: bool = field(default_factory=lambda: _env_bool("FLASK_DEBUG", False))

    log_level: str = field(
        default_factory=lambda: _env_str("PAPERVAULT_LOG_LEVEL", "INFO")
    )

    openai_model: str = field(
        default_factory=lambda: _env_str("PAPERVAULT_OPENAI_MODEL", "gpt-3.5-turbo")
    )
    openai_temperature: float = field(
        default_factory=lambda: _env_float("PAPERVAULT_OPENAI_TEMPERATURE", 0.5)
    )
    openai_max_keywords: int = field(
        default_factory=lambda: _env_int("PAPERVAULT_OPENAI_MAX_KEYWORDS", 10)
    )

    cors_origins: str = field(
        default_factory=lambda: _env_str("PAPERVAULT_CORS_ORIGINS", "")
    )

    max_page_size: int = field(
        default_factory=lambda: _env_int("PAPERVAULT_MAX_PAGE_SIZE", 200)
    )
    default_page_size: int = field(
        default_factory=lambda: _env_int("PAPERVAULT_DEFAULT_PAGE_SIZE", 50)
    )


def get_settings() -> Settings:
    """Build a fresh ``Settings`` instance from the current environment."""

    return Settings()
