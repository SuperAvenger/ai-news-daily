# AI 资讯日报

每天抓取过去 24 小时内的 AI 资讯，生成中文标题和摘要，经完整性校验后推送到飞书。

## 数据源

1. Hacker News 热门榜和 Algolia 近期索引
2. Reddit 的 AI、Machine Learning、LocalLLaMA、singularity 社区
3. `config/feeds.json` 中配置的官方博客、科技媒体和 arXiv RSS

所有来源统一执行发布时间过滤和 URL 规范化去重。缺少发布时间的内容不会进入日报。

## 质量控制

1. 严格限制在滚动 24 小时窗口内
2. 移除跟踪参数后进行跨来源去重
3. DeepSeek 批量生成失败时自动重试缺失条目
4. 中文标题或摘要缺失时停止推送
5. 推送前复核时间、重复项、中文内容和实际条数
6. 飞书 HTTP 与业务返回码均成功才视为推送完成

## GitHub Secrets

1. `FEISHU_WEBHOOK`，飞书群机器人 Webhook
2. `DEEPSEEK_API_KEY`，DeepSeek API Key

## 可选环境变量

1. `DEEPSEEK_MODEL`，默认 `deepseek-v4-flash`
2. `APP_TIMEZONE`，默认 `Asia/Shanghai`
3. `LOOKBACK_HOURS`，默认 `24`
4. `MAX_TOTAL_ITEMS`，默认 `20`

## 本地验证

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/fetch_and_push.py
```
