"""ACL Anthology 自动发现

重构后逻辑：
1. 从 ACL Anthology 官网的 venues 页面获取目标 venues 列表。
2. 对每个 venue，访问其 venue 页面，提取所有年份的 events 链接。
3. 对每个 event 页面，自动检测实际使用的论文编号命名。Anthology 从 2020 起
   全面切换到 ``/YYYY.{venue}-{track}.N/`` 的现代命名（生成的 tag 形如
   ``^2026.acl*``）；2019 及更早仍是 ``/Xyy-NNNN/`` 的旧命名，其中首字母
   来自 ``P``（ACL）、``D``（EMNLP）、``N``（NAACL）、``E``（EACL）、
   ``C``（COLING）、``W``（Workshop）等，生成的 tag 形如 ``^P05-*``。
4. 生成对应的 tag 和配置条目，并按 canonical URL 与既有 ``conf/*.json`` 去重。

当前关注的核心 venues：ACL, EMNLP, NAACL, EACL, COLING
"""

import re
from typing import List, Dict, Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseDiscovery

# 核心 venues（与 PaperVault 主要关注的 NLP 顶级会议对应）
CORE_VENUES = {
    "acl": "ACL",
    "emnlp": "EMNLP",
    "naacl": "NAACL",
    "eacl": "EACL",
    "coling": "COLING",
}

_EVENTS_LINK_RE = re.compile(r"^/events/([a-z0-9]+)-(\d{4})/$")
# Legacy Anthology paper IDs (pre-2020): e.g. /P05-1001/, /D19-1234/
_PAPER_LINK_RE = re.compile(r"^/[A-Za-z]\d{2}-\d+/$")
_PREFIX_RE = re.compile(r"^/([A-Za-z]\d{2})-\d+/$")
# Modern Anthology paper IDs (2020+): e.g. /2026.acl-long.1/, /2024.findings-emnlp.42/
# We only need the stem group ("YYYY.venue-track") to compute the tag body,
# so a full-URL matcher would be dead weight — `_MODERN_STEM_RE` is enough.
_MODERN_STEM_RE = re.compile(r"^/(\d{4}\.[a-zA-Z0-9-]+)\.\d+/$")


def _tag_for(prefix: str) -> str:
    # `prefix` is a fully-formed tag body: for old Anthology IDs the caller
    # supplies e.g. "P05-" (keeping the trailing hyphen), for modern IDs
    # the caller supplies e.g. "2026.acl". We simply wrap it in `^...*`.
    return f"^{prefix}*"


