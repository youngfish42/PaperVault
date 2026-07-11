import re

import yaml
from bs4 import BeautifulSoup

from collector.http import SESSION, HEADERS
from collector.merge import _merge_paper_record
from collector.sources.openreview import _extract_forum_id, _fetch_openreview_abstract


def search_abs_from_dblp(url):
    try:
        r = SESSION.get(url, headers=HEADERS)
    except Exception as e:
        msg = str(e)
        if "doesn't match either of 'aaai.org'" in msg:
            hostname = e.request.url.replace('//','/').split('/')[1]
            url = e.request.url.replace(hostname,'aaai.org')
        r = SESSION.get(url, headers=HEADERS)

    soup = BeautifulSoup(r.text, "html.parser")

    abstract = ""
    if 'ieee' in r.url:
        script_tag = soup.find(lambda tag: tag.name == 'script' and 'xplGlobal.document.metadata' in tag.text)
        if script_tag:
            try:
                abstract = yaml.safe_load(script_tag.text.split('\n\t')[-1].strip()[28:-1])['abstract']
            except Exception:
                pass

    elif 'acm' in r.url:
        abstract_section = soup.find(class_="abstractSection")
        if abstract_section and abstract_section.p:
            abstract = abstract_section.p.get_text(strip=True)

    elif 'openreview' in r.url:
        # 通过 forum id 调 OpenReview API，先 v2 再 v1，最大化命中率
        try:
            forum_id = _extract_forum_id(r.url) or r.url.split("=")[-1]
            abstract = _fetch_openreview_abstract(forum_id)
        except Exception:
            pass

    elif 'mlr.press' in r.url:
        elem = soup.find(id="abstract")
        if elem:
            abstract = elem.get_text(strip=True)

    elif 'aaai' in r.url:
        abstract_elem = soup.find(class_="abstract")
        if abstract_elem and abstract_elem.p:
            abstract = abstract_elem.p.get_text(strip=True)

    elif 'ijcai' in r.url:
        proceedings = soup.find(class_="proceedings-detail")
        if proceedings:
            col = proceedings.find(class_="col-md-12")
            if col:
                abstract = col.get_text(strip=True)

    elif 'springer' in r.url:
        elem = soup.find(id="Abs1-content")
        if elem and elem.next_element:
            abstract = elem.next_element.get_text(strip=True)

    elif 'jmlr' in r.url:
        elem = soup.find(class_="abstract")
        if elem:
            abstract = elem.get_text(strip=True)

    return abstract


def search_from_dblp(url, name, res):
    r = SESSION.get(url, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")
    if name not in res:
        res[name] = []

    # In-batch dedupe by external paper URL. DBLP multi-volume conferences
    # (ECCV/AAAI/IJCAI ...) call this function once per volume, and the same
    # entry can occasionally surface in more than one listing page.
    seen_urls: dict = {p["paper_url"]: p for p in res[name] if p.get("paper_url")}

    for paper_item in soup.find_all("li", class_="entry"):
        drop_down = paper_item.find("li", class_="drop-down")
        if not drop_down or not drop_down.div or not drop_down.div.a:
            continue
        paper_url = drop_down.div.a.get("href", "")
        if not paper_url:
            continue

        paper_name = paper_item.find(class_="title", itemprop="name")
        if not paper_name:
            continue

        paper_authors = [
            re.sub(r"\d", "", author["title"]).strip()
            for author in paper_item.find_all(class_=None, itemprop="name") if author.has_attr("title")]

        items = [item.string if item.string else item for item in paper_name.contents]
        paper = "".join([item for item in items if isinstance(item, str)])
        try:
            # paper_abstract = search_abs_from_dblp(paper_url)
            paper_abstract = "" # due to limits
        except:
            print(f"Skip url:{paper_url}")
            paper_abstract = ""
        if paper and paper[-1] == ".":
            paper = paper[:-1]
        record = {
            "paper_name": paper,
            "paper_url": paper_url,
            "paper_authors": paper_authors,
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
