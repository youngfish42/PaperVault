# NOTE: json/os/time are re-exposed on the package namespace so tests can do
# ``monkeypatch.setattr(collector.time, "time", ...)`` /
# ``monkeypatch.setattr("collector.json.load", ...)`` and have the patch reach
# the code paths inside pipeline.py that access these modules via ``import``.
# Do NOT remove them just because the shim body doesn't reference them.
import json
import os
import time

from data_artifacts import ensure_cache_local, sync_cache_artifacts

from .http import HEADERS, SESSION, _create_session
from .merge import (
    _better_str,
    _better_list,
    _better_code,
    _merge_paper_record,
    _merge_with_cache,
)
from .progress import COLLECT_PROGRESS_FILE, COLLECT_FAILURES_FILE, load_collect_progress, save_collect_progress
from .io import _to_gz_path, load_cache, save_cache
from .code_links import _GITHUB_RE, extract_github_link, add_code_links

from .sources.openreview import (
    OPENREVIEW_REJECTED_VENUE_KEYWORDS,
    OPENREVIEW_ACCEPTED_VENUE_KEYWORDS,
    is_openreview_accepted_venue,
    _is_openreview_accepted,
    _or_field,
    _extract_forum_id,
    _fetch_openreview_abstract,
    _url_targets_rejected_venue,
    search_from_iclr_openreview,
    _batch_fetch_openreview_abstracts,
    search_from_iclr_official,
    search_from_iclr,
)
from .sources.nips import search_abs_from_nips, search_from_nips
from .sources.acl import _parse_acl_volume, _is_acl_volume_entry, search_from_acl
from .sources.dblp import search_abs_from_dblp, search_from_dblp
from .sources.thecvf import search_abs_from_thecvf, search_from_thecvf

from .pipeline import collect, do_collect


if __name__ == "__main__":
    do_collect(cache_file="cache/cache.jsonl.gz", force=True)
