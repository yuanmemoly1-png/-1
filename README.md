# 每日新闻联播自动简报

每天晚间自动抓取当天《新闻联播》内容，用 LLM 生成简报，推送到微信。

## 工作流程

```
GitHub Actions 每天 21:45（北京时间）触发
  → 央视网接口抓取当天 19:00 档节目单（未更新则每 5 分钟重试，最多等 40 分钟）
  → LLM 生成 400 字以内的分板块简报（未配置则回退为原始内容清单）
  → WxPusher / Server酱 / PushPlus 推送到微信
```

## 部署步骤

1. **推送渠道（任选其一）**
   - WxPusher：<https://wxpusher.zjiecode.com> 微信扫码登录，创建应用获取 `appToken`（`AT_` 开头）；关注应用后拿到用户 `UID`（`UID_` 开头）
   - Server酱：<https://sct.ftqq.com> 微信登录后获取 SendKey
   - PushPlus：<https://www.pushplus.plus> 微信扫码后获取 token

2. **LLM API key**
   - 任意 OpenAI 兼容接口均可，默认使用 DeepSeek（<https://platform.deepseek.com>）
   - 不配置也能用，只是推送原始内容清单而不是 AI 摘要

3. **创建 GitHub 仓库并推送本目录代码**

4. **配置 Secrets**：仓库 → Settings → Secrets and variables → Actions
   - 推送（必填其一）：
     - `WXPUSHER_APP_TOKEN` + `WXPUSHER_UID`（WxPusher，UID 多个用英文逗号分隔）
     - 或 `SERVERCHAN_SENDKEY`，或 `PUSHPLUS_TOKEN`
   - `LLM_API_KEY`：LLM 的 key（可选）
   - 可选 Variables：`LLM_BASE_URL`（默认 `https://api.deepseek.com`）、`LLM_MODEL`（默认 `deepseek-v4-pro`，想省钱可换 `deepseek-v4-flash`）

5. **测试**：Actions 页面选择 "每日新闻联播推送" → Run workflow 手动触发一次，微信应收到简报

之后每天 21:45（北京时间）自动运行。GitHub 定时任务可能有几分钟延迟，脚本内置重试可容忍文字稿更新偏晚的情况。

## 本地运行

```bash
pip install -r requirements.txt
export WXPUSHER_APP_TOKEN=AT_xxx
export WXPUSHER_UID=UID_xxx
export LLM_API_KEY=sk-xxx          # 可选
python xwlb_daily.py
```

不设置任何 key 时会把简报打印到终端，方便调试抓取逻辑。
