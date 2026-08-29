#!/usr/bin/env python3
"""Fetch fresh AI news, enrich it in Chinese, and push a validated Feishu card."""

from __future__ import annotations

import calendar
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup


REPO_ROOT = Path(__file__).resolve().parent.parent
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_ENDPOINT = os.environ.get(
    "DEEPSEEK_ENDPOINT", "https://api.deepseek.com/v1/chat/completions"
)
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
APP_TIMEZONE = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Shanghai"))
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "24"))
MAX_TOTAL_ITEMS = int(os.environ.get("MAX_TOTAL_ITEMS", "20"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "20"))

HEADERS = {
    "User-Agent": "ai-news-daily/2.0 (+https://github.com/SuperAvenger/ai-news-daily)"
}

AI_TITLE_PATTERN = re.compile(
    r"\b(?:ai|llms?|gpt(?:-?\d[\w.-]*)?|chatgpt|openai|anthropic|claude|gemini|"
    r"deepseek|copilot|neural|transformers?|diffusion|midjourney|dall-e)\b|"
    r"artificial intelligence|machine learning|deep learning|generative ai|"
    r"foundation model",
    re.IGNORECASE,
)

TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "source",
}


class PipelineError(RuntimeError):
    """Raised when a digest would be incomplete or misleading."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_recent(
    published_at: datetime | None,
    now: datetime | None = None,
    hours: int = LOOKBACK_HOURS,
) -> bool:
    """Return True only for timestamps inside the requested rolling window."""
    if published_at is None:
        return False
    current = _as_utc(now or utc_now())
    published = _as_utc(published_at)
    return current - timedelta(hours=hours) <= published <= current + timedelta(minutes=5)


def canonicalize_url(url: str) -> str:
    """Normalize URLs so tracking parameters and superficial variants deduplicate."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        if not host:
            return ""
        port = parts.port
        netloc = host
        if port and not (
            (parts.scheme.lower() == "http" and port == 80)
            or (parts.scheme.lower() == "https" and port == 443)
        ):
            netloc = f"{host}:{port}"
        query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            lowered = key.lower()
            if lowered.startswith("utm_") or lowered in TRACKING_PARAMS:
                continue
            query.append((key, value))
        path = re.sub(r"/{2,}", "/", parts.path or "/")
        if path != "/":
            path = path.rstrip("/")
        return urlunsplit(("https", netloc, path, urlencode(sorted(query)), ""))
    except (TypeError, ValueError):
        return ""


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title.lower())


