def _better_str(prev: str, new: str) -> str:
    """Return whichever string carries more information.

    Used during in-batch and cache-merge dedupe: when two records describe the
    same paper, prefer the longer non-empty value so we never silently drop an
    abstract that was filled by a later collector pass or by a backfill
    workflow.
    """
    prev = prev or ""
    new = new or ""
    if not prev:
        return new
    if not new:
        return prev
    return new if len(new) > len(prev) else prev


def _better_list(prev, new):
    """Same intent as ``_better_str`` but for author lists."""
    prev = prev or []
    new = new or []
    if not prev:
        return new
    if not new:
        return prev
    return new if len(new) > len(prev) else prev


def _better_code(prev: str, new: str) -> str:
    """Prefer a real code link over the ``'#'`` placeholder."""
    prev = (prev or "").strip()
    new = (new or "").strip()
    if prev and prev != "#":
        return prev
    if new and new != "#":
        return new
    return prev or new or "#"


def _merge_paper_record(existing: dict, incoming: dict) -> None:
    """Field-level merge of two paper dicts that share the same dedupe key.

    The caller has already decided ``existing`` and ``incoming`` describe the
    same paper. We enrich ``existing`` in place by taking the longer abstract,
    the longer author list, and any non-placeholder code link.
    """
    existing["paper_abstract"] = _better_str(
        existing.get("paper_abstract"), incoming.get("paper_abstract")
    )
    existing["paper_authors"] = _better_list(
        existing.get("paper_authors"), incoming.get("paper_authors")
    )
    existing["paper_code"] = _better_code(
        existing.get("paper_code"), incoming.get("paper_code")
    )


def _merge_with_cache(new_res, cache_res, multi_volume_names, collected_dblp_names):
    """Merge ``cache_res`` (loaded from cache.jsonl.gz) into ``new_res``.

    Historically this function only deduped URLs for multi-volume DBLP confs,
    which let non-DBLP venues silently accumulate the same paper across reruns.
    We now:

    * Keep the old "drop the cache entirely if we did not recollect this conf"
      branch (``conf_name not in result``) — used to copy untouched cache
      records straight through.
    * For every conf we *did* recollect, URL-dedupe the cache against the new
      records and field-merge (longer abstract / authors / non-placeholder
      code) so cache abstracts survive a fresh source run.
    """
    result = dict(new_res)
    for conf_name, papers in cache_res.items():
        if conf_name not in result:
            result[conf_name] = papers
            continue
        url_index: dict = {p["paper_url"]: p for p in result[conf_name] if p.get("paper_url")}
        for p in papers:
            paper_url = p.get("paper_url")
            if not paper_url:
                result[conf_name].append(p)
                continue
            prior = url_index.get(paper_url)
            if prior is None:
                result[conf_name].append(p)
                url_index[paper_url] = p
            else:
                _merge_paper_record(prior, p)
    return result
