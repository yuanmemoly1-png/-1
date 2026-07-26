#!/usr/bin/env python3
"""每日新闻自动简报：新闻联播 + AI/科技/财经前沿 → LLM 摘要与深度解读 → 推送到微信。

环境变量：
    LLM_API_KEY          LLM API key（可选；不设置则推送原始内容清单）
    LLM_BASE_URL         OpenAI 兼容接口地址，默认 https://api.deepseek.com
    LLM_MODEL            模型名，默认 deepseek-v4-pro
    WXPUSHER_APP_TOKEN   WxPusher 应用 appToken（三种渠道任选其一）
    WXPUSHER_UID         WxPusher 用户 UID，多个用英文逗号分隔
    SERVERCHAN_SENDKEY   Server酱 SendKey
    PUSHPLUS_TOKEN       PushPlus token
    MAX_WAIT_MINUTES     当天节目尚未更新时的最长等待分钟数，默认 40
"""

import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests

CCTV_API = (
    "https://api.cntv.cn/NewVideo/getVideoListByColumn"
    "?id=TOPC1451528971114112&n=10&sort=desc&p=1&mode=0&serviceId=tvcctv"
)

# 前沿新闻 RSS 源：按需增删，个别源失效不影响整体推送
FEEDS = {
    "AI 前沿": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "科技动态": "https://36kr.com/feed",
    "财经要闻": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
}
ITEMS_PER_FEED = 10    # 每个源送给 LLM 筛选的最大条目数
FEED_FRESH_HOURS = 48  # 只保留最近的新闻；无时间信息的条目直接保留

BEIJING = timezone(timedelta(hours=8))
MAX_WAIT_MINUTES = int(os.getenv("MAX_WAIT_MINUTES", "40"))
RETRY_INTERVAL_SECONDS = 300

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


# ---------- 新闻联播 ----------

def fetch_episode(date_str: str):
    """从央视网接口找当天 19:00 档《新闻联播》，返回 (title, url, brief)；未找到返回 None。"""
    resp = requests.get(CCTV_API, headers=UA, timeout=20)
    resp.raise_for_status()
    items = resp.json().get("data", {}).get("list", [])
    for it in items:
        m = re.search(r"(\d{8})\s+(\d{2}):(\d{2})", it.get("title", ""))
        if m and m.group(1) == date_str and m.group(2) == "19":
            return it["title"], it.get("url", ""), it.get("brief", "").strip()
    return None


# ---------- 前沿新闻 RSS ----------

def strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()


def fetch_feed_items() -> dict[str, list[dict]]:
    """抓取各 RSS 源近两天的新条目，返回 {类别: [{title, summary, link}]}。"""
    result = {}
    now = datetime.now(timezone.utc)
    for cat, url in FEEDS.items():
        try:
            feed = feedparser.parse(url, request_headers=UA)
            items = []
            for e in feed.entries:
                title = (e.get("title") or "").strip()
                if not title:
                    continue
                pub = e.get("published_parsed") or e.get("updated_parsed")
                if pub:
                    dt = datetime(*pub[:6], tzinfo=timezone.utc)
                    if now - dt > timedelta(hours=FEED_FRESH_HOURS):
                        continue
                summary = strip_html(e.get("summary") or "")[:200]
                items.append({"title": title, "summary": summary,
                              "link": (e.get("link") or "").strip()})
                if len(items) >= ITEMS_PER_FEED:
                    break
            if items:
                result[cat] = items
            print(f"{cat}: {len(items)} 条候选")
        except Exception as e:
            print(f"{cat} 抓取失败（跳过）: {e}")
    return result


# ---------- LLM ----------

def call_llm(prompt: str) -> str | None:
    """调用 OpenAI 兼容接口；未配置 key 时返回 None。"""
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return None
    base = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("LLM_MODEL", "deepseek-v4-pro")
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def summarize_xwlb(date_str: str, brief: str) -> str | None:
    prompt = (
        f"以下是{date_str}《新闻联播》的主要内容清单。\n"
        "请整理成一份适合微信阅读的每日简报，分两部分：\n\n"
        "【第一部分：今日要闻速览】\n"
        "1. 分四个板块：今日头条、国内要闻、国际动态、联播快讯；\n"
        "2. 每条一行，用简短的一两句话概括要点，保留关键数据。\n\n"
        "【第二部分：深度解读】\n"
        "1. 从当天新闻中挑出 2~3 条最有分量的，逐条展开分析；\n"
        "2. 每条分析包括：这件事的背景和来龙去脉、释放的政策信号或趋势、"
        "对普通人/行业/国际格局可能产生的影响；\n"
        "3. 要有自己的判断和洞察，不要复述新闻原文；拿不准的推断要注明是推测；\n"
        "4. 如有可挖掘的关联，指出几条新闻之间的内在联系（如同一政策主线的不同侧面）。\n\n"
        "整体要求：总长度 800 字以内，输出 Markdown，不要额外解释。\n\n"
        f"{brief}"
    )
    return call_llm(prompt)


