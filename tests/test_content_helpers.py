from scripts.fetch_and_push import (
    _parse_numbered,
    _truncate,
    deduplicate_groups,
    deduplicate_items,
    is_quality_article,
    match_keywords,
)


def test_parse_numbered_ignores_model_preamble():
    text = "以下是结果：\n1. 第一条\n2、第二条"
    assert _parse_numbered(text) == ["第一条", "第二条"]


def test_parse_numbered_falls_back_to_nonempty_lines():
    assert _parse_numbered("第一条\n\n第二条") == ["第一条", "第二条"]


def test_truncate_preserves_short_titles_and_bounds_long_titles():
    assert _truncate("short") == "short"
    assert len(_truncate("x" * 200)) < 200


def test_configured_feed_quality_and_relevance_are_deterministic():
    settings = {"min_title_length": 8, "blacklist_keywords": ["广告"]}
    assert is_quality_article("OpenAI 发布新的推理模型", "技术细节", settings)
    assert not is_quality_article("这是一条广告推广内容", "", settings)
    assert match_keywords("OpenAI 发布模型", "", ["OpenAI", "Anthropic"]) == 0.75
    assert match_keywords("普通科技新闻标题", "", ["OpenAI"]) == 0


def test_deduplicate_items_ignores_tracking_query_and_duplicate_title():
    items = [
        {"title": "OpenAI launches model", "link": "https://example.com/story?utm_source=x"},
        {"title": "Different title", "link": "https://example.com/story"},
        {"title": "OpenAI launches model", "link": "https://other.example/story"},
    ]

    assert deduplicate_items(items) == [items[0]]


def test_deduplicate_groups_preserves_original_sections():
    first = [{"title": "Same story", "link": "https://example.com/a"}]
    second = [
        {"title": "Same story", "link": "https://other.example/a"},
        {"title": "Another story", "link": "https://example.com/b"},
    ]

    assert deduplicate_groups(first, second) == (first, [second[1]])
