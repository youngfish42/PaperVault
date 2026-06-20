import argparse
import glob
import os
import sys
import json
import re
from datetime import datetime
from collections import defaultdict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt

from collector import collect, save_cache, load_cache
from data_artifacts import ensure_cache_local, sync_cache_artifacts

try:
    from wordcloud import WordCloud
except ImportError:
    WordCloud = None

COMMENT_CONFS_LIST_START = "<!-- confs-list-start -->"
COMMENT_CONFS_LIST_END = "<!-- confs-list-end -->"
COMMENT_STATS_START = "<!-- stats-start -->"
COMMENT_STATS_END = "<!-- stats-end -->"
COMMENT_RECENT_UPDATE_START = "<!-- recent-update-start -->"
COMMENT_RECENT_UPDATE_END = "<!-- recent-update-end -->"

cache_path = os.path.join(os.path.dirname(__file__), "cache", "cache.jsonl.gz")
readme_path = "README.md"
readme_en_path = "README.en.md"
acl_conf_path = os.path.join(os.path.dirname(__file__), "conf", "acl_conf.json")
dblp_conf_path = os.path.join(os.path.dirname(__file__), "conf", "dblp_conf.json")
nips_conf_path = os.path.join(os.path.dirname(__file__), "conf", "nips_conf.json")
iclr_conf_path = os.path.join(os.path.dirname(__file__), "conf", "iclr_conf.json")
thecvf_conf_path = os.path.join(os.path.dirname(__file__), "conf", "thecvf_conf.json")
stats_dir = os.path.join(os.path.dirname(__file__), "pics", "stats")
stats_html_path = os.path.join(os.path.dirname(__file__), "docs", "stats.html")
meta_path = os.path.join(os.path.dirname(__file__), "cache", "readme_meta.json")

# Nature-inspired color palette (muted, professional)
NATURE_COLORS = [
    "#2E5C8A", "#7BA05B", "#C44E52", "#DD8452",
    "#9370DB", "#55A3B9", "#8C8C8C", "#E3A018",
    "#4C4C4C", "#A0C4E8", "#C49C94",
]

# 按 CCF 第七版推荐目录分类（A 类为核心，同时包含项目已收录的其他会议/期刊）
CATEGORY_MAP = {
    "计算机体系结构/高性能计算/存储系统": [
        "TOCS", "TOS", "TCAD", "TC", "TPDS", "TACO",
        "PPOPP", "FAST", "DAC", "HPCA", "MICRO", "SC", "ASPLOS", "ISCA", "ATC", "EUROSYS", "HPDC",
    ],
    "计算机网络": [
        "JSAC", "TMC", "TON",
        "SIGCOMM", "MOBICOM", "INFOCOM", "NSDI",
    ],
    "网络与信息安全": [
        "TDSC", "TIFS", "JOC",
        "CCS", "EUROCRYPT", "SP", "CRYPTO", "USS", "NDSS",
    ],
    "软件工程/系统软件/程序设计语言": [
        "TOPLAS", "TOSEM", "TSE", "TSC",
        "PLDI", "POPL", "FSE", "SOSP", "OOPSLA", "ASE", "ICSE", "ISSTA", "OSDI", "FM",
    ],
    "数据库/数据挖掘/内容检索": [
        "TODS", "TOIS", "TKDE", "VLDBJ",
        "SIGMOD", "KDD", "ICDE", "SIGIR", "VLDB", "CIKM", "WSDM", "WWW", "ECIR", "ICDM", "RECSYS",
    ],
    "计算机科学理论": [
        "TIT", "IANDC", "SICOMP",
        "STOC", "SODA", "CAV", "FOCS", "LICS", "COLT", "ALT",
    ],
    "计算机图形学与多媒体": [
        "TOG", "TIP", "TVCG", "TMM",
        "MM", "SIGGRAPH", "VR", "IEEEVIS", "BMVC", "MICCAI", "ICME",
    ],
    "人工智能": [
        "AI", "TPAMI", "IJCV", "JMLR",
        "AAAI", "NIPS", "ACL", "CVPR", "ICCV", "ICML", "ICLR", "AISTATS", "UAI", "TNNLS", "MLJ", "IJCAI",
        "COLING", "EACL", "EMNLP", "NAACL", "ECCV", "WACV", "MLSYS",
    ],
    "人机交互与普适计算": [
        "TOCHI", "IJHCS",
        "CSCW", "CHI", "UBICOMP", "UIST",
    ],
    "语音": [
        "ICASSP", "INTERSPEECH", "TASLP",
    ],
    "交叉/综合/新兴": [
        "JACM", "PROCIEEE", "SCIS", "BIOINFORMATICS",
        "RTSS", "ISWC",
    ],
}

