from bs4 import BeautifulSoup

from collector.http import SESSION, HEADERS
from collector.merge import _merge_paper_record
from collector.url_types import is_venue_index


def search_abs_from_thecvf(url):
    r = SESSION.get(url, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")
    abstract_elem = soup.find(id="abstract")
    if abstract_elem:
        return abstract_elem.get_text(strip=True)
    return ""

def search_from_thecvf(url, name, res):
    r = SESSION.get(url, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")
    if name not in res:
        res[name] = []

    # In-batch dedupe by absolute paper URL. theCVF day-split pages
    # (day1.py / day2.py / ALL.py) historically share entries, so the same
    # paper may appear on more than one collected URL within a run.
    seen_urls: dict = {p["paper_url"]: p for p in res[name] if p.get("paper_url")}

    for paper_item in soup.find_all("dt", class_="ptitle"):
        a_tag = paper_item.a
        if a_tag is None:
            continue
        href = a_tag.get("href", "")
        if not href:
            continue
        url_postfix = href
        if url_postfix.startswith('/'):
            url_postfix = url_postfix[1:]
        paper_url = "https://openaccess.thecvf.com/" + href
        # Skip venue landing pages that occasionally appear as ``<a>`` items
        # inside ALL.py / day1.py / day2.py listings (e.g. cross-year
        # navigation stubs). Real paper hrefs always end in ``_paper.html``.
        if is_venue_index(paper_url):
            continue
        paper = a_tag.string if a_tag.string else a_tag.get_text(strip=True)

        authors = []
        ns = paper_item.next_sibling
        if ns:
            ns2 = ns.next_sibling
            if ns2:
                authors = [author.string for author in ns2.find_all('a', href='#') if author.string]

        paper_abstract = ""
        try:
            # Delayed import: scripts/cvf_abstract itself imports HEADERS +
            # search_abs_from_thecvf from this module at load time, so the
            # reverse import must stay inside the function body.
            # NOTE: importing from `scripts.cvf_abstract` (side-effect-free)
            # rather than `scripts.fetch_cvf_abstracts` (a CLI entrypoint
            # that mutates sys.path and reconfigures stdio at import time).
            from scripts.cvf_abstract import fetch_cvf_abstract
            paper_abstract = fetch_cvf_abstract(paper_url)
        except Exception as e:
            print(f"[!] cvf-abs miss {paper_url}: {e}")
            paper_abstract = ""
        record = {
            "paper_name": paper,
            "paper_url": paper_url,
            "paper_authors": authors,
            "paper_abstract": paper_abstract,
            "paper_code": "#",
        }
        prior = seen_urls.get(paper_url)
        if prior is not None:
            _merge_paper_record(prior, record)
            continue
        res[name].append(record)
        seen_urls[paper_url] = record
    return res
