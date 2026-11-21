"""DBLP 自动发现（会议 + 期刊）

重构后逻辑：
1. 周期性访问各刊物在 DBLP 的根目录（index.html）。
2. 从根目录页面解析出所有卷/年份的直达链接。
3. 仅保留目标年份范围内的链接，并过滤掉已在 dblp_conf.json 或其他信源
   （ACL / CVF / OpenReview / NeurIPS）中覆盖的条目。
4. 对于根目录无法访问的少数刊物，保留轻量的 fallback HEAD 探测。

CCF 第七版 A 类会议与期刊已按分类补全；与其他信源重复的会议（如 ACL、
CVPR、ICCV、ECCV、WACV、ICLR、NeurIPS、MLSys）已从 DBLP 发现列表中剔除，
避免重复获取。
"""

import json
import re
import time
from pathlib import Path
from typing import List, Dict, Any, Set

from bs4 import BeautifulSoup

from .base import BaseDiscovery

CACHE_FILE = Path("cache/dblp_discovery_cache.json")
CACHE_TTL_EXIST = 30 * 86400      # 已确认存在的 URL 缓存 30 天
CACHE_TTL_NOT_EXIST = 7 * 86400   # 已确认不存在的 URL 缓存 7 天

# ---------------------------------------------------------------------------
# 正则：从根目录页面提取有效链接
# ---------------------------------------------------------------------------
_CONF_LINK_RE = re.compile(
    r"^https?://dblp(?:\.org|\.uni-trier\.de)/db/conf/[^/]+/[^/]*\d{4}(?:-\d+)?\.html$"
)
_JOURNAL_LINK_RE = re.compile(
    r"^https?://dblp(?:\.org|\.uni-trier\.de)/db/journals/[^/]+/[^/]*\d+\.html$"
)
_YEAR_RE = re.compile(r"(\d{4})")