class ACLDiscovery(BaseDiscovery):
    @staticmethod
    def _canon_url(url: str) -> str:
        """规范化 URL，用于跨源合并的稳健去重。

        Anthology 站点内部的 URL 都由 :func:`urljoin` 从同一 canonical base 构造，
        差异极少；但是人工维护 ``conf/acl_conf.json`` 时可能留下 ``http://`` /
        无尾斜杠 / 混合大小写 host 等变体，导致 ``existing_urls`` 去重集合无法
        识别这些等价条目。这里做最小归一化：strip / rstrip trailing '/' / lower-case，
        并将 http 统一为 https。
        """
        u = (url or "").strip().rstrip("/")
        if u.lower().startswith("http://"):
            u = "https://" + u[7:]
        return u.lower()

    def discover(self, start_year: int, end_year: int) -> List[Dict[str, Any]]:
        results = []
        # 用 URL（而不是 name）来判断是否已存在——这与下游 merge_conf 的语义
        # 完全一致 (see [generate_conf.merge_conf](../../discovery/generate_conf.py#L83-L91))。
        # 用 name 去重会导致："只删了 ACL2026 主会那条错 tag、保留了 findings"
        # 之后主会 URL 因为同名的 findings 还在 existing 里而永远无法被重写。
        existing_urls = {
            self._canon_url(item.get("url"))
            for item in self.existing
            if item.get("url")
        }

        for venue_id, venue_name in CORE_VENUES.items():
            venue_url = f"https://aclanthology.org/venues/{venue_id}/"
            text = self._get_text(venue_url, timeout=20, retries=2)
            if not text:
                continue

            soup = BeautifulSoup(text, "html.parser")
            event_links = set()
            for a in soup.find_all("a", href=True):
                href = a["href"]
                m = _EVENTS_LINK_RE.match(href)
                if not m:
                    continue
                v_id, year_str = m.groups()
                if v_id != venue_id:
                    continue
                year = int(year_str)
                if year < start_year or year > end_year:
                    continue
                full_url = urljoin(venue_url, href)
                event_links.add((year, full_url))

            for year, event_url in sorted(event_links):
                name = f"{venue_name}{year}"
                main_seen = self._canon_url(event_url) in existing_urls

                if not main_seen:
                    # 探测该 event 页面实际使用的前缀
                    prefix = self._detect_event_prefix(event_url, venue_id=venue_id, year=year)
                    if not prefix:
                        # fallback：按常见规则推断
                        prefix = self._fallback_prefix(venue_id, year)

                    tag = _tag_for(prefix)
                    results.append(
                        {
                            "name": name,
                            "tag": tag,
                            "url": event_url,
                        }
                    )
                    existing_urls.add(self._canon_url(event_url))

                # Findings（2020 年后部分会议有 findings）——独立判断 URL，
                # 让 findings 缺失时也能单独补齐。
                findings_url = (
                    f"https://aclanthology.org/volumes/{year}.findings-{venue_id}/"
                )
                if self._canon_url(findings_url) in existing_urls:
                    continue
                if self._head_ok(findings_url):
                    # 与现有行为保持一致：findings 合并到同名会议
                    results.append(
                        {
                            "name": name,
                            "tag": f"^{year}.findings*",
                            "url": findings_url,
                        }
                    )
                    existing_urls.add(self._canon_url(findings_url))

        return results

    def _detect_event_prefix(self, event_url: str, venue_id: str = "", year: int = 0) -> str:
        """访问 event 页面，检测论文编号前缀。

        返回值是一个可以直接被 :func:`_tag_for` 包裹的 "tag body"：

        - 现代 Anthology 命名（2020+）：形如 ``2026.acl``，最终会被拼成 ``^2026.acl*``，
          可以匹配 ``/2026.acl-long.1/`` / ``/2026.acl-short.2/`` 等所有主会 volume。
        - 旧式 Anthology 命名（≤2019）：形如 ``P05-`` (保留尾部连字符)，
          最终会被拼成 ``^P05-*``，与历史 tag 完全一致。

        探测优先级：先尝试新格式（因为 2020 年起全部 core venue 都改用新命名）；
        没有新格式论文时，再退回旧格式；两者都失败则由 :func:`_fallback_prefix` 兜底。
        """
        text = self._get_text(event_url, timeout=15, retries=2)
        if not text:
            return ""

        soup = BeautifulSoup(text, "html.parser")

        # ---- 现代格式 (2020+): /YYYY.{stem}.N/ ---------------------------
        modern_stem_counts: Dict[str, int] = {}
        for a in soup.find_all("a", href=True):
            m = _MODERN_STEM_RE.match(a["href"])
            if not m:
                continue
            stem = m.group(1)  # e.g. "2026.acl-long"
            modern_stem_counts[stem] = modern_stem_counts.get(stem, 0) + 1

        if modern_stem_counts:
            # 只保留与当前 event (venue_id+year) 匹配的 stem，避免把外部引用的 volume 误算进来。
            if venue_id and year:
                stem_prefix = f"{year}.{venue_id}"
                filtered = {s: c for s, c in modern_stem_counts.items()
                            if s == stem_prefix or s.startswith(f"{stem_prefix}-")}
                if not filtered:
                    return ""
                modern_stem_counts = filtered
            # 优先剔除明显是 workshop / co-located 的 stem（比如 2024.acl-ws / *-workshop）
            non_ws = {s: c for s, c in modern_stem_counts.items()
                      if not (s.endswith("-ws") or "-workshop" in s or ".ws" in s)}
            pool = non_ws or modern_stem_counts
            best_stem = max(pool, key=pool.get)  # e.g. "2026.acl-long"

            # 收敛到该 venue 的公共前缀：拿到所有以 "{year}.{venue_id}" 开头的 stem
            # 的最长公共前缀。对 ACL2026 会得到 "2026.acl-"，去掉尾部 '-' 后就是 "2026.acl"，
            # 与人工维护的 ^2024.acl* / ^2025.acl* 等 tag 完全一致。
            #
            # 边界处理：当 same_venue 只有一个元素（例如 event 页面在探测时段
            # 只暴露了单一 track，如 /2026.acl-long.N/ 尚未上线短论文），
            # _longest_common_prefix(["2026.acl-long"]) 会返回完整字符串
            # "2026.acl-long"，其末字符 'g' 不会被 rstrip("-") 剥掉，最终生成
            # 过窄的 tag ^2026.acl-long*，漏掉后续上线的 short/demo/srw。
            # 因此：当 venue_id / year 均已知时，直接返回 "{year}.{venue_id}"，
            # 与 _fallback_prefix 的现代格式规则保持一致，让单-track 和多-track
            # 走同一条稳定路径。
            same_venue = [s for s in pool
                          if venue_id and s.startswith(f"{year}.{venue_id}")]
            if same_venue and venue_id and year:
                common = f"{year}.{venue_id}"
            elif same_venue:
                common = _longest_common_prefix(same_venue).rstrip("-")
            else:
                # 纵深防御：当调用方省略 venue_id（公开签名默认 ""），same_venue
                # 恒为空，会掉进这里。此时 best_stem 可能是单-track 字面串
                # （如 "2026.acl-long"），rstrip("-") 无法剥掉末尾 'g'，会复现
                # 前文修好的单-track LCP 退化。改用 rsplit("-", 1)[0] 把 track
                # 段整段砍掉，让 fallback 路径与 venue_id 已知时的行为一致。
                if "-" in best_stem:
                    common = best_stem.rsplit("-", 1)[0]
                else:
                    common = best_stem
            if common:
                return common

        # ---- 旧格式 (≤2019): /Xyy-NNNN/ ---------------------------------
        prefix_counts: Dict[str, int] = {}
        for strong in soup.find_all("strong"):
            a = strong.find("a", href=_PAPER_LINK_RE)
            if not a:
                continue
            m = _PREFIX_RE.match(a["href"])
            if m:
                prefix = m.group(1)
                prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1

        if not prefix_counts:
            return ""

        # 优先选择非 W 前缀（W 通常是 workshop）；如果全是 W 则选最多的
        non_w = {p: c for p, c in prefix_counts.items() if not p.startswith("W")}
        if non_w:
            best_old = max(non_w, key=non_w.get)
        else:
            best_old = max(prefix_counts, key=prefix_counts.get)
        # 保留尾部连字符，让 _tag_for 直接拼出与历史 tag 一致的 "^P05-*" 形式。
        return f"{best_old}-"

    def _fallback_prefix(self, venue_id: str, year: int) -> str:
        """当页面探测彻底失败时，才使用历史规则兜底。

        注意：从 2020 年起 Anthology 已全面切换到 ``YYYY.{venue}-{track}.N`` 新格式，
        因此对 2020+ 的目标年份直接返回新格式的 tag body ``"{year}.{venue_id}"``；
        对 ≤2019 的历史年份保持旧的 ``"{Xyy}-"`` 兜底形式。
        """
        if year >= 2020 and venue_id:
            return f"{year}.{venue_id}"
        yy = str(year)[2:]
        prefix_map = {
            "acl": "P",
            "emnlp": "D",
            "naacl": "N",
            "eacl": "E",
            "coling": "C",
        }
        return f"{prefix_map.get(venue_id, 'P')}{yy}-"


def _longest_common_prefix(strings: List[str]) -> str:
    if not strings:
        return ""
    shortest = min(strings, key=len)
    for i, ch in enumerate(shortest):
        for other in strings:
            if other[i] != ch:
                return shortest[:i]
    return shortest