def summarize_feeds(date_str: str, items_by_cat: dict[str, list[dict]]) -> str | None:
    raw = []
    for cat, items in items_by_cat.items():
        raw.append(f"【{cat}】")
        for i, it in enumerate(items, 1):
            line = f"{i}. {it['title']}"
            if it["summary"]:
                line += f" —— {it['summary']}"
            if it["link"]:
                line += f"（链接: {it['link']}）"
            raw.append(line)
    prompt = (
        f"以下是{date_str}从多个科技、财经媒体抓取的新闻标题和摘要（含英文源）。\n"
        "请整理成中文简报，要求：\n"
        "1. 按原类别分板块；\n"
        "2. 每个类别挑选 3~5 条最重要、最有信息量的，每条一行，格式为：\n"
        "   - [中文标题概括](该条的原始链接) —— 一句要点或影响点评；\n"
        "3. 链接必须使用我在输入中给出的原始链接，原样复制，不要编造、不要修改；\n"
        "4. 英文新闻标题翻译成中文，保留关键公司名、人名和数据；\n"
        "5. 末尾加一段「今日主线」：用两三句话点出跨板块的趋势或关联，要有判断；\n"
        "6. 总长度 500 字以内，输出 Markdown，不要额外解释。\n\n"
        + "\n".join(raw)
    )
    return call_llm(prompt)


def feeds_fallback(items_by_cat: dict[str, list[dict]]) -> str:
    """LLM 不可用时的兜底：直接列出各源标题（带链接）。"""
    lines = []
    for cat, items in items_by_cat.items():
        lines.append(f"**{cat}**")
        for it in items[:5]:
            if it["link"]:
                lines.append(f"- [{it['title']}]({it['link']})")
            else:
                lines.append(f"- {it['title']}")
    return "\n".join(lines)


# ---------- 推送 ----------

def push_wxpusher(app_token: str, uids: list[str], title: str, content: str) -> None:
    resp = requests.post(
        "https://wxpusher.zjiecode.com/api/send/message",
        json={
            "appToken": app_token,
            "content": content,
            "summary": title,
            "contentType": 3,  # markdown
            "uids": uids,
        },
        timeout=20,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 1000:
        raise RuntimeError(f"WxPusher 推送失败: {result}")


def push_serverchan(sendkey: str, title: str, content: str) -> None:
    resp = requests.post(
        f"https://sctapi.ftqq.com/{sendkey}.send",
        data={"title": title, "desp": content},
        timeout=20,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"Server酱推送失败: {result}")


def push_pushplus(token: str, title: str, content: str) -> None:
    resp = requests.post(
        "https://www.pushplus.plus/send",
        json={"token": token, "title": title, "content": content, "template": "markdown"},
        timeout=20,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 200:
        raise RuntimeError(f"PushPlus 推送失败: {result}")


def push(title: str, content: str) -> None:
    app_token = os.getenv("WXPUSHER_APP_TOKEN")
    uids = [u.strip() for u in os.getenv("WXPUSHER_UID", "").split(",") if u.strip()]
    sendkey = os.getenv("SERVERCHAN_SENDKEY")
    token = os.getenv("PUSHPLUS_TOKEN")
    if app_token and uids:
        push_wxpusher(app_token, uids, title, content)
        print("已通过 WxPusher 推送")
    elif sendkey:
        push_serverchan(sendkey, title, content)
        print("已通过 Server酱 推送")
    elif token:
        push_pushplus(token, title, content)
        print("已通过 PushPlus 推送")
    else:
        print("未配置推送渠道，直接输出内容：\n")
        print(title)
        print(content)


# ---------- 主流程 ----------

def main() -> None:
    now = datetime.now(BEIJING)
    date_str = now.strftime("%Y%m%d")
    date_disp = now.strftime("%Y年%m月%d日")
    sections = []

    # 板块一：新闻联播（文字稿一般在 21:00 后更新，抓不到时定期重试）
    deadline = time.time() + MAX_WAIT_MINUTES * 60
    episode = None
    while True:
        try:
            episode = fetch_episode(date_str)
        except requests.RequestException as e:
            print(f"抓取央视网接口出错（将重试）: {e}")
        if episode:
            break
        if time.time() >= deadline:
            print(f"等待 {MAX_WAIT_MINUTES} 分钟后仍未找到 {date_disp} 的节目，退出。")
            sys.exit(1)
        print(f"{date_disp} 的节目尚未更新，{RETRY_INTERVAL_SECONDS // 60} 分钟后重试……")
        time.sleep(RETRY_INTERVAL_SECONDS)

    title, url, brief = episode
    print(f"找到节目: {title}\n{url}")
    try:
        xwlb_body = summarize_xwlb(date_disp, brief)
    except requests.RequestException as e:
        print(f"新闻联播摘要失败，回退到原始清单: {e}")
        xwlb_body = None
    sections.append("## 📺 新闻联播\n\n" + (xwlb_body or brief))

    # 板块二：AI / 科技 / 财经前沿
    feeds = fetch_feed_items()
    if feeds:
        try:
            feeds_body = summarize_feeds(date_disp, feeds)
        except requests.RequestException as e:
            print(f"前沿新闻摘要失败，回退到标题列表: {e}")
            feeds_body = None
        sections.append("## 🌐 前沿速递（AI · 科技 · 财经）\n\n" + (feeds_body or feeds_fallback(feeds)))
    else:
        print("所有 RSS 源均不可用，跳过前沿板块")

    content = "\n\n".join(sections)
    if url:
        content += f"\n\n---\n[查看完整节目视频]({url})"
    push(f"每日简报 · {date_disp}", content)


if __name__ == "__main__":
    main()