def dedupe_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate globally by canonical URL and normalized title."""
    result: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for original in items:
        item = dict(original)
        canonical = canonicalize_url(item.get("link", ""))
        title_key = _title_key(item.get("title", ""))
        if not canonical or not title_key:
            continue
        if canonical in seen_urls or title_key in seen_titles:
            continue
        item["canonical_url"] = canonical
        seen_urls.add(canonical)
        seen_titles.add(title_key)
        result.append(item)
    return result


def _is_ai_title(title: str) -> bool:
    return bool(AI_TITLE_PATTERN.search(title))


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _item(
    *,
    title: str,
    link: str,
    source: str,
    published_at: datetime,
    score: int = 0,
    comments: int = 0,
    summary: str = "",
    weight: int = 0,
) -> dict[str, Any]:
    return {
        "title": title.strip(),
        "link": link.strip(),
        "source": source,
        "published_at": _iso(published_at),
        "score": int(score or 0),
        "points": int(score or 0),
        "comments": int(comments or 0),
        "summary": summary.strip(),
        "weight": int(weight or 0),
    }


def _get_json(url: str, **kwargs: Any) -> Any:
    timeout = kwargs.pop("timeout", REQUEST_TIMEOUT)
    response = requests.get(url, headers=HEADERS, timeout=timeout, **kwargs)
    response.raise_for_status()
    return response.json()


def _fetch_hn_story(story_id: int, now: datetime) -> dict[str, Any] | None:
    try:
        data = _get_json(
            f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
            timeout=min(REQUEST_TIMEOUT, 8),
        )
        if not data or data.get("type") != "story" or data.get("dead") or data.get("deleted"):
            return None
        title = data.get("title", "")
        link = data.get("url", "")
        published = datetime.fromtimestamp(int(data.get("time", 0)), tz=timezone.utc)
        if not title or not link or not _is_ai_title(title) or not is_recent(published, now):
            return None
        return _item(
            title=title,
            link=link,
            source="Hacker News",
            published_at=published,
            score=data.get("score", 0),
            comments=data.get("descendants", 0),
        )
    except (requests.RequestException, TypeError, ValueError) as exc:
        print(f"HN story {story_id} failed: {exc}")
        return None


def _fetch_hn_algolia(now: datetime, limit: int) -> list[dict[str, Any]]:
    cutoff = int((now - timedelta(hours=LOOKBACK_HOURS)).timestamp())
    try:
        data = _get_json(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={
                "tags": "story",
                "hitsPerPage": 100,
                "numericFilters": f"created_at_i>{cutoff},points>2",
            },
        )
    except requests.RequestException as exc:
        print(f"HN Algolia failed: {exc}")
        return []

    items = []
    for hit in data.get("hits", []):
        title = hit.get("title", "")
        link = hit.get("url", "")
        try:
            published = datetime.fromtimestamp(int(hit.get("created_at_i", 0)), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            continue
        if title and link and _is_ai_title(title) and is_recent(published, now):
            items.append(
                _item(
                    title=title,
                    link=link,
                    source="Hacker News",
                    published_at=published,
                    score=hit.get("points", 0),
                    comments=hit.get("num_comments", 0),
                )
            )
    return sorted(dedupe_items(items), key=lambda row: row["score"], reverse=True)[:limit]


def fetch_hacker_news(limit: int = 12, now: datetime | None = None) -> list[dict[str, Any]]:
    """Fetch recent AI stories from HN and enforce a strict rolling window."""
    current = _as_utc(now or utc_now())
    items: list[dict[str, Any]] = []
    try:
        story_ids = _get_json(
            "https://hacker-news.firebaseio.com/v0/topstories.json"
        )[:100]
        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = [executor.submit(_fetch_hn_story, sid, current) for sid in story_ids]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    items.append(result)
    except (requests.RequestException, TypeError) as exc:
        print(f"HN top stories failed: {exc}")

    if len(items) < limit:
        items.extend(_fetch_hn_algolia(current, limit * 2))
    items = dedupe_items(items)
    items.sort(key=lambda row: (row["score"], row["published_at"]), reverse=True)
    return items[:limit]


def _feed_datetime(entry: Any) -> datetime | None:
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(field)
        if value:
            return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    for field in ("published", "updated", "created"):
        value = entry.get(field)
        if not value:
            continue
        try:
            parsed = feedparser._parse_date(value)  # type: ignore[attr-defined]
            if parsed:
                return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            continue
    return None


def _clean_html(value: str, limit: int = 500) -> str:
    text = BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)[:limit]


def fetch_reddit_ai(limit: int = 8, now: datetime | None = None) -> list[dict[str, Any]]:
    """Fetch recent Reddit AI posts and exclude undated fallback entries."""
    current = _as_utc(now or utc_now())
    items: list[dict[str, Any]] = []
    for subreddit in ("artificial", "MachineLearning", "LocalLLaMA", "singularity"):
        try:
            data = _get_json(
                f"https://www.reddit.com/r/{subreddit}/hot.json?limit=15",
                params={"raw_json": 1},
            )
            for post in data.get("data", {}).get("children", []):
                row = post.get("data", {})
                if not row.get("title") or row.get("stickied"):
                    continue
                published = datetime.fromtimestamp(
                    int(row.get("created_utc", 0)), tz=timezone.utc
                )
                if not is_recent(published, current):
                    continue
                link = row.get("url", "")
                if link.startswith("/"):
                    link = f"https://www.reddit.com{link}"
                items.append(
                    _item(
                        title=row["title"],
                        link=link,
                        source=f"r/{subreddit}",
                        published_at=published,
                        score=row.get("score", 0),
                        comments=row.get("num_comments", 0),
                        summary=_clean_html(row.get("selftext", "")),
                    )
                )
        except requests.RequestException as exc:
            print(f"Reddit r/{subreddit} failed: {exc}")
    items = dedupe_items(items)
    items.sort(key=lambda row: (row["score"], row["published_at"]), reverse=True)
    return items[:limit]


def _load_feed_config() -> dict[str, Any]:
    with (REPO_ROOT / "config" / "feeds.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def fetch_rss_supplement(
    limit: int = 20, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Fetch configured RSS feeds and reject stale or undated entries."""
    current = _as_utc(now or utc_now())
    config = _load_feed_config()
    settings = config.get("settings", {})
    per_feed = int(settings.get("max_items_per_feed", 10))
    blacklist = tuple(word.lower() for word in settings.get("blacklist_keywords", []))
    items: list[dict[str, Any]] = []

    for feed in config.get("feeds", []):
        try:
            response = requests.get(
                feed["url"], headers=HEADERS, timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
            for entry in parsed.entries[:per_feed]:
                title = str(entry.get("title", "")).strip()
                link = str(entry.get("link", "")).strip()
                published = _feed_datetime(entry)
                lowered = title.lower()
                if (
                    not title
                    or not link
                    or not is_recent(published, current)
                    or any(word in lowered for word in blacklist)
                ):
                    continue
                summary = _clean_html(
                    entry.get("summary", "") or entry.get("description", "")
                )
                items.append(
                    _item(
                        title=title,
                        link=link,
                        source=feed["name"],
                        published_at=published,
                        summary=summary,
                        weight=feed.get("weight", 0),
                    )
                )
        except (requests.RequestException, KeyError, ValueError) as exc:
            print(f"RSS {feed.get('name', 'unknown')} failed: {exc}")

    items = dedupe_items(items)
    items.sort(key=lambda row: (row["weight"], row["published_at"]), reverse=True)
    return items[:limit]


def select_balanced(
    hn_items: list[dict[str, Any]],
    reddit_items: list[dict[str, Any]],
    rss_items: list[dict[str, Any]],
    max_total: int = MAX_TOTAL_ITEMS,
) -> list[dict[str, Any]]:
    """Keep source diversity, then fill unused capacity with remaining fresh items."""
    quotas = (
        (hn_items, min(8, max_total)),
        (reddit_items, min(5, max_total)),
        (rss_items, min(7, max_total)),
    )
    selected: list[dict[str, Any]] = []
    leftovers: list[dict[str, Any]] = []
    for group, quota in quotas:
        selected.extend(group[:quota])
        leftovers.extend(group[quota:])
    selected = dedupe_items(selected)
    if len(selected) < max_total:
        selected.extend(dedupe_items([*selected, *leftovers])[len(selected) : max_total])
    return dedupe_items(selected)[:max_total]


def _call_deepseek_json(prompt: str, attempts: int = 3) -> dict[str, Any]:
    if not DEEPSEEK_API_KEY:
        raise PipelineError("DEEPSEEK_API_KEY is missing; refusing to send an untranslated digest")

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "你是严谨的中文科技新闻编辑，只能依据输入内容，不得补充未经提供的事实。",
            },
            {"role": "user", "content": prompt},
        ],
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "max_tokens": 1800,
        "stream": False,
    }
    last_error = "unknown error"
    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(
                DEEPSEEK_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"].strip()
            return json.loads(content)
        except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            print(f"DeepSeek attempt {attempt}/{attempts} failed: {last_error}")
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    raise PipelineError(f"DeepSeek failed after {attempts} attempts: {last_error}")


def _contains_chinese(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value or ""))


