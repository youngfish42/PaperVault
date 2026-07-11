import json
import os
import time

from collections import Counter
from tqdm import tqdm

from data_artifacts import ensure_cache_local, sync_cache_artifacts

from collector.io import _to_gz_path, load_cache, save_cache
from collector.merge import _merge_with_cache
from collector.code_links import add_code_links
from collector.progress import (
    COLLECT_PROGRESS_FILE,
    COLLECT_FAILURES_FILE,
    load_collect_progress,
    save_collect_progress,
)
from collector.sources import SOURCE_REGISTRY
from collector.sources.acl import search_from_acl
from collector.sources.openreview import search_from_iclr
from collector.sources.thecvf import search_from_thecvf
from collector.sources.nips import search_from_nips
from collector.sources.dblp import search_from_dblp


def collect(cache_file=None, force=False, soft_timeout=None):
    import collector as _pkg

    res = {}
    failures = []
    progress = {} if force else _pkg.load_collect_progress()

    with open("conf/acl_conf.json", "r") as f:
        acl_conf = json.load(f)
    with open("conf/dblp_conf.json", "r") as f:
        dblp_conf = json.load(f)
    with open("conf/nips_conf.json", "r") as f:
        nips_conf = json.load(f)
    with open("conf/iclr_conf.json", "r") as f:
        iclr_conf = json.load(f)
    with open("conf/thecvf_conf.json", "r") as f:
        thecvf_conf = json.load(f)

    cache_conf = set()
    cache_res = {}
    gz_path = _to_gz_path(cache_file) if cache_file else None
    if not force and gz_path is not None and os.path.exists(gz_path):
        cache_res = _pkg.load_cache(cache_file)
        cache_conf = set(cache_res.keys())

    dblp_name_counter = Counter(conf["name"] for conf in dblp_conf if conf.get("name"))
    multi_volume_dblp_names = {
        name for name, count in dblp_name_counter.items() if count > 1
    }
    # 同一 ACL name（例如 ACL2026）常被拆成 events 主页 + findings volume 两条
    # 独立 conf。legacy-skip（cache 中已存在此 name → 直接标 legacy 跳过）会误
    # 伤第二条条目：主会先入库后，findings volume 永远进不来。这里收集所有出
    # 现多次的 ACL name，让 _should_skip 对这些 name 放行，改由 progress 里的
    # 每 (source, url) key 独立追踪各入口是否已完成。
    acl_name_counter = Counter(conf["name"] for conf in acl_conf if conf.get("name"))
    multi_volume_acl_names = {
        name for name, count in acl_name_counter.items() if count > 1
    }

    start_time = time.time()
    collected_dblp_names = set()
    save_tracker = {"last": 0}

    def _is_timeout():
        if soft_timeout and start_time is not None:
            elapsed = time.time() - start_time
            if elapsed >= soft_timeout:
                print(f"[!] Soft timeout ({soft_timeout}s, elapsed {elapsed:.0f}s) reached.")
                return True
        return False

    def _save_state():
        now = time.time()
        if now - save_tracker["last"] < 5:
            return
        save_tracker["last"] = now
        _pkg.save_collect_progress(progress)
        if cache_file:
            merged = _merge_with_cache(res, cache_res, multi_volume_dblp_names, collected_dblp_names)
            tmp_file = cache_file + ".tmp"
            _pkg.save_cache(tmp_file, merged)
            os.replace(tmp_file, cache_file)
            print(f"[*] Incremental cache saved: {cache_file}")

    def _should_skip(source, url, name):
        if force:
            return False
        key = f"{source}::{url}"
        # 多入口 ACL 会议（例如 ACL2026 events + findings volume）之前可能
        # 已经被误标 legacy 写进 progress，导致后续 workflow 永远跳过第二
        # 条入口。这里主动清理这类残留 legacy 记录，让下一次运行自愈。
        if source == "ACL" and name in multi_volume_acl_names:
            existing = progress.get(key)
            if isinstance(existing, dict) and existing.get("legacy"):
                progress.pop(key, None)
        if key in progress:
            return True
        if name in cache_conf:
            if source == "DBLP" and name in multi_volume_dblp_names:
                return False
            if source == "ACL" and name in multi_volume_acl_names:
                # 多入口 ACL 会议不写 legacy progress，交给具体 URL 各自跑一遍。
                return False
            progress[key] = {"name": name, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "legacy": True}
            return True
        return False

    def _dblp_track_collected_name(name):
        collected_dblp_names.add(name)

    conf_by_file = {
        "conf/acl_conf.json": acl_conf,
        "conf/iclr_conf.json": iclr_conf,
        "conf/thecvf_conf.json": thecvf_conf,
        "conf/nips_conf.json": nips_conf,
        "conf/dblp_conf.json": dblp_conf,
    }

    for spec in SOURCE_REGISTRY:
        source_confs = conf_by_file[spec.conf_file]
        search_fn = getattr(_pkg, spec.search_fn_name)
        post_run_hook = _dblp_track_collected_name if spec.post_run_hook_name == "_dblp_track_collected_name" else None

        for conf in tqdm(source_confs, desc=spec.tqdm_desc, dynamic_ncols=True):
            try:
                if not all(conf.get(k) for k in spec.required_conf_keys):
                    print(f"[!] Skip invalid {spec.key} conf: {conf}")
                    continue

                url, name = conf["url"], conf["name"]
                if _should_skip(spec.key, url, name):
                    continue
                if _is_timeout():
                    break

                if spec.empty_result_soft_fail:
                    tag = conf["tag"]
                    before = len(res.get(name, []))
                    res = search_fn(url, tag, name, res)
                    after = len(res.get(name, []))
                    if after == before:
                        msg = (
                            f"[!] {spec.key} '{name}' matched 0 papers for tag={tag!r} at {url}. "
                            "Suspect tag/href mismatch (e.g. legacy ^Xyy-* tag against "
                            "modern /{year}.{venue}-{track}.N/ hrefs)."
                        )
                        print(msg)
                        failures.append({
                            "source": spec.key,
                            "name": name,
                            "url": url,
                            "error": f"empty result for tag={tag!r}",
                        })
                    else:
                        progress[f"{spec.key}::{url}"] = {"name": name, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
                else:
                    res = search_fn(url, name, res)
                    progress[f"{spec.key}::{url}"] = {"name": name, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}

                if post_run_hook:
                    post_run_hook(name)

                _save_state()
            except Exception as e:
                # Keep the human-readable log and the machine-readable
                # failures.json entry pinned to the *same* source label. The
                # historical collector.py logged CVF failures as
                # "openaccess.thecvf" (not the internal registry key
                # "thecvf"); external log-grep tooling relies on that
                # spelling, so we compute the label once and reuse it.
                error_source_label = "openaccess.thecvf" if spec.key == "thecvf" else spec.key
                print(f"[!] Failed to collect {error_source_label} '{conf.get('name', 'unknown')}': {e}")
                failures.append({
                    "source": error_source_label,
                    "name": conf.get("name"),
                    "url": conf.get("url"),
                    "error": str(e),
                })
        _save_state()

    final_res = _merge_with_cache(res, cache_res, multi_volume_dblp_names, collected_dblp_names)

    res = add_code_links(final_res)

    failures_path = _pkg.COLLECT_FAILURES_FILE
    try:
        os.makedirs(os.path.dirname(failures_path) or ".", exist_ok=True)
        with open(failures_path, "w", encoding="utf-8") as f:
            json.dump(failures, f, ensure_ascii=False, indent=2)
        if failures:
            print(f"[!] {len(failures)} conference(s) failed. Details saved to {failures_path}")
    except Exception as e:
        print(f"[!] Could not save failure log: {e}")

    return res


def do_collect(cache_file=None, force=False, soft_timeout=None):
    import collector as _pkg

    gz_path = _to_gz_path(cache_file) if cache_file else None
    # Synchronise the local cache with the Hugging Face authoritative copy
    # before deciding whether we need to do a full or incremental collection.
    if gz_path:
        _pkg.ensure_cache_local(gz_path, refresh=True)
    if force or gz_path is None or not os.path.exists(gz_path):
        print(f"[+] Collecting papers...")
        res = collect(cache_file, force=force, soft_timeout=soft_timeout)
        _pkg.save_cache(cache_file, res)
        if cache_file:
            _pkg.sync_cache_artifacts(
                cache_path=cache_file,
                commit_message="Update PaperVault data artifacts after collection",
            )
    else:
        print(f"[+] Loading from cache...")
        res = _pkg.load_cache(cache_file)
    return res
