from discovery.ijcai import IJCAIDiscovery


def test_discovers_only_new_main_proceedings_and_preserves_volumes():
    html = """
    <a href="/proceedings/2026/">IJCAI 2026</a>
    <a href="/proceedings_aai4g/2024/">AI for Good special track</a>
    <a href="/proceedings/2001-1">Volume 1</a>
    <a href="/proceedings/2001-2">Volume 2</a>
    <a href="https://ijcai.org/proceedings/2001-1">Duplicate</a>
    """
    discovery = IJCAIDiscovery(existing_conf=[{"name": "IJCAI2026", "url": "old"}])
    discovery._get_text = lambda *args, **kwargs: html
    assert discovery.discover(1969, 2026) == [
        {"name": "IJCAI2001", "url": "https://ijcai.org/proceedings/2001-1"},
        {"name": "IJCAI2001", "url": "https://ijcai.org/proceedings/2001-2"},
    ]


def test_discovery_returns_empty_when_index_is_unavailable():
    discovery = IJCAIDiscovery()
    discovery._get_text = lambda *args, **kwargs: ""
    assert discovery.discover(2025, 2026) == []
