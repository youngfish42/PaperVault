"""NeurIPS / MLSys 自动发现"""

from typing import List, Dict, Any
from .base import BaseDiscovery


class NeurIPSDiscovery(BaseDiscovery):
    """NeurIPS proceedings: https://papers.nips.cc/paper/{year}"""

    def discover(self, start_year: int, end_year: int) -> List[Dict[str, Any]]:
        results = []
        for year in range(start_year, end_year + 1):
            name = f"NIPS{year}"
            if name in self.existing_names:
                continue
            url = f"https://papers.nips.cc/paper/{year}"
            if self._head_ok(url):
                results.append({"name": name, "url": url})
        return results


class MLSysDiscovery(BaseDiscovery):
    """MLSys proceedings: https://proceedings.mlsys.org/paper/{year}"""

    def discover(self, start_year: int, end_year: int) -> List[Dict[str, Any]]:
        results = []
        for year in range(start_year, end_year + 1):
            name = f"MLSys{year}"
            if name in self.existing_names:
                continue
            url = f"https://proceedings.mlsys.org/paper/{year}"
            if self._head_ok(url):
                results.append({"name": name, "url": url})
        return results
