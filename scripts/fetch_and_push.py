#!/usr/bin/env python3
"""
AI 资讯日报 - 按热度排序版
主源: Hacker News + Reddit (有真实投票数据)
辅助: RSS 源 (补充)
翻译/摘要: DeepSeek
"""
import os
import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

FEISHU_WEBHOOK = os.environ.get('FEISHU_WEBHOOK', '')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
DEEPSEEK_ENDPOINT = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# ── DeepSeek 统一调用 ──────────────────────────────────────

def _call_deepseek(prompt: str, max_tokens: int = 600) -> str:
    """统一调用 DeepSeek API，返回文本。失败返回空字符串并打印原因。"""
    if not DEEPSEEK_API_KEY:
        print("⚠️ DEEPSEEK_API_KEY not set, skip LLM call")
        return ""
    try:
        resp = requests.post(
            DEEPSEEK_ENDPOINT,
            headers={'Authorization': f'Bearer {DEEPSEEK_API_KEY}', 'Content-Type': 'application/json'},
            json={'model': DEEPSEEK_MODEL, 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': max_tokens},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()['choices'][0]['message']['content'].strip()
        else:
            print(f"DeepSeek HTTP {resp.status_code}: {resp.text[:200]}")
            return ""
    except Exception as e:
        print(f"DeepSeek call failed: {e}")
        return ""


def _parse_numbered(text: str) -> list[str]:
    """从 LLM 输出中提取编号列表"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    numbered = []
    for line in lines:
        match = re.match(r'^\d+[\.\)、]\s*(.+)', line)
        if match:
            numbered.append(match.group(1))
    return numbered or lines


# ── 数据源采集 ─────────────────────────────────────────────

def fetch_hacker_news(limit=20):
    """Hacker News 热门 AI 帖子 (按 points 排序)"""
    items = []
    try:
        resp = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            timeout=15,
        )
        if resp.status_code == 200:
            story_ids = resp.json()[:50]
            for sid in story_ids:
                try:
                    sr = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=10)
                    if sr.status_code == 200:
                        d = sr.json()
                        title = d.get("title", "")
                        ai_keywords = [
                            "ai ", " ai", "ai-", "-ai", "ai-powered", "ai driven",
                            "llm", "gpt", "claude", "gemini", "deepseek", "openai", "anthropic",
                            "chatgpt", "copilot", "machine learning", "deep learning",
                            "neural", "transformer", "large language model", "foundation model",
                            "diffusion", "stable diffusion", "midjourney", "dall-e",
                            "artificial intelligence", "generative ai", "gen ai",
                        ]
                        title_lower = title.lower()
                        if any(kw in title_lower for kw in ai_keywords) and d.get("url"):
                            items.append({
                                "title": title,
                                "link": d["url"],
                                "source": "Hacker News",
                                "points": d.get("score", 0),
                                "comments": d.get("descendants", 0),
                                "score": d.get("score", 0),
                                "date": "",
                            })
                except:
                    continue
                if len(items) >= limit:
                    break
    except Exception as e:
        print(f"HN fetch failed: {e}, trying Algolia...")
        try:
            resp = requests.get(
                "https://hn.algolia.com/api/v1/search",
                params={"query": "AI LLM GPT Claude", "tags": "story", "hitsPerPage": 30, "numericFilters": "points>10"},
                timeout=15,
            )
            for hit in resp.json().get("hits", []):
                if hit.get("title") and hit.get("url"):
                    items.append({
                        "title": hit["title"],
                        "link": hit["url"],
                        "source": "Hacker News",
                        "points": hit.get("points", 0),
                        "comments": hit.get("num_comments", 0),
                        "score": hit.get("points", 0),
                        "date": hit.get("created_at", "")[:16],
                    })
        except Exception as e2:
            print(f"Algolia also failed: {e2}")

    items.sort(key=lambda x: x["score"], reverse=True)
    return items[:limit]


def fetch_reddit_ai(limit=15):
    """Reddit AI 热门帖子 (按 hot 排序)"""
    items = []
    subreddits = ["artificial", "MachineLearning", "LocalLLaMA", "singularity"]
    for sub in subreddits:
        try:
            resp = requests.get(
                f"https://www.reddit.com/r/{sub}/hot.json?limit=10",
                headers={"User-Agent": "Mozilla/5.0 (compatible; ai-news-bot/1.0)"},
                timeout=15,
            )
            if resp.status_code != 200:
                print(f"  Reddit r/{sub}: JSON {resp.status_code}, trying RSS...")
                resp = requests.get(
                    f"https://www.reddit.com/r/{sub}/hot.rss?limit=10",
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=15,
                )
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for entry in soup.find_all("entry")[:8]:
                        title = entry.find("title")
                        link = entry.find("link")
                        if title and link:
                            href = link.get("href", "")
                            items.append({
                                "title": title.get_text(strip=True)[:120],
                                "link": href,
                                "source": f"r/{sub}",
                                "points": 0,
                                "comments": 0,
                                "score": 0,
                                "date": "",
                            })
                continue

            data = resp.json()
            for post in data.get("data", {}).get("children", []):
                d = post.get("data", {})
                if d.get("title") and not d.get("stickied"):
                    url = d.get("url", "")
                    if url.startswith("/r/"):
                        url = f"https://www.reddit.com{url}"
                    score = d.get("score", 0)
                    items.append({
                        "title": d["title"][:120],
                        "link": url,
                        "source": f"r/{sub}",
                        "points": score,
                        "comments": d.get("num_comments", 0),
                        "score": score,
                        "date": datetime.fromtimestamp(d.get("created_utc", 0)).strftime("%m-%d %H:%M"),
                    })
        except Exception as e:
            print(f"Reddit ({sub}) failed: {e}")

    items.sort(key=lambda x: x["score"], reverse=True)
    return items[:limit]


def fetch_rss_supplement(limit=10):
    """RSS 补充源 (带摘要)"""
    feeds = [
        ("https://techcrunch.com/category/artificial-intelligence/feed/", "TechCrunch"),
        ("https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "The Verge"),
        ("https://blog.google/technology/ai/rss/", "Google AI"),
    ]
    items = []
    for url, name in feeds:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")
            for item in soup.find_all("item")[:5]:
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description") or item.find("summary") or item.find("content:encoded")
                if title and link:
                    href = link.get_text(strip=True) or (link.next_sibling.strip() if link.next_sibling else "")
                    summary = ""
                    if desc:
                        raw = desc.get_text(strip=True)
                        summary = BeautifulSoup(raw, "html.parser").get_text(strip=True)[:200]

                    items.append({
                        "title": title.get_text(strip=True),
                        "link": href,
                        "source": name,
                        "summary": summary,
                        "score": 0,
                        "date": "",
                    })
        except Exception as e:
            print(f"RSS ({name}) failed: {e}")
    return items[:limit]


# ── 翻译 + 摘要 ───────────────────────────────────────────

def translate_batch(titles):
    """批量翻译标题为中文 — 失败时保留原标题"""
    if not DEEPSEEK_API_KEY:
        print("⚠️ No DEEPSEEK_API_KEY, titles will remain in English")
        return titles

    all_translated = []
    for i in range(0, len(titles), 10):
        batch = titles[i:i+10]
        numbered = "\n".join(f"{j+1}. {t}" for j, t in enumerate(batch))
        prompt = f"""将以下英文AI新闻标题翻译成简洁中文，保留编号，只输出翻译结果，不要加任何解释：
{numbered}"""

        content = _call_deepseek(prompt, max_tokens=600)
        if content:
            parsed = _parse_numbered(content)
            all_translated.extend(parsed)
        else:
            all_translated.extend(batch)  # 失败保留原标题
        time.sleep(0.5)

    while len(all_translated) < len(titles):
        all_translated.append(titles[len(all_translated)])
    return all_translated[:len(titles)]


def summarize_items(items):
    """为每条新闻生成中文摘要 (30-50字) — 基于标题生成，失败时截取标题"""
    if not items:
        return []

    if not DEEPSEEK_API_KEY:
        print("⚠️ No DEEPSEEK_API_KEY, using truncated titles as summaries")
        return [_truncate(item['title']) for item in items]

    all_summaries = []
    for i in range(0, len(items), 5):
        batch = items[i:i+5]
        numbered = "\n".join(f"{j+1}. {item['title']}" for j, item in enumerate(batch))
        prompt = f"""为以下 AI/科技新闻各写一句 30-50 字的中文摘要，说明这条新闻的核心内容。保留编号，只输出摘要，不要加解释：
{numbered}"""

        content = _call_deepseek(prompt, max_tokens=600)
        if content:
            parsed = _parse_numbered(content)
            all_summaries.extend(parsed)
        # 补齐缺失
        while len(all_summaries) < i + len(batch):
            all_summaries.append(_truncate(batch[len(all_summaries) - i]['title']))
        time.sleep(0.5)

    return all_summaries[:len(items)]


def _truncate(title: str) -> str:
    """截取标题作为兜底摘要"""
    clean = re.sub(r'^Show HN:\s*', '', title)
    return clean[:50] + ("..." if len(clean) > 50 else "")


# ── 飞书推送 ──────────────────────────────────────────────

def push_to_feishu(hn_items, reddit_items, rss_items):
    """推送飞书"""
    if not FEISHU_WEBHOOK:
        print("FEISHU_WEBHOOK not set")
        return

    lines = []

    # Hacker News
    if hn_items:
        lines.append(f"**🔥 Hacker News 热门** ({len(hn_items)}条)")
        for i, item in enumerate(hn_items, 1):
            title = item.get('cn_title', item['title'])
            summary = item.get('cn_summary', '')
            lines.append(f"\n**{i}. {title}**")
            if summary:
                lines.append(f"💡 {summary}")
            lines.append(f"⬆️ {item.get('points', 0)}分 | 💬 {item.get('comments', 0)}评论")
            lines.append(f"🔗 [阅读原文]({item['link']})")

    # Reddit
    if reddit_items:
        lines.append(f"\n**🔴 Reddit 热门** ({len(reddit_items)}条)")
        for i, item in enumerate(reddit_items, 1):
            title = item.get('cn_title', item['title'])
            summary = item.get('cn_summary', '')
            lines.append(f"\n**{i}. {title}**")
            if summary:
                lines.append(f"💡 {summary}")
            lines.append(f"⬆️ {item.get('points', 0)}分 | 📍 {item['source']}")
            lines.append(f"🔗 [阅读原文]({item['link']})")

    # RSS 补充
    if rss_items:
        lines.append(f"\n**📰 更多资讯** ({len(rss_items)}条)")
        for i, item in enumerate(rss_items, 1):
            title = item.get('cn_title', item['title'])
            summary = item.get('cn_summary', '')
            lines.append(f"\n**{i}. {title}**")
            if summary:
                lines.append(f"💡 {summary}")
            lines.append(f"📍 {item['source']}")
            lines.append(f"🔗 [阅读原文]({item['link']})")

    message = "\n".join(lines)

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"🤖 AI 资讯日报 ({datetime.now().strftime('%m/%d')})"},
                "template": "purple",
            },
            "elements": [{"tag": "markdown", "content": message}],
        },
    }

    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=30)
        print(f"飞书推送: {resp.status_code}")
        if resp.status_code == 200:
            print("✅ 推送成功")
        else:
            print(f"❌ {resp.text[:200]}")
    except Exception as e:
        print(f"推送失败: {e}")


# ── 主流程 ─────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("🤖 AI 资讯日报 (热度排序版)")
    print("=" * 60)

    # 1. 采集
    print("\n>>> Hacker News...")
    hn_items = fetch_hacker_news(15)
    print(f"  获取 {len(hn_items)} 条")

    print("\n>>> Reddit...")
    reddit_items = fetch_reddit_ai(10)
    print(f"  获取 {len(reddit_items)} 条")

    print("\n>>> RSS 补充...")
    rss_items = fetch_rss_supplement(5)
    print(f"  获取 {len(rss_items)} 条")

    all_items = hn_items + reddit_items + rss_items
    if not all_items:
        print("⚠️ 无数据，退出")
        return

    # 2. 翻译标题
    all_titles = [i['title'] for i in all_items]
    print(f"\n>>> 翻译 {len(all_titles)} 个标题...")
    translated_titles = translate_batch(all_titles)

    idx = 0
    for item in all_items:
        item['cn_title'] = translated_titles[idx] if idx < len(translated_titles) else item['title']
        idx += 1

    # 3. 生成摘要
    print(f">>> 生成 {len(all_items)} 条摘要...")
    summaries = summarize_items(all_items)

    for i, item in enumerate(all_items):
        item['cn_summary'] = summaries[i] if i < len(summaries) else ''

    # 4. 推送
    print("\n>>> 推送到飞书...")
    push_to_feishu(hn_items, reddit_items, rss_items)

    # 5. 保存
    output_dir = Path(__file__).parent.parent / "output"
    output_dir.mkdir(exist_ok=True)
    with open(output_dir / "ai-news.json", "w", encoding="utf-8") as f:
        json.dump({"update_time": datetime.now().isoformat(), "items": all_items}, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 完成，共 {len(all_items)} 条")


if __name__ == "__main__":
    main()
