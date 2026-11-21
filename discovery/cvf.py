"""CVF (thecvf.com) 自动发现"""

import re
from typing import List, Dict, Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .base import BaseDiscovery

MENU_URLS = (
    "https://openaccess.thecvf.com/menu",
    "https://openaccess.thecvf.com/menu_other.html",
)
CVF_HOST = "openaccess.thecvf.com"
CONF_NAME_RE = re.compile(r"^([A-Za-z]+)(\d{4})$")
EXISTING_NAME_RE = re.compile(r"^([A-Za-z]+)\d+$")


class CVFDiscovery(BaseDiscovery):
    def discover(self, start_year: int, end_year: int) -> List[Dict[str, Any]]:
        allowed_prefixes = {
            match.group(1).upper()
            for name in self.existing_names
            if (match := EXISTING_NAME_RE.match(name or ""))
        }
        results = []
        seen = set()
        for menu_url in MENU_URLS:
            html = self._get_text(menu_url)
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for anchor in soup.find_all("a", href=True):
                if anchor.get_text(" ", strip=True) != "Main Conference":
                    continue
                url = urljoin(menu_url, anchor["href"])
                if urlsplit(url).netloc.lower() != CVF_HOST:
                    continue
                event = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
                if event.endswith(".py"):
                    event = event[:-3]
                match = CONF_NAME_RE.fullmatch(event)
                if match is None:
                    continue
                prefix, year_text = match.groups()
                name = f"{prefix.upper()}{year_text}"
                year = int(year_text)
                if (
                    prefix.upper() not in allowed_prefixes
                    or not start_year <= year <= end_year
                    or name in self.existing_names
                    or name in seen
                ):
                    continue
                seen.add(name)
                results.append({"name": name, "url": url})
        return results
