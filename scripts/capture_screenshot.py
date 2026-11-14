"""Capture a PaperVault web UI screenshot for the README.

Usage (after starting the backend at :5001 and the Vite dev server at :8080):

    python scripts/capture_screenshot.py                       # Chinese (default)
    python scripts/capture_screenshot.py --lang en             # English
    python scripts/capture_screenshot.py --url http://localhost:8080/ \
        --output pics/screenshot/web.jpg --width 1280 --height 540 --scale 2

The default viewport is 1280x540 (~21:9 cinematic banner) captured at 2x
device scale. This wide-and-short ratio matches the landing hero region
(brand title + search box + helper buttons) so the screenshot looks
visually balanced when embedded at width=850 in the README.

Language switching:
    ``--lang en`` forces the SPA into English by (1) launching the browser
    context with ``locale='en-US'`` and (2) seeding ``localStorage`` with
    ``papervault.lang='en'`` *before* the first navigation, so the very
    first render is already English (no zh-flash before toggle). When
    ``--output`` is not given, the file name becomes ``web.<lang>.jpg``.

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
DEFAULT_OUTPUT_DIR = REPO_ROOT / "pics" / "screenshot"
DEFAULT_URL = "http://localhost:8080/"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 540
DEFAULT_SCALE = 2
DEFAULT_QUALITY = 88
DEFAULT_LANG = "zh"

# Per-language Playwright knobs. The locale acts as a *fallback* signal for
# ``i18n.ts:detect()`` (which keys off ``navigator.language``); we also seed
# localStorage and, as a final guarantee, click the toolbar language toggle
# (``toolbar.lang`` whose label is the *opposite* language's name) so the
# SPA visibly enters the desired locale before we take the screenshot.
LANG_PROFILES = {
    "zh": {
        "locale": "zh-CN",
        # When the SPA is in zh-mode, the toggle reads "English" (i.e. the
        # *other* language). So to *stay* in zh we want to see "English" on
        # the toggle, and to *switch into* zh we click the link labelled
        # "中文" (which means the current state is en).
        "toggle_label_when_current": "English",
        "toggle_label_to_switch_in": "中文",
    },
    "en": {
        "locale": "en-US",
        "toggle_label_when_current": "中文",
        "toggle_label_to_switch_in": "English",
    },
}


def _default_output(lang: str) -> Path:
    suffix = "" if lang == "zh" else f".{lang}"
    return DEFAULT_OUTPUT_DIR / f"web{suffix}.jpg"


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
    lang: str,
) -> None:
    profile = LANG_PROFILES[lang]
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
            locale=profile["locale"],
        )
        # Seed the SPA's persisted language *before* any script on the page
        # runs, so the very first render is already in the desired locale.
        # ``i18n.ts`` reads ``localStorage['papervault.lang']`` in its
        # ``detect()`` helper at module load time.
        context.add_init_script(
            f"try{{localStorage.setItem('papervault.lang','{lang}')}}catch(e){{}}"
        )

        page = context.new_page()
        print(f"[capture] navigating to {url} (lang={lang})")
        page.goto(url, wait_until="networkidle", timeout=45_000)

        # Positive readiness probe + active toggle.
        #
        # The toolbar exposes a single language toggle whose label is the
        # *opposite* language's name (i18n key ``toolbar.lang``):
        #   - SPA in zh -> link reads "English"
        #   - SPA in en -> link reads "中文"
        #
        # So to enter the desired language we (a) check whether the toggle
        # already shows the *opposite* label (meaning we're already there),
        # and (b) otherwise click the link whose text matches
        # ``toggle_label_to_switch_in`` (the label that triggers a switch
        # into the desired language).
        target_label = profile["toggle_label_when_current"]
        switch_label = profile["toggle_label_to_switch_in"]

        def _toggle_text() -> str:
            return page.evaluate(
                """() => {
                    const links = document.querySelectorAll('a, .el-link, span');
                    for (const el of links) {
                        const t = (el.textContent || '').trim();
                        if (t === 'English' || t === '中文') return t;
                    }
                    return '';
                }"""
            )

        # Wait until any toggle text shows up at all (SPA mounted).
        page.wait_for_function(
            """() => {
                const links = document.querySelectorAll('a, .el-link, span');
                for (const el of links) {
                    const t = (el.textContent || '').trim();
                    if (t === 'English' || t === '中文') return true;
                }
                return false;
            }""",
            timeout=15_000,
        )

        current = _toggle_text()
        if current != target_label:
            print(
                f"[capture] toggle reads '{current}', clicking '{switch_label}'"
                f" to enter lang={lang}"
            )
            page.get_by_text(switch_label, exact=True).first.click()
            page.wait_for_function(
                """target => {
                    const links = document.querySelectorAll('a, .el-link, span');
                    for (const el of links) {
                        const t = (el.textContent || '').trim();
                        if (t === target) return true;
                    }
                    return false;
                }""",
                arg=target_label,
                timeout=10_000,
            )
        else:
            print(f"[capture] toggle already reads '{target_label}', lang OK")

        # Small extra settle for facet panels / fonts.
        time.sleep(1.0)

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
        f"(viewport {width}x{height} @ {scale}x, {size_kb:.1f} KB, lang={lang})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument(
        "--lang",
        default=DEFAULT_LANG,
        choices=sorted(LANG_PROFILES.keys()),
        help="UI language to capture (default: zh; English -> web.en.jpg)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output file (default: pics/screenshot/web[.<lang>].jpg)",
    )
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument(
        "--scale",
        type=int,
        default=DEFAULT_SCALE,
        help="device scale factor (default 2 for Retina)",
    )
    parser.add_argument("--quality", type=int, default=DEFAULT_QUALITY)
    args = parser.parse_args()

    output: Path = (args.output or _default_output(args.lang)).resolve()

    capture(
        args.url,
        output,
        args.width,
        args.height,
        args.quality,
        args.scale,
        args.lang,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
