from discovery.miccai import MICCAIDiscovery, MICCAI_OPENACCESS_ROOT


def test_discovers_only_new_main_conference_years():
    html = """
    <a href="https://papers.miccai.org/miccai-2026-sat/">Satellite</a>
    <a href="https://papers.miccai.org/miccai-2026/">2026</a>
    <a href="https://papers.miccai.org/miccai-2025/">2025</a>
    <a href="https://papers.miccai.org/miccai-2024/">2024</a>
    <a href="https://papers.miccai.org/miccai-2025/">Duplicate 2025</a>
    <a href="https://conferences.miccai.org/2023/papers/">2023</a>
    """
    discovery = MICCAIDiscovery(existing_conf=[{"name": "MICCAI2024", "url": "old"}])
    discovery._get_text = lambda *args, **kwargs: html

    assert discovery.discover(2024, 2026) == [
        {"name": "MICCAI2026", "url": "https://papers.miccai.org/miccai-2026/"},
        {"name": "MICCAI2025", "url": "https://papers.miccai.org/miccai-2025/"},
    ]


def test_discovery_returns_empty_when_root_is_unavailable():
    discovery = MICCAIDiscovery()
    discovery._get_text = lambda *args, **kwargs: ""
    assert discovery.discover(2024, 2026) == []
    assert MICCAI_OPENACCESS_ROOT == "https://papers.miccai.org/"
