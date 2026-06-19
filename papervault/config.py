"""Runtime configuration for the PaperVault web service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    base_dir: Path = _BASE_DIR
    cache_path: Path = _BASE_DIR / "cache" / "cache.jsonl.gz"
    static_folder: Path = _BASE_DIR / "static" / "dist"

    host: str = os.environ.get("HOST", "127.0.0.1")
    port: int = int(os.environ.get("PORT", "5001"))
    debug: bool = os.environ.get("FLASK_DEBUG", "0") == "1"

    log_level: str = os.environ.get("PAPERVAULT_LOG_LEVEL", "INFO")

    openai_model: str = os.environ.get("PAPERVAULT_OPENAI_MODEL", "gpt-3.5-turbo")
    openai_temperature: float = float(
        os.environ.get("PAPERVAULT_OPENAI_TEMPERATURE", "0.5")
    )
    openai_max_keywords: int = int(
        os.environ.get("PAPERVAULT_OPENAI_MAX_KEYWORDS", "10")
    )

    cors_origins: str = os.environ.get("PAPERVAULT_CORS_ORIGINS", "")

    max_page_size: int = int(os.environ.get("PAPERVAULT_MAX_PAGE_SIZE", "200"))
    default_page_size: int = int(os.environ.get("PAPERVAULT_DEFAULT_PAGE_SIZE", "50"))


def get_settings() -> Settings:
    return Settings()
