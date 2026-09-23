"""Anubis 反爬挑战求解器（DBLP 当前部署于 Anubis 之后）。

DBLP 自 2026-09 起全站启用 Anubis（https://github.com/TecharoHQ/anubis）
PoW 验证：普通请求会拿到 HTTP 200 的挑战页（"Making sure you're not a
bot!"），直接解析会拿到 0 条论文。本模块按会话求解一次 PoW 并种下
cookie，之后该 session 的请求即正常放行。

算法与 Anubis 官方 JS worker 一致：
``sha256(f"{randomData}{nonce}")``，要求哈希前 ``difficulty // 2`` 字节为 0，
难度为奇数时下一个字节的高半字节也为 0。难度 5 约 100 万次哈希（1-2s）。

使用方式::

    from collector.anubis import get_with_anubis
    resp = get_with_anubis(SESSION, url, headers=HEADERS)

求解失败会抛出 :class:`AnubisUnsolvableError`，调用方应让该 URL 走失败
路径（记 failures、不写 progress），避免把挑战页当成"空收录"。
"""

import hashlib
import json
import random
import re
import time
from urllib.parse import urlsplit

import requests

# 挑战页内嵌的挑战 JSON（Anubis 全版本均带此 script id）
_CHALLENGE_RE = re.compile(
    r'<script id="anubis_challenge" type="application/json">(.*?)</script>',
    re.S,
)

# 求解与重试预算
MAX_SOLVE_NONCE = 1 << 26          # 约 6700 万次哈希，远超难度 5-6 的期望次数
MAX_CHALLENGE_ROUNDS = 3           # 同一 URL 最多连续求解几轮挑战
RATE_LIMIT_ATTEMPTS = 4
RATE_LIMIT_BACKOFF = 3.0           # 无 Retry-After 时，第 n 次 429 退避 RATE_LIMIT_BACKOFF * n 秒
MAX_RATE_LIMIT_DELAY = 60.0        # 单次 429 退避上限（含 Retry-After），防止长时间阻塞软超时


class AnubisUnsolvableError(RuntimeError):
    """Anubis 挑战无法求解（预算耗尽 / 端点异常 / 算法变更）。"""


class RateLimitedError(AnubisUnsolvableError):
    """429 限流在重试预算内未解除。继承 AnubisUnsolvableError，使调用方的
    反爬守卫（re-raise / failures 路径）对两类"被拦截"一视同仁。"""


def is_challenge(resp: requests.Response) -> bool:
    """判断响应是否为 Anubis 挑战页。

    只按页面标记判定，不设 Content-Type 门槛（门槛只会造成漏判：挑战页
    一旦被当成正常页，上游会解析成 0 篇并写 empty 标记）。标记位于页面
    <head> 内，扫描前 64KB 即可，避免为超大页面多做一次全文解码。
    """
    return "anubis_challenge" in resp.text[:65536]


def _solve_pow(random_data: str, difficulty: int) -> tuple:
    """求解 PoW，返回 (hex_hash, nonce, elapsed_seconds)。"""
    full_zero_bytes = difficulty // 2
    odd_nibble = difficulty % 2 == 1
    t0 = time.time()
    nonce = 0
    while nonce < MAX_SOLVE_NONCE:
        digest = hashlib.sha256(f"{random_data}{nonce}".encode()).digest()
        ok = not any(digest[:full_zero_bytes])
        if ok and odd_nibble:
            ok = digest[full_zero_bytes] >> 4 == 0
        if ok:
            return digest.hex(), nonce, time.time() - t0
        nonce += 1
    raise AnubisUnsolvableError(
        f"PoW budget exhausted (difficulty={difficulty}, tried {nonce} nonces)"
    )


def _pass_challenge(session: requests.Session, resp: requests.Response) -> None:
    """解析挑战页、求解 PoW、请求 pass-challenge 端点种 cookie。

    所有失败（坏 JSON / 缺字段 / 请求异常 / 被拒绝）统一转换为
    :class:`AnubisUnsolvableError`，保证调用方的失败契约成立。
    """
    m = _CHALLENGE_RE.search(resp.text)
    if not m:
        raise AnubisUnsolvableError("challenge page missing anubis_challenge JSON")
    try:
        payload = json.loads(m.group(1))
        challenge = payload["challenge"]
        difficulty = payload["rules"]["difficulty"]
        random_data = challenge["randomData"]
        challenge_id = challenge["id"]
    except (ValueError, KeyError, TypeError) as e:
        raise AnubisUnsolvableError(f"malformed challenge payload: {e}") from e

    solution, nonce, elapsed = _solve_pow(random_data, difficulty)

    parsed = urlsplit(resp.url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    try:
        pr = session.get(
            f"{base}/.within.website/x/cmd/anubis/api/pass-challenge",
            params={
                "id": challenge_id,
                "response": solution,
                "nonce": nonce,
                "redir": parsed.path,
                "elapsedTime": max(int(elapsed * 1000), 1),
            },
            timeout=30,
            allow_redirects=False,
        )
    except requests.RequestException as e:
        raise AnubisUnsolvableError(f"pass-challenge request failed: {e}") from e
    if pr.status_code not in (200, 302, 303):
        raise AnubisUnsolvableError(
            f"pass-challenge rejected with HTTP {pr.status_code}"
        )
    print(f"[+] Anubis challenge solved for {parsed.netloc} "
          f"(difficulty={difficulty}, nonce={nonce})")


def _rate_limit_delay(resp: requests.Response, attempt: int) -> float:
    """429 退避时长：优先遵循服务端 Retry-After（封顶 MAX_RATE_LIMIT_DELAY），
    否则线性退避并加抖动。封顶避免单次 sleep 拖过采集软超时。"""
    raw = resp.headers.get("Retry-After")
    if raw:
        try:
            return min(max(float(raw), 0.0), MAX_RATE_LIMIT_DELAY)
        except ValueError:
            pass
    return RATE_LIMIT_BACKOFF * attempt + random.uniform(0, 1.0)


def get_with_anubis(session: requests.Session, url: str, **kw) -> requests.Response:
    """GET 一个可能位于 Anubis 之后的 URL。

    - 命中挑战页：求解 PoW 种 cookie 后重发（最多 MAX_CHALLENGE_ROUNDS 轮）；
    - 命中 429（urllib3 Retry 不覆盖的状态码）：按 Retry-After / 线性退避
      重试；预算耗尽抛 :class:`RateLimitedError`——若直接返回 429 响应，
      上游会把空响应体解析成 0 篇并写 empty 标记，持续限流被静默掩盖；
    - 5xx 不在本层重试：collector 的 SESSION 已由 urllib3 Retry 处理
      （两层叠加会让单个 URL 发出数十次请求），discovery 的调用方则用
      ``raise_for_status`` 走自己的重试循环；
    - 其余情况直接返回响应。
    """
    kw.setdefault("timeout", 30)
    rounds = 0
    attempts = 0
    while True:
        resp = session.get(url, **kw)
        if is_challenge(resp):
            rounds += 1
            if rounds > MAX_CHALLENGE_ROUNDS:
                raise AnubisUnsolvableError(
                    f"still challenged after {MAX_CHALLENGE_ROUNDS} solve rounds: {url}"
                )
            _pass_challenge(session, resp)
            continue
        if resp.status_code == 429:
            attempts += 1
            if attempts > RATE_LIMIT_ATTEMPTS:
                raise RateLimitedError(
                    f"still rate limited (429) after {RATE_LIMIT_ATTEMPTS} retries: {url}"
                )
            time.sleep(_rate_limit_delay(resp, attempts))
            continue
        return resp
