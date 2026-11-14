"""CVF (thecvf.com) 自动发现"""

from typing import List, Dict, Any
from .base import BaseDiscovery


CONF_NAMES = ["CVPR", "ICCV", "WACV", "ECCV"]


class CVFDiscovery(BaseDiscovery):
    def discover(self, start_year: int, end_year: int) -> List[Dict[str, Any]]:
        results = []
        for conf in CONF_NAMES:
            for year in range(start_year, end_year + 1):
                name = f"{conf}{year}"
                if name in self.existing_names:
                    continue

                # ECCV 只在偶数年举办
                if conf == "ECCV" and year % 2 != 0:
                    continue

                url = f"https://openaccess.thecvf.com/{conf}{year}?day=all"
                # WACV 某些年份没有 ?day=all
                if self._head_ok(url):
                    results.append({"name": name, "url": url})
                else:
                    # 尝试不带 day=all（早期 WACV）
                    alt_url = f"https://openaccess.thecvf.com/{conf}{year}"
                    if alt_url != url and self._head_ok(alt_url):
                        results.append({"name": name, "url": alt_url})
        return results
