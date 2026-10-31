from bs4 import BeautifulSoup

from collector.http import SESSION, HEADERS
from collector.merge import _merge_paper_record


def search_abs_from_nips(url):
    r = SESSION.get(url, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")
    # 新结构：h2.section-label + p.paper-abstract
    h2 = soup.find('h2', class_='section-label')
    if h2 and 'Abstract' in h2.text:
        abstract_elem = h2.find_next_sibling()
        if abstract_elem:
            return abstract_elem.get_text(strip=True)
    # 旧结构 fallback
    h4 = soup.find(lambda tag: tag.name == "h4" and 'Abstract' in tag.text)
    if h4 and h4.next_sibling and h4.next_sibling.next_sibling:
        return h4.next_sibling.next_sibling.text.strip()
    return ""

def search_from_nips(url, name, res):
    r = SESSION.get(url, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")
    if name not in res:
        res[name] = []
    # In-batch dedupe by absolute paper URL. NeurIPS proceedings pages are
    # single-volume so this mainly catches the "we already ran for this conf
    # earlier in the session" case.
    seen_urls: dict = {p["paper_url"]: p for p in res[name] if p.get("paper_url")}
    url_prefix = "https://" + url[8:].split("/")[0]
    col = soup.find(class_="col")
    if not col or not col.ul:
        return res
    for paper_item in col.ul.find_all("li"):
        a_tag = paper_item.a
        if a_tag is None:
            continue
        href = a_tag.get("href")
        if not href:
            continue
        paper_url = url_prefix + href
        # 新结构：span class="paper-authors"
        authors_span = paper_item.find("span", class_="paper-authors")
        if authors_span:
            paper_author = [author.strip() for author in authors_span.get_text(strip=True).split(',')]
        # 旧结构 fallback：i 标签
        elif paper_item.i is not None and paper_item.i.string is not None:
            paper_author = [author.strip() for author in paper_item.i.string.split(',')]
        else:
            paper_author = []
        try:
            paper_abstract = search_abs_from_nips(paper_url)
        except Exception as e:
            print(f"Skip url:{paper_url}")
            paper_abstract = ""

        paper_name = a_tag.string if a_tag.string else a_tag.get_text(strip=True)
        record = {
            "paper_name": paper_name,
            "paper_url": paper_url,
            "paper_authors": paper_author,
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
