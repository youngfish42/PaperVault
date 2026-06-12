import json
import os
import re
import warnings
from collections import Counter
import yaml
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from tqdm import tqdm

import gzip
from data_artifacts import sync_cache_artifacts

# 忽略 ACL Anthology 某些 XML 页面被 HTML 解析器解析时的警告
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36"
}


def _create_session():
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.mount("http://", HTTPAdapter(max_retries=retries))
    return session


SESSION = _create_session()

def _is_openreview_accepted(venue: str) -> bool:
    """判断 OpenReview 论文的 venue 字段是否表示已接收。

    排除：Submitted / Reject / Withdrawn / Desk Rejected
    保留：Oral / Spotlight / Poster / Accept / Top
    """
    if not venue:
        return False
    venue_lower = venue.lower()
    # 严格排除未接收/撤回状态
    if any(k in venue_lower for k in ("submitted", "reject", "withdrawn", "desk rejected")):
        return False
    # 包含已接收状态（ oral, spotlight, poster, accept, top ）
    if any(k in venue_lower for k in ("oral", "spotlight", "poster", "accept", "top")):
        return True
    return False


def _or_field(content: dict, key: str):
    """提取 OpenReview note.content 中的字段，兼容 v1 平铺与 v2 {"value": ...} 两种结构。

    v1: content = {"title": "Paper Title", "abstract": "...", "authors": [...]}
    v2: content = {"title": {"value": "Paper Title"}, "abstract": {"value": "..."}, ...}
    """
    if not isinstance(content, dict):
        return None
    val = content.get(key)
    if isinstance(val, dict) and "value" in val:
        return val.get("value")
    return val


def _extract_forum_id(url: str) -> str:
    """从 openreview.net 链接中提取 forum/paper id。"""
    if not url:
        return ""
    m = re.search(r"(?:forum|pdf)\?id=([A-Za-z0-9_\-]+)", url)
    return m.group(1) if m else ""


def _fetch_openreview_abstract(forum_id: str) -> str:
    """根据 forum_id 从 OpenReview 拿到 abstract。

    OpenReview 当前同时存在两个 API：
      - v1: https://api.openreview.net/notes?forum=<id>
      - v2: https://api2.openreview.net/notes?forum=<id>
    旧投稿（≲ 2023）走 v1，新投稿（2024+）走 v2。
    先试 v2，若失败/为空再回退 v1，避免漏抓。
    """
    if not forum_id:
        return ""
    for api_root in ("https://api2.openreview.net", "https://api.openreview.net"):
        try:
            r = SESSION.get(
                f"{api_root}/notes?forum={forum_id}",
                headers=HEADERS,
                timeout=15,
            )
            if r.status_code != 200:
                continue
            notes = r.json().get("notes", [])
            if not notes:
                continue
            # 论坛 root note 通常是第一条；逐个找直到拿到 abstract
            for note in notes:
                abs_val = _or_field(note.get("content", {}) or {}, "abstract")
                if abs_val:
                    return abs_val.strip() if isinstance(abs_val, str) else ""
        except Exception:
            continue
    return ""


