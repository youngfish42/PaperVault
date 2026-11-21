from collector.sources.ijcai import (
    IJCAI_PROCEEDINGS_INDEX,
    canonicalize_ijcai_url,
    parse_ijcai_proceedings_index,
)

from .base import BaseDiscovery


class IJCAIDiscovery(BaseDiscovery):
    def discover(self, start_year: int, end_year: int):
        text = self._get_text(IJCAI_PROCEEDINGS_INDEX, timeout=30)
        if not text:
            return []
        existing_urls = {
            canonicalize_ijcai_url(item.get("url", "")) for item in self.existing
        }
        results = []
        for entry in parse_ijcai_proceedings_index(text):
            year = int(entry["name"][-4:])
            if start_year <= year <= end_year and entry["url"] not in existing_urls:
                results.append(entry)
        return results
