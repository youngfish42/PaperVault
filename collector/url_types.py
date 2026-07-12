"""URL classifier that separates "single-paper" URLs from "venue-index" URLs.

Background
----------
The abstract backfill pipeline used to treat every collected ``paper_url``
as if it always pointed to an individual paper page. A large chunk of
records in ``cache.jsonl.gz`` turned out to be *venue index* URLs though:
  * ACL Anthology's ``/2021.acl-long.0/`` entry (the volume-front-matter
    stub that lives next to real papers ``.1/`` ``.2/`` ...);
  * theCVF's yearly landing page (``/CVPR2020`` without any
    ``content_.../..._paper.html`` suffix);
  * OpenReview's ``/group?id=ICLR.cc/2021/Conference`` conference-root
    page.

These URLs by construction never contain a single-paper abstract, so any
attempt to backfill them is guaranteed to fail. This module gives the
collector and the abstract backfill scripts a *single* place to make that
decision instead of scattering ad-hoc regexes across every source module.

The predicate is intentionally conservative: unknown domains fall through
to ``"unknown"`` rather than being coerced into ``"paper"``, so future
callers can decide whether "unknown" should be treated as "paper" (the
historical default) or as "venue-index" (safer for pipelines that must
not touch venue landing pages).

Corresponds to spec ``FR-3`` / ``AC-3``.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse


UrlKind = Literal["paper", "venue-index", "unknown"]


# ---------- ACL Anthology --------------------------------------------------
# Volume front matter: ``/2021.acl-long.0/`` (note the trailing ``.0``) is
# the auto-generated front-matter stub; single papers live at ``.1/``
# ``.2/`` and so on. Old-style anthology paper ids ``P05-1001`` are always
# individual papers and never end in ``00``.
_ACL_FRONT_MATTER_RE = re.compile(r"^/\d{4}\.[A-Za-z0-9-]+\.0/?$")
# ``/volumes/XXX/`` and ``/events/xxx-YYYY/`` are catalog pages that
# enumerate volumes but do not carry a single abstract themselves.
_ACL_VOLUME_CATALOG_RE = re.compile(r"^/(volumes|events)/")
# A single-paper URL looks like ``/2023.acl-long.42/`` or ``/P05-1001/``.
# The trailing sequence-number is at least 1 (``.0`` is reserved for the
# front-matter stub, already caught by ``_ACL_FRONT_MATTER_RE``). We
# still tighten the regex here so a future reordering of the checks in
# ``_classify_aclanthology`` cannot silently regress and label ``.0`` as
# a paper URL.
_ACL_PAPER_RE = re.compile(
    r"^/(?:\d{4}\.[A-Za-z0-9-]+\.(?:0*[1-9][0-9]*)|[A-Za-z]\d{2}-\d+)/?$"
)


def _classify_aclanthology(path: str) -> UrlKind:
    if not path or path == "/":
        return "venue-index"
    if _ACL_FRONT_MATTER_RE.match(path):
        return "venue-index"
    if _ACL_VOLUME_CATALOG_RE.match(path):
        return "venue-index"
    if _ACL_PAPER_RE.match(path):
        return "paper"
    return "unknown"


# ---------- theCVF Open Access --------------------------------------------
# Individual papers always have a ``..._paper.html`` (or ``..._paper.pdf``)
# in the URL; landing pages such as ``/CVPR2020`` or ``/menu`` do not.
_THECVF_PAPER_RE = re.compile(
    r"content[_/].*_paper\.(html|pdf)$", re.IGNORECASE
)
# Year landing pages: ``/CVPR2020``, ``/CVPR2020?day=...``, ``/menu``.
_THECVF_LANDING_RE = re.compile(
    r"^/(?:CVPR|ICCV|WACV)\d{4}(?:_workshops)?(?:/[^/]*)?$", re.IGNORECASE
)


def _classify_thecvf(path: str) -> UrlKind:
    if not path or path == "/":
        return "venue-index"
    if _THECVF_PAPER_RE.search(path):
        return "paper"
    if _THECVF_LANDING_RE.match(path):
        return "venue-index"
    if path.lower().endswith("/menu"):
        return "venue-index"
    return "unknown"


# ---------- OpenReview -----------------------------------------------------
# Individual paper: ``/forum?id=<forum_id>`` or ``/pdf?id=<forum_id>``.
# Conference root:  ``/group?id=ICLR.cc/2021/Conference``.
def _classify_openreview(path: str, query: str) -> UrlKind:
    # A ``/forum`` or ``/pdf`` path with an empty ``id=`` (or no query at
    # all) is a landing page in disguise (spec Task 4, review issue #3):
    # the abstract pipeline would otherwise waste a real HTTP request on
    # a URL that can never yield a paper.
    if path in ("/forum", "/pdf"):
        if not query or "id=" not in query:
            return "venue-index"
        # Reject ``?id=`` with an empty value (``id=&foo=1`` or ``id=``).
        if re.search(r"(?:^|&)id=(?:&|$)", query):
            return "venue-index"
        return "paper"
    if path == "/group":
        return "venue-index"
    if not path or path == "/":
        return "venue-index"
    return "unknown"


# ---------- IJCAI proceedings ---------------------------------------------
# Individual papers live at ``/proceedings/YYYY/NNN`` where ``NNN`` is the
# per-paper sequence id (e.g. ``/proceedings/2020/0001``). The bare
# ``/proceedings/YYYY[/]`` path is the year landing / TOC page and never
# contains a single abstract — the IJCAI fetcher would otherwise happily
# match some unrelated ``<div class="col-md-12">`` on that page and
# return a spurious body (fourth review pass, issue #4).
_IJCAI_PAPER_RE = re.compile(r"^/proceedings/\d{4}/\d+/?$")
_IJCAI_LANDING_RE = re.compile(r"^/proceedings/\d{4}/?$")


def _classify_ijcai(path: str) -> UrlKind:
    if not path or path == "/":
        return "venue-index"
    if _IJCAI_PAPER_RE.match(path):
        return "paper"
    if _IJCAI_LANDING_RE.match(path):
        return "venue-index"
    # ``/proceedings`` (no year) is the top-level catalog.
    if path.rstrip("/") == "/proceedings":
        return "venue-index"
    return "unknown"


def classify_paper_url(url: str) -> UrlKind:
    """Classify a paper URL into one of ``paper`` / ``venue-index`` / ``unknown``.

    Only three domains are meaningful today because those are the ones
    where the backfill pipeline is currently poisoned by venue-index
    entries. Everything else returns ``"unknown"`` so callers can pick a
    domain-specific policy without this module ever silently downgrading a
    real paper URL.
    """
    if not url or not isinstance(url, str):
        return "unknown"
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""

    if host.endswith("aclanthology.org"):
        return _classify_aclanthology(path)
    if host.endswith("openaccess.thecvf.com"):
        return _classify_thecvf(path)
    if host.endswith("openreview.net"):
        return _classify_openreview(path, parsed.query or "")
    if host.endswith("ijcai.org"):
        return _classify_ijcai(path)

    return "unknown"


def is_venue_index(url: str) -> bool:
    """Convenience predicate used by source modules to skip landing pages."""
    return classify_paper_url(url) == "venue-index"
