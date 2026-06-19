"""PaperVault web application package.

Phase 1 of the refactor introduces an application factory + Blueprint layout
and a versioned ``/api/v1`` surface. Legacy ``/api/search`` and
``/api/get_guess_you_like`` are removed because no external clients depend on
them yet (see ``docs/refactor-plan.md``).
"""

from .app import create_app

__all__ = ["create_app"]
