from types import SimpleNamespace

import pytest

from collector.io import save_cache
from scripts import fetch_ijcai_openaccess as cli


def _args(dry_run=False, replay=False, no_sync=False):
    return SimpleNamespace(
        offline=False,
        dry_run=dry_run,
        replay=replay,
        no_sync=no_sync,
        year=None,
        limit=None,
        concurrency=1,
        snapshot_dir=None,
        refresh=False,
    )


def _isolate_local_files(monkeypatch, tmp_path):
    cache = tmp_path / "cache.jsonl.gz"
    save_cache(str(cache), {})
    conf = tmp_path / "ijcai_conf.json"
    conf.write_text(
        '[{"name":"IJCAI2001","url":"https://ijcai.org/proceedings/2001-1"}]',
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "CACHE_FILE", cache)
    monkeypatch.setattr(cli, "CONF_FILE", conf)
    monkeypatch.setattr(cli, "_save_atomic", lambda *args: None)


@pytest.mark.parametrize("dry_run,replay", [(True, False), (False, True)])
def test_dry_run_and_replay_never_refresh_or_upload_hf(
    monkeypatch, tmp_path, dry_run, replay
):
    _isolate_local_files(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli, "ensure_cache_local",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("HF refresh")),
    )
    monkeypatch.setattr(
        cli, "sync_cache_artifacts",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("HF upload")),
    )
    if dry_run:
        monkeypatch.setattr(
            cli, "collect_ijcai_papers",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("collection")),
        )
    else:
        monkeypatch.setattr(cli, "collect_ijcai_papers", lambda *args, **kwargs: [])

    assert cli.run(_args(dry_run=dry_run, replay=replay)) == 0


def test_real_collection_still_refreshes_hf_parent(monkeypatch, tmp_path):
    _isolate_local_files(monkeypatch, tmp_path)
    calls = []

    def _refresh(path, refresh):
        calls.append((path, refresh))
        return path, "parent"

    monkeypatch.setattr(cli, "ensure_cache_local", _refresh)
    monkeypatch.setattr(cli, "collect_ijcai_papers", lambda *args, **kwargs: [])
    assert cli.run(_args(no_sync=True)) == 0
    assert calls == [(cli.CACHE_FILE, True)]
