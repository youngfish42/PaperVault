"""Capture a PaperVault web UI screenshot for the README.

Usage (after starting the backend at :5001 and the Vite dev server at :8080):

    python scripts/capture_screenshot.py
    python scripts/capture_screenshot.py --url http://localhost:8080/ \
        --output pics/screenshot/web.jpg --width 1280 --height 540 --scale 2

The default viewport is 1280x540 (~21:9 cinematic banner) captured at 2x
device scale. This wide-and-short ratio matches the landing hero region
(brand title + search box + helper buttons) so the screenshot looks
visually balanced when embedded at width=850 in the README.

If the Chromium binary used by Playwright is missing, the script will try to
install it automatically via ``python -m playwright install chromium``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "pics" / "screenshot" / "web.jpg"
DEFAULT_URL = "http://localhost:8080/"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 540
DEFAULT_SCALE = 2
DEFAULT_QUALITY = 88


def _ensure_chromium() -> None:
    """Install Chromium for Playwright on first use."""
    print("[capture] chromium not found, running 'playwright install chromium' ...")
    subprocess.check_call(
        [sys.executable, "-m", "playwright", "install", "chromium"]
    )


def capture(
    url: str,
    output: Path,
    width: int,
    height: int,
    quality: int,
    scale: int,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except PlaywrightError as exc:
            if "Executable doesn't exist" not in str(exc):
                raise
            _ensure_chromium()
            browser = p.chromium.launch()

        context = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=scale,
            locale="zh-CN",
        )
        page = context.new_page()
        print(f"[capture] navigating to {url}")
        page.goto(url, wait_until="networkidle", timeout=45_000)

        # Give the SPA a moment to finish hydration / facet rendering.
        time.sleep(1.5)

        # Best-effort: dismiss any lingering Element Plus overlay so the
        # screenshot reflects the default landing state.
        try:
            page.keyboard.press("Escape")
        except PlaywrightError:
            pass

        page.screenshot(
            path=str(output),
            type="jpeg",
            quality=quality,
            full_page=False,
            clip={"x": 0, "y": 0, "width": width, "height": height},
        )
        browser.close()

    size_kb = output.stat().st_size / 1024
    print(
        f"[capture] saved {output.relative_to(REPO_ROOT)} "
        f"(viewport {width}x{height} @ {scale}x, {size_kb:.1f} KB)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--scale", type=int, default=DEFAULT_SCALE,
                        help="device scale factor (default 2 for Retina)")
    parser.add_argument("--quality", type=int, default=DEFAULT_QUALITY)
    args = parser.parse_args()

    capture(
        args.url,
        args.output.resolve(),
        args.width,
        args.height,
        args.quality,
        args.scale,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
