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
    model: Optional[str] = None
    max_keywords: Optional[int] = Field(default=None, ge=1, le=50)


class SuggestResponse(BaseModel):
    keywords: List[str]
    timecost_ms: float
    model: str
