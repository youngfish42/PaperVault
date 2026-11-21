import re
from urllib.parse import urljoin, urlsplit

from .base import BaseDiscovery

MICCAI_OPENACCESS_ROOT = "https://papers.miccai.org/"
MICCAI_MAIN_PATH_RE = re.compile(r"^/miccai-(\d{4})/?$")


class MICCAIDiscovery(BaseDiscovery):
    def discover(self, start_year: int, end_year: int):
        text = self._get_text(MICCAI_OPENACCESS_ROOT, timeout=30)
        if not text:
            return []

        from bs4 import BeautifulSoup

        results = []
        seen_years = set()
        soup = BeautifulSoup(text, "html.parser")
        for anchor in soup.find_all("a", href=True):
            url = urljoin(MICCAI_OPENACCESS_ROOT, anchor["href"])
            parsed = urlsplit(url)
            if parsed.netloc.lower() != "papers.miccai.org":
                continue
            match = MICCAI_MAIN_PATH_RE.match(parsed.path)
            if match is None:
                continue
            year = int(match.group(1))
            name = f"MICCAI{year}"
            if year < start_year or year > end_year or year in seen_years:
                continue
            if name in self.existing_names:
                continue
            seen_years.add(year)
            results.append({"name": name, "url": url})
        return results
