from __future__ import annotations

import gzip
import re
import unicodedata
from pathlib import Path
from typing import Iterable, Iterator, Optional
from urllib.parse import urlsplit, urlunsplit

ACL_BIBTEX_URL = "https://aclanthology.org/anthology+abstracts.bib.gz"
ACL_HOST = "aclanthology.org"
_FIELD_RE = re.compile(r"(?m)^\s*([A-Za-z][\w-]*)\s*=\s*")
_ACCENT_RE = re.compile(r"\\([`'\-\"^~=.v])(?:\{)?([A-Za-z])(?:\})?")
_ACCENT_MARKS = {
    "`": "\u0300", "'": "\u0301", "-": "\u0304", '"': "\u0308",
    "^": "\u0302", "~": "\u0303", "=": "\u0304", ".": "\u0307", "v": "\u030c",
}
_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "theta": "θ", "lambda": "λ", "mu": "μ", "pi": "π", "sigma": "σ",
    "phi": "φ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ",
    "Lambda": "Λ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Omega": "Ω",
}


def canonical_acl_url(url: str) -> str:
    parsed = urlsplit((url or "").strip())
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if not path.endswith("/"):
        path += "/"
    return urlunsplit(("https", host, path, "", ""))


def is_acl_paper_url(url: str) -> bool:
    parsed = urlsplit(canonical_acl_url(url))
    if parsed.netloc != ACL_HOST:
        return False
    path = parsed.path.strip("/")
    return bool(
        re.fullmatch(r"(?:\d{4}\.[A-Za-z0-9-]+\.\d+|[A-Za-z]\d{2}-\d+)", path)
    )


def iter_bibtex_entries(lines: Iterable[str]) -> Iterator[str]:
    buffer = []
    depth = 0
    for line in lines:
        if depth == 0 and line.lstrip().startswith("@"):
            buffer = [line]
        elif not buffer:
            continue
        else:
            buffer.append(line)
        if not buffer:
            continue
        escaped = False
        for char in line:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
        if buffer and depth == 0:
            yield "".join(buffer)
            buffer = []
    if buffer:
        raise ValueError("Unclosed BibTeX entry")


def _unwrap_value(value: str) -> str:
    value = value.strip().rstrip(",").strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    if len(value) >= 2 and value[0] == "{" and value[-1] == "}":
        return value[1:-1]
    return value


def parse_bibtex_entry(entry: str) -> dict[str, str]:
    comma = entry.find(",")
    if comma < 0 or not entry.rstrip().endswith("}"):
        raise ValueError("Malformed BibTeX entry")
    body = entry[comma + 1:entry.rfind("}")]
    matches = list(_FIELD_RE.finditer(body))
    fields = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        fields[match.group(1).lower()] = _unwrap_value(body[match.end():end])
    return fields


def load_bibtex_metadata(
    path: Path, target_urls: Optional[set[str]] = None
) -> dict[str, dict[str, str]]:
    targets = {canonical_acl_url(url) for url in target_urls} if target_urls is not None else None
    metadata = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for raw_entry in iter_bibtex_entries(handle):
            fields = parse_bibtex_entry(raw_entry)
            url = canonical_acl_url(fields.get("url", ""))
            if not is_acl_paper_url(url) or (targets is not None and url not in targets):
                continue
            metadata[url] = fields
    return metadata


def _replace_accent(match: re.Match) -> str:
    mark = _ACCENT_MARKS.get(match.group(1), "")
    return unicodedata.normalize("NFKC", match.group(2) + mark)


def clean_acl_abstract(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\\href\{([^{}]+)\}\{([^{}]+)\}", r"\2", text)
    text = re.sub(r"\\url\{([^{}]+)\}", r"\1", text)
    for command, symbol in _GREEK.items():
        text = re.sub(rf"\\{command}\b", symbol, text)
    text = re.sub(r"\\(?:text(?:rm|bf|it|tt)?|emph|underline|mathbf|mathit|mathrm|mathcal|mathbb|mathsf|mathtt)\b", "", text)
    text = re.sub(r"\\(?:c)\{([A-Za-z])\}", lambda match: match.group(1) + "\u0327", text)
    text = re.sub(r"\\(?:aa|AA|o|O|ss)\b", lambda m: {"aa": "å", "AA": "Å", "o": "ø", "O": "Ø", "ss": "ß"}[m.group(0)[1:]], text)
    text = _ACCENT_RE.sub(_replace_accent, text)
    text = text.replace("~", " ")
    text = re.sub(r"\\([&#%_$])", r"\1", text)
    text = text.replace("---", "—").replace("--", "–")
    text = re.sub(r"[{}]", "", text)
    text = text.replace("$", "")
    return unicodedata.normalize("NFKC", re.sub(r"\s+", " ", text).strip())