def _url_key(url: str) -> str:
    """URL 去重用的归一化键。

    DBLP 的镜像域名（dblp.org / dblp.uni-trier.de / dblp.dagstuhl.de …）
    指向同一页面；conf 中历史上存在镜像域名的条目（如 IJCV2020-2022），
    若按原始字符串比较会被误判为新条目。DBLP 系列主机统一按 path 比较。
    """
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if "dblp" in parts.netloc.lower():
        return parts.path
    return url


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cache(cache: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 会议配置（根目录页面 + 起始年份 + 配置名称前缀）
# 注意：已剔除被其他信源覆盖的会议 —— ACL/EMNLP/NAACL/EACL/COLING
# (ACL), CVPR/ICCV/ECCV/WACV (CVF), ICLR/NIPS (OpenReview), MLSys (NIPS)
# ---------------------------------------------------------------------------
CONFERENCES: Dict[str, Dict[str, Any]] = {
    # --- 人工智能 ---
    "aaai":   {"root": "https://dblp.org/db/conf/aaai/index.html",   "start_year": 1980, "name": "AAAI"},
    "icml":   {"root": "https://dblp.org/db/conf/icml/index.html",   "start_year": 1980, "name": "ICML"},
    "ijcai":  {"root": "https://dblp.org/db/conf/ijcai/index.html",  "start_year": 1980, "name": "IJCAI"},
    # --- 计算机图形学与多媒体 ---
    "mm":     {"root": "https://dblp.org/db/conf/mm/index.html",     "start_year": 1980, "name": "MM"},
    "vr":     {"root": "https://dblp.org/db/conf/vr/index.html",     "start_year": 1980, "name": "VR"},
    "visualization": {"root": "https://dblp.org/db/conf/visualization/index.html", "start_year": 1980, "name": "IEEEVIS"},
    "siggraph": {"root": "https://dblp.org/db/conf/siggraph/index.html", "start_year": 1980, "name": "SIGGRAPH"},
    # --- 数据库/数据挖掘/内容检索 ---
    "kdd":    {"root": "https://dblp.org/db/conf/kdd/index.html",    "start_year": 1980, "name": "KDD"},
    "sigir":  {"root": "https://dblp.org/db/conf/sigir/index.html",  "start_year": 1980, "name": "SIGIR"},
    "cikm":   {"root": "https://dblp.org/db/conf/cikm/index.html",   "start_year": 1980, "name": "CIKM"},
    "wsdm":   {"root": "https://dblp.org/db/conf/wsdm/index.html",   "start_year": 1980, "name": "WSDM"},
    "www":    {"root": "https://dblp.org/db/conf/www/index.html",    "start_year": 1980, "name": "WWW"},
    "ecir":   {"root": "https://dblp.org/db/conf/ecir/index.html",   "start_year": 1980, "name": "ECIR"},
    "icdm":   {"root": "https://dblp.org/db/conf/icdm/index.html",   "start_year": 1980, "name": "ICDM"},
    "recsys": {"root": "https://dblp.org/db/conf/recsys/index.html", "start_year": 1980, "name": "RECSYS"},
    "vldb":   {"root": "https://dblp.org/db/conf/vldb/index.html",   "start_year": 1980, "name": "VLDB"},
    "icde":   {"root": "https://dblp.org/db/conf/icde/index.html",   "start_year": 1980, "name": "ICDE"},
    # --- 语音与多媒体 ---
    "icassp":      {"root": "https://dblp.org/db/conf/icassp/index.html",      "start_year": 1980, "name": "ICASSP"},
    "interspeech": {"root": "https://dblp.org/db/conf/interspeech/index.html", "start_year": 1980, "name": "INTERSPEECH"},
    # 注意：icme 无独立根目录，fallback 见下方
    "icme":        {"root": "https://dblp.org/db/conf/icmcs/icme/index.html",  "start_year": 1980, "name": "ICME", "fallback": True},
    # --- 机器学习（理论向）---
    "colt":   {"root": "https://dblp.org/db/conf/colt/index.html",   "start_year": 1980, "name": "COLT"},
    "aistats":{"root": "https://dblp.org/db/conf/aistats/index.html","start_year": 1980, "name": "AISTATS"},
    "alt":    {"root": "https://dblp.org/db/conf/alt/index.html",    "start_year": 1980, "name": "ALT"},
    "uai":    {"root": "https://dblp.org/db/conf/uai/index.html",    "start_year": 1980, "name": "UAI"},
    # --- 数据库与系统 ---
    "fast":   {"root": "https://dblp.org/db/conf/fast/index.html",   "start_year": 1980, "name": "FAST"},
    "sigmod": {"root": "https://dblp.org/db/conf/sigmod/index.html", "start_year": 1980, "name": "SIGMOD"},
    # --- 其他 ---
    "iswc":   {"root": "https://dblp.org/db/conf/semweb/iswc/index.html", "start_year": 1980, "name": "ISWC"},
    "bmvc":   {"root": "https://dblp.org/db/conf/bmvc/index.html",   "start_year": 1980, "name": "BMVC"},
    "miccai": {"root": "https://dblp.org/db/conf/miccai/index.html", "start_year": 1980, "name": "MICCAI"},
    # --- 计算机体系结构/高性能计算/存储系统 ---
    "ppopp":  {"root": "https://dblp.org/db/conf/ppopp/index.html",  "start_year": 1980, "name": "PPoPP"},
    "hpca":   {"root": "https://dblp.org/db/conf/hpca/index.html",   "start_year": 1980, "name": "HPCA"},
    "micro":  {"root": "https://dblp.org/db/conf/micro/index.html",  "start_year": 1980, "name": "MICRO"},
    "sc":     {"root": "https://dblp.org/db/conf/sc/index.html",     "start_year": 1980, "name": "SC"},
    "asplos": {"root": "https://dblp.org/db/conf/asplos/index.html", "start_year": 1980, "name": "ASPLOS"},
    "isca":   {"root": "https://dblp.org/db/conf/isca/index.html",   "start_year": 1980, "name": "ISCA"},
    "usenix": {"root": "https://dblp.org/db/conf/usenix/index.html", "start_year": 1980, "name": "ATC"},
    "eurosys":{"root": "https://dblp.org/db/conf/eurosys/index.html","start_year": 1980, "name": "EUROSYS"},
    "hpdc":   {"root": "https://dblp.org/db/conf/hpdc/index.html",   "start_year": 1980, "name": "HPDC"},
    "dac":    {"root": "https://dblp.org/db/conf/dac/index.html",    "start_year": 1980, "name": "DAC"},
    # --- 计算机网络 ---
    "sigcomm":{"root": "https://dblp.org/db/conf/sigcomm/index.html","start_year": 1980, "name": "SIGCOMM"},
    "infocom":{"root": "https://dblp.org/db/conf/infocom/index.html","start_year": 1980, "name": "INFOCOM"},
    "mobicom":{"root": "https://dblp.org/db/conf/mobicom/index.html","start_year": 1980, "name": "MOBICOM"},
    "nsdi":   {"root": "https://dblp.org/db/conf/nsdi/index.html",   "start_year": 1980, "name": "NSDI"},
    # --- 网络与信息安全 ---
    "ccs":    {"root": "https://dblp.org/db/conf/ccs/index.html",    "start_year": 1980, "name": "CCS"},
    "eurocrypt":{"root":"https://dblp.org/db/conf/eurocrypt/index.html","start_year":1980,"name":"EUROCRYPT"},
    "crypto": {"root": "https://dblp.org/db/conf/crypto/index.html", "start_year": 1980, "name": "CRYPTO"},
    "sp":     {"root": "https://dblp.org/db/conf/sp/index.html",     "start_year": 1980, "name": "SP"},
    "uss":    {"root": "https://dblp.org/db/conf/uss/index.html",    "start_year": 1980, "name": "USS"},
    "ndss":   {"root": "https://dblp.org/db/conf/ndss/index.html",   "start_year": 1980, "name": "NDSS"},
    # --- 软件工程/系统软件/程序设计语言 ---
    "pldi":   {"root": "https://dblp.org/db/conf/pldi/index.html",   "start_year": 1980, "name": "PLDI"},
    "popl":   {"root": "https://dblp.org/db/conf/popl/index.html",   "start_year": 1980, "name": "POPL"},
    "sigsoft":{"root": "https://dblp.org/db/conf/sigsoft/index.html","start_year": 1980, "name": "FSE"},
    "sosp":   {"root": "https://dblp.org/db/conf/sosp/index.html",   "start_year": 1980, "name": "SOSP"},
    "oopsla": {"root": "https://dblp.org/db/conf/oopsla/index.html", "start_year": 1980, "name": "OOPSLA"},
    "kbse":   {"root": "https://dblp.org/db/conf/kbse/index.html",   "start_year": 1980, "name": "ASE"},
    "icse":   {"root": "https://dblp.org/db/conf/icse/index.html",   "start_year": 1980, "name": "ICSE"},
    "issta":  {"root": "https://dblp.org/db/conf/issta/index.html",  "start_year": 1980, "name": "ISSTA"},
    "osdi":   {"root": "https://dblp.org/db/conf/osdi/index.html",   "start_year": 1980, "name": "OSDI"},
    "fm":     {"root": "https://dblp.org/db/conf/fm/index.html",     "start_year": 1980, "name": "FM"},
    # --- 计算机科学理论 ---
    "stoc":   {"root": "https://dblp.org/db/conf/stoc/index.html",   "start_year": 1980, "name": "STOC"},
    "soda":   {"root": "https://dblp.org/db/conf/soda/index.html",   "start_year": 1980, "name": "SODA"},
    "cav":    {"root": "https://dblp.org/db/conf/cav/index.html",    "start_year": 1980, "name": "CAV"},
    "focs":   {"root": "https://dblp.org/db/conf/focs/index.html",   "start_year": 1980, "name": "FOCS"},
    "lics":   {"root": "https://dblp.org/db/conf/lics/index.html",   "start_year": 1980, "name": "LICS"},
    # --- 人机交互与普适计算 ---
    "cscw":   {"root": "https://dblp.org/db/conf/cscw/index.html",   "start_year": 1980, "name": "CSCW"},
    "chi":    {"root": "https://dblp.org/db/conf/chi/index.html",    "start_year": 1980, "name": "CHI"},
    "huc":    {"root": "https://dblp.org/db/conf/huc/index.html",    "start_year": 1980, "name": "UBICOMP"},
    "uist":   {"root": "https://dblp.org/db/conf/uist/index.html",   "start_year": 1980, "name": "UIST"},
    # --- 交叉/综合/新兴 ---
    "rtss":   {"root": "https://dblp.org/db/conf/rtss/index.html",   "start_year": 1980, "name": "RTSS"},
    # --- 地理空间信息（GIS / 遥感）---
    # SIGSPATIAL 在 DBLP 的会议流 key 为 conf/gis（前身为 ACM GIS 研讨会系列）
    "sigspatial": {"root": "https://dblp.org/db/conf/gis/index.html", "start_year": 1980, "name": "SIGSPATIAL"},
    "igarss": {"root": "https://dblp.org/db/conf/igarss/index.html", "start_year": 1980, "name": "IGARSS"},
}

# ---------------------------------------------------------------------------
# 期刊配置（根目录页面 + 起始年份 + 配置名称前缀）
# ---------------------------------------------------------------------------
JOURNALS: Dict[str, Dict[str, Any]] = {
    # --- 人工智能 ---
    "ai":    {"root": "https://dblp.org/db/journals/ai/index.html",    "start_year": 1980, "name": "AI"},
    "pami":  {"root": "https://dblp.org/db/journals/pami/index.html",  "start_year": 1980, "name": "TPAMI"},
    "ijcv":  {"root": "https://dblp.org/db/journals/ijcv/index.html",  "start_year": 1980, "name": "IJCV"},
    "jmlr":  {"root": "https://dblp.org/db/journals/jmlr/index.html",  "start_year": 1980, "name": "JMLR"},
    # --- 计算机图形学与多媒体 ---
    "tog":   {"root": "https://dblp.org/db/journals/tog/index.html",   "start_year": 1980, "name": "TOG"},
    "tip":   {"root": "https://dblp.org/db/journals/tip/index.html",   "start_year": 1980, "name": "TIP"},
    "tvcg":  {"root": "https://dblp.org/db/journals/tvcg/index.html",  "start_year": 1980, "name": "TVCG"},
    "tmm":   {"root": "https://dblp.org/db/journals/tmm/index.html",   "start_year": 1980, "name": "TMM"},
    # --- 数据库/数据挖掘/内容检索 ---
    "tods":  {"root": "https://dblp.org/db/journals/tods/index.html",  "start_year": 1980, "name": "TODS"},
    "tois":  {"root": "https://dblp.org/db/journals/tois/index.html",  "start_year": 1980, "name": "TOIS"},
    "tkde":  {"root": "https://dblp.org/db/journals/tkde/index.html",  "start_year": 1980, "name": "TKDE"},
    "vldb":  {"root": "https://dblp.org/db/journals/vldb/index.html",  "start_year": 1980, "name": "VLDBJ"},
    "pvldb": {"root": "https://dblp.org/db/journals/pvldb/index.html", "start_year": 1980, "name": "VLDB"},
    # --- 计算机体系结构/高性能计算/存储系统 ---
    "tocs":  {"root": "https://dblp.org/db/journals/tocs/index.html",  "start_year": 1980, "name": "TOCS"},
    "tos":   {"root": "https://dblp.org/db/journals/tos/index.html",   "start_year": 1980, "name": "TOS"},
    "tcad":  {"root": "https://dblp.org/db/journals/tcad/index.html",  "start_year": 1980, "name": "TCAD"},
    "tc":    {"root": "https://dblp.org/db/journals/tc/index.html",    "start_year": 1980, "name": "TC"},
    "tpds":  {"root": "https://dblp.org/db/journals/tpds/index.html",  "start_year": 1980, "name": "TPDS"},
    "taco":  {"root": "https://dblp.org/db/journals/taco/index.html",  "start_year": 1980, "name": "TACO"},
    # --- 计算机网络 ---
    "jsac":  {"root": "https://dblp.org/db/journals/jsac/index.html",  "start_year": 1980, "name": "JSAC"},
    "tmc":   {"root": "https://dblp.org/db/journals/tmc/index.html",   "start_year": 1980, "name": "TMC"},
    "ton":   {"root": "https://dblp.org/db/journals/ton/index.html",   "start_year": 1980, "name": "TON"},
    # --- 网络与信息安全 ---
    "tdsc":  {"root": "https://dblp.org/db/journals/tdsc/index.html",  "start_year": 1980, "name": "TDSC"},
    "tifs":  {"root": "https://dblp.org/db/journals/tifs/index.html",  "start_year": 1980, "name": "TIFS"},
    "joc":   {"root": "https://dblp.org/db/journals/joc/index.html",   "start_year": 1980, "name": "JOC"},
    # --- 软件工程/系统软件/程序设计语言 ---
    "toplas":{"root": "https://dblp.org/db/journals/toplas/index.html","start_year": 1980, "name": "TOPLAS"},
    "tosem": {"root": "https://dblp.org/db/journals/tosem/index.html", "start_year": 1980, "name": "TOSEM"},
    "tse":   {"root": "https://dblp.org/db/journals/tse/index.html",   "start_year": 1980, "name": "TSE"},
    "tsc":   {"root": "https://dblp.org/db/journals/tsc/index.html",   "start_year": 1980, "name": "TSC"},
    # --- 计算机科学理论 ---
    "tit":   {"root": "https://dblp.org/db/journals/tit/index.html",   "start_year": 1980, "name": "TIT"},
    "iandc": {"root": "https://dblp.org/db/journals/iandc/index.html", "start_year": 1980, "name": "IANDC"},
    "siamcomp":{"root":"https://dblp.org/db/journals/siamcomp/index.html","start_year":1980,"name":"SICOMP"},
    # --- 人机交互与普适计算 ---
    "tochi": {"root": "https://dblp.org/db/journals/tochi/index.html", "start_year": 1980, "name": "TOCHI"},
    "ijhcs": {"root": "https://dblp.org/db/journals/ijhcs/index.html", "start_year": 1980, "name": "IJHCS"},
    "jacm":  {"root": "https://dblp.org/db/journals/jacm/index.html",  "start_year": 1980, "name": "JACM"},
    "pieee": {"root": "https://dblp.org/db/journals/pieee/index.html", "start_year": 1980, "name": "PROCIEEE"},
    "chinaf":{"root": "https://dblp.org/db/journals/chinaf/index.html","start_year": 1980, "name": "SCIS"},
    "bioinformatics":{"root":"https://dblp.org/db/journals/bioinformatics/index.html","start_year":1980,"name":"Bioinformatics"},
    # --- 已有期刊（保留历史名称映射）---
    "tnn":   {"root": "https://dblp.org/db/journals/tnn/index.html",   "start_year": 1980, "name": "TNNLS"},
    "taslp": {"root": "https://dblp.org/db/journals/taslp/index.html", "start_year": 1980, "name": "TASLP"},
    "ml":    {"root": "https://dblp.org/db/journals/ml/index.html",    "start_year": 1980, "name": "MLJ"},
    # --- 地理空间信息（GIS / 遥感）---
    # 注意 DBLP key 与常见缩写不一致：IJGIS=gis，CEUS=urban，JSTARS=staeors
    "gis":   {"root": "https://dblp.org/db/journals/gis/index.html",   "start_year": 1980, "name": "IJGIS"},
    "tgis":  {"root": "https://dblp.org/db/journals/tgis/index.html",  "start_year": 1980, "name": "TGIS"},
    "aeog":  {"root": "https://dblp.org/db/journals/aeog/index.html",  "start_year": 1980, "name": "IJAEO"},
    "urban": {"root": "https://dblp.org/db/journals/urban/index.html", "start_year": 1980, "name": "CEUS"},
    "geoinformatica": {"root": "https://dblp.org/db/journals/geoinformatica/index.html", "start_year": 1980, "name": "GeoInformatica"},
    "josis": {"root": "https://dblp.org/db/journals/josis/index.html", "start_year": 1980, "name": "JOSIS"},
    "staeors": {"root": "https://dblp.org/db/journals/staeors/index.html", "start_year": 1980, "name": "JSTARS"},
}

# ---------------------------------------------------------------------------
# 被其他信源覆盖、应在 DBLP 中跳过的会议/期刊前缀
# ---------------------------------------------------------------------------
_OTHER_SOURCE_PREFIXES: Set[str] = {
    # ACL 信源
    "ACL", "EMNLP", "NAACL", "EACL", "COLING",
    # CVF 信源
    "CVPR", "ICCV", "ECCV", "WACV",
    # OpenReview / NIPS 信源
    "ICLR", "NIPS", "MLSYS",
}


class DBLPDiscovery(BaseDiscovery):
    def __init__(
        self,
        name: str = "",
        existing_conf: List[Dict[str, Any]] = None,
        other_confs: List[Dict[str, Any]] = None,
    ):
        super().__init__(name, existing_conf)
        self._cache = _load_cache()
        self._cache_dirty = False
        self._skip_prefixes = set(_OTHER_SOURCE_PREFIXES)

        # 从其他信源的 existing_conf 中再提取已被覆盖的前缀
        if other_confs:
            for item in other_confs:
                n = item.get("name", "")
                m = re.match(r"^([A-Za-z]+)", n)
                if m:
                    self._skip_prefixes.add(m.group(1).upper())

    # -----------------------------------------------------------------------
    # 缓存 HEAD（保留给 fallback 场景）
    # -----------------------------------------------------------------------
    def _head_ok_cached(self, url: str, timeout: int = 10) -> bool:
        now = time.time()
        entry = self._cache.get(url)
        if entry:
            ts = entry.get("ts", 0)
            ttl = CACHE_TTL_EXIST if entry.get("exists") else CACHE_TTL_NOT_EXIST
            if now - ts < ttl:
                return entry["exists"]

        result = self._head_ok(url, timeout)
        self._cache[url] = {"exists": result, "ts": now}
        self._cache_dirty = True
        return result

    # -----------------------------------------------------------------------
    # 解析会议根目录
    # -----------------------------------------------------------------------
    def _discover_conf_from_root(
        self,
        meta: Dict[str, Any],
        start_year: int,
        end_year: int,
        existing_urls: Set[str],
    ) -> List[Dict[str, Any]]:
        results = []
        text = self._get_text(meta["root"], timeout=15, retries=2)
        if not text:
            return results

        from urllib.parse import urljoin

        # 同一年度可能对应多个页面（"-N" 多卷、SIGSPATIAL/WWW 的卫星研讨会等），
        # name 不是唯一键：仅以 URL 去重，否则 DBLP 后续为已记录年度新增的页面
        # 会因同名被跳过。seen_urls 额外覆盖本次运行内的新发现，避免页面内重复
        # 链接造成重复追加；去重按归一化键比较，兼容 conf 中的镜像域名条目。
        seen_urls = {_url_key(u) for u in existing_urls}

        soup = BeautifulSoup(text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(meta["root"], a["href"])
            if not _CONF_LINK_RE.match(href):
                continue

            m = _YEAR_RE.search(href)
            if not m:
                continue
            year = int(m.group(1))
            if year < start_year or year > end_year:
                continue

            key = _url_key(href)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            results.append({"name": f"{meta['name']}{year}", "url": href})

        return results

    # -----------------------------------------------------------------------
    # 解析期刊根目录
    # -----------------------------------------------------------------------
    def _discover_journal_from_root(
        self,
        meta: Dict[str, Any],
        start_year: int,
        end_year: int,
        existing_urls: Set[str],
    ) -> List[Dict[str, Any]]:
        results = []
        text = self._get_text(meta["root"], timeout=15, retries=2)
        if not text:
            return results

        from urllib.parse import urljoin

        # 期刊普遍按卷拆分页面，同一年度可能有多卷（如 IJAEO/CEUS/JOSIS）。
        # name 不是唯一键：仅以 URL 去重，否则 DBLP 后续为已记录年度新增的卷
        # 会因同名被跳过。seen_urls 额外覆盖本次运行内的新发现；去重按归一化键
        # 比较，兼容 conf 中的镜像域名条目。
        seen_urls = {_url_key(u) for u in existing_urls}

        soup = BeautifulSoup(text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(meta["root"], a["href"])
            if not _JOURNAL_LINK_RE.match(href):
                continue

            # DBLP 常把年份放在 <a> 之外（例如 li 文本 "Volume 44: 2022"）
            container_text = a.parent.get_text(" ", strip=True)
            m = _YEAR_RE.search(container_text)
            if not m:
                continue
            year = int(m.group(1))
            if year < start_year or year > end_year:
                continue

            key = _url_key(href)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            results.append({"name": f"{meta['name']}{year}", "url": href})

        return results

    # -----------------------------------------------------------------------
    # Fallback：旧式 HEAD 探测（用于根目录不存在的少数会议）
    # -----------------------------------------------------------------------
    def _discover_conf_fallback(
        self,
        abbrev: str,
        meta: Dict[str, Any],
        start_year: int,
        end_year: int,
        existing_urls: Set[str],
    ) -> List[Dict[str, Any]]:
        results = []
        path_override = {
            "iswc": "conf/semweb/iswc",
            "icme": "conf/icmcs/icme",
        }
        path = path_override.get(abbrev, f"conf/{abbrev}/{abbrev}")

        for year in range(start_year, end_year + 1):
            name = f"{meta['name']}{year}"
            if name in self.existing_names:
                continue

            base_url = f"https://dblp.org/db/{path}{year}.html"
            if abbrev == "miccai":
                # 多卷会议：连续 3 次失败停止
                consecutive_fail = 0
                for vol in range(1, 41):
                    url = f"https://dblp.org/db/{path}{year}-{vol}.html"
                    if url in existing_urls:
                        consecutive_fail = 0
                        continue
                    if self._head_ok_cached(url):
                        results.append({"name": name, "url": url})
                        consecutive_fail = 0
                    else:
                        consecutive_fail += 1
                        if consecutive_fail >= 3:
                            break
                    time.sleep(0.3)
                if not any(r["name"] == name for r in results):
                    if base_url not in existing_urls and self._head_ok_cached(base_url):
                        results.append({"name": name, "url": base_url})
                        time.sleep(0.3)
            else:
                if base_url not in existing_urls and self._head_ok_cached(base_url):
                    results.append({"name": name, "url": base_url})
                time.sleep(0.3)
        return results

    # -----------------------------------------------------------------------
    # 主发现逻辑
    # -----------------------------------------------------------------------
    def discover(self, start_year: int, end_year: int) -> List[Dict[str, Any]]:
        results = []
        existing_urls = {item.get("url") for item in self.existing}

        try:
            # ---------- 会议 ----------
            for abbrev, meta in CONFERENCES.items():
                if meta["name"] in self._skip_prefixes:
                    continue

                conf_start = meta.get("start_year", 2019)
                sy = max(start_year, conf_start)
                if sy > end_year:
                    continue

                discovered = self._discover_conf_from_root(
                    meta, sy, end_year, existing_urls
                )
                if not discovered and meta.get("fallback"):
                    discovered = self._discover_conf_fallback(
                        abbrev, meta, sy, end_year, existing_urls
                    )
                results.extend(discovered)
                time.sleep(0.5)

            # ---------- 期刊 ----------
            for abbrev, meta in JOURNALS.items():
                if meta["name"] in self._skip_prefixes:
                    continue

                journal_start = meta.get("start_year", 2019)
                sy = max(start_year, journal_start)
                if sy > end_year:
                    continue

                discovered = self._discover_journal_from_root(
                    meta, sy, end_year, existing_urls
                )
                results.extend(discovered)
                time.sleep(0.5)

        finally:
            if self._cache_dirty:
                _save_cache(self._cache)

        return results
