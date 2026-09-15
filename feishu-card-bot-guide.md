# 飞书群机器人卡片推送 · AI 自动实现指南

> 本文件是一份**给 AI 看的实现规格**。把本文件直接发给你的 AI（任意支持写代码的助手），
> 它就能据此生成一个「连接飞书群机器人、推送结构化卡片」的基础程序。
> 具体业务逻辑（推什么内容、什么时机推）由你自己后续扩展，本指南只负责**把卡片可靠地推到飞书群**这一件事。

---

## 0. 给 AI 的开场指令（复制给 AI）

```
你是我的编程助手。请按下面《飞书群机器人卡片推送 · 实现规格》实现一个最小可用程序：
- 语言：Python 3
- 只依赖 requests 库
- 输出一个 push_card.py（命令行工具）+ 一个 config.json（配置文件）
- 实现自动选群、失败重试、内容去重、卡片格式校验、换行修复、失败告警
- 代码要能直接跑，带清晰注释
- 不要硬编码任何凭证，全部走配置文件
实现完告诉我怎么用。
```

---

## 1. 功能目标（一句话）

把程序生成的结构化 JSON 卡片，通过**飞书群自定义机器人**的 webhook 实时推送到群里。

---

## 2. 飞书侧接入（人工操作，一次即可）

1. 飞书群 → 右上角「设置」→「群机器人」→「添加机器人」→ 选「自定义机器人」
2. 创建后复制 **webhook 地址**，形如：
   `https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx`
3. （建议）开启「签名校验」并保存 secret，防止 URL 泄露后被滥用
4. 把 webhook 地址填进程序的配置文件

---

## 3. 配置文件规格（config.json）

```json
{
  "feishu_webhook":      "<默认群地址，留空则必填下面其一>",
  "feishu_webhook_test": "<测试群地址>",
  "feishu_webhook_prod": "<正式群地址>",
  "webhook_default_key": "feishu_webhook",
  "secret":              "<签名secret，未开启签名校验可留空>"
}
```

- **双群设计**：`prod` 用于正式推送，`test` 用于调试。切换只改配置，不在代码里硬编码。
- **自动选群**：周一至周五用 `prod`，周末用 `test`。允许环境变量 `FEISHU_PUSH_ENV=prod|test` 强制覆盖。

---

## 4. 推送脚本规格（push_card.py）

### 4.1 命令行接口

```bash
python3 push_card.py <card.json> [--webhook KEY] [--dry-run] [--force]
```

| 参数 | 作用 |
|---|---|
| `<card.json>` | 卡片内容文件（支持两种格式，见第 5 节） |
| `--webhook KEY` | 显式指定群（最高优先级，覆盖自动选群） |
| `--dry-run` | 只打印目标群和卡片结构，不真正发送 |
| `--force` | 绕过去重，强制重推 |

### 4.2 必须实现的工程能力

| 能力 | 说明 |
|---|---|
| **自动选群** | 按星期（或环境变量）选 `prod` / `test` |
| **失败重试** | 网络异常指数退避（如 2s / 5s，最多 2 次）；已知业务错误码（如 url 失效）不重试 |
| **内容去重** | 相同内容 10 分钟内只推一次（按内容 hash 记录），防刷屏 |
| **卡片校验** | 推送前校验 JSON 结构合法；紧急可用开关绕过 |
| **换行修复** | 自动把字面 `\n` 还原为真实换行（飞书 lark_md 渲染坑） |
| **失败告警** | 推送失败自动发一张「⚠️系统异常」蓝卡到测试群 |

### 4.3 HTTP 调用

POST `application/json` 到 webhook 地址，body 即第 5 节的卡片 JSON。
若启用签名校验，body 需附带 `timestamp`（秒级时间戳）与 `sign`（HMAC-SHA256 签名）。

---

## 5. 卡片 JSON 格式规范

飞书 **interactive 卡片**。两种写法都支持：

### 5.1 完整格式（飞书原生）

```json
{
  "msg_type": "interactive",
  "card": {
    "config": { "wide_screen_mode": true },
    "header": { "title": { "tag": "plain_text", "content": "卡片标题" }, "template": "red" },
    "elements": [
      { "tag": "div", "text": { "tag": "lark_md", "content": "**加粗** 和换行" } }
    ]
  }
}
```

### 5.2 简写格式（推荐，程序自动包装成完整格式）

```json
{
  "title": "盘中脉冲·10:00窗口",
  "color": "red",
  "elements": [
    { "tag": "div", "text": { "tag": "lark_md", "content": "卡片正文，支持 **Markdown**" } }
  ],
  "note": "机器检测·非AI研判"
}
```

- 正文统一用 `{"tag":"div","text":{"tag":"lark_md","content":"..."}}` 写 Markdown
- **颜色白名单**（用错飞书不识别、卡片变无色）：
  `blue / red / green / turquoise / yellow / orange / purple / grey / violet / indigo / carmine`
  非法值统一兜底为 `red`（注意：飞书**不识别** `lime`）

---

## 6. 工程注意点（务必告诉 AI）

1. **频率限制**：飞书群机器人约 **20 条/分钟/群**。密集推送必须做节流（窗口聚合 / 去重 / 最小事件阈值）。
2. **安全**：webhook URL 即凭证，泄露可被任何人往你群推消息。建议开启签名校验。
3. **自动选群按星期判断，不含法定节假日**。节假日想推测试群，设 `FEISHU_PUSH_ENV=test`。
4. **换行坑**：程序生成的卡片里若含 `\n`，推送前必须还原成真实换行，否则飞书会显示成字面反斜杠 n。

---

## 7. 收尾（给 AI 的验收要求）

请生成后确认：
- `python3 push_card.py sample.json --dry-run` 能正确打印目标群与卡片结构
- `python3 push_card.py sample.json` 能真实推送到配置里的群
- 配置示例 `config.example.json` 随代码附带，方便他人照填

---

*本指南只解决「连接机器人 → 推卡片」这一层。推什么内容、何时推、怎么聚合信号，
由你自己的业务决定，本指南不预设。*