CATEGORY_MAP_EN = {
    "Computer Architecture / HPC / Storage": [
        "TOCS", "TOS", "TCAD", "TC", "TPDS", "TACO",
        "PPOPP", "FAST", "DAC", "HPCA", "MICRO", "SC", "ASPLOS", "ISCA", "ATC", "EUROSYS", "HPDC",
    ],
    "Computer Networks": [
        "JSAC", "TMC", "TON",
        "SIGCOMM", "MOBICOM", "INFOCOM", "NSDI",
    ],
    "Network & Information Security": [
        "TDSC", "TIFS", "JOC",
        "CCS", "EUROCRYPT", "SP", "CRYPTO", "USS", "NDSS",
    ],
    "Software Engineering / Systems / PL": [
        "TOPLAS", "TOSEM", "TSE", "TSC",
        "PLDI", "POPL", "FSE", "SOSP", "OOPSLA", "ASE", "ICSE", "ISSTA", "OSDI", "FM",
    ],
    "Database / Data Mining / IR": [
        "TODS", "TOIS", "TKDE", "VLDBJ",
        "SIGMOD", "KDD", "ICDE", "SIGIR", "VLDB", "CIKM", "WSDM", "WWW", "ECIR", "ICDM", "RECSYS",
    ],
    "Theoretical Computer Science": [
        "TIT", "IANDC", "SICOMP",
        "STOC", "SODA", "CAV", "FOCS", "LICS", "COLT", "ALT",
    ],
    "Computer Graphics & Multimedia": [
        "TOG", "TIP", "TVCG", "TMM",
        "MM", "SIGGRAPH", "VR", "IEEEVIS", "BMVC", "MICCAI", "ICME",
    ],
    "Artificial Intelligence": [
        "AI", "TPAMI", "IJCV", "JMLR",
        "AAAI", "NIPS", "ACL", "CVPR", "ICCV", "ICML", "ICLR", "AISTATS", "UAI", "TNNLS", "MLJ", "IJCAI",
        "COLING", "EACL", "EMNLP", "NAACL", "ECCV", "WACV", "MLSYS",
    ],
    "Human-Computer Interaction & Ubicomp": [
        "TOCHI", "IJHCS",
        "CSCW", "CHI", "UBICOMP", "UIST",
    ],
    "Speech": [
        "ICASSP", "INTERSPEECH", "TASLP",
    ],
    "Interdisciplinary / Comprehensive / Emerging": [
        "JACM", "PROCIEEE", "SCIS", "BIOINFORMATICS",
        "RTSS", "ISWC",
    ],
}

# Map each publication series to its category color for consistent theming across charts
SERIES_COLOR_MAP = {}
for idx, names in enumerate(CATEGORY_MAP.values()):
    color = NATURE_COLORS[idx % len(NATURE_COLORS)]
    for name in names:
        SERIES_COLOR_MAP[name] = color


HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PaperVault Statistics</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/echarts-wordcloud@2.1.0/dist/echarts-wordcloud.min.js"></script>
<style>
body { font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif; margin:0; padding:20px; background:#fafafa; color:#333; }
.container { max-width:1000px; margin:0 auto; }
h1 { text-align:center; margin-bottom:6px; font-weight:700; }
.subtitle { text-align:center; color:#888; margin-bottom:36px; font-size:14px; }
.card { background:#fff; border-radius:8px; padding:20px; margin-bottom:24px; box-shadow:0 2px 8px rgba(0,0,0,0.06); }
.chart { width:100%; height:420px; }
.footer { text-align:center; color:#aaa; font-size:12px; margin-top:20px; }
</style>
</head>
<body>
<div class="container">
  <h1>PaperVault 数据统计</h1>
  <div class="subtitle">Data Statistics &middot; {{total_papers:,}} papers / {{total_series}} series / {{total_abstracts:,}} with abstracts / {{total_code:,}} with code</div>

  <div class="card">
    <div id="chart-category" class="chart"></div>
  </div>

  <div class="card">
    <div id="chart-year" class="chart"></div>
  </div>

  <div class="card">
    <div id="chart-wordcloud" class="chart"></div>
  </div>

  <div class="footer">Generated by PaperVault maintain.py</div>
</div>

<script>
const catData = {{cat_data}};
const yearData = {{year_data}};
const wordcloudData = {{wordcloud_data}};

// ---------- Papers by Category ----------
const chartCat = echarts.init(document.getElementById('chart-category'));
chartCat.setOption({
  title: { text: '各研究领域论文分布 (Papers by Research Field)', left: 'center', textStyle: { fontSize: 16, fontWeight: 'bold' } },
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
  grid: { left: '3%', right: '8%', bottom: '3%', top: '12%', containLabel: true },
  xAxis: { type: 'value', name: '论文数量 (Paper Count)', nameTextStyle: { fontWeight: 'bold' } },
  yAxis: { type: 'category', data: catData.labels, axisLabel: { fontWeight: 'bold' }, inverse: true },
  series: [{
    type: 'bar',
    data: catData.values.map((v, i) => ({ value: v, itemStyle: { color: catData.colors[i] } })),
    barWidth: '55%',
    label: { show: true, position: 'right', fontWeight: 'bold', color: '#1a1a1a' }
  }]
});

// ---------- Papers by Year ----------
const chartYear = echarts.init(document.getElementById('chart-year'));
chartYear.setOption({
  title: { text: '历年论文收录趋势 (Annual Paper Collection Trend)', left: 'center', textStyle: { fontSize: 16, fontWeight: 'bold' } },
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
  legend: { data: yearData.categories, bottom: 0, type: 'scroll', textStyle: { fontSize: 11 } },
  grid: { left: '3%', right: '4%', bottom: '12%', top: '12%', containLabel: true },
  xAxis: { type: 'category', data: yearData.years, name: '年份 (Year)', nameTextStyle: { fontWeight: 'bold' }, axisLabel: { interval: 1, rotate: 30 } },
  yAxis: { type: 'value', name: '论文数量 (Paper Count)', nameTextStyle: { fontWeight: 'bold' } },
  series: yearData.series.map((s, i) => ({
    name: s.name,
    type: 'bar',
    stack: 'total',
    data: s.data,
    itemStyle: { color: yearData.colors[i] },
    barWidth: '55%'
  }))
});

// ---------- Word Cloud ----------
const chartWord = echarts.init(document.getElementById('chart-wordcloud'));
chartWord.setOption({
  title: { text: 'Publication Series 词云 (Word Cloud)', left: 'center', textStyle: { fontSize: 16, fontWeight: 'bold' } },
  tooltip: { show: true },
  series: [{
    type: 'wordCloud',
    shape: 'circle',
    left: 'center',
    top: 'center',
    width: '95%',
    height: '95%',
    sizeRange: [14, 90],
    rotationRange: [-10, 10],
    rotationStep: 15,
    gridSize: 12,
    drawOutOfBound: false,
    layoutAnimation: true,
    textStyle: { fontFamily: 'sans-serif', fontWeight: 'bold' },
    emphasis: { focus: 'self', textStyle: { textShadowBlur: 5, textShadowColor: '#333' } },
    data: wordcloudData
  }]
});

window.addEventListener('resize', () => { chartCat.resize(); chartYear.resize(); chartWord.resize(); });
</script>
</body>
</html>
'''


def generate_new_readme(src: str, content: str, start_comment: str, end_comment: str) -> str:
    """Generate a new Readme.md by replacing content between markers."""
    pattern = f"{start_comment}[\\s\\S]+{end_comment}"
    repl = f"{start_comment}\n\n{content}\n\n{end_comment}"
    if re.search(pattern, src) is None:
        print(f"can not find section in src, please check it, it should be {start_comment} and {end_comment}")
        return src
    return re.sub(pattern, repl, src)


def _load_all_confs():
    """Load all conference configs and return a dict of {upper_name: set(years)}."""
    confs_list = {}
    for files in [acl_conf_path, dblp_conf_path, nips_conf_path, iclr_conf_path, thecvf_conf_path]:
        with open(files, "r", encoding="utf-8") as f:
            for conf in json.load(f):
                m = re.search(r"\d{4}", conf["name"])
                if not m:
                    continue
                year = m.group()
                conf_name = conf["name"][: m.start()].strip().upper()
                confs_list.setdefault(conf_name, set()).add(year)
    return confs_list


def compute_stats(cache_data: dict):
    """Compute statistics from cache data."""
    total_papers = sum(len(papers) for papers in cache_data.values())
    total_abstracts = 0
    total_code = 0
    papers_by_year = defaultdict(int)
    papers_by_conf = defaultdict(int)
    papers_by_year_cat = defaultdict(lambda: defaultdict(int))
    for conf_key, papers in cache_data.items():
        m = re.match(r"([A-Za-z]+)(\d{4})", conf_key)
        conf_base = m.group(1).upper() if m else conf_key
        year = m.group(2) if m else "Unknown"
        papers_by_conf[conf_base] += len(papers)
        papers_by_year[year] += len(papers)
        for p in papers:
            if p.get("paper_abstract") and str(p.get("paper_abstract")).strip():
                total_abstracts += 1
            code_link = str(p.get("paper_code") or "").strip()
            if code_link and code_link != "#":
                total_code += 1
        # Categorize by year for stacked chart
        for cat, names in CATEGORY_MAP.items():
            if conf_base in names:
                papers_by_year_cat[year][cat] += len(papers)
                break

    confs_list = _load_all_confs()
    total_series = len(confs_list)
    total_instances = sum(len(years) for years in confs_list.values())

    # Category stats
    cat_stats = {}
    for cat, names in CATEGORY_MAP.items():
        cnt = sum(papers_by_conf.get(n, 0) for n in names)
        cat_stats[cat] = cnt

    return {
        "total_papers": total_papers,
        "total_abstracts": total_abstracts,
        "total_code": total_code,
        "total_series": total_series,
        "total_instances": total_instances,
        "papers_by_year": dict(sorted(papers_by_year.items())),
        "papers_by_year_cat": {y: dict(papers_by_year_cat[y]) for y in sorted(papers_by_year_cat.keys())},
        "papers_by_conf": dict(papers_by_conf),
        "cat_stats": cat_stats,
    }


def generate_stats_html(stats: dict):
    """Generate an interactive ECharts HTML page for statistics."""
    os.makedirs(os.path.dirname(stats_html_path), exist_ok=True)

    # Prepare category data
    cat_labels = [f"{zh} ({en})" for zh, en in zip(CATEGORY_MAP.keys(), CATEGORY_MAP_EN.keys())]
    cat_values = [stats["cat_stats"][cat] for cat in CATEGORY_MAP.keys()]
    cat_colors = [NATURE_COLORS[i % len(NATURE_COLORS)] for i in range(len(CATEGORY_MAP))]

    # Prepare year data
    years = sorted(stats["papers_by_year_cat"].keys())
    cat_names = list(CATEGORY_MAP.keys())
    cat_labels = [f"{zh} ({en})" for zh, en in zip(CATEGORY_MAP.keys(), CATEGORY_MAP_EN.keys())]
    year_series = []
    for i, cat in enumerate(cat_names):
        year_series.append({
            "name": cat_labels[i],
            "data": [stats["papers_by_year_cat"].get(y, {}).get(cat, 0) for y in years]
        })
    year_colors = [NATURE_COLORS[i % len(NATURE_COLORS)] for i in range(len(cat_names))]

    # Prepare wordcloud data with per-series colors
    wordcloud_data = []
    for name, count in sorted(stats["papers_by_conf"].items(), key=lambda x: -x[1]):
        wordcloud_data.append({
            "name": name,
            "value": count,
            "textStyle": {"color": SERIES_COLOR_MAP.get(name, "#8C8C8C")},
        })

    # Simple template substitution (avoid full Jinja2 dependency)
    html = HTML_TEMPLATE
    html = html.replace("{{total_papers:,}}", f"{stats['total_papers']:,}")
    html = html.replace("{{total_series}}", f"{stats['total_series']}")
    html = html.replace("{{total_abstracts:,}}", f"{stats['total_abstracts']:,}")
    html = html.replace("{{total_code:,}}", f"{stats['total_code']:,}")
    html = html.replace("{{cat_data}}", json.dumps({
        "labels": cat_labels,
        "values": cat_values,
        "colors": cat_colors,
    }, ensure_ascii=False))
    html = html.replace("{{year_data}}", json.dumps({
        "years": years,
        "series": year_series,
        "categories": cat_names,
        "colors": year_colors,
    }, ensure_ascii=False))
    html = html.replace("{{wordcloud_data}}", json.dumps(wordcloud_data, ensure_ascii=False))

    with open(stats_html_path, "w", encoding="utf-8") as f:
        f.write(html)


def _find_cjk_font():
    """Find an available CJK font file on the system.

    Returns:
        tuple[str, str] | tuple[None, None]: (font_family, font_path) if found,
        else (None, None).
    """
    candidates = [
        ("Noto Sans CJK SC", ["NotoSansCJK", "NotoSansCJKsc", "NotoSansSC"]),
        ("Noto Sans SC", ["NotoSansSC", "NotoSansCJK"]),
        ("WenQuanYi Micro Hei", ["wqy-microhei", "wqy-zenhei"]),
        ("WenQuanYi Zen Hei", ["wqy-zenhei", "wqy-microhei"]),
        ("SimHei", ["simhei"]),
        ("Microsoft YaHei", ["msyh"]),
    ]

    # Helper to check whether a file path looks like a CJK font we expect
    def _matches_candidate(filename: str) -> tuple[str, str] | None:
        lower_name = filename.lower()
        for family, keywords in candidates:
            if any(kw.lower() in lower_name for kw in keywords):
                return family, filename
        return None

    # ------------------------------------------------------------------
    # 1) Try fc-list first (most reliable on Linux CI environments)
    # ------------------------------------------------------------------
    try:
        import subprocess

        result = subprocess.run(
            ["fc-list", ":lang=zh", "-f", "%{family}\t%{file}\n"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = line.split("\t", 1)
                if len(parts) != 2:
                    continue
                family_part, filepath = parts
                filepath = filepath.strip()
                if not os.path.exists(filepath):
                    continue
                # family_part may contain multiple families separated by comma
                family = family_part.split(",")[0].strip()
                match = _matches_candidate(os.path.basename(filepath))
                if match:
                    # Use the actual family name reported by fc-list instead of
                    # the hard-coded candidate name. A .ttc file may contain
                    # multiple sub-fonts (e.g. Mono vs Sans) and the hard-coded
                    # name may not match what fontconfig reports.
                    return family, filepath
                # If filename doesn't match but family does, still trust fc-list
                for expected_family, _ in candidates:
                    if expected_family.lower() in family.lower():
                        return expected_family, filepath
    except Exception:
        pass

    # ------------------------------------------------------------------
    # 2) Try matplotlib font manager (with filename verification)
    # ------------------------------------------------------------------
    for family, _ in candidates:
        try:
            path = fm.findfont(
                fm.FontProperties(family=family), fallback_to_default=False
            )
            if path and os.path.exists(path):
                # Critical: ensure the returned file is actually a CJK font,
                # NOT a fallback like DejaVuSans.ttf which matplotlib may
                # return despite fallback_to_default=False in some versions.
                if _matches_candidate(os.path.basename(path)):
                    return family, path
        except Exception:
            continue

    # ------------------------------------------------------------------
    # 3) Search common system paths
    # ------------------------------------------------------------------
    common_paths = [
        "/usr/share/fonts/opentype/noto",
        "/usr/share/fonts/truetype/noto",
        "/usr/share/fonts/noto-cjk",
        "/usr/share/fonts/truetype/noto-cjk",
        "/usr/share/fonts/opentype/noto-cjk",
        "/usr/share/fonts/truetype/wqy",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.fonts"),
        os.path.expanduser("~/Library/Fonts"),  # macOS
        "/System/Library/Fonts",  # macOS
        "C:/Windows/Fonts",  # Windows
    ]

    for family, keywords in candidates:
        for directory in common_paths:
            if not os.path.isdir(directory):
                continue
            for root, _, files in os.walk(directory):
                for f in files:
                    lower_f = f.lower()
                    if any(kw.lower() in lower_f for kw in keywords):
                        return family, os.path.join(root, f)
                # Limit depth to 2 levels
                depth = root[len(directory):].count(os.sep)
                if depth >= 2:
                    break
    return None, None


def _ensure_chinese_font():
    """Configure matplotlib to support Chinese characters.

    Clears the matplotlib font cache so that fonts installed after the cache
    was first built (e.g. in CI) are discovered.

    Returns:
        str | None: Path to the CJK font file if found, else None.
    """
    # Clear matplotlib font cache so newly installed fonts are discovered
    # Use matplotlib.get_cachedir() for portability across platforms
    try:
        import matplotlib

        cache_dir = matplotlib.get_cachedir()
    except Exception:
        cache_dir = os.path.expanduser("~/.cache/matplotlib")

    if cache_dir and os.path.isdir(cache_dir):
        for cache_file in glob.glob(os.path.join(cache_dir, "fontlist-*.json")):
            try:
                os.remove(cache_file)
            except OSError:
                pass

    # Force matplotlib to rebuild its font manager so it picks up any
    # system fonts installed after the environment was created (common in CI).
    try:
        fm.fontManager = fm.FontManager()
    except Exception:
        pass

    # Set a comprehensive CJK font stack.  When fonts-noto-cjk-extra is
    # installed, "Noto Sans CJK SC" (Regular weight) will be available and
    # picked up automatically by matplotlib's findfont.  We list several
    # fallbacks so that even if the exact family name differs across OS
    # versions, a usable CJK font is found.
    plt.rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "Noto Sans CJK KR",
        "Noto Sans CJK JP",
        "Noto Sans Mono CJK SC",
        "Noto Serif CJK SC",
        "WenQuanYi Micro Hei",
        "SimHei",
        "Microsoft YaHei",
        "DejaVu Sans",
        "sans-serif",
    ]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["svg.fonttype"] = "path"

    # Try to locate a concrete font file for wordcloud (which needs a path).
    family, font_path = _find_cjk_font()
    if family and font_path:
        try:
            fm.fontManager.addfont(font_path)
        except Exception as exc:
            print(f"[!] Warning: addfont failed for {font_path}: {exc}")
        # Debug: verify matplotlib can resolve a CJK family
        try:
            resolved = fm.findfont(fm.FontProperties(family="Noto Sans CJK SC"))
            print(f"[*] Matplotlib resolved 'Noto Sans CJK SC' -> {resolved}")
        except Exception as exc:
            print(f"[!] Could not resolve 'Noto Sans CJK SC': {exc}")
        return font_path

    return None


def generate_charts_svg(stats: dict):
    """Generate Nature-style statistical charts as SVG files."""
    cjk_font_path = _ensure_chinese_font()
    has_cjk = cjk_font_path is not None
    os.makedirs(stats_dir, exist_ok=True)

    if has_cjk:
        cat_labels = [f"{zh}\n({en})" for zh, en in zip(CATEGORY_MAP.keys(), CATEGORY_MAP_EN.keys())]
        title_cat = "各研究领域论文分布 (Papers by Research Field)"
        xlabel_cat = "论文数量 (Paper Count)"
        title_year = "历年论文收录趋势 (Annual Paper Collection Trend)"
        xlabel_year = "年份 (Year)"
        ylabel_year = "论文数量 (Paper Count)"
        metrics = [
            ("收录刊物系列\nPublication Series", f"{stats['total_series']}", NATURE_COLORS[0]),
            ("会议/年份实例\nConf / Year Instances", f"{stats['total_instances']}", NATURE_COLORS[1]),
            ("总论文数量\nTotal Papers", f"{stats['total_papers']:,}", NATURE_COLORS[2]),
            ("含摘要论文\nPapers w/ Abstract", f"{stats['total_abstracts']:,}", NATURE_COLORS[5]),
            ("含开源代码论文\nPapers w/ Code", f"{stats['total_code']:,}", NATURE_COLORS[3]),
        ]
    else:
        cat_labels = list(CATEGORY_MAP_EN.keys())
        title_cat = "Papers by Research Field"
        xlabel_cat = "Paper Count"
        title_year = "Annual Paper Collection Trend"
        xlabel_year = "Year"
        ylabel_year = "Paper Count"
        metrics = [
            ("Publication Series", f"{stats['total_series']}", NATURE_COLORS[0]),
            ("Conf / Year Instances", f"{stats['total_instances']}", NATURE_COLORS[1]),
            ("Total Papers", f"{stats['total_papers']:,}", NATURE_COLORS[2]),
            ("Papers w/ Abstract", f"{stats['total_abstracts']:,}", NATURE_COLORS[5]),
            ("Papers w/ Code", f"{stats['total_code']:,}", NATURE_COLORS[3]),
        ]

    # ---------- Chart 1: Papers by Category (horizontal bar) ----------
    fig, ax = plt.subplots(figsize=(11, 6))
    cats = list(stats["cat_stats"].keys())
    vals = list(stats["cat_stats"].values())
    colors = [NATURE_COLORS[i % len(NATURE_COLORS)] for i in range(len(cats))]

    bars = ax.barh(cat_labels, vals, color=colors, height=0.55, edgecolor="white", linewidth=0.5)
    ax.set_xlabel(xlabel_cat, fontsize=13, fontweight="bold")
    ax.set_title(title_cat, fontsize=15, fontweight="bold", pad=18)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_linewidth(0.8)
    ax.spines["bottom"].set_linewidth(0.8)
    ax.tick_params(axis="both", labelsize=10, labelcolor="#1a1a1a")
    ax.invert_yaxis()

    for bar in bars:
        width = bar.get_width()
        ax.text(width + max(vals) * 0.01, bar.get_y() + bar.get_height() / 2,
                f"{int(width):,}", ha="left", va="center", fontsize=10, fontweight="bold", color="#1a1a1a")

    ax.set_xlim(0, max(vals) * 1.15)
    ax.grid(axis="x", linestyle="--", linewidth=0.4, alpha=0.5)
    fig.tight_layout()
    fig.savefig(os.path.join(stats_dir, "papers_by_category.svg"), format="svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)

    # ---------- Chart 2: Papers by Year (stacked vertical bar) ----------
    fig, ax = plt.subplots(figsize=(11, 6.2))
    years = sorted(stats["papers_by_year_cat"].keys())
    bottom = np.zeros(len(years))

    cat_keys = list(CATEGORY_MAP.keys() if has_cjk else CATEGORY_MAP_EN.keys())
    cat_labels = [f"{zh}\n({en})" for zh, en in zip(CATEGORY_MAP.keys(), CATEGORY_MAP_EN.keys())] if has_cjk else list(CATEGORY_MAP_EN.keys())
    for i, cat in enumerate(cat_keys):
        vals = [stats["papers_by_year_cat"].get(y, {}).get(cat, 0) for y in years]
        ax.bar(
            years, vals, bottom=bottom,
            color=NATURE_COLORS[i % len(NATURE_COLORS)],
            width=0.65, edgecolor="white", linewidth=0.5,
            label=cat_labels[i],
        )
        bottom += vals

    ax.set_xlabel(xlabel_year, fontsize=13, fontweight="bold")
    ax.set_ylabel(ylabel_year, fontsize=13, fontweight="bold")
    ax.set_title(title_year, fontsize=15, fontweight="bold", pad=18)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_linewidth(0.8)
    ax.spines["bottom"].set_linewidth(0.8)
    ax.tick_params(axis="both", labelsize=10, labelcolor="#1a1a1a")
    # Thin out x-axis labels to avoid crowding: show every other year + rotate
    show_indices = list(range(0, len(years), 2))
    ax.set_xticks(show_indices)
    ax.set_xticklabels([years[i] for i in show_indices], rotation=45, ha="right")
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), frameon=False, fontsize=9)
    ax.grid(axis="y", linestyle="--", linewidth=0.4, alpha=0.5)
    fig.tight_layout()
    fig.savefig(os.path.join(stats_dir, "papers_by_year.svg"), format="svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)

    # ---------- Chart 3: Overview Infographic (big numbers) ----------
    generate_overview_svg(stats, metrics=metrics)


def generate_overview_svg(stats: dict, metrics=None):
    """Generate only the stats_overview.svg infographic.

    Allows callers (e.g. CI workflows) to refresh just the overview chart
    without rewriting sibling SVGs produced by `generate_charts_svg`.
    """
    cjk_font_path = _ensure_chinese_font()
    has_cjk = cjk_font_path is not None
    os.makedirs(stats_dir, exist_ok=True)

    if metrics is None:
        if has_cjk:
            metrics = [
                ("收录刊物系列\nPublication Series", f"{stats['total_series']}", NATURE_COLORS[0]),
                ("会议/年份实例\nConf / Year Instances", f"{stats['total_instances']}", NATURE_COLORS[1]),
                ("总论文数量\nTotal Papers", f"{stats['total_papers']:,}", NATURE_COLORS[2]),
                ("含摘要论文\nPapers w/ Abstract", f"{stats['total_abstracts']:,}", NATURE_COLORS[5]),
                ("含开源代码论文\nPapers w/ Code", f"{stats['total_code']:,}", NATURE_COLORS[3]),
            ]
        else:
            metrics = [
                ("Publication Series", f"{stats['total_series']}", NATURE_COLORS[0]),
                ("Conf / Year Instances", f"{stats['total_instances']}", NATURE_COLORS[1]),
                ("Total Papers", f"{stats['total_papers']:,}", NATURE_COLORS[2]),
                ("Papers w/ Abstract", f"{stats['total_abstracts']:,}", NATURE_COLORS[5]),
                ("Papers w/ Code", f"{stats['total_code']:,}", NATURE_COLORS[3]),
            ]

    n = len(metrics)
    col_width = 2.5
    total_width = n * col_width
    fig, ax = plt.subplots(figsize=(max(10, total_width), 2.4))
    ax.set_xlim(0, total_width)
    ax.set_ylim(0, 2.4)
    ax.axis("off")

    x_positions = [col_width / 2 + i * col_width for i in range(n)]
    for (label, value, color), x in zip(metrics, x_positions):
        ax.text(x, 1.55, value, fontsize=32, fontweight="black", ha="center", va="center", color=color)
        ax.text(x, 0.55, label, fontsize=12, ha="center", va="center", color="#444444", linespacing=1.4)
        if x < x_positions[-1]:
            ax.plot([x + col_width / 2, x + col_width / 2], [0.25, 1.85], color="#DDDDDD", linewidth=0.8)

    fig.tight_layout()
    fig.savefig(os.path.join(stats_dir, "stats_overview.svg"), format="svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def generate_wordcloud_svg(stats: dict):
    """Generate a horizontal wordcloud of publication series weighted by paper count."""
    if WordCloud is None:
        print("[!] wordcloud package not installed, skipping wordcloud generation.")
        return

    cjk_font_path = _ensure_chinese_font()
    os.makedirs(stats_dir, exist_ok=True)

    frequencies = stats.get("papers_by_conf", {})
    if not frequencies:
        return

    wc_kwargs = {}
    if cjk_font_path:
        wc_kwargs["font_path"] = cjk_font_path

    wc = WordCloud(
        width=2400,
        height=900,
        background_color="white",
        max_words=100,
        relative_scaling=0.6,
        prefer_horizontal=0.92,
        min_font_size=12,
        max_font_size=260,
        color_func=lambda word, *args, **kwargs: SERIES_COLOR_MAP.get(word, "#8C8C8C"),
        random_state=42,
        **wc_kwargs,
    ).generate_from_frequencies(frequencies)

    fig, ax = plt.subplots(figsize=(12, 4.5))
    ax.imshow(wc, interpolation="bilinear")
    ax.axis("off")
    fig.tight_layout(pad=0)
    fig.savefig(os.path.join(stats_dir, "wordcloud.svg"), format="svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _build_hierarchical_confs_list(category_map, lang: str = "zh"):
    """Build a hierarchical markdown list of conferences grouped by category."""
    confs = _load_all_confs()
    assigned = set()
    lines = []

    series_label = "个系列" if lang == "zh" else "series"
    edition_label = "届" if lang == "zh" else "editions"
    others_label = "其他" if lang == "zh" else "Others"

    for cat, names in category_map.items():
        cat_confs = []
        for n in names:
            if n in confs:
                years = sorted(confs[n])
                cat_confs.append((n, years))
                assigned.add(n)
        if not cat_confs:
            continue
        cat_confs.sort(key=lambda x: x[0])
        lines.append(f"<details>\n<summary><b>{cat}</b> ({len(cat_confs)} {series_label})</summary>\n\n")
        for name, years in cat_confs:
            line = f"- **{name}** {min(years)}-{max(years)}"
            if len(years) > 1:
                line += f" ({len(years)} {edition_label})"
            lines.append(line + "\n")
        lines.append("\n</details>\n")

    # Any unassigned conferences
    unassigned = sorted(set(confs.keys()) - assigned)
    if unassigned:
        lines.append(f"<details>\n<summary><b>{others_label}</b> ({len(unassigned)} {series_label})</summary>\n\n")
        for name in unassigned:
            years = sorted(confs[name])
            line = f"- **{name}** {min(years)}-{max(years)}"
            if len(years) > 1:
                line += f" ({len(years)} {edition_label})"
            lines.append(line + "\n")
        lines.append("\n</details>\n")

    return "".join(lines)


def build_hierarchical_confs_list():
    return _build_hierarchical_confs_list(CATEGORY_MAP, lang="zh")


def build_hierarchical_confs_list_en():
    return _build_hierarchical_confs_list(CATEGORY_MAP_EN, lang="en")


def _read_meta():
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _write_meta(meta):
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def build_recent_update_brief(meta: dict, stats: dict):
    """Build the recent update brief markdown."""
    last_date = meta.get("last_update", datetime.now().strftime("%Y-%m-%d"))
    new_papers = meta.get("new_papers", 0)
    new_confs = meta.get("new_conferences", 0)

    lines = [
        f"- 📅 **最近更新日期**: {last_date}",
        f"- 🆕 **本次新增论文**: {new_papers:,} 篇",
    ]
    if new_confs:
        lines.append(f"- 📢 **本次新增会议**: {new_confs} 个")
    lines.append(f"- 📊 **数据库规模**: {stats['total_papers']:,} 篇论文 / {stats['total_series']} 个刊物系列 / {stats['total_abstracts']:,} 篇含摘要 / {stats['total_code']:,} 篇含开源代码")

    return "\n".join(lines)


def build_recent_update_brief_en(meta: dict, stats: dict):
    """Build the English recent update brief markdown."""
    last_date = meta.get("last_update", datetime.now().strftime("%Y-%m-%d"))
    new_papers = meta.get("new_papers", 0)
    new_confs = meta.get("new_conferences", 0)

    lines = [
        f"- 📅 **Last Updated**: {last_date}",
        f"- 🆕 **New Papers This Update**: {new_papers:,}",
    ]
    if new_confs:
        lines.append(f"- 📢 **New Conferences This Update**: {new_confs}")
    lines.append(f"- 📊 **Database Scale**: {stats['total_papers']:,} papers / {stats['total_series']} publication series / {stats['total_abstracts']:,} with abstracts / {stats['total_code']:,} with code")

    return "\n".join(lines)


def build_stats_section(stats: dict):
    """Build the statistics markdown section with SVG charts + HTML link."""
    lines = [
        '<p align="center">',
        '  <img src="./pics/stats/stats_overview.svg" alt="统计概览" width="850" />',
        "</p>",
        "",
        '<p align="center">',
        '  <img src="./pics/stats/papers_by_category.svg" alt="各领域论文数量" width="850" />',
        "</p>",
        "",
        '<p align="center">',
        '  <img src="./pics/stats/papers_by_year.svg" alt="历年论文收录趋势" width="850" />',
        "</p>",
        "",
        '<p align="center">',
        '  <img src="./pics/stats/wordcloud.svg" alt="刊物系列词云" width="900" />',
        "</p>",
        "",
        "📊 [查看交互式统计图表 (View Interactive Statistics)](./docs/stats.html)",
    ]
    return "\n".join(lines)


def build_stats_section_en(stats: dict):
    """Build the English statistics markdown section with SVG charts + HTML link."""
    lines = [
        '<p align="center">',
        '  <img src="./pics/stats/stats_overview.svg" alt="Statistics Overview" width="850" />',
        "</p>",
        "",
        '<p align="center">',
        '  <img src="./pics/stats/papers_by_category.svg" alt="Papers by Research Field" width="850" />',
        "</p>",
        "",
        '<p align="center">',
        '  <img src="./pics/stats/papers_by_year.svg" alt="Annual Paper Collection Trend" width="850" />',
        "</p>",
        "",
        '<p align="center">',
        '  <img src="./pics/stats/wordcloud.svg" alt="Publication Series Word Cloud" width="900" />',
        "</p>",
        "",
        "📊 [View Interactive Statistics](./docs/stats.html)",
    ]
    return "\n".join(lines)


def _update_single_readme(path: str, lang: str, stats: dict, meta: dict):
    """Update a single README file (zh or en)."""
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    # Update recent update section
    if lang == "zh":
        brief_md = build_recent_update_brief(meta, stats)
    else:
        brief_md = build_recent_update_brief_en(meta, stats)
    src = generate_new_readme(src, brief_md, COMMENT_RECENT_UPDATE_START, COMMENT_RECENT_UPDATE_END)

    # Update stats section
    if lang == "zh":
        stats_md = build_stats_section(stats)
    else:
        stats_md = build_stats_section_en(stats)
    src = generate_new_readme(src, stats_md, COMMENT_STATS_START, COMMENT_STATS_END)

    # Update confs list
    if lang == "zh":
        confs_md = build_hierarchical_confs_list()
    else:
        confs_md = build_hierarchical_confs_list_en()
    src = generate_new_readme(src, confs_md, COMMENT_CONFS_LIST_START, COMMENT_CONFS_LIST_END)

    with open(path, "w", encoding="utf-8") as f:
        f.write(src)


def update_readme():
    # README rendering only reads the cache, but we still refresh so the rendered
    # stats reflect the latest authoritative HF revision.
    ensure_cache_local(cache_path, refresh=True)
    cache_data = load_cache(cache_path) if os.path.exists(cache_path) else {}
    stats = compute_stats(cache_data)
    meta = _read_meta()

    # Generate SVG charts and interactive HTML stats page
    generate_charts_svg(stats)
    generate_wordcloud_svg(stats)
    generate_stats_html(stats)

    # Update Chinese README
    _update_single_readme(readme_path, "zh", stats, meta)

    # Update English README
    if os.path.exists(readme_en_path):
        _update_single_readme(readme_en_path, "en", stats, meta)


def force_update():
    # Refresh the local cache to record the current HF head as our parent_commit
    # baseline; even though force mode rebuilds the cache from scratch, this gives
    # the upload step a valid parent so the optimistic lock can detect concurrent
    # writers landing between our download and upload windows.
    ensure_cache_local(cache_path, refresh=True)
    res = collect(cache_file=None, force=True)
    save_cache(cache_path, res)
    sync_cache_artifacts(
        cache_path=cache_path,
        commit_message="Update PaperVault data artifacts after force rebuild",
    )
    stats = compute_stats(res)
    meta = {
        "last_update": datetime.now().strftime("%Y-%m-%d"),
        "new_papers": stats["total_papers"],
        "new_conferences": stats["total_instances"],
    }
    _write_meta(meta)
    update_readme()


def incremental_update(soft_timeout=None):
    """增量收集：只收集 conf 中有但 cache 中没有的会议，并更新 README。"""
    # Sync the local cache with HF before deciding whether to fall back to a
    # full rebuild; this avoids re-collecting everything just because the local
    # workspace was freshly checked out without the file.
    ensure_cache_local(cache_path, refresh=True)
    if not os.path.exists(cache_path):
        print("[!] Cache file not found, falling back to force update...")
        force_update()
        return

    print("[+] Running incremental collection...")
    if soft_timeout:
        print(f"[*] Soft timeout: {soft_timeout}s ({soft_timeout/3600:.1f}h)")
    before = load_cache(cache_path)
    before_count = sum(len(papers) for papers in before.values())
    before_confs = set(before.keys())

    res = collect(cache_file=cache_path, force=False, soft_timeout=soft_timeout)
    save_cache(cache_path, res)
    sync_cache_artifacts(
        cache_path=cache_path,
        commit_message="Update PaperVault data artifacts after incremental collect",
    )

    after_count = sum(len(papers) for papers in res.values())
    new_confs = set(res.keys()) - before_confs
    new_papers = after_count - before_count
    print(f"[+] Collected {new_papers} new papers across {len(new_confs)} new conference(s).")
    if new_confs:
        print(f"    New conferences: {', '.join(sorted(new_confs))}")

    failures_path = os.path.join(os.path.dirname(cache_path), "collect_failures.json")
    if os.path.exists(failures_path):
        try:
            with open(failures_path, "r", encoding="utf-8") as f:
                failures = json.load(f)
            if failures:
                print(f"[!] Previous/current run had {len(failures)} failure(s). They will be retried in the next run.")
        except Exception:
            pass

    meta = {
        "last_update": datetime.now().strftime("%Y-%m-%d"),
        "new_papers": new_papers,
        "new_conferences": len(new_confs),
    }
    _write_meta(meta)

    update_readme()
    print("[+] README updated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PaperVault maintenance utilities")
    parser.add_argument("command", nargs="?", choices=["force", "collect"], help="Command to run")
    parser.add_argument("--soft-timeout", type=float, default=None, help="Soft timeout in seconds (e.g. 18000 for 5h)")
    args = parser.parse_args()

    if args.command == "force":
        force_update()
    elif args.command == "collect":
        incremental_update(soft_timeout=args.soft_timeout)
    else:
        update_readme()
