"""Flask web entrypoint for PaperVault.

The actual application is constructed in :mod:`papervault.app` via
``create_app``; legacy ``/api/search`` and ``/api/get_guess_you_like`` have
been removed in favour of the versioned ``/api/v1`` surface (see
``docs/refactor-plan.md``).
"""

from __future__ import annotations

import logging
import os

from papervault import create_app
from papervault.config import get_settings

settings = get_settings()
app = create_app(settings)

logger = logging.getLogger("papervault.entry")


if __name__ == "__main__":
    debug = settings.debug or os.environ.get("FLASK_DEBUG", "0") == "1"
    host = settings.host
    port = settings.port
    logger.info("Starting Flask on %s:%s (debug=%s)", host, port, debug)
    app.run(debug=debug, host=host, port=port, use_reloader=debug)
