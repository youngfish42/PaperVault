"""Pydantic v2 request/response schemas for the v1 API."""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PageMeta(BaseModel):
    page: int
    size: int
    total: int


class PaperOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    conf: str
    year: str
    title: str
    url: Optional[str] = None
    authors: List[str] = Field(default_factory=list)
    abstract: Optional[str] = None
    code: Optional[str] = None


class ConfYear(BaseModel):
    year: str
    count: int


class ConfOut(BaseModel):
    name: str
    total: int
    years: List[ConfYear]


class PaperSearchParams(BaseModel):
    q: Optional[str] = Field(default=None, description="Free-text query against title (or author when field=author).")
    field: str = Field(default="title", pattern="^(title|author|any)$")
    conf: List[str] = Field(default_factory=list, description="One or more conference names; case-insensitive.")
    since: Optional[int] = Field(default=None, ge=1900, le=2100)
    until: Optional[int] = Field(default=None, ge=1900, le=2100)
    author: Optional[str] = None
    sort: str = Field(default="-year", pattern="^-?(year|conf|title)$")
    page: int = Field(default=1, ge=1)
    # NOTE: the *authoritative* upper bound on ``size`` is
    # ``settings.max_page_size`` (validated at the API layer). We keep a wide
    # pydantic lower-bound check (``ge=1``) without any hard upper bound so
    # there is exactly one place to update the limit.
    size: int = Field(default=50, ge=1)

    @field_validator("conf", mode="before")
    @classmethod
    def _split_conf(cls, value: Any):
        if value is None:
            return []
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return value


class SuggestRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    provider: Optional[str] = Field(default=None, max_length=64)
    base_url: Optional[str] = Field(default=None, max_length=512)
    model: Optional[str] = Field(default=None, max_length=128)
    api_key: Optional[str] = Field(default=None, max_length=512)
    protocol: Optional[str] = Field(default=None, max_length=32)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_keywords: Optional[int] = Field(default=None, ge=1, le=50)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=4096)

    @field_validator("provider", "protocol", mode="before")
    @classmethod
    def _strip_lower(cls, value: Any):
        return _strip_or_none(value, lower=True)

    @field_validator("base_url", "model", "api_key", mode="before")
    @classmethod
    def _strip_normalize(cls, value: Any):
        return _strip_or_none(value, lower=False)


def _strip_or_none(value: Any, *, lower: bool = False) -> Any:
    """Trim whitespace; turn empty string into ``None``; optionally lowercase.

    Used by ``SuggestRequest`` field validators to keep the request shape
    consistent across optional string fields. Non-string values pass through
    unchanged so Pydantic can produce its standard type-error.
    """

    if value is None:
        return None
    if not isinstance(value, str):
        return value
    value = value.strip()
    if value == "":
        return None
    return value.lower() if lower else value


class SuggestResponse(BaseModel):
    keywords: List[str]
    timecost_ms: float
    model: str
    provider: str
    protocol: str


class RerankRequest(BaseModel):
    """Re-rank payload sent to ``POST /v1/ai/rerank``.

    ``paper_ids`` are the SHA-1-prefixed 16-char paper hashes the backend
    uses internally (see ``services.papers.Paper.id``). We intentionally
    cap the batch size server-side to keep a single LLM call bounded —
    even with truncated abstracts, 300 papers × ~100 tokens each still
    costs ~30k input tokens, which is the practical ceiling for the
    providers currently wired in.

    The provider / model / api_key fields mirror ``SuggestRequest`` so a
    caller can reuse exactly the same settings object on both endpoints.
    """

    query: str = Field(min_length=1, max_length=200)
    paper_ids: List[str] = Field(min_length=1, max_length=300)
    provider: Optional[str] = Field(default=None, max_length=64)
    base_url: Optional[str] = Field(default=None, max_length=512)
    model: Optional[str] = Field(default=None, max_length=128)
    api_key: Optional[str] = Field(default=None, max_length=512)
    protocol: Optional[str] = Field(default=None, max_length=32)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)

    @field_validator("provider", "protocol", mode="before")
    @classmethod
    def _strip_lower(cls, value: Any):
        return _strip_or_none(value, lower=True)

    @field_validator("base_url", "model", "api_key", mode="before")
    @classmethod
    def _strip_normalize(cls, value: Any):
        return _strip_or_none(value, lower=False)

    @field_validator("paper_ids", mode="after")
    @classmethod
    def _dedupe_paper_ids(cls, value: List[str]) -> List[str]:
        # Drop duplicates while preserving first-seen order so the LLM
        # prompt never lists the same paper twice and the response never
        # surfaces duplicate ``paper_id`` entries to the UI (the previous
        # implementation could emit duplicates when an id appeared twice
        # *and* the LLM forgot to score it: both copies would land in the
        # ``missing`` tail list).
        #
        # Note: shape validation (``^[0-9a-f]{16}$``) is *not* enforced
        # here on purpose — the endpoint contract is that unknown /
        # malformed ids flow through to ``skipped_ids`` so the caller
        # can render a non-fatal warning rather than retry the whole
        # batch. ``PaperRepository.get_by_id`` is the single point that
        # turns a string into a hit or a skip.
        return list(dict.fromkeys(value))


class RerankEntry(BaseModel):
    paper_id: str
    # Score is normalized to ``[0.0, 1.0]`` server-side; the UI multiplies
    # by 100 for display. Providers that natively score on a different
    # scale (e.g. 0-10 or 0-100) are mapped into this range inside
    # ``services.rerank``.
    score: float = Field(ge=0.0, le=1.0)


class RerankResponse(BaseModel):
    # The full re-ranked list, highest score first. ``paper_ids`` that the
    # LLM failed to score are appended at the tail with a default score so
    # the caller never loses items from the input batch.
    ordered: List[RerankEntry]
    # Paper ids the caller sent that did not resolve in the local index
    # (typically stale ids). Empty list means every id was honoured.
    skipped_ids: List[str] = Field(default_factory=list)
    timecost_ms: float
    model: str
    provider: str
    protocol: str
