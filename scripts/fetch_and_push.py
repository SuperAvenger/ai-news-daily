#!/usr/bin/env python3
"""
RSS 每日摘要 - Kimi (Moonshot) 翻译
"""

import json
import feedparser
import requests
from datetime import datetime
from pathlib import Path
import os
import re
import time

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

# Kimi (Moonshot) 配置
KIMI_API_KEY = os.environ.get('KIMI_API_KEY', '')
KIMI_ENDPOINT = "https://api.moonshot.cn/v1/chat/completions"
KIMI_MODEL = "kimi-k2-0905-preview"

DETAILED_LOGS = []


def load_feeds():
    config_path = Path(__file__).parent.parent / 'config' / 'feeds.json'
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def translate_with_kimi(title, content):
    """Kimi 翻译"""
    if not KIMI_API_KEY:
        print(f"  [Kimi] 缺少 API Key")
        return None
    
    prompt = f"用 50-80 字中文总结以下新闻，只输出中文内容：\n\n标题：{title}\n内容：{content[:400]}\n\n摘要："
    
    try:
        resp = requests.post(
            KIMI_ENDPOINT,
            headers={
                'Authorization': f'Bearer {KIMI_API_KEY}',
                'Content-Type': 'application/json'
            },
            json={
                'model': KIMI_MODEL,
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 200
            },
            timeout=30
        )
        
        print(f"  [Kimi] 状态码：{resp.status_code}")
        
        if resp.status_code == 200:
            result = resp.json()
            print(f"  [Kimi] 响应：{result}")
            if 'choices' in result and result['choices']:
                summary = result['choices'][0]['message']['content'].strip()
                print(f"  [Kimi] 摘要：{summary[:80]}...")
                # 简单清理，保留中文
                summary = summary.replace('"', '').replace("'", '').strip()
                # 只要包含中文就返回
                if re.search(r'[\u4e00-\u9fff]', summary):
                    return summary[:120]
                else:
                    print(f"  [Kimi] 警告：返回内容不含中文")
            else:
                print(f"  [Kimi] 警告：无 choices")
        else:
            print(f"  [Kimi] 错误：{resp.text[:200]}")
        return None
    except Exception as e:
        print(f"  [Kimi] 异常：{e}")
        return None


def ai_translate_and_summarize(title, content, index=0):
    """AI 翻译"""
    clean_content = re.sub(r'<[^>]+>', '', content or '')
    clean_content = re.sub(r'\s+', ' ', clean_content).strip()
    
    # 如果 content 为空或与 title 几乎相同，用 title 翻译
    use_title_only = (not clean_content) or (len(clean_content) < len(title) * 1.5 and title in clean_content)
    
    text = title + ' ' + clean_content
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    english_words = len(re.findall(r'[a-zA-Z]+', text))
    
    # 如果英文单词数远多于中文字符，需要翻译
    if english_words > chinese_chars * 3:
        print(f"  [判断] 英文{english_words} vs 中文{chinese_chars} → 需要翻译")
    else:
        print(f"  [判断] 英文{english_words} vs 中文{chinese_chars} → 已有中文")
        return clean_content[:120] + ('...' if len(clean_content) > 120 else '')
    
    # 准备翻译内容
    translate_content = title if use_title_only else clean_content[:600]
    
    log_entry = {
        'index': index,
        'title': title,
        'timestamp': datetime.now().isoformat()
    }
    
    result = translate_with_kimi(title, translate_content)
    if result:
        log_entry['model'] = KIMI_MODEL
        log_entry['success'] = True
        DETAILED_LOGS.append(log_entry)
        return result
    
    log_entry['model'] = 'fallback'
    log_entry['success'] = False
    DETAILED_LOGS.append(log_entry)
    return f"[EN] {title}"


def is_quality_article(title, summary, config):
    if len(title) < config.get('min_title_length', 8):
        return False
    for keyword in config.get('blacklist_keywords', []):
        if keyword.lower() in (title + ' ' + (summary or '')).lower():
            return False
    return True


def match_keywords(title, summary, keywords):
    if not keywords:
        return 1
    text = (title + ' ' + (summary or '')).lower()
    matches = sum(1 for kw in keywords if kw.lower() in text)
    return 0.5 + (matches / len(keywords)) * 0.5 if matches >= 1 else 0


