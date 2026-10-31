"""Regression tests for :mod:`collector.url_types`.

Corresponds to spec ``AC-3`` / ``TR-4.*``.
"""

from __future__ import annotations

import pytest

from collector.url_types import classify_paper_url, is_venue_index


# ---------- ACL Anthology --------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        # New-style single paper URLs
        "https://aclanthology.org/2023.acl-long.1/",
        "https://aclanthology.org/2023.acl-long.42/",
        "https://aclanthology.org/2021.findings-emnlp.100/",
        # Old-style anthology paper ids
        "https://aclanthology.org/P05-1001/",
        "https://aclanthology.org/N12-1001/",
    ],
)
def test_acl_paper_urls_classified_as_paper(url):
    assert classify_paper_url(url) == "paper"
    assert not is_venue_index(url)


@pytest.mark.parametrize(
    "url",
    [
        # Front-matter stub — the file that historically poisoned the cache
        "https://aclanthology.org/2021.acl-long.0/",
        "https://aclanthology.org/2020.findings-emnlp.0/",
        # Volume / event catalog pages
        "https://aclanthology.org/volumes/2023.acl-long/",
        "https://aclanthology.org/events/acl-2023/",
        # Site root
        "https://aclanthology.org/",
    ],
)
def test_acl_venue_index_urls(url):
    assert classify_paper_url(url) == "venue-index"
    assert is_venue_index(url)


# ---------- theCVF Open Access --------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "https://openaccess.thecvf.com/content/CVPR2023/html/"
        "Author_Paper_Title_CVPR_2023_paper.html",
        "https://openaccess.thecvf.com/content_CVPR_2020/html/"
        "Foo_Bar_CVPR_2020_paper.html",
    ],
)
def test_thecvf_paper_urls(url):
    assert classify_paper_url(url) == "paper"


@pytest.mark.parametrize(
    "url",
    [
        # Year landing pages
        "https://openaccess.thecvf.com/CVPR2020",
        "https://openaccess.thecvf.com/ICCV2019",
        "https://openaccess.thecvf.com/WACV2024",
        # Landing "menu" style
        "https://openaccess.thecvf.com/menu",
        # Site root
        "https://openaccess.thecvf.com/",
    ],
)
def test_thecvf_venue_index_urls(url):
    assert classify_paper_url(url) == "venue-index"


# ---------- OpenReview -----------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "https://openreview.net/pdf?id=abc123",
        "https://openreview.net/forum?id=abc123",
    ],
)
def test_openreview_paper_urls(url):
    assert classify_paper_url(url) == "paper"


@pytest.mark.parametrize(
    "url",
    [
        "https://openreview.net/group?id=ICLR.cc/2021/Conference",
        "https://openreview.net/",
        # Review issue #3: an empty ``id=`` value must classify as venue-index
        # so the pipeline never issues a real HTTP for a landing page.
        "https://openreview.net/pdf?id=",
        "https://openreview.net/forum?id=",
        "https://openreview.net/pdf?id=&foo=1",
        # No query string at all.
        "https://openreview.net/pdf",
        "https://openreview.net/forum",
    ],
)
def test_openreview_venue_index_urls(url):
    assert classify_paper_url(url) == "venue-index"


# ---------- Fallback / unknown --------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "https://arxiv.org/abs/2301.00001",
        "https://papers.nips.cc/paper/2020/hash/xyz",
        "https://dblp.org/rec/conf/aaai/foo",
        "",
        None,
    ],
)
def test_unknown_domains_return_unknown(url):
    assert classify_paper_url(url) == "unknown"
    assert not is_venue_index(url or "")


# ---------- IJCAI proceedings (fourth review pass, issue #4) --------------
@pytest.mark.parametrize(
    "url",
    [
        "https://www.ijcai.org/proceedings/2020/0001",
        "https://www.ijcai.org/proceedings/2023/42",
        "https://www.ijcai.org/proceedings/2019/100/",
        "https://ijcai.org/proceedings/2024/007",
    ],
)
def test_ijcai_paper_urls(url):
    assert classify_paper_url(url) == "paper"
    assert not is_venue_index(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.ijcai.org/proceedings/2020",
        "https://www.ijcai.org/proceedings/2020/",
        "https://www.ijcai.org/proceedings/",
        "https://www.ijcai.org/proceedings",
    ],
)
def test_ijcai_venue_index_urls(url):
    assert classify_paper_url(url) == "venue-index"
    assert is_venue_index(url)
