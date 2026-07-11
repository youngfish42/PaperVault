from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass(frozen=True)
class SourceSpec:
    key: str
    conf_file: str
    tqdm_desc: str
    search_fn_name: str
    required_conf_keys: tuple = ("name", "url")
    post_run_hook_name: Optional[str] = None
    empty_result_soft_fail: bool = False


SOURCE_REGISTRY: tuple[SourceSpec, ...] = (
    SourceSpec(
        key="ACL",
        conf_file="conf/acl_conf.json",
        tqdm_desc="[+] Collecting ACL",
        search_fn_name="search_from_acl",
        required_conf_keys=("name", "url", "tag"),
        empty_result_soft_fail=True,
    ),
    SourceSpec(
        key="ICLR",
        conf_file="conf/iclr_conf.json",
        tqdm_desc="[+] Collecting ICLR",
        search_fn_name="search_from_iclr",
    ),
    SourceSpec(
        key="thecvf",
        conf_file="conf/thecvf_conf.json",
        tqdm_desc="[+] Collecting openaccess.thecvf",
        search_fn_name="search_from_thecvf",
    ),
    SourceSpec(
        key="NeurIPS",
        conf_file="conf/nips_conf.json",
        tqdm_desc="[+] Collecting NeurIPS",
        search_fn_name="search_from_nips",
    ),
    SourceSpec(
        key="DBLP",
        conf_file="conf/dblp_conf.json",
        tqdm_desc="[+] Collecting DBLP",
        search_fn_name="search_from_dblp",
        post_run_hook_name="_dblp_track_collected_name",
    ),
)
