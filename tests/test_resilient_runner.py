import unittest
from unittest.mock import patch

from scripts import fetch_and_push as app
from scripts import run_resilient as resilient


class ResilientRunnerTests(unittest.TestCase):
    def test_parse_accepts_english_product_name_with_chinese_summary(self):
        payload = {
            "items": [
                {
                    "index": 1,
                    "title_zh": "Gemini-3.5-Transcribe",
                    "summary_zh": "据标题信息，这是一个与语音转录能力相关的 Gemini 产品更新。",
                }
            ]
        }
        parsed = resilient.parse_enrichment_resilient(payload, 1)
        self.assertIn(1, parsed)
        self.assertTrue(app._contains_chinese(parsed[1][0]))
        self.assertEqual(parsed[1][0], "AI资讯：Gemini-3.5-Transcribe")

    def test_parse_still_rejects_non_chinese_summary(self):
        payload = {
            "items": [
                {
                    "index": 1,
                    "title_zh": "Gemini-3.5-Transcribe",
                    "summary_zh": "English only summary",
                }
            ]
        }
        self.assertEqual(resilient.parse_enrichment_resilient(payload, 1), {})

    def test_enrich_drops_one_irrecoverable_item(self):
        items = [
            {"title": "bad", "link": "https://example.com/bad"},
            {"title": "good", "link": "https://example.com/good"},
        ]
        calls = []

        def fake_enrich(rows):
            calls.append([row["title"] for row in rows])
            if any(row["title"] == "bad" for row in rows):
                raise app.PipelineError("Chinese enrichment missing for: bad")
            result = [dict(row) for row in rows]
            for row in result:
                row["cn_title"] = "中文标题"
                row["cn_summary"] = "这是中文摘要"
            return result

        with patch.object(resilient, "_ORIGINAL_ENRICH_ITEMS", side_effect=fake_enrich):
            result = resilient.enrich_items_resilient(items)

        self.assertEqual([row["title"] for row in result], ["good"])
        self.assertEqual(calls, [["bad", "good"], ["good"]])

    def test_enrich_keeps_non_missing_pipeline_errors_fatal(self):
        with patch.object(
            resilient,
            "_ORIGINAL_ENRICH_ITEMS",
            side_effect=app.PipelineError("DEEPSEEK_API_KEY is missing"),
        ):
            with self.assertRaisesRegex(app.PipelineError, "DEEPSEEK_API_KEY"):
                resilient.enrich_items_resilient([{"title": "x"}])


if __name__ == "__main__":
    unittest.main()
