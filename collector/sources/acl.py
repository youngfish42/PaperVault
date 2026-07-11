import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from collector.http import SESSION, HEADERS
from collector.merge import _merge_paper_record


def _parse_acl_volume(volume_url: str, tag: str, name: str, res: dict):
    """解析 ACL Anthology 单个 volume 页面"""
    if name not in res:
        res[name] = []
    # In-batch dedupe by anthology paper URL. ACL events span multiple
    # volumes (main / findings / short / long ...) and ``search_from_acl``
    # may invoke us once per volume; the same volume should never produce
    # duplicates but a re-run after a partial failure can.
    seen_urls: dict = {p["paper_url"]: p for p in res[name] if p.get("paper_url")}
    r = SESSION.get(volume_url, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")

    # 新版页面：href 格式如 /2023.acl-long.1/
    # 旧版页面（2019 年前）：href 格式如 /P05-1001/、/N12-1001/ 等
    strongs = soup.find_all("strong")
    for strong in strongs:
        a = strong.find(
            "a",
            href=re.compile(r"^/(?:\d{4}\.[a-zA-Z0-9-]+\.\d+|[A-Za-z]\d{2}-\d+)/$"),
        )
        if not a:
            continue
        paper = a.text.strip()
        if not paper:
            continue
        # 跳过 volume 封面页（Proceedings of ...）
        if paper.lower().startswith("proceedings of"):
            continue
        # tag 过滤：如 ^/2023.acl* 只匹配 acl 相关 volume
        href = a["href"]
        tag_pattern = tag.lstrip("^").rstrip("*")
        if tag_pattern not in href:
            continue

        paper_url = "https://aclanthology.org" + href

        # 作者：在 strong 的父容器中查找 people 链接
        container = strong.find_parent()
        paper_authors = []
        if container:
            for author in container.find_all("a", href=re.compile("people/")):
                author_text = author.string or author.text
                if author_text:
                    paper_authors.append(author_text.strip())

        # abstract：根据 href 构造 div id，如 /2023.acl-long.1/ -> abstract-2023--acl-long--1
        paper_id = href.strip("/").replace(".", "--")
        abstract_div = soup.find(id=f"abstract-{paper_id}")
        paper_abstract = abstract_div.text.strip() if abstract_div else ""

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


_ACL_VOLUME_DIR_RE = re.compile(r"^/volumes/[A-Za-z0-9._-]+/$")
_ACL_ALLOWED_HOSTS = ("", "aclanthology.org", "www.aclanthology.org")


def _is_acl_volume_entry(url: str) -> bool:
    """入口 URL 是否已经指向一个具体的 ACL Anthology volume 页 (/volumes/XXX/)。

    findings-acl / findings-emnlp / … 这些 conf 记录直接用 volume 页作入口，
    此时不应再枚举页面内其它 /volumes/ 链接，否则会把 .bib/.enw/.xml 等元数据
    下载链接（或页面上出现的相邻 volume）当作 volume 页去解析，导致 empty result。

    host 采用白名单校验：空 netloc（相对路径）、aclanthology.org、www.aclanthology.org
    才被接受；防止将来该谓词被误用于非 Anthology 域名的 URL。
    """
    parsed = urlparse(url)
    if parsed.netloc not in _ACL_ALLOWED_HOSTS:
        return False
    return bool(parsed.path) and _ACL_VOLUME_DIR_RE.match(parsed.path) is not None


def search_from_acl(url, tag, name, res):
    """解析 ACL Anthology events / volume 页面。

    - 入口是 events 页 (``/events/xxx-YYYY/``)：枚举页面内的 /volumes/XXX/ 子页
      分别解析。
    - 入口本身就是 volume 页 (``/volumes/XXX/``)：直接解析当前页，不再枚举。
      Anthology Hugo 模板下 volume 页面上出现的 /volumes/ 链接大多是
      ``.bib/.enw/.xml`` 元数据下载或跨 volume 的横向导航，两者都不应作为
      新的 volume 入口重新拉取。
    """
    if name not in res:
        res[name] = []

    if _is_acl_volume_entry(url):
        return _parse_acl_volume(url, tag, name, res)

    r = SESSION.get(url, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")

    # 提取所有 volume 目录链接（新版 events 页面结构）——只接受 /volumes/XXX/
    # 目录形态，剔除 /volumes/XXX.bib、.enw、.xml、.pdf 等元数据下载。
    volume_links = set()
    for a in soup.find_all("a", href=re.compile(r"^/volumes/[A-Za-z0-9._-]+/$")):
        href = a["href"]
        if not href.startswith("http"):
            href = "https://aclanthology.org" + href
        volume_links.add(href)

    if volume_links:
        # 新版：遍历各 volume 页面
        for volume_url in sorted(volume_links):
            res = _parse_acl_volume(volume_url, tag, name, res)
    else:
        # 旧版 fallback：直接解析当前页面（兼容早期年份）
        res = _parse_acl_volume(url, tag, name, res)

    return res
