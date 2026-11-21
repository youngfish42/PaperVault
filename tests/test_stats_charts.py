import json
import re

import maintain


def _sample_cache():
    return {
        "NIPS1999": [{"paper_code": "https://github.com/example/1999"}],
        "NIPS2000": [
            {},
            {"paper_code": "#"},
            {"paper_code": "  "},
            {"paper_code": "https://github.com/example/2000"},
        ],
        "SIGCOMM2002": [
            {"paper_code": "https://github.com/example/2002"},
            {},
        ],
    }


def test_compute_stats_counts_code_by_category():
    stats = maintain.compute_stats(_sample_cache())

    assert stats["total_code"] == 3
    assert stats["cat_stats"]["人工智能"] == 5
    assert stats["cat_code_stats"]["人工智能"] == 2
    assert stats["cat_stats"]["计算机网络"] == 2
    assert stats["cat_code_stats"]["计算机网络"] == 1
    assert stats["cat_stats"]["计算机科学理论"] == 0
    assert stats["cat_code_stats"]["计算机科学理论"] == 0


def test_chart_years_start_at_2000_and_fill_missing_years():
    years = maintain._chart_years({
        "1969": {},
        "1999": {},
        "2000": {},
        "2002": {},
        "Unknown": {},
    })

    assert years == ["2000", "2001", "2002"]
    assert maintain._chart_years({"1969": {}, "1999": {}}) == []


def test_stats_html_contains_code_metrics_and_filtered_years(tmp_path, monkeypatch):
    output_path = tmp_path / "stats.html"
    monkeypatch.setattr(maintain, "stats_html_path", str(output_path))

    maintain.generate_stats_html(maintain.compute_stats(_sample_cache()))

    html = output_path.read_text(encoding="utf-8")
    cat_data = json.loads(re.search(r"const catData = (.*);", html).group(1))
    year_data = json.loads(re.search(r"const yearData = (.*);", html).group(1))
    ai_index = list(maintain.CATEGORY_MAP).index("人工智能")
    network_index = list(maintain.CATEGORY_MAP).index("计算机网络")

    assert cat_data["code_values"][ai_index] == 2
    assert cat_data["code_values"][network_index] == 1
    assert "code_ratios" not in cat_data
    assert "barGap: '-100%'" in html
    assert year_data["years"] == ["2000", "2001", "2002"]
    assert all(series["data"][1] == 0 for series in year_data["series"])
