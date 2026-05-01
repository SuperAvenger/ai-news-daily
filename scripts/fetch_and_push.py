#!/usr/bin/env python3
"""
AI 资讯日报 - 按热度排序版
主源: Hacker News + Reddit (有真实投票数据)
辅助: RSS 源 (补充)
翻译: DeepSeek
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


# ── 数据源采集 ─────────────────────────────────────────────

def fetch_hacker_news(limit=20):
    """Hacker News 热门 AI 帖子 (按 points 排序)"""
    items = []
    try:
        # 搜索 AI 相关，按热度排序
        resp = requests.get(
            "https://hn.algolia.com/api/v1/search",
            params={
                "query": "AI OR LLM OR GPT OR Claude OR Gemini OR DeepSeek OR OpenAI OR Anthropic",
                "tags": "story",
                "hitsPerPage": 30,
                "numericFilters": "points>10",  # 至少10个赞
            },
            timeout=15,
        )
        data = resp.json()
        for hit in data.get("hits", []):
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
    except Exception as e:
        print(f"HN fetch failed: {e}")
    
    # 按热度排序
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
    """RSS 补充源 (不排序，作为补充)"""
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
                if title and link:
                    href = link.get_text(strip=True) or (link.next_sibling.strip() if link.next_sibling else "")
                    items.append({
                        "title": title.get_text(strip=True),
                        "link": href,
                        "source": name,
                        "score": 0,
                        "date": "",
                    })
        except Exception as e:
            print(f"RSS ({name}) failed: {e}")
    return items[:limit]


# ── DeepSeek 翻译 ──────────────────────────────────────────

def translate_batch(titles):
    """批量翻译标题 (减少 API 调用)"""
    if not DEEPSEEK_API_KEY:
        return titles
    
    # 每批最多 10 个
    all_translated = []
    for i in range(0, len(titles), 10):
        batch = titles[i:i+10]
        numbered = "\n".join(f"{j+1}. {t}" for j, t in enumerate(batch))
        prompt = f"""将以下英文AI新闻标题翻译成中文，保留编号，只输出翻译结果：
{numbered}"""
        
        try:
            resp = requests.post(
                DEEPSEEK_ENDPOINT,
                headers={
                    'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': DEEPSEEK_MODEL,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': 500,
                },
                timeout=30,
            )
            if resp.status_code == 200:
                result = resp.json()
                content = result['choices'][0]['message']['content'].strip()
                # 解析编号
                for line in content.split('\n'):
                    line = line.strip()
                    m = re.match(r'^\d+[\.\)、]\s*(.+)', line)
                    if m:
                        all_translated.append(m.group(1))
                    elif line:
                        all_translated.append(line)
        except Exception as e:
            print(f"Translate batch failed: {e}")
            all_translated.extend(batch)
        
        time.sleep(0.5)
    
    # 补齐
    while len(all_translated) < len(titles):
        all_translated.append(titles[len(all_translated)])
    
    return all_translated[:len(titles)]


def translate_summaries(titles, summaries):
    """批量翻译摘要"""
    if not DEEPSEEK_API_KEY:
        return summaries
    
    all_translated = []
    for i in range(0, len(summaries), 5):
        batch = summaries[i:i+5]
        numbered = "\n".join(f"{j+1}. {t[:200]}" for j, t in enumerate(batch))
        prompt = f"""将以下英文AI新闻摘要翻译成中文（每条50-80字），保留编号，只输出翻译：
{numbered}"""
        
        try:
            resp = requests.post(
                DEEPSEEK_ENDPOINT,
                headers={
                    'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': DEEPSEEK_MODEL,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': 800,
                },
                timeout=30,
            )
            if resp.status_code == 200:
                result = resp.json()
                content = result['choices'][0]['message']['content'].strip()
                for line in content.split('\n'):
                    line = line.strip()
                    m = re.match(r'^\d+[\.\)、]\s*(.+)', line)
                    if m:
                        all_translated.append(m.group(1))
                    elif line:
                        all_translated.append(line)
        except Exception as e:
            print(f"Translate summaries failed: {e}")
            all_translated.extend(batch)
        
        time.sleep(0.5)
    
    while len(all_translated) < len(summaries):
        all_translated.append(summaries[len(all_translated)])
    
    return all_translated[:len(summaries)]


# ── 飞书推送 ──────────────────────────────────────────────

def push_to_feishu(hn_items, reddit_items, rss_items):
    """推送飞书"""
    if not FEISHU_WEBHOOK:
        print("FEISHU_WEBHOOK not set")
        return

    lines = []
    total = 0

    # Hacker News (最热门)
    if hn_items:
        lines.append(f"**🔥 Hacker News 热门** ({len(hn_items)}条)")
        for i, item in enumerate(hn_items, 1):
            total += 1
            points = item.get('points', 0)
            title = item.get('cn_title', item['title'])
            lines.append(f"\n**{i}. {title}**")
            lines.append(f"⬆️ {points}分 | 💬 {item.get('comments',0)}评论")
            lines.append(f"🔗 [阅读原文]({item['link']})")

    # Reddit (第二热门)
    if reddit_items:
        lines.append(f"\n**🔴 Reddit 热门** ({len(reddit_items)}条)")
        for i, item in enumerate(reddit_items, 1):
            total += 1
            points = item.get('points', 0)
            title = item.get('cn_title', item['title'])
            lines.append(f"\n**{i}. {title}**")
            lines.append(f"⬆️ {points}分 | 📍 {item['source']}")
            lines.append(f"🔗 [阅读原文]({item['link']})")

    # RSS 补充
    if rss_items:
        lines.append(f"\n**📰 更多资讯** ({len(rss_items)}条)")
        for i, item in enumerate(rss_items, 1):
            total += 1
            title = item.get('cn_title', item['title'])
            lines.append(f"\n**{i}. {title}**")
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

    # 2. 批量翻译标题
    all_titles = [i['title'] for i in hn_items + reddit_items + rss_items]
    print(f"\n>>> 翻译 {len(all_titles)} 个标题...")
    translated = translate_batch(all_titles)

    idx = 0
    for item in hn_items:
        item['cn_title'] = translated[idx] if idx < len(translated) else item['title']
        idx += 1
    for item in reddit_items:
        item['cn_title'] = translated[idx] if idx < len(translated) else item['title']
        idx += 1
    for item in rss_items:
        item['cn_title'] = translated[idx] if idx < len(translated) else item['title']
        idx += 1

    # 3. 推送
    print("\n>>> 推送到飞书...")
    push_to_feishu(hn_items, reddit_items, rss_items)

    # 4. 保存
    output_dir = Path(__file__).parent.parent / "output"
    output_dir.mkdir(exist_ok=True)
    all_items = hn_items + reddit_items + rss_items
    with open(output_dir / "ai-news.json", "w", encoding="utf-8") as f:
        json.dump({"update_time": datetime.now().isoformat(), "items": all_items}, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 完成，共 {len(hn_items) + len(reddit_items) + len(rss_items)} 条")


if __name__ == "__main__":
    main()
