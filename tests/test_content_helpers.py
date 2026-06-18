from scripts.fetch_and_push import _parse_numbered, _truncate


def test_parse_numbered_ignores_model_preamble():
    text = "以下是结果：\n1. 第一条\n2、第二条"
    assert _parse_numbered(text) == ["第一条", "第二条"]


def test_parse_numbered_falls_back_to_nonempty_lines():
    assert _parse_numbered("第一条\n\n第二条") == ["第一条", "第二条"]


def test_truncate_preserves_short_titles_and_bounds_long_titles():
    assert _truncate("short") == "short"
    assert len(_truncate("x" * 200)) < 200
