import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import requests

from scripts import reddit_fallback as reddit


class RedditFallbackTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc)

    @patch.object(reddit, "_fetch_subreddit_rss")
    @patch.object(reddit, "_fetch_subreddit_json")
    def test_json_failure_uses_rss(self, fetch_json, fetch_rss):
        fetch_json.side_effect = requests.HTTPError("403")
        fetch_rss.return_value = [
            {
                "title": "AI fallback story",
                "link": "https://www.reddit.com/r/artificial/comments/x/story",
                "canonical_url": "https://www.reddit.com/r/artificial/comments/x/story",
                "source": "r/artificial",
                "published_at": self.now.isoformat(),
                "score": 0,
                "points": 0,
                "comments": 0,
                "summary": "fallback",
                "weight": 0,
            }
        ]

        with patch.object(reddit, "SUBREDDITS", ("artificial",)):
            rows = reddit.fetch_reddit_ai_resilient(8, self.now)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "r/artificial")
        fetch_rss.assert_called_once()

    @patch.object(reddit, "_fetch_subreddit_rss")
    @patch.object(reddit, "_fetch_subreddit_json")
    def test_json_success_does_not_call_rss(self, fetch_json, fetch_rss):
        fetch_json.return_value = []
        with patch.object(reddit, "SUBREDDITS", ("LocalLLaMA",)):
            rows = reddit.fetch_reddit_ai_resilient(8, self.now)
        self.assertEqual(rows, [])
        fetch_rss.assert_not_called()

    @patch.object(reddit.requests, "get")
    def test_rss_parses_recent_atom_entry(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.content = b'''<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>AI model update</title>
            <link href="https://www.reddit.com/r/artificial/comments/x/story" />
            <updated>2026-08-29T23:30:00+00:00</updated>
            <content type="html">New AI model discussion</content>
          </entry>
        </feed>'''
        get.return_value = response

        rows = reddit._fetch_subreddit_rss("artificial", self.now)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "AI model update")
        self.assertEqual(rows[0]["source"], "r/artificial")


if __name__ == "__main__":
    unittest.main()
