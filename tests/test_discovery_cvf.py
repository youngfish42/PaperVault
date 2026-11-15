from discovery.cvf import CVFDiscovery, MENU_URLS


def test_discovery_accepts_only_confirmed_main_conference_families(monkeypatch):
    menu = """
      <a href="CVPR2027">Main Conference</a>
      <a href="CVPR2027_findings">Findings</a>
      <a href="CVPR2027_workshops">Workshops</a>
      <a href="CVPR2026">Main Conference</a>
      <a href="ICCV2027">Main Conference</a>
      <a href="ACCV2027">Main Conference</a>
      <a href="https://www.ecva.net/ECCV2028">Main Conference</a>
      <a href="CVPR2024">Main Conference</a>
    """
    other = '<a href="ECCV2028">Main Conference</a>'
    discovery = CVFDiscovery(existing_conf=[
        {"name": "CVPR2026", "url": "https://openaccess.thecvf.com/CVPR2026"},
        {"name": "ICCV2025", "url": "https://openaccess.thecvf.com/ICCV2025"},
        {"name": "ECCV2018", "url": "https://openaccess.thecvf.com/ECCV2018"},
    ])
    monkeypatch.setattr(
        discovery,
        "_get_text",
        lambda url: menu if url == MENU_URLS[0] else other,
    )
    assert discovery.discover(2025, 2028) == [
        {"name": "CVPR2027", "url": "https://openaccess.thecvf.com/CVPR2027"},
        {"name": "ICCV2027", "url": "https://openaccess.thecvf.com/ICCV2027"},
        {"name": "ECCV2028", "url": "https://openaccess.thecvf.com/ECCV2028"},
    ]


def test_discovery_without_confirmed_families_adds_nothing(monkeypatch):
    discovery = CVFDiscovery(existing_conf=[])
    monkeypatch.setattr(
        discovery,
        "_get_text",
        lambda _url: '<a href="CVPR2027">Main Conference</a>',
    )
    assert discovery.discover(2025, 2028) == []