def fetch_feed_with_headers(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return feedparser.parse(resp.content)
    except Exception as e:
        print(f"  ❌ 抓取失败：{e}")
        return feedparser.parse(url)


def fetch_feeds(feeds_config):
    articles = []
    settings = feeds_config.get('settings', {})
    total_fetched = 0
    category_stats = {}
    
    print(f"\n🤖 翻译配置:")
    print(f"   模型：{KIMI_MODEL}")
    print(f"   API Key: {'✅' if KIMI_API_KEY else '❌'}")
    print("=" * 70)
    
    for feed_config in feeds_config['feeds']:
        try:
            print(f"\n📰 抓取：{feed_config['name']}")
            feed = fetch_feed_with_headers(feed_config['url'])
            
            if not feed.entries:
                print(f"  ⚠️ 无内容")
                continue
            
            weight = feed_config.get('weight', 5)
            keywords = feed_config.get('keywords', [])
            category = feed_config['category']
            
            if category not in category_stats:
                category_stats[category] = {'fetched': 0, 'passed': 0, 'translated': 0, 'english': 0}
            
            article_index = 0
            
            for entry in feed.entries[:settings.get('max_items_per_feed', 15)]:
                total_fetched += 1
                category_stats[category]['fetched'] += 1
                article_index += 1
                
                title = entry.title
                summary = entry.get('summary', '')
                
                if not is_quality_article(title, summary, settings):
                    continue
                
                match_score = match_keywords(title, summary, keywords)
                
                is_en = feed_config.get('language', 'zh') == 'en'
                if is_en:
                    print(f"  🌐 [{article_index:2d}] {title[:40]}...")
                else:
                    print(f"  📝 [{article_index:2d}] {title[:40]}...")
                
                brief = ai_translate_and_summarize(title, summary, article_index)
                
                if brief.startswith('[EN]'):
                    category_stats[category]['english'] += 1
                    if is_en:
                        print(f"      ⚠️ 英文原文")
                else:
                    category_stats[category]['translated'] += 1
                    print(f"      ✅ {brief[:40]}...")
                
                time.sleep(0.1)
                
                articles.append({
                    'category': category,
                    'source': feed_config['name'],
                    'title': title,
                    'link': entry.link,
                    'summary': brief,
                    'weight': weight,
                    'match_score': match_score,
                    'score': weight,
                    'published': entry.get('published_parsed') or entry.get('updated_parsed'),
                })
                category_stats[category]['passed'] += 1
                
        except Exception as e:
            print(f"❌ 抓取失败 {feed_config['name']}: {e}")
    
    print("\n" + "=" * 70)
    print("📊 分类统计:")
    for cat, stats in category_stats.items():
        print(f"{cat}: 抓取{stats['fetched']} → 通过{stats['passed']} → 翻译{stats['translated']} → 英文{stats['english']}")
    
    success = sum(1 for log in DETAILED_LOGS if log.get('success'))
    failed = sum(1 for log in DETAILED_LOGS if not log.get('success'))
    
    print(f"\n📝 API 统计:")
    print(f"   翻译成功：{success} 次")
    print(f"   降级英文：{failed} 次")
    
    log_file = Path(__file__).parent.parent / 'detailed_api_logs.json'
    with open(log_file, 'w', encoding='utf-8') as f:
        json.dump(DETAILED_LOGS, f, ensure_ascii=False, indent=2)
    print(f"\n📄 日志已保存：{log_file}")
    
    by_category = {}
    for article in articles:
        by_category.setdefault(article['category'], []).append(article)
    
    def sort_key(article):
        time_val = 0
        if article.get('published'):
            try:
                time_val = time.mktime(article['published'])
            except:
                pass
        return (article['score'], time_val)
    
    final_articles = []
    for cat, items in by_category.items():
        items.sort(key=sort_key, reverse=True)
        final_articles.extend(items[:15])
    
    print(f"\n✅ 总计：{total_fetched} → {len(final_articles)}")
    return final_articles


def format_message(articles):
    if not articles:
        return "今日暂无内容"
    
    by_category = {}
    for article in articles:
        by_category.setdefault(article['category'], []).append(article)
    
    sorted_categories = sorted(by_category.items(), key=lambda x: len(x[1]), reverse=True)
    
    lines = [
        f"📰 **每日新闻摘要** ({datetime.now().strftime('%Y年%m月%d日')})",
        f"共 **{len(articles)}** 条",
        "=" * 50,
        ""
    ]
    
    for category, items in sorted_categories:
        lines.append(f"\n{category} ({len(items)}条)")
        lines.append("-" * 40)
        
        for i, item in enumerate(items, 1):
            # 如果标题是英文但摘要是中文，用摘要前 40 字作为显示标题
            display_title = item['title']
            if re.search(r'[\u4e00-\u9fff]', item['summary']) and not re.search(r'[\u4e00-\u9fff]', item['title']):
                clean_summary = re.sub(r'[📰💡]', '', item['summary']).strip()
                display_title = clean_summary[:40] + ('...' if len(clean_summary) > 40 else '')
            
            lines.append(f"\n**{i:2d}. {display_title}**")
            lines.append(f"📍 {item['source']}")
            lines.append(f"💡 {item['summary']}")
            lines.append(f"🔗 [阅读原文]({item['link']})")
    
    lines.append("\n" + "=" * 50)
    return '\n'.join(lines)


def push_to_feishu(message):
    webhook = os.environ.get('FEISHU_WEBHOOK')
    if not webhook:
        print("\n⚠️ 未配置飞书 Webhook")
        return
    
    try:
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": "📰 每日新闻摘要"}, "template": "blue"},
                "elements": [{"tag": "markdown", "content": message}]
            }
        }
        resp = requests.post(webhook, json=payload, timeout=30)
        print(f"\n飞书推送：{resp.status_code}")
        if resp.status_code == 200:
            print("✅ 推送成功！")
        else:
            print(f"❌ 推送失败：{resp.text[:100]}")
    except Exception as e:
        print(f"推送失败：{e}")


def main():
    print("=" * 70)
    print("🚀 RSS 智能摘要 - Kimi (Moonshot)")
    print("=" * 70)
    
    config = load_feeds()
    print(f"📋 {len(config['feeds'])} 个 RSS 源")
    
    articles = fetch_feeds(config)
    if not articles:
        print("⚠️ 没有文章")
        return
    
    message = format_message(articles)
    push_to_feishu(message)
    
    print("\n" + "=" * 70)
    print("✅ 完成")


if __name__ == '__main__':
    main()
