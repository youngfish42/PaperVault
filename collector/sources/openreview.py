import re
import time
from urllib.parse import unquote_plus

from bs4 import BeautifulSoup

from collector.http import SESSION, HEADERS
from collector.merge import _merge_paper_record


# OpenReview venue 过滤关键字（统一来源，供 collector 与 discovery 共用）
# 任何包含以下子串（大小写不敏感）的 venue 视为「未接收」，不应被采集，
# 也不应在 discovery 阶段为其生成抓取 URL。
OPENREVIEW_REJECTED_VENUE_KEYWORDS = (
    "submitted",        # "Submitted to ICLR 2025" / "NeurIPS 2022 Submitted"
    "reject",           # "Reject" / "Conference Desk Rejected Submission"
    "withdrawn",        # "Conference Withdrawn Submission"
    "desk rejected",    # 冗余兜底，防止 "reject" 关键字未来被改名
)

# 已接收 venue 的正向白名单关键字。
OPENREVIEW_ACCEPTED_VENUE_KEYWORDS = (
    "oral",
    "spotlight",
    "poster",
    "accept",
    "top",              # "ICLR 2023 notable top 5%" / "top 25%"
    "blogpost",         # "ICLR 2025 Blogpost Track"（官方正式 track，保留）
)


def is_openreview_accepted_venue(venue: str) -> bool:
    """判断 OpenReview 论文的 venue 字段是否表示「已接收」。

    采用「先黑名单后白名单」双重过滤：
    - 排除：Submitted / Reject / Withdrawn / Desk Rejected
    - 保留：Oral / Spotlight / Poster / Accept / Top
    其余未知 venue 默认排除，保持保守策略。
    """
    if not venue:
        return False
    venue_lower = venue.lower()
    if any(k in venue_lower for k in OPENREVIEW_REJECTED_VENUE_KEYWORDS):
        return False
    if any(k in venue_lower for k in OPENREVIEW_ACCEPTED_VENUE_KEYWORDS):
        return True
    return False


# 保留旧名以兼容内部调用点
_is_openreview_accepted = is_openreview_accepted_venue


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


def _url_targets_rejected_venue(url: str) -> bool:
    """快速判断一条 OpenReview 抓取 URL 是否指向「未接收」venue。

    历史遗留的 conf/iclr_conf.json 中可能仍残留 content.venue=Submitted%20to...
    /Withdrawn%20Submission/Desk%20Rejected%20Submission 这类 URL。
    一旦发现，立即整条跳过，避免无谓的分页请求与噪音日志。

    使用 ``unquote_plus`` 而非 ``unquote`` 是为了同时还原 ``%20`` 与 ``+`` 两种
    空格编码形态，否则若 venue 写成 ``Desk+Rejected+Submission`` 这类
    ``quote_plus`` 风格 URL，含空格的关键字（如 ``"desk rejected"``）将无法命中。
    """
    if not url:
        return False
    lowered = unquote_plus(url).lower()
    return any(k in lowered for k in OPENREVIEW_REJECTED_VENUE_KEYWORDS)


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

    # In-batch dedupe by OpenReview forum_id. The same forum_id can be returned
    # more than once when (a) the v1 endpoint partially succeeds before we fall
    # back to v2, (b) the API duplicates a note across offset boundaries, or
    # (c) a previous run already appended the same paper to ``res[name]``.
    # We track existing forum_ids on entry so re-invocations of the same
    # (url, name) pair are idempotent and we merge late-arriving abstracts
    # instead of silently dropping them.
    seen_ids: dict = {}
    for existing in res[name]:
        fid = _extract_forum_id(existing.get("paper_url") or "")
        if fid:
            seen_ids[fid] = existing

    # 源头过滤：若 URL 本身指向 Submitted/Withdrawn/Rejected 等未接收 venue，
    # 直接跳过，避免拉取大量必然被 _is_openreview_accepted 丢弃的论文。
    if _url_targets_rejected_venue(url):
        print(f"[~] Skip non-accepted venue URL for {name}: {url}")
        return res

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

                forum_id = item.get("id") or ""
                record = {
                    "paper_name": title,
                    "paper_url": "https://openreview.net/pdf?id=" + forum_id,
                    "paper_authors": paper_authors,
                    "paper_abstract": abstract,
                    "paper_code": "#",
                }
                prior = seen_ids.get(forum_id) if forum_id else None
                if prior is not None:
                    # Same forum_id reappeared (offset boundary, v1->v2
                    # fallback or re-run). Merge enriched fields into the
                    # already-stored record and skip the duplicate append.
                    _merge_paper_record(prior, record)
                    continue
                res[name].append(record)
                if forum_id:
                    seen_ids[forum_id] = record
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

    # In-batch dedupe: keep one record per OpenReview forum_id, even if the
    # schedule page (or a previous run that already appended into ``res[name]``)
    # lists the same paper twice.
    seen_ids: dict = {}
    for existing in res[name]:
        fid = _extract_forum_id(existing.get("paper_url") or "")
        if fid:
            seen_ids[fid] = existing

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
        forum_id = _extract_forum_id(paper_url)
        prior = seen_ids.get(forum_id) if forum_id else None
        if prior is not None:
            # Same paper already collected (page duplication or prior run).
            # Merge any non-empty fields we just parsed into the stored record
            # and queue *that* one for the abstract backfill so the merged
            # record gets enriched.
            _merge_paper_record(prior, item)
            new_items.append(prior)
            continue
        res[name].append(item)
        new_items.append(item)
        if forum_id:
            seen_ids[forum_id] = item

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
