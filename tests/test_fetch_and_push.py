import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from scripts import fetch_and_push as app


class NewsPipelineTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 22, 0, 0, tzinfo=timezone.utc)

    def make_item(self, title="AI launch", link="https://example.com/story"):
        return {
            "title": title,
            "link": link,
            "source": "Hacker News",
            "published_at": (self.now - timedelta(hours=2)).isoformat(),
            "score": 10,
            "points": 10,
            "comments": 2,
            "summary": "",
            "cn_title": "人工智能产品发布",
            "cn_summary": "据标题信息，该产品公布了新的人工智能功能与相关更新。",
        }

    def test_canonicalize_url_removes_tracking_fragment_and_trailing_slash(self):
        url = "http://Example.com/path/?utm_source=x&b=2&a=1#section"
        self.assertEqual(
            app.canonicalize_url(url), "https://example.com/path?a=1&b=2"
        )

    def test_dedupe_items_uses_canonical_url(self):
        first = self.make_item(link="https://example.com/story?utm_source=a")
        second = self.make_item(
            title="Different title", link="http://example.com/story/#top"
        )
        self.assertEqual(len(app.dedupe_items([first, second])), 1)

    def test_recent_window_rejects_stale_future_and_missing_dates(self):
        self.assertTrue(app.is_recent(self.now - timedelta(hours=23), self.now))
        self.assertFalse(app.is_recent(self.now - timedelta(hours=25), self.now))
        self.assertFalse(app.is_recent(self.now + timedelta(hours=1), self.now))
        self.assertFalse(app.is_recent(None, self.now))

    def test_ai_title_matching_handles_punctuation(self):
        self.assertTrue(app._is_ai_title("AI: A new model launches"))
        self.assertTrue(app._is_ai_title("GPT-6.1 benchmark results"))
        self.assertFalse(app._is_ai_title("Sailing around the world"))

    @patch.object(app, "_get_json")
    def test_hn_story_rejects_stale_content(self, get_json):
        get_json.return_value = {
            "type": "story",
            "title": "AI: an old launch",
            "url": "https://example.com/old",
            "time": int((self.now - timedelta(days=2)).timestamp()),
            "score": 500,
        }
        self.assertIsNone(app._fetch_hn_story(1, self.now))

    @patch.object(app.requests, "get")
    @patch.object(app, "_load_feed_config")
    def test_rss_rejects_undated_entries(self, load_config, get):
        load_config.return_value = {
            "feeds": [{"name": "Example", "url": "https://example.com/rss", "weight": 1}],
            "settings": {"max_items_per_feed": 10, "blacklist_keywords": []},
        }
        response = Mock()
        response.content = b"<rss><channel><item><title>AI launch</title><link>https://example.com/a</link></item></channel></rss>"
        response.raise_for_status.return_value = None
        get.return_value = response
        self.assertEqual(app.fetch_rss_supplement(5, self.now), [])

    def test_parse_enrichment_keeps_only_complete_chinese_rows(self):
        payload = {
            "items": [
                {"index": 1, "title_zh": "中文标题", "summary_zh": "这是中文摘要"},
                {"index": 2, "title_zh": "English", "summary_zh": "中文摘要"},
            ]
        }
        self.assertEqual(app._parse_enrichment(payload, 2), {1: ("中文标题", "这是中文摘要")})

    def test_validate_digest_rejects_duplicates(self):
        first = self.make_item()
        second = self.make_item(title="Another AI title")
        with self.assertRaises(app.PipelineError):
            app.validate_digest([first, second], self.now)

    def test_validate_digest_accepts_fresh_unique_chinese_content(self):
        first = self.make_item()
        second = self.make_item(
            title="New LLM benchmark", link="https://example.org/benchmark"
        )
        second["cn_title"] = "新大模型基准发布"
        second["cn_summary"] = "据标题信息，一项新的大模型评测基准已经发布并提供比较。"
        app.validate_digest([first, second], self.now)

    def test_render_count_matches_actual_items(self):
        items = [self.make_item()]
        rendered = app.render_markdown(items)
        self.assertIn("Hacker News（1条）", rendered)
        self.assertEqual(rendered.count("[阅读原文]"), 1)


if __name__ == "__main__":
    unittest.main()