def _parse_enrichment(data: dict[str, Any], expected: int) -> dict[int, tuple[str, str]]:
    parsed: dict[int, tuple[str, str]] = {}
    for row in data.get("items", []):
        try:
            index = int(row["index"])
            title = str(row["title_zh"]).strip()
            summary = str(row["summary_zh"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if 1 <= index <= expected and _contains_chinese(title) and _contains_chinese(summary):
            parsed[index] = (title, summary)
    return parsed


def enrich_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate and summarize every item, retrying partial batches individually."""
    enriched = [dict(item) for item in items]
    for start in range(0, len(enriched), 8):
        batch = enriched[start : start + 8]
        input_rows = [
            {
                "index": index,
                "source": item["source"],
                "title": item["title"],
                "excerpt": item.get("summary", "")[:500],
            }
            for index, item in enumerate(batch, 1)
        ]
        prompt = (
            "将输入新闻处理成中文。title_zh 为忠实简洁的中文标题，summary_zh 为35至70字中文摘要。"
            "摘要只能依据标题和 excerpt，信息不足时明确写‘据标题信息’。"
            "返回 JSON 对象，格式为 {\"items\":[{\"index\":1,\"title_zh\":\"...\","
            "\"summary_zh\":\"...\"}]}，条数与输入完全一致。输入：\n"
            + json.dumps(input_rows, ensure_ascii=False)
        )
        parsed = _parse_enrichment(_call_deepseek_json(prompt), len(batch))

        for local_index, item in enumerate(batch, 1):
            value = parsed.get(local_index)
            if value is None:
                single_prompt = (
                    "把这条新闻翻译并概括为中文。返回 JSON 对象，格式为 "
                    "{\"items\":[{\"index\":1,\"title_zh\":\"...\","
                    "\"summary_zh\":\"35至70字摘要\"}]}。只能依据输入：\n"
                    + json.dumps(input_rows[local_index - 1], ensure_ascii=False)
                )
                retry = _parse_enrichment(_call_deepseek_json(single_prompt), 1)
                value = retry.get(1)
            if value is None:
                raise PipelineError(f"Chinese enrichment missing for: {item['title']}")
            item["cn_title"], item["cn_summary"] = value
    return enriched


def validate_digest(
    items: list[dict[str, Any]], now: datetime | None = None
) -> None:
    """Fail closed before sending stale, duplicate, or untranslated content."""
    if not items:
        raise PipelineError("No fresh AI news found")
    if len(items) > MAX_TOTAL_ITEMS:
        raise PipelineError(f"Digest contains {len(items)} items, maximum is {MAX_TOTAL_ITEMS}")

    current = _as_utc(now or utc_now())
    urls: set[str] = set()
    titles: set[str] = set()
    for item in items:
        try:
            published = datetime.fromisoformat(item["published_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PipelineError(f"Invalid publication time: {item.get('title', '')}") from exc
        if not is_recent(published, current):
            raise PipelineError(f"Stale item escaped filtering: {item['title']}")
        canonical = canonicalize_url(item.get("link", ""))
        title_key = _title_key(item.get("title", ""))
        if canonical in urls or title_key in titles:
            raise PipelineError(f"Duplicate item escaped filtering: {item['title']}")
        urls.add(canonical)
        titles.add(title_key)
        if not _contains_chinese(item.get("cn_title", "")):
            raise PipelineError(f"Untranslated title: {item['title']}")
        if not _contains_chinese(item.get("cn_summary", "")):
            raise PipelineError(f"Missing Chinese summary: {item['title']}")


def _group_items(items: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    groups = [
        ("🔥 Hacker News", [item for item in items if item["source"] == "Hacker News"]),
        ("🔴 Reddit", [item for item in items if item["source"].startswith("r/")]),
        (
            "📰 RSS 资讯",
            [
                item
                for item in items
                if item["source"] != "Hacker News" and not item["source"].startswith("r/")
            ],
        ),
    ]
    return [(name, rows) for name, rows in groups if rows]


def render_markdown(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for group_name, rows in _group_items(items):
        lines.append(f"**{group_name}（{len(rows)}条）**")
        for index, item in enumerate(rows, 1):
            local_time = datetime.fromisoformat(item["published_at"]).astimezone(APP_TIMEZONE)
            lines.extend(
                [
                    f"\n**{index}. {item['cn_title']}**",
                    f"💡 {item['cn_summary']}",
                    f"🕐 {local_time:%m-%d %H:%M} | 📍 {item['source']}",
                ]
            )
            if item.get("points"):
                lines.append(
                    f"⬆️ {item['points']}分 | 💬 {item.get('comments', 0)}评论"
                )
            lines.append(f"🔗 [阅读原文]({item['link']})")
        lines.append("")
    return "\n".join(lines).strip()


def push_to_feishu(items: list[dict[str, Any]], generated_at: datetime) -> None:
    if not FEISHU_WEBHOOK:
        raise PipelineError("FEISHU_WEBHOOK is missing")
    local_date = generated_at.astimezone(APP_TIMEZONE)
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"🤖 AI 资讯日报（{local_date:%Y-%m-%d}，共{len(items)}条）",
                },
                "template": "purple",
            },
            "elements": [{"tag": "markdown", "content": render_markdown(items)}],
        },
    }
    response = requests.post(FEISHU_WEBHOOK, json=payload, timeout=30)
    response.raise_for_status()
    try:
        result = response.json()
    except ValueError as exc:
        raise PipelineError("Feishu returned a non-JSON response") from exc
    code = result.get("code", result.get("StatusCode", 0))
    if code not in (0, "0", None):
        raise PipelineError(f"Feishu rejected the message: {result}")
    print(f"Feishu push succeeded with {len(items)} items")


def save_output(items: list[dict[str, Any]], generated_at: datetime) -> Path:
    output_dir = REPO_ROOT / "output"
    output_dir.mkdir(exist_ok=True)
    target = output_dir / "ai-news.json"
    payload = {
        "generated_at": generated_at.isoformat(),
        "timezone": str(APP_TIMEZONE),
        "lookback_hours": LOOKBACK_HOURS,
        "item_count": len(items),
        "items": items,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def main() -> None:
    generated_at = utc_now()
    print(
        f"AI news pipeline started at {generated_at.isoformat()}, "
        f"window={LOOKBACK_HOURS}h, max={MAX_TOTAL_ITEMS}"
    )
    hn_items = fetch_hacker_news(12, generated_at)
    reddit_items = fetch_reddit_ai(8, generated_at)
    rss_items = fetch_rss_supplement(20, generated_at)
    print(
        f"Collected HN={len(hn_items)}, Reddit={len(reddit_items)}, RSS={len(rss_items)}"
    )

    selected = select_balanced(hn_items, reddit_items, rss_items)
    selected = enrich_items(selected)
    validate_digest(selected, generated_at)
    output_path = save_output(selected, generated_at)
    print(f"Validated {len(selected)} unique fresh items; saved {output_path}")
    push_to_feishu(selected, generated_at)


if __name__ == "__main__":
    main()