def search_from_iclr_openreview(url, name, res):
    """通过 OpenReview API 获取 ICLR 论文，自动分页并过滤已接收论文。

    旧的按 venue 分类型查询（Oral/Poster/Spotlight 各自一条 URL）已废弃。
    改为统一查询 Blind_Submission，在代码内根据 venue 过滤，避免重复和遗漏。

    同时兼容两种 OpenReview API：
      - v1 (api.openreview.net)：content 字段为 {"abstract": "..."} 平铺结构
      - v2 (api2.openreview.net)：content 字段为 {"abstract": {"value": "..."}} 嵌套结构
    若配置 URL 指向 v1 而该年份实际为 v2 投稿（如 2024 之后的 ICLR/NeurIPS），
    会在 v1 返回空时自动回退到 v2 端点重试。
    """
    if name not in res:
        res[name] = []

    # 清理 URL 中已有的 offset/limit，由本函数自行分页
    base_url = re.sub(r"&offset=\d+", "", url)
    base_url = re.sub(r"&limit=\d+", "", base_url)

    # 为每个 URL 准备 (v1, v2) 两个变体，先用配置的端点，必要时回退
    base_variants = [base_url]
    if "api.openreview.net" in base_url and "api2.openreview.net" not in base_url:
        base_variants.append(base_url.replace("api.openreview.net", "api2.openreview.net"))

    collected_any = False
    for variant in base_variants:
        offset = 0
        limit = 1000
        got_in_this_variant = 0

        while True:
            paginated_url = f"{variant}&offset={offset}&limit={limit}"
            try:
                r = SESSION.get(paginated_url, headers=HEADERS, timeout=60)
                data = r.json()
            except Exception:
                break
            notes = data.get("notes", [])
            if not notes:
                break

            for item in notes:
                content = item.get("content", {}) or {}
                venue = _or_field(content, "venue") or ""
                if not _is_openreview_accepted(venue):
                    continue

                title = _or_field(content, "title") or ""
                paper_authors = _or_field(content, "authors") or []
                # authors 字段在部分旧数据中可能为 None，兜底处理
                if paper_authors is None:
                    paper_authors = []
                abstract = _or_field(content, "abstract") or ""

                res[name].append(
                    {
                        "paper_name": title,
                        "paper_url": "https://openreview.net/pdf?id=" + item["id"],
                        "paper_authors": paper_authors,
                        "paper_abstract": abstract,
                        "paper_code": "#",
                    }
                )
                got_in_this_variant += 1

            if len(notes) < limit:
                break
            offset += limit

        if got_in_this_variant > 0:
            collected_any = True
            break  # 当前端点已成功，不再尝试 fallback

    if not collected_any:
        # 两个端点均未返回任何已接收论文，留个提示
        print(f"[!] OpenReview returned no accepted notes for {name} ({url})")

    return res


def _batch_fetch_openreview_abstracts(forum_ids):
    """根据一批 forum_id 批量获取 abstract。

    OpenReview v2 (`api2.openreview.net/notes`) 支持 `ids=A,B,C,...` 批量查询，
    单次请求建议不超过 ~100 个 id，以避免 URL 过长或服务端限流。
    对 v2 返回为空的 id，会降级到 v1 单条 fallback（兼容历史投稿）。
    返回：dict[forum_id] -> abstract (空串表示未拿到)。
    """
    result = {fid: "" for fid in forum_ids if fid}
    if not result:
        return result

    pending = list(result.keys())
    chunk = 100

    # 先用 v2 批量
    for i in range(0, len(pending), chunk):
        batch = pending[i:i + chunk]
        ids_param = ",".join(batch)
        try:
            r = SESSION.get(
                f"https://api2.openreview.net/notes?ids={ids_param}",
                headers=HEADERS,
                timeout=30,
            )
            if r.status_code != 200:
                continue
            for note in r.json().get("notes", []):
                nid = note.get("id")
                if not nid:
                    continue
                abs_val = _or_field(note.get("content", {}) or {}, "abstract")
                if isinstance(abs_val, str) and abs_val.strip():
                    result[nid] = abs_val.strip()
        except Exception:
            continue
        # 友好限流
        time.sleep(0.5)

    # 对仍空的 id 走 v1 单条 fallback（_fetch_openreview_abstract 内部已带 v2->v1 顺序）
    missing = [fid for fid, abs_ in result.items() if not abs_]
    for fid in missing:
        try:
            abs_ = _fetch_openreview_abstract(fid)
            if abs_:
                result[fid] = abs_
        except Exception:
            pass
        time.sleep(0.2)

    return result


