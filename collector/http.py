"""共享 HTTP 会话与 Anubis 挑战处理。

SESSION/HEADERS 供各数据源使用；DBLP 自 2026-09 起部署 Anubis PoW 反爬，
涉及 DBLP 的请求应使用 ``get_with_anubis``（命中挑战页时按会话求解一次，
失败后抛 :class:`AnubisUnsolvableError`，避免把验证页当成空收录）。
"""

import warnings

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import XMLParsedAsHTMLWarning

from collector.anubis import (  # noqa: F401  (re-exported for callers)
    AnubisUnsolvableError,
    RateLimitedError,
    get_with_anubis,
    is_challenge,
)

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
