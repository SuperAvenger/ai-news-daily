#!/usr/bin/env python3
"""Run the AI news pipeline with guarded fallbacks."""

from __future__ import annotations

import os
from typing import Any

from scripts import fetch_and_push_base as app
from scripts.reddit_fallback import fetch_reddit_ai_resilient


MAX_SKIPPED_ENRICHMENTS = int(os.environ.get("MAX_SKIPPED_ENRICHMENTS", "3"))
_ORIGINAL_ENRICH_ITEMS = app.enrich_items


def parse_enrichment_resilient(
    data: dict[str, Any], expected: int
) -> dict[int, tuple[str, str]]:
    """Accept English product/model names when the Chinese summary is valid."""
    parsed: dict[int, tuple[str, str]] = {}
    for row in data.get("items", []):
        try:
            index = int(row["index"])
            title = str(row["title_zh"]).strip()
            summary = str(row["summary_zh"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not (1 <= index <= expected) or not title or not app._contains_chinese(summary):
            continue
        if not app._contains_chinese(title):
            title = f"AI资讯：{title}"
        parsed[index] = (title, summary)
    return parsed


def enrich_items_resilient(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Skip a small number of irrecoverable items instead of losing the whole digest."""
    remaining = [dict(item) for item in items]
    skipped: list[str] = []

    while remaining:
        try:
            return _ORIGINAL_ENRICH_ITEMS(remaining)
        except app.PipelineError as exc:
            message = str(exc)
            prefix = "Chinese enrichment missing for: "
            if not message.startswith(prefix):
                raise
            failed_title = message[len(prefix) :]
            failed_index = next(
                (
                    index
                    for index, item in enumerate(remaining)
                    if item.get("title") == failed_title
                ),
                None,
            )
            if failed_index is None:
                raise
            skipped.append(failed_title)
            print(
                f"Warning: dropping item after enrichment retries: {failed_title} "
                f"({len(skipped)}/{MAX_SKIPPED_ENRICHMENTS})"
            )
            if len(skipped) > MAX_SKIPPED_ENRICHMENTS:
                raise app.PipelineError(
                    "Too many enrichment failures; refusing to publish a degraded digest"
                ) from exc
            remaining.pop(failed_index)

    raise app.PipelineError("No items remained after enrichment retries")


def install_resilience() -> None:
    app._parse_enrichment = parse_enrichment_resilient
    app.enrich_items = enrich_items_resilient
    app.fetch_reddit_ai = fetch_reddit_ai_resilient


def main() -> None:
    install_resilience()
    app.main()


if __name__ == "__main__":
    main()