def search_from_iclr_official(url, name, res):
    """通过 ICLR 官方 Schedule 页面获取论文（适用于 OpenReview API 不可用的年份，如 2024+）。

    页面结构：
        div.maincard (class 包含 poster / oral)
            div.maincardBody   -> 标题
            div.maincardFooter -> 作者（用 · 分隔）
            a[href*=openreview.net/forum?id=] -> OpenReview 链接

    采集完毕后，会按 forum_id 批量调用 OpenReview API v2 回填 abstract，
    从源头减少 scripts/fetch_abstracts.py 的 backfill 工作量。
    """
    if name not in res:
        res[name] = []

    r = SESSION.get(url, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")

    new_items = []  # 本次新增条目，用于稍后批量回填
    for card in soup.find_all("div", class_="maincard"):
        classes = card.get("class", [])
        # 只收集主会论文（poster / oral），排除 workshop / event / break 等
        if "poster" not in classes and "oral" not in classes:
            continue

        title_elem = card.find("div", class_="maincardBody")
        author_elem = card.find("div", class_="maincardFooter")
        or_link = card.find("a", href=lambda x: x and "openreview.net/forum?id=" in x)

        if not title_elem or not or_link:
            continue

        paper_name = title_elem.get_text(strip=True)
        paper_url = or_link.get("href", "")
        # 统一为 pdf 链接，与 OpenReview API 方式保持一致
        if "openreview.net/forum?id=" in paper_url:
            paper_url = paper_url.replace("openreview.net/forum?id=", "openreview.net/pdf?id=")

        # 解析作者：ICLR 官网用中间点 "·" 分隔
        authors = []
        if author_elem:
            author_text = author_elem.get_text(strip=True)
            authors = [a.strip() for a in author_text.split("·") if a.strip()]

        item = {
            "paper_name": paper_name,
            "paper_url": paper_url,
            "paper_authors": authors,
            "paper_abstract": "",  # 占位，稍后批量回填
            "paper_code": "#",
        }
        res[name].append(item)
        new_items.append(item)

    # 从源头批量回填 abstract，避免遗留给 fetch_abstracts.py
    forum_ids = []
    item_by_id = {}
    for it in new_items:
        fid = _extract_forum_id(it["paper_url"])
        if fid:
            forum_ids.append(fid)
            item_by_id[fid] = it

    if forum_ids:
        try:
            abs_map = _batch_fetch_openreview_abstracts(forum_ids)
            filled = 0
            for fid, abs_ in abs_map.items():
                if abs_ and fid in item_by_id:
                    item_by_id[fid]["paper_abstract"] = abs_
                    filled += 1
            print(f"    [+] {name}: filled abstracts for {filled}/{len(forum_ids)} forums via OpenReview API")
        except Exception as e:
            print(f"    [!] {name}: OpenReview batch abstract fetch failed: {e}")

    return res


def search_from_iclr(url, name, res):
    """ICLR 论文获取入口，自动根据 URL 类型选择解析策略。"""
    if "api.openreview.net" in url:
        return search_from_iclr_openreview(url, name, res)
    elif "iclr.cc" in url:
        return search_from_iclr_official(url, name, res)
    else:
        # fallback：默认当作 OpenReview API 处理
        return search_from_iclr_openreview(url, name, res)

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
        res[name].append(
            {
                "paper_name": paper_name,
                "paper_url": paper_url,
                "paper_authors": paper_author,
                "paper_abstract": paper_abstract,
                "paper_code": "#",
            }
        )
    return res


def _parse_acl_volume(volume_url: str, tag: str, name: str, res: dict):
    """解析 ACL Anthology 单个 volume 页面"""
    if name not in res:
        res[name] = []
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

        res[name].append(
            {
                "paper_name": paper,
                "paper_url": paper_url,
                "paper_authors": paper_authors,
                "paper_abstract": paper_abstract,
                "paper_code": "#",
            }
        )
    return res


def search_from_acl(url, tag, name, res):
    """解析 ACL Anthology events 页面，自动跳转各 volume"""
    r = SESSION.get(url, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")
    if name not in res:
        res[name] = []

    # 提取所有 volume 链接（新版 events 页面结构）
    volume_links = set()
    for a in soup.find_all("a", href=re.compile(r"/volumes/")):
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
        res[name].append(
            {
                "paper_name": paper, 
                "paper_url": paper_url,
                "paper_authors": paper_authors,
                "paper_abstract": paper_abstract,
                "paper_code": "#",
            }
        )
    return res


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
        paper = a_tag.string if a_tag.string else a_tag.get_text(strip=True)
        
        authors = []
        ns = paper_item.next_sibling
        if ns:
            ns2 = ns.next_sibling
            if ns2:
                authors = [author.string for author in ns2.find_all('a', href='#') if author.string]
        
        try:
            paper_abstract = search_abs_from_thecvf(paper_url)
        except:
            print(f"Skip url:{paper_url}")
            paper_abstract = ""
        res[name].append(
            {
                "paper_name": paper, 
                "paper_url": paper_url,
                "paper_authors": authors,
                "paper_abstract": paper_abstract,
                "paper_code": "#",
            }
        )
    return res


# ---------- 代码链接提取（参考 FL-paper-update-tracker） ----------
import re as _re

_GITHUB_RE = _re.compile(
    r"https?://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+(?:/[^\s\)\]\}>\"'`]*)?"
)


def extract_github_link(text: str) -> str:
    """从文本中提取第一个 GitHub 仓库链接，清理尾部标点。"""
    if not text:
        return ""
    matches = _GITHUB_RE.findall(text)
    if not matches:
        return ""
    url = matches[0]
    url = url.rstrip(".,;:'\")]}>")
    return url


def add_code_links(res):
    """扫描论文 abstract 中的 GitHub 链接，补充 code 字段。

    旧逻辑（爬取外部 Markdown 仓库）已废弃，改为直接从 abstract 中
    正则匹配 GitHub 链接。保留已有非 '#' 的 code_links 不变。
    """
    for conf_name, papers in res.items():
        for ii, item in enumerate(papers):
            existing = (item.get("paper_code") or "#").strip()
            if existing and existing != "#":
                continue
            abstract = (item.get("paper_abstract") or "").strip()
            if not abstract:
                continue
            link = extract_github_link(abstract)
            if link:
                papers[ii]["paper_code"] = link
    return res

COLLECT_PROGRESS_FILE = "cache/collect_progress.json"


def load_collect_progress():
    if not os.path.exists(COLLECT_PROGRESS_FILE):
        return {}
    with open(COLLECT_PROGRESS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("completed", {})


def save_collect_progress(progress):
    os.makedirs(os.path.dirname(COLLECT_PROGRESS_FILE), exist_ok=True)
    with open(COLLECT_PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "completed": progress}, f, ensure_ascii=False, indent=2)


def _merge_with_cache(new_res, cache_res, multi_volume_names, collected_dblp_names):
    result = dict(new_res)
    for conf_name, papers in cache_res.items():
        if conf_name not in result:
            result[conf_name] = papers
        elif conf_name in multi_volume_names and conf_name in collected_dblp_names:
            existing_urls = {p["paper_url"] for p in result[conf_name]}
            for p in papers:
                if p["paper_url"] not in existing_urls:
                    result[conf_name].append(p)
    return result


def collect(cache_file=None, force=False, soft_timeout=None):
    res = {}
    failures = []
    progress = {} if force else load_collect_progress()

    acl_conf = json.load(open("conf/acl_conf.json", "r"))
    dblp_conf = json.load(open("conf/dblp_conf.json", "r"))
    nips_conf = json.load(open("conf/nips_conf.json", "r"))
    iclr_conf = json.load(open("conf/iclr_conf.json", "r"))
    thecvf_conf = json.load(open("conf/thecvf_conf.json", "r"))

    cache_conf = set()
    cache_res = {}
    gz_path = cache_file + ".gz" if cache_file and not cache_file.endswith(".gz") else cache_file
    if not force and gz_path is not None and os.path.exists(gz_path):
        cache_res = load_cache(cache_file)
        cache_conf = set(cache_res.keys())

    dblp_name_counter = Counter(conf["name"] for conf in dblp_conf if conf.get("name"))
    multi_volume_dblp_names = {
        name for name, count in dblp_name_counter.items() if count > 1
    }

    start_time = time.time()
    collected_dblp_names = set()
    save_tracker = {"last": 0}

    def _is_timeout():
        if soft_timeout and start_time is not None:
            elapsed = time.time() - start_time
            if elapsed >= soft_timeout:
                print(f"[!] Soft timeout ({soft_timeout}s, elapsed {elapsed:.0f}s) reached.")
                return True
        return False

    def _save_state():
        now = time.time()
        if now - save_tracker["last"] < 5:
            return
        save_tracker["last"] = now
        save_collect_progress(progress)
        if cache_file:
            merged = _merge_with_cache(res, cache_res, multi_volume_dblp_names, collected_dblp_names)
            tmp_file = cache_file + ".tmp"
            save_cache(tmp_file, merged)
            os.replace(tmp_file, cache_file)
            print(f"[*] Incremental cache saved: {cache_file}")

    def _should_skip(source, url, name):
        if force:
            return False
        key = f"{source}::{url}"
        if key in progress:
            return True
        if name in cache_conf:
            if source == "DBLP" and name in multi_volume_dblp_names:
                return False
            progress[key] = {"name": name, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "legacy": True}
            return True
        return False

    for conf in tqdm(acl_conf, desc="[+] Collecting ACL", dynamic_ncols=True):
        try:
            if not (conf.get("name") and conf.get("url") and conf.get("tag")):
                print(f"[!] Skip invalid ACL conf: {conf}")
                continue
            url, tag, name = conf["url"], conf["tag"], conf["name"]
            if _should_skip("ACL", url, name):
                continue
            if _is_timeout():
                break
            res = search_from_acl(url, tag, name, res)
            progress[f"ACL::{url}"] = {"name": name, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
            _save_state()
        except Exception as e:
            print(f"[!] Failed to collect ACL '{conf.get('name', 'unknown')}': {e}")
            failures.append({"source": "ACL", "name": conf.get("name"), "url": conf.get("url"), "error": str(e)})
    _save_state()
        
    for conf in tqdm(iclr_conf, desc="[+] Collecting ICLR", dynamic_ncols=True):
        try:
            if not (conf.get("name") and conf.get("url")):
                print(f"[!] Skip invalid ICLR conf: {conf}")
                continue
            url, name = conf["url"], conf["name"]
            if _should_skip("ICLR", url, name):
                continue
            if _is_timeout():
                break
            res = search_from_iclr(url, name, res)
            progress[f"ICLR::{url}"] = {"name": name, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
            _save_state()
        except Exception as e:
            print(f"[!] Failed to collect ICLR '{conf.get('name', 'unknown')}': {e}")
            failures.append({"source": "ICLR", "name": conf.get("name"), "url": conf.get("url"), "error": str(e)})
    _save_state()
        
    for conf in tqdm(thecvf_conf, desc="[+] Collecting openaccess.thecvf", dynamic_ncols=True):
        try:
            if not (conf.get("name") and conf.get("url")):
                print(f"[!] Skip invalid thecvf conf: {conf}")
                continue
            url, name = conf["url"], conf["name"]
            if _should_skip("thecvf", url, name):
                continue
            if _is_timeout():
                break
            res = search_from_thecvf(url, name, res)
            progress[f"thecvf::{url}"] = {"name": name, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
            _save_state()
        except Exception as e:
            print(f"[!] Failed to collect openaccess.thecvf '{conf.get('name', 'unknown')}': {e}")
            failures.append({"source": "openaccess.thecvf", "name": conf.get("name"), "url": conf.get("url"), "error": str(e)})
    _save_state()

    for conf in tqdm(nips_conf, desc="[+] Collecting NeurIPS", dynamic_ncols=True):
        try:
            if not (conf.get("name") and conf.get("url")):
                print(f"[!] Skip invalid NeurIPS conf: {conf}")
                continue
            url, name = conf["url"], conf["name"]
            if _should_skip("NeurIPS", url, name):
                continue
            if _is_timeout():
                break
            res = search_from_nips(url, name, res)
            progress[f"NeurIPS::{url}"] = {"name": name, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
            _save_state()
        except Exception as e:
            print(f"[!] Failed to collect NeurIPS '{conf.get('name', 'unknown')}': {e}")
            failures.append({"source": "NeurIPS", "name": conf.get("name"), "url": conf.get("url"), "error": str(e)})
    _save_state()

    for conf in tqdm(dblp_conf, desc="[+] Collecting DBLP", dynamic_ncols=True):
        try:
            if not (conf.get("name") and conf.get("url")):
                print(f"[!] Skip invalid DBLP conf: {conf}")
                continue
            url, name = conf["url"], conf["name"]
            if _should_skip("DBLP", url, name):
                continue
            if _is_timeout():
                break
            res = search_from_dblp(url, name, res)
            progress[f"DBLP::{url}"] = {"name": name, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
            collected_dblp_names.add(name)
            _save_state()
        except Exception as e:
            print(f"[!] Failed to collect DBLP '{conf.get('name', 'unknown')}': {e}")
            failures.append({"source": "DBLP", "name": conf.get("name"), "url": conf.get("url"), "error": str(e)})
    _save_state()

    final_res = _merge_with_cache(res, cache_res, multi_volume_dblp_names, collected_dblp_names)

    res = add_code_links(final_res)

    if failures:
        failures_path = os.path.join(os.path.dirname(cache_file) if cache_file else ".", "collect_failures.json")
        try:
            with open(failures_path, "w", encoding="utf-8") as f:
                json.dump(failures, f, ensure_ascii=False, indent=2)
            print(f"[!] {len(failures)} conference(s) failed. Details saved to {failures_path}")
        except Exception as e:
            print(f"[!] Could not save failure log: {e}")

    return res


def _to_gz_path(path):
    """统一将 .jsonl 路径转为 .jsonl.gz 路径。"""
    if path.endswith(".jsonl") and not path.endswith(".jsonl.gz"):
        return path + ".gz"
    return path


def load_cache(path):
    """读取缓存。优先从 gzip 加载，回退到旧版纯文本 JSONL。"""
    gz_path = _to_gz_path(path)
    if os.path.exists(gz_path):
        data = {}
        with gzip.open(gz_path, "rt", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    paper = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Malformed JSON on line {line_num} of {gz_path}: {e}")
                if "conf" not in paper or not isinstance(paper["conf"], str):
                    raise ValueError(
                        f"Missing or invalid 'conf' field on line {line_num} of {gz_path}"
                    )
                conf = paper.pop("conf")
                if conf not in data:
                    data[conf] = []
                data[conf].append(paper)
        return data
    # 兼容旧版纯文本 JSONL
    if os.path.exists(path):
        print(f"[!] Loading from legacy {path}. Consider migrating to gzip manually.")
        data = {}
        with open(path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    paper = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Malformed JSON on line {line_num} of {path}: {e}")
                if "conf" not in paper or not isinstance(paper["conf"], str):
                    raise ValueError(
                        f"Missing or invalid 'conf' field on line {line_num} of {path}"
                    )
                conf = paper.pop("conf")
                if conf not in data:
                    data[conf] = []
                data[conf].append(paper)
        return data
    return {}


def save_cache(path, data):
    """将 dict[conf_name] -> list[paper_dict] 写入 gzip JSONL。"""
    gz_path = _to_gz_path(path)
    with gzip.open(gz_path, "wt", encoding="utf-8") as f:
        for conf, papers in data.items():
            for paper in papers:
                record = dict(paper)
                record["conf"] = conf
                f.write(json.dumps(record, ensure_ascii=False) + "\n")


def do_collect(cache_file=None, force=False, soft_timeout=None):
    gz_path = _to_gz_path(cache_file) if cache_file else None
    if force or gz_path is None or not os.path.exists(gz_path):
        print(f"[+] Collecting papers...")
        res = collect(cache_file, force=force, soft_timeout=soft_timeout)
        save_cache(cache_file, res)
        if cache_file:
            sync_cache_artifacts(
                cache_path=cache_file,
                commit_message="Update PaperVault data artifacts after collection",
            )
    else:
        print(f"[+] Loading from cache...")
        res = load_cache(cache_file)
    return res


if __name__ == "__main__":
    do_collect(cache_file="cache/cache.jsonl.gz", force=True)
