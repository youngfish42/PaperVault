"""验证 ``extract_abstract_from_pdf_bytes`` 的正确性：抽出来的是不是该论文真正的摘要。

方法：从 cache 分层抽样缺摘要的 .pdf 直链记录（ceur-ws / vldb / ndss /
usenix / 其他 host，层内尽量铺开年份），下载 PDF 并抽取；用标题查
OpenAlex（``/works?search=``，过 ``_title_gate`` ≥0.85 + ``_year_gate`` ±1
门控）拿参考摘要，OpenAlex 无结果时退化到 S2 ``search/match``；归一化后
用 SequenceMatcher ratio + 前 200 字符包含关系判定 PASS / REVIEW / FAIL。

用法::

    python scripts/validate_pdf_extract.py                      # 默认 24 篇, seed 42
    python scripts/validate_pdf_extract.py --sample-size 12 --seed 7
    python scripts/validate_pdf_extract.py --output docs/pdf_extract_validation.md
"""

import argparse
import difflib
import gzip
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.fetch_abstracts import (  # noqa: E402
    CACHE_FILE,
    _download_pdf_bytes,
    _openalex_title_match_fetch,
    _reconstruct_openalex_abstract,
    _s2_title_match_fetch,
    _title_gate,
    _year_gate,
    clean_abstract,
)
from papervault.services.abstract_fetchers.pdf_extract import (  # noqa: E402
    extract_abstract_from_pdf_bytes,
)

# 分层配额（sample-size=24 的基准）：host 谓词按顺序匹配，先中先得。
STRATA = [
    ("ceur-ws", lambda u: "ceur-ws.org" in u, 6),
    ("vldb", lambda u: "vldb.org" in u, 6),
    ("ndss", lambda u: "ndss-symposium.org" in u, 4),
    ("usenix", lambda u: "usenix.org" in u, 4),
    ("other", lambda u: True, 4),
]

COPYRIGHT_MARKERS = ("permission to make digital", "copyright held by")
INTRO_MARKERS = ("1. introduction", "i. introduction", "1 introduction")


def _conf_year(conf: str) -> int:
    m = re.search(r"(\d{4})", conf or "")
    return int(m.group(1)) if m else 0


