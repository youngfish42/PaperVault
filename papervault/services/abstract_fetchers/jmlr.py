"""JMLR (``jmlr.org``) abstract fetcher.

Verified against real pages on 2026-07-18 across three structural
generations of JMLR paper pages:

Modern layout (verified on v10 → v25, i.e. JMLR 2009-2025)::

    <h3>Abstract</h3>
    <p class="abstract">
      ... full abstract prose ...
    </p>

The ``<p class="abstract">`` node is stable; primary selector is a
single ``soup.find("p", class_="abstract")``.

Legacy layout (verified on v1 ``meila00a`` and v5 ``evendar03a`` /
``lanckriet04a``)::

    <h3>Abstract</h3>
    plain text nodes with the full abstract prose,
    possibly interleaved with inline <i>/<sup> children ...
    <font color="gray"><p>[abs]</p></font>
    [<a href="...pdf">pdf</a>] ...

Crucially the abstract prose sits as **raw NavigableString / inline
element** children of the parent (the ``<h3>``'s siblings), *not*
inside a wrapping ``<p>``. Iterating ``heading.find_next_siblings()``
skips all NavigableString nodes and would return empty; instead we
walk ``heading.next_siblings`` and stop when we hit the download-links
block, which is signalled by any of:

* another heading (``<h1..h6>``)
* a ``<font>`` (JMLR wraps ``[abs]`` in ``<font color="gray">``)
* a ``<p>`` (JMLR wraps ``[pdf]/[ps]`` in a ``<p>``)
* a leading ``[`` in a text sibling — this catches the bare ``[pdf]``
  block that some pages emit without the ``<font>`` wrapper.

We deliberately drop the previous ``<b>Abstract:</b>`` fallback: none
of the pages we sampled across v1..v25 use that layout. If a genuine
edge case emerges later it should come with a real URL / fixture so we
know what we are targeting.

Fallback gating: the modern selector wins whenever it produces at
least :data:`MIN_ABSTRACT_CHARS` of prose. If it matches but returns
a shorter string (a corner case on hybrid pages that carry both an
empty ``<p class="abstract">`` shell and a legacy sibling layout), we
retry the legacy walk and keep whichever branch produced the longer
text. This mirrors the pipeline's own MIN_ABSTRACT_CHARS gate so we
never silently accept a truncated modern hit while a fuller legacy
version is sitting one branch away.

Host handling: both ``www.jmlr.org`` and ``jmlr.org`` are accepted;
the dispatcher already strips a leading ``www.``, so we only list the
canonical form.
"""

from __future__ import annotations

from bs4 import BeautifulSoup, NavigableString

from .base import AbstractResult, Fetcher, MIN_ABSTRACT_CHARS
from ._http import clean_text, http_get


_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_STOP_TAGS = _HEADING_TAGS | {"font", "p"}


def _collect_between_heading_and_links(heading) -> str:
    """Walk siblings after ``heading`` collecting text until the JMLR
    download-links block starts.

    Handles the legacy ``<h3>Abstract</h3>`` + raw text-node layout used
    on volumes v1..v5 as well as any future page that keeps the text
    outside an explicit ``<p>``. The modern layout already matches the
    primary ``<p class="abstract">`` selector, so this helper is only
    invoked as a fallback and is expected to be defensive.
    """
    parts: list[str] = []
    for node in heading.next_siblings:
        # 1) Raw text node: keep it.
        if isinstance(node, NavigableString):
            piece = clean_text(str(node))
            if piece:
                # Some legacy pages leave a stray ``[`` at the start of
                # the download-links row *outside* the <font> wrapper.
                # If we see one before we have accumulated any prose,
                # skip it; if we see one after prose has started, treat
                # it as a stop signal.
                if piece.startswith("["):
                    if parts:
                        break
                    continue
                parts.append(piece)
            continue

        # 2) Element node.
        name = getattr(node, "name", None)
        if name is None:
            continue
        if name in _STOP_TAGS:
            # New section (heading) or the JMLR link block (<font>/<p>).
            break

        # 3) Inline element such as <i>, <sup>, <em>, <b>, <span>. Some
        # legacy JMLR abstracts have inline math markup. Keep the text
        # but stop if the element itself is empty (defensive).
        piece = clean_text(node.get_text(" "))
        if piece:
            parts.append(piece)

    return " ".join(parts).strip()


class _JMLRFetcher(Fetcher):
    allowed_hosts = ("jmlr.org",)
    source = "jmlr"

    def fetch(self, url: str) -> AbstractResult:
        resp, err = http_get(url)
        if err is not None:
            return err
        soup = BeautifulSoup(resp.text, "html.parser")

        text = ""

        # Modern layout: <p class="abstract">
        node = soup.find("p", class_="abstract")
        if node is not None:
            text = clean_text(node.get_text(" "))

        # Legacy layout: raw text between <h3>Abstract</h3> and the
        # download-links block. Entered when the primary selector did
        # not match OR produced prose shorter than MIN_ABSTRACT_CHARS —
        # some hybrid pages (e.g. a v1..v5 URL rehosted with a
        # near-empty modern shell) match ``<p class="abstract">`` but
        # keep the real prose in the legacy sibling layout, so treating
        # "matched but too short" as "no modern hit" gives the legacy
        # branch a chance to recover the content.
        if not text or len(text) < MIN_ABSTRACT_CHARS:
            for heading in soup.find_all(list(_HEADING_TAGS)):
                label = heading.get_text(strip=True).lower()
                if not label.startswith("abstract"):
                    continue
                legacy_text = _collect_between_heading_and_links(heading)
                if legacy_text and len(legacy_text) >= len(text):
                    # Only replace the modern result if legacy actually
                    # improves on it (defensive: avoid overwriting a
                    # short-but-real abstract with an empty legacy walk
                    # that stopped immediately at the modern <p>).
                    text = legacy_text
                if text and len(text) >= MIN_ABSTRACT_CHARS:
                    break

        if not text or len(text) < MIN_ABSTRACT_CHARS:
            return AbstractResult(
                ok=False, url=url, source=self.source,
                reason="empty_abstract", http_status=resp.status_code,
            )
        return AbstractResult(
            ok=True, url=url, abstract=text, source=self.source,
            http_status=resp.status_code,
        )


JMLR_FETCHER = _JMLRFetcher()
