import re

_GITHUB_RE = re.compile(
    r"https?://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+(?:/[^\s\)\]\}>\"'`]*)?"
)


def extract_github_link(text: str) -> str:
    """从文本中提取第一个 GitHub 仓库链接，清理尾部标点。"""
    if not text:
        return ""
    matches = _GITHUB_RE.findall(text)
    if not matches:
        return ""
    url = matches[0]
    url = url.rstrip(".,;:'\")]}>")
    return url


def add_code_links(res):
    """扫描论文 abstract 中的 GitHub 链接，补充 code 字段。

    旧逻辑（爬取外部 Markdown 仓库）已废弃，改为直接从 abstract 中
    正则匹配 GitHub 链接。保留已有非 '#' 的 code_links 不变。
    """
    for conf_name, papers in res.items():
        for ii, item in enumerate(papers):
            existing = (item.get("paper_code") or "#").strip()
            if existing and existing != "#":
                continue
            abstract = (item.get("paper_abstract") or "").strip()
            if not abstract:
                continue
            link = extract_github_link(abstract)
            if link:
                papers[ii]["paper_code"] = link
    return res