def _load_pdf_candidates() -> list:
    """缺摘要且 paper_url 以 .pdf 结尾的记录。"""
    out = []
    with gzip.open(CACHE_FILE, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            p = json.loads(line)
            if (p.get("paper_abstract") or "").strip():
                continue
            url = p.get("paper_url", "")
            if not url.lower().endswith(".pdf"):
                continue
            if not (p.get("paper_name") or "").strip():
                continue
            out.append(p)
    return out


def _sample_stratified(papers: list, sample_size: int, seed: int) -> list:
    rng = random.Random(seed)
    picked = []
    claimed_urls = set()
    for name, pred, base_quota in STRATA:
        quota = max(1, round(sample_size * base_quota / 24))
        pool = [p for p in papers if pred(p["paper_url"]) and p["paper_url"] not in claimed_urls]
        # 层内按年份铺开：按年份排序后均匀取点 + seed 抖动
        pool.sort(key=lambda p: (_conf_year(p.get("conf", "")), p["paper_url"]))
        if len(pool) <= quota:
            chosen = pool
        else:
            step = len(pool) / quota
            idxs = []
            for i in range(quota):
                lo = int(i * step)
                hi = int((i + 1) * step)
                idxs.append(rng.randint(lo, max(lo, hi - 1)))
            chosen = [pool[i] for i in sorted(set(idxs))][:quota]
        for p in chosen:
            claimed_urls.add(p["paper_url"])
            picked.append((name, p))
    return picked


def _normalize(text: str) -> str:
    """小写 + 去连字符断行 + 压缩空白（两侧同口径，仅用于比对）。"""
    t = (text or "").lower()
    t = re.sub(r"-\s+", "", t)  # hyphen line-break: "informa- tion" → "information"
    t = re.sub(r"[^\w]+", " ", t)
    return " ".join(t.split())


def _reference_abstract(paper: dict) -> tuple:
    """(reference_abstract, ref_source)。两级：OpenAlex → S2 match。

    参考摘要必须过标题门控（ratio≥0.85）与年份守卫才可用。
    """
    title = (paper.get("paper_name") or "").strip()
    conf = paper.get("conf", "")
    data, _err = _openalex_title_match_fetch(title)
    if data:
        if _title_gate(data.get("title") or "", title) and _year_gate(conf, data.get("publication_year")):
            ref = clean_abstract(_reconstruct_openalex_abstract(data.get("abstract_inverted_index") or {}) or "")
            if ref:
                return ref, "openalex"
    data, _err = _s2_title_match_fetch(title)
    if data:
        if _title_gate(data.get("title") or "", title) and _year_gate(conf, data.get("year")):
            ref = clean_abstract(data.get("abstract") or "")
            if ref:
                return ref, "semanticscholar"
    return None, ""


def _diagnose(extracted: str) -> dict:
    low = (extracted or "").lower()
    return {
        "len": len(extracted or ""),
        "copyright": any(m in low for m in COPYRIGHT_MARKERS),
        "intro_leak": any(m in low for m in INTRO_MARKERS),
        "keywords_leak": bool(re.search(r"\b(keywords|index terms)\b", low)),
    }


def _verdict(extracted: str, reference) -> tuple:
    """返回 (verdict, ratio, prefix_match)。"""
    if not extracted:
        return "FAIL", 0.0, False
    if not reference:
        return "NOREF", None, False
    a, b = _normalize(extracted), _normalize(reference)
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    prefix = a[:200] and b[:200] and (a[:200] in b or b[:200] in a)
    if ratio >= 0.80 or prefix:
        return "PASS", ratio, bool(prefix)
    if ratio >= 0.5:
        return "REVIEW", ratio, bool(prefix)
    return "FAIL", ratio, bool(prefix)


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate PDF abstract extraction against reference abstracts")
    ap.add_argument("--sample-size", type=int, default=24)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--output", type=str, default="docs/pdf_extract_validation.md")
    args = ap.parse_args()

    t0 = time.time()
    papers = _load_pdf_candidates()
    print(f"[*] PDF candidates without abstract: {len(papers)}")
    samples = _sample_stratified(papers, args.sample_size, args.seed)
    print(f"[*] Sampled {len(samples)} papers (seed={args.seed})")

    rows = []
    for i, (stratum, p) in enumerate(samples, 1):
        url = p["paper_url"]
        title = (p.get("paper_name") or "").strip()
        conf = p.get("conf", "")
        print(f"[{i}/{len(samples)}] ({stratum}) [{conf}] {title[:60]}")
        print(f"    {url}")

        pdf_bytes, dl_err = _download_pdf_bytes(url)
        extracted = ""
        dl_note = ""
        if pdf_bytes is None:
            dl_note = f"download failed: {dl_err}"
        else:
            extracted = extract_abstract_from_pdf_bytes(pdf_bytes)

        reference, ref_src = _reference_abstract(p)
        verdict, ratio, prefix = _verdict(extracted, reference)
        diag = _diagnose(extracted)
        rows.append({
            "stratum": stratum, "conf": conf, "year": _conf_year(conf),
            "url": url, "title": title,
            "extracted": extracted, "reference": reference, "ref_src": ref_src,
            "verdict": verdict, "ratio": ratio, "prefix": prefix,
            "diag": diag, "dl_note": dl_note,
        })
        ratio_s = f"{ratio:.2f}" if ratio is not None else "  -  "
        print(f"    -> {verdict:6} ratio={ratio_s} len={diag['len']} ref={ref_src or '-'} {dl_note}")

    referenced = [r for r in rows if r["reference"]]
    passed = [r for r in referenced if r["verdict"] == "PASS"]
    review = [r for r in rows if r["verdict"] == "REVIEW"]
    failed = [r for r in rows if r["verdict"] == "FAIL"]
    noref = [r for r in rows if r["verdict"] == "NOREF"]
    pass_rate = len(passed) / len(referenced) * 100 if referenced else 0.0
    elapsed = time.time() - t0

    print()
    print(f"[*] Done in {elapsed:.0f}s. referenced={len(referenced)}, "
          f"PASS={len(passed)} ({pass_rate:.0f}% of referenced), "
          f"REVIEW={len(review)}, FAIL={len(failed)}, NOREF={len(noref)}")

    # ---------- markdown report ----------
    lines = [
        "# PDF abstract extraction validation",
        "",
        f"- date: {time.strftime('%Y-%m-%d %H:%M')}",
        f"- sample-size: {args.sample_size}, seed: {args.seed}",
        f"- referenced: {len(referenced)}, PASS: {len(passed)} ({pass_rate:.0f}%), "
        f"REVIEW: {len(review)}, FAIL: {len(failed)}, NOREF: {len(noref)}",
        "",
        "| # | verdict | ratio | stratum | conf | len | copyright | intro-leak | kw-leak | title |",
        "|---|---------|-------|---------|------|-----|-----------|------------|---------|-------|",
    ]
    for i, r in enumerate(rows, 1):
        ratio_s = f"{r['ratio']:.2f}" if r["ratio"] is not None else "-"
        d = r["diag"]
        lines.append(
            f"| {i} | {r['verdict']} | {ratio_s} | {r['stratum']} | {r['conf']} | "
            f"{d['len']} | {'Y' if d['copyright'] else ''} | {'Y' if d['intro_leak'] else ''} | "
            f"{'Y' if d['keywords_leak'] else ''} | {r['title'][:60]} |"
        )
    lines.append("")
    lines.append("## REVIEW / FAIL / NOREF details")
    for i, r in enumerate(rows, 1):
        if r["verdict"] not in ("REVIEW", "FAIL", "NOREF"):
            continue
        lines += [
            "",
            f"### [{i}] {r['verdict']} — [{r['conf']}] {r['title']}",
            "",
            f"- url: {r['url']}",
            f"- ref: {r['ref_src'] or 'none'} | ratio: "
            + (f"{r['ratio']:.2f}" if r["ratio"] is not None else "-")
            + (f" | note: {r['dl_note']}" if r["dl_note"] else ""),
            "",
            "**extracted**:",
            "",
            "```",
            (r["extracted"] or "(empty)")[:1500],
            "```",
        ]
        if r["reference"]:
            lines += ["", "**reference**:", "", "```", r["reference"][:1500], "```"]

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[*] Report written to {out}")


if __name__ == "__main__":
    main()
