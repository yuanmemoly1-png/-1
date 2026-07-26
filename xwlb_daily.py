#!/usr/bin/env python3
"""每日新闻联播自动简报：抓取央视网当天节目单 → LLM 摘要 → 推送到微信。

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

import requests

CCTV_API = (
    "https://api.cntv.cn/NewVideo/getVideoListByColumn"
    "?id=TOPC1451528971114112&n=10&sort=desc&p=1&mode=0&serviceId=tvcctv"
)

BEIJING = timezone(timedelta(hours=8))
MAX_WAIT_MINUTES = int(os.getenv("MAX_WAIT_MINUTES", "40"))
RETRY_INTERVAL_SECONDS = 300

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


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


def summarize(date_str: str, brief: str) -> str | None:
    """调用 OpenAI 兼容接口生成摘要；未配置 key 时返回 None。"""
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return None
    base = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("LLM_MODEL", "deepseek-v4-pro")
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
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        },
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


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


def main() -> None:
    now = datetime.now(BEIJING)
    date_str = now.strftime("%Y%m%d")
    date_disp = now.strftime("%Y年%m月%d日")

    # 文字稿一般在 21:00 后更新，抓不到时定期重试
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

    summary = None
    try:
        summary = summarize(date_disp, brief)
    except requests.RequestException as e:
        print(f"LLM 摘要失败，回退到原始清单: {e}")

    body = summary or brief
    content = f"{body}\n\n---\n[查看完整节目视频]({url})" if url else body
    push(f"新闻联播 · {date_disp}", content)


if __name__ == "__main__":
    main()
