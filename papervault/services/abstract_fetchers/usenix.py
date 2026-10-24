"""USENIX (``www.usenix.org``) abstract fetcher.

Verified against real pages on 2026-10-19:

* Legacy layout (pre-2020, e.g.
  ``https://www.usenix.org/conference/nsdi12/technical-sessions/presentation/zaharia``)
  and the modern Drupal layout (e.g.
  ``https://www.usenix.org/conference/nsdi24/presentation/zhang-zili-pipelining``)
  BOTH place the abstract inside::

      <div class="field field-name-field-paper-description ...">
        <div class="field-items"><div class="field-item">
          <p>... full abstract prose ...</p>   (legacy pages use <p class="p1">)

  so one selector covers both templates.

* A further fallback handles the oldest layouts where the abstract
  follows a literal "Abstract" heading (``<H3>Abstract</H3>`` on the
  2001-2006 era pages). Those pages use ``<P>`` as a *separator* and
  keep the prose in raw text nodes, so the walk iterates
  ``next_siblings`` (including NavigableString) and stops at the first
  heading / list / rule (the "View the full text" ``<UL>`` block).

Path guard: ``usenix.org`` also serves the front page, membership and
conference landing pages. Fetched only:

* ``/conference/...`` URLs that also contain ``/presentation/``,
* non-media pages under ``/legacy/events/``,
* ``/events/<slug>/tech/<name>.html`` paper pages (2001-2006 era) —
  but NOT ``techAbstracts.html`` (a multi-abstract index with anchors,
  not a single-paper page) and not bare ``/events/<slug>/`` landings,
* ``/publications/library/proceedings/<conf>/....html`` paper pages.

Media files (``.pdf`` ``.mp3`` ``.mp4`` ``.ppt`` ``.ps`` ``.zip``
``.wav`` ...) always short-circuit before the network call.

Registry note: the dispatcher canonicalises hosts by stripping a
leading ``www.``, so only the bare ``usenix.org`` key can ever match
(listing ``www.usenix.org`` in ``allowed_hosts`` would be a dead
registry key).
"""

from __future__ import annotations

from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .base import AbstractResult, Fetcher, MIN_ABSTRACT_CHARS
from ._http import clean_text, http_get, strip_abstract_label

# Media / non-HTML payloads linked from USENIX pages — never fetched.
_MEDIA_EXTENSIONS = (
    ".pdf", ".mp3", ".mp4", ".ppt", ".pptx", ".ps", ".zip",
    ".wav", ".gz", ".mov", ".m4v", ".epub",
)


def _looks_like_presentation(url: str) -> bool:
    """Return True iff the URL looks like a USENIX paper/presentation page."""
    path = (urlparse(url).path or "").lower()
    if path.endswith(_MEDIA_EXTENSIONS):
        return False
    if "/conference/" in path and "/presentation/" in path:
        return True
    if "/legacy/events/" in path:
        return True
    if "/events/" in path and "/tech/" in path and path.endswith((".html", ".htm")):
        # ``techAbstracts.html`` is a per-edition index page that stacks
        # every paper's abstract behind anchors — not a single-paper page.
        if path.rsplit("/", 1)[-1] == "techabstracts.html":
            return False
        return True
    if "/publications/library/proceedings/" in path and path.endswith((".html", ".htm")):
        return True
    return False


# Sibling tags that terminate the abstract walk on the oldest layouts:
# another heading, the "View the full text" list, horizontal rules, ...
_HEADING_WALK_STOP_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "hr", "table", "form", "address",
}


def _extract_heading_walk(soup) -> str:
    """Fallback for the oldest layouts: abstract prose after an "Abstract"
    heading (``<h1>-<h6>`` or ``<strong>`` labels).

    ``/conference/``-era pages wrap the prose in ``<p>`` paragraphs,
    while the 2001-2006 era pages (``/events/<slug>/tech/``,
    ``/publications/library/proceedings/``) use ``<P>`` as a bare
    separator and keep the prose in *raw text nodes* — so the walk must
    iterate ``next_siblings`` (which includes NavigableString) rather
    than ``find_next_siblings`` (which skips text), and must stop at the
    "View the full text" ``<UL>`` / next heading instead of the first
    non-``<p>`` sibling.
    """
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong"]):
        if not heading.get_text(strip=True).lower().startswith("abstract"):
            continue
        # html.parser does NOT auto-close bare ``<P>`` separators, so on
        # the 2001-2006 pages the following prose nests inside the first
        # ``<P>`` and a ``next_siblings`` walk would either miss it or
        # (via ``get_text`` on the nested chain) slurp the trailing
        # "View the full text" <UL> block. ``next_elements`` walks in
        # document order regardless of nesting and lets us break at the
        # first stop tag before its text is consumed. The heading's own
        # descendants (the literal "Abstract" label) are skipped first.
        own = {id(d) for d in heading.descendants}
        parts = []
        for el in heading.next_elements:
            if id(el) in own:
                continue
            name = getattr(el, "name", None)
            if name in _HEADING_WALK_STOP_TAGS:
                break
            if name is None:
                parts.append(str(el))
        text = clean_text(" ".join(parts))
        if text:
            return text
    return ""


class _UsenixFetcher(Fetcher):
    allowed_hosts = ("usenix.org",)
    source = "usenix"

    def fetch(self, url: str) -> AbstractResult:
        if not _looks_like_presentation(url):
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="no_abstract_available",
            )

        resp, err = http_get(url)
        if err is not None:
            return err
        soup = BeautifulSoup(resp.text, "html.parser")

        text = ""
        node = soup.find("div", class_="field-name-field-paper-description")
        if node is not None:
            strip_abstract_label(node)
            text = clean_text(node.get_text(" "))
        # Container residue too short to be an abstract → still try the
        # heading-walk fallback before giving up.
        if len(text) < MIN_ABSTRACT_CHARS:
            text = _extract_heading_walk(soup)

        if not text or len(text) < MIN_ABSTRACT_CHARS:
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="empty_abstract", http_status=resp.status_code,
            )
        return AbstractResult(
            ok=True, url=url, abstract=text, source=self.source,
            http_status=resp.status_code,
        )


USENIX_FETCHER = _UsenixFetcher()
