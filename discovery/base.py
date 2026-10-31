"""Discovery 基类与通用工具"""

import abc
import os
import time
from typing import List, Dict, Any
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter

from collector.anubis import get_with_anubis, is_challenge

CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "im.young@foxmail.com")

DISCOVERY_AGENT = (
    "PaperVault/1.0 (+https://github.com/youngfish42/PaperVault; "
    f"contact: {CONTACT_EMAIL})"
    if CONTACT_EMAIL
    else "PaperVault/1.0 (+https://github.com/youngfish42/PaperVault)"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36 " + DISCOVERY_AGENT
    )
}


def _create_session() -> requests.Session:
    """创建一个新的 Session，禁用系统代理，避免 Windows 自动代理问题"""
    session = requests.Session()
    session.trust_env = False
    session.mount("https://", HTTPAdapter(max_retries=2))
    session.mount("http://", HTTPAdapter(max_retries=2))
    return session


_ANUBIS_SESSION = None


def _anubis_session() -> requests.Session:
    """DBLP（Anubis 反爬）专用的进程级 Session：cookie 复用，PoW 只解一次。"""
    global _ANUBIS_SESSION
    if _ANUBIS_SESSION is None:
        _ANUBIS_SESSION = _create_session()
    return _ANUBIS_SESSION


def _is_dblp_url(url: str) -> bool:
    return "dblp" in urlsplit(url).netloc.lower()


class BaseDiscovery(abc.ABC):
    """自动发现会议配置的抽象基类"""

    def __init__(self, name: str = "", existing_conf: List[Dict[str, Any]] = None):
        self.name = name
        self.existing = existing_conf or []
        self.existing_names = {item["name"] for item in self.existing}

    @abc.abstractmethod
    def discover(self, start_year: int, end_year: int) -> List[Dict[str, Any]]:
        """返回新发现的配置列表，每条为 dict(name=..., url=..., [tag=...])"""
        ...

    def _head_ok(self, url: str, timeout: int = 10) -> bool:
        """发送 HEAD 请求检查 URL 是否可访问，带重试。"""
        # DBLP 在 Anubis 之后：HEAD 会拿到 200 挑战页而误判 exists，
        # 改走 PoW 求解后的 GET，按最终状态码与内容判定。
        if _is_dblp_url(url):
            for attempt in range(2):
                try:
                    resp = get_with_anubis(
                        _anubis_session(), url, headers=HEADERS, timeout=timeout
                    )
                    return resp.status_code == 200 and not is_challenge(resp)
                except Exception:
                    if attempt == 0:
                        time.sleep(2)
                        continue
                    return False
        for attempt in range(2):
            try:
                with _create_session() as session:
                    resp = session.head(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
                    if resp.status_code == 429:
                        # 被限流，等待后重试
                        if attempt == 0:
                            time.sleep(3)
                            continue
                        return False
                    return resp.status_code == 200
            except requests.exceptions.ConnectionError:
                if attempt == 0:
                    time.sleep(2)
                    continue
                return False
            except Exception:
                return False
        return False

    def _get_json(self, url: str, timeout: int = 30, retries: int = 3) -> Any:
        for attempt in range(retries):
            try:
                with _create_session() as session:
                    resp = session.get(url, headers=HEADERS, timeout=timeout)
                    resp.raise_for_status()
                    return resp.json()
            except Exception as e:
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt)
        return None

    def _get_text(self, url: str, timeout: int = 15, retries: int = 2) -> str:
        for attempt in range(retries):
            try:
                if _is_dblp_url(url):
                    resp = get_with_anubis(
                        _anubis_session(), url, headers=HEADERS, timeout=timeout
                    )
                    resp.raise_for_status()
                    return resp.text
                with _create_session() as session:
                    resp = session.get(url, headers=HEADERS, timeout=timeout)
                    resp.raise_for_status()
                    return resp.text
            except Exception:
                if attempt == retries - 1:
                    return ""
                time.sleep(2 ** attempt)
        return ""
