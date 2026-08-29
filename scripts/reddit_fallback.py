#!/usr/bin/env python3
"""Reddit fetcher with JSON first and RSS fallback."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import feedparser
import requests

from scripts import fetch_and_push_base as app

SUBREDDITS = ("artificial", "MachineLearning", "LocalLLaMA", "singularity")


def _entry_link(entry: Any) -> str:
    link = str(entry.get("link", "") or "").strip()
    if link:
        return link
    for candidate in entry.get("links", []) or []:
        href = str(candidate.get("href", "") or "").strip()
        if href:
            return href
    return ""


def _fetch_subreddit_rss(subreddit: str, now: datetime) -> list[dict[str, Any]]:
    """Fetch one subreddit through its public Atom feed."""
    url = f"https://www.reddit.com/r/{subreddit}/hot/.rss?limit=15"
    response = requests.get(url, headers=app.HEADERS, timeout=app.REQUEST_TIMEOUT)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    items: list[dict[str, Any]] = []

    for entry in parsed.entries[:15]:
        title = str(entry.get("title", "") or "").strip()
        link = _entry_link(entry)
        published = app._feed_datetime(entry)
        if not title or not link or not app.is_recent(published, now):
            continue
        summary = app._clean_html(
            str(entry.get("summary", "") or entry.get("content", "") or "")
        )
        items.append(
            app._item(
                title=title,
                link=link,
                source=f"r/{subreddit}",
                published_at=published,
                summary=summary,
            )
        )
    return items


def _fetch_subreddit_json(subreddit: str, now: datetime) -> list[dict[str, Any]]:
    data = app._get_json(
        f"https://www.reddit.com/r/{subreddit}/hot.json?limit=15",
        params={"raw_json": 1},
    )
    items: list[dict[str, Any]] = []
    for post in data.get("data", {}).get("children", []):
        row = post.get("data", {})
        if not row.get("title") or row.get("stickied"):
            continue
        published = datetime.fromtimestamp(int(row.get("created_utc", 0)), tz=timezone.utc)
        if not app.is_recent(published, now):
            continue
        link = row.get("url", "")
        if link.startswith("/"):
            link = f"https://www.reddit.com{link}"
        items.append(
            app._item(
                title=row["title"],
                link=link,
                source=f"r/{subreddit}",
                published_at=published,
                score=row.get("score", 0),
                comments=row.get("num_comments", 0),
                summary=app._clean_html(row.get("selftext", "")),
            )
        )
    return items


def fetch_reddit_ai_resilient(
    limit: int = 8, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Fetch Reddit via JSON and fall back to RSS per subreddit on HTTP failure."""
    current = app._as_utc(now or app.utc_now())
    items: list[dict[str, Any]] = []

    for subreddit in SUBREDDITS:
        try:
            rows = _fetch_subreddit_json(subreddit, current)
        except (requests.RequestException, TypeError, ValueError) as exc:
            print(f"Reddit JSON r/{subreddit} failed: {exc}; trying RSS")
            try:
                rows = _fetch_subreddit_rss(subreddit, current)
                print(f"Reddit RSS r/{subreddit} recovered {len(rows)} items")
            except (requests.RequestException, TypeError, ValueError) as rss_exc:
                print(f"Reddit RSS r/{subreddit} failed: {rss_exc}")
                rows = []
        items.extend(rows)

    items = app.dedupe_items(items)
    items.sort(
        key=lambda row: (row.get("score", 0), row["published_at"]), reverse=True
    )
    return items[:limit]
