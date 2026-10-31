"""OpenReview (ICLR / NeurIPS) 自动发现"""

from typing import List, Dict, Any, Set
from urllib.parse import quote
from .base import BaseDiscovery

# 复用 collector 中的「已接收 venue」判定，确保 discovery 与采集端口径一致。
# 这样可以从源头避免将 Submitted / Withdrawn / Desk Rejected 等未接收 venue
# 写入 conf/iclr_conf.json，从而减少抓取开销与缓存污染。
#
# 不在此处提供 fallback 内联实现，避免与 collector.py 中的关键字常量失同步
# （例如新增 "blogpost" 白名单时遗漏会造成 ICLR Blogpost Track 被误过滤）。
# 若 import 失败应直接抛错暴露环境问题，而不是静默退化到一个过时的实现。
import sys
from pathlib import Path

# 兼容 `python -m discovery.generate_conf` 与从子目录直接执行两种调用方式
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from collector import is_openreview_accepted_venue  # noqa: E402


# OpenReview 2024 起将主接口迁移到 v2（api2.openreview.net），
# 同时 invitation 字段从 Blind_Submission 改名为 Submission。
# 这里为 2024+ 与旧年份分别配置端点，确保两端的会议都能被发现。
V2_START_YEAR = 2024


class OpenReviewDiscovery(BaseDiscovery):
    """
    通过 OpenReview API 自动发现 ICLR 和 NeurIPS 的 venue 配置。
    每个 venue 可能需要多条 URL（分页，每页 limit=1000）。

    对 2024+ 年份优先使用 v2 端点（api2.openreview.net），
    其 content 字段为嵌套 {"value": ...} 结构，包含 abstract，
    采集时即可被 collector.py 一并写入，无需 backfill。
    """

    def __init__(self, existing_conf: List[Dict[str, Any]] = None):
        super().__init__("openreview", existing_conf)

    @staticmethod
    def _api_root(year: int) -> str:
        return "https://api2.openreview.net" if year >= V2_START_YEAR else "https://api.openreview.net"

    def _fetch_venues(self, invitation_tpl: str, year: int) -> List[str]:
        """返回该年份下所有 distinct 的 *已接收* venue 字符串。

        在源头过滤掉 Submitted / Reject / Withdrawn / Desk Rejected 等未接收
        venue，避免后续为它们生成无效的抓取 URL。
        """
        invitation = invitation_tpl.format(year=year)
        api_root = self._api_root(year)
        url = (
            f"{api_root}/notes?"
            f"invitation={quote(invitation, safe='')}&"
            f"details=replyCount&offset=0&limit=1000"
        )
        try:
            data = self._get_json(url, timeout=30, retries=3)
        except Exception:
            return []
        venues: Set[str] = set()
        skipped: Set[str] = set()
        for note in data.get("notes", []):
            content = note.get("content", {}) or {}
            v = content.get("venue")
            # v2 的 content 字段是嵌套结构 {"venue": {"value": "..."}}
            if isinstance(v, dict):
                v = v.get("value")
            if not v:
                continue
            if is_openreview_accepted_venue(v):
                venues.add(v)
            else:
                skipped.add(v)
        if skipped:
            print(f"    [-] OpenReview: skip {len(skipped)} non-accepted venue(s) for {invitation}: {sorted(skipped)}")
        return sorted(venues)

    def _count_for_venue(self, venue: str, invitation: str, year: int) -> int:
        """估算某个 venue 的论文数量，用于决定需要几条 URL"""
        api_root = self._api_root(year)
        url = (
            f"{api_root}/notes?"
            f"content.venue={quote(venue, safe='')}&"
            f"invitation={quote(invitation, safe='')}&"
            f"offset=0&limit=1"
        )
        try:
            data = self._get_json(url, timeout=30, retries=2)
            return data.get("count", 0)
        except Exception:
            return 0

    def _generate_urls(self, name: str, venue: str, invitation: str, count: int, year: int) -> List[Dict[str, Any]]:
        limit = 1000
        pages = (count + limit - 1) // limit
        api_root = self._api_root(year)
        results = []
        for i in range(pages):
            offset = i * limit
            url = (
                f"{api_root}/notes?"
                f"content.venue={quote(venue, safe='')}&"
                f"details=replyCount&"
                f"offset={offset}&limit={limit}&"
                f"invitation={quote(invitation, safe='')}"
            )
            results.append({"name": name, "url": url})
        return results

    def discover(self, start_year: int, end_year: int) -> List[Dict[str, Any]]:
        results = []
        # 每条 source 是 (prefix, [invitation_tpl ...])
        # 2024+ ICLR/NeurIPS 在 v2 中 invitation 改名为 Submission；
        # 老年份继续用 Blind_Submission。我们对两种模板都试，取有结果者。
        sources = [
            ("ICLR", [
                "ICLR.cc/{year}/Conference/-/Submission",
                "ICLR.cc/{year}/Conference/-/Blind_Submission",
            ]),
            ("NIPS", [
                "NeurIPS.cc/{year}/Conference/-/Submission",
                "NeurIPS.cc/{year}/Conference/-/Blind_Submission",
            ]),
        ]
        for prefix, invitation_tpls in sources:
            for year in range(start_year, end_year + 1):
                name = f"{prefix}{year}"
                # 按 v2 优先级：2024+ 先 Submission，老年份先 Blind_Submission
                ordered_tpls = (
                    invitation_tpls if year >= V2_START_YEAR else list(reversed(invitation_tpls))
                )
                found = False
                for invitation_tpl in ordered_tpls:
                    invitation = invitation_tpl.format(year=year)
                    venues = self._fetch_venues(invitation_tpl, year)
                    if not venues:
                        continue
                    for venue in venues:
                        # 防御性双保险：即便 _fetch_venues 已过滤，这里再次校验，
                        # 避免未来重构或 venue 字符串构造路径绕过过滤。
                        if not is_openreview_accepted_venue(venue):
                            continue
                        count = self._count_for_venue(venue, invitation, year)
                        if count == 0:
                            continue
                        results.extend(self._generate_urls(name, venue, invitation, count, year))
                    found = True
                    break  # 已找到有效 invitation，无需再试其它模板
                if not found:
                    print(f"    [!] OpenReview returned no venues for {name} (tried {ordered_tpls})")
        return results
