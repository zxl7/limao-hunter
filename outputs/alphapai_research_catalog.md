# Alpha派 research skill 全量接口目录（v2.0.9）

安装位置：`/Users/zxl/.workbuddy/skills/alphapai-research`
调用前缀：

```bash
PY=/Users/zxl/.workbuddy/binaries/python/envs/default/bin/python
cd /Users/zxl/.workbuddy/skills/alphapai-research && $PY scripts/alphapai_client.py <命令>
```

基础地址：`https://open-api.rabyte.cn/alpha/open-api/v1/<path>`
判定成功：`code == 200000`（HTTP 200 不等于业务成功）

---

## 0. 基础

| 命令 | 说明 |
|---|---|
| `config --show` / `--set-key KEY` / `--set-url URL` | 查看或写入配置 |
| `hello` | 鉴权与连通性健康检查 |

---

## 1. 文档类数据（一手原文为主）

### 1.1 公告 `announcement`

| 子命令 | 说明 |
|---|---|
| `list` | 检索公告 |
| `pdf-download` | 下载公告 PDF |
| `parsing-download` | 下载公告 PDF 深度解析（Markdown，优先用） |

`list` 关键参数：`--page-num/--page-size`（≤100）、`--keyword`、`--end-date-from/to`（**报告期**，非发布日）、`--publish-from/to`（实际挂网时间）、`--industry-code/name`、`--stock-code/name`、`--market {A,HK,US}`、`--type-code/name`、`--sort-by {actual_publish_time,publish_time,end_date,score}`、`--sort-order`

### 1.2 机构研报 `research-report`

| 子命令 | 说明 |
|---|---|
| `list` | 检索研报（`--scope` 必填：domestic / foreign / independent） |
| `detail` | 研报详情 |
| `pdf-download` | 下载 PDF |
| `parsing-download` | 下载解析结果 |

`list` 关键参数：`--report-type`（macro/strategy/industry/company/hk_research/us_research/fixed_income/exchange_rate/financial_engineering/esg/daily/stock_recommend/fund/new_stock/other）、`--report-feature {deep,new_fortune}`、`--country-region`、`--language`、`--page-count-range {lt_10,between_10_30,gt_30}`、`--institution-code/name`、`--stock-symbol/name`

### 1.3 会议纪要 `meeting-minutes`

| 子命令 | 说明 |
|---|---|
| `list` | 检索公开投研会议（`--market-type {A,HK,US}` 必填） |
| `detail` | 纪要详情（`ai_note` / `asr_note`） |

`list` 关键参数：`--begin-time/--end-time`（到秒）、`--meeting-tag {executive_attended,new_fortune,china_concept}`、`--content-type`（company_communication/performance_meeting/expert_communication/company_analysis/industry_analysis/conference/fund_manager_view）、`--duration {lt_30m,between_30m_60m,gt_60m}`

### 1.4 券商点评 `brokerage-commentary`（别名 `review`）

| 子命令 | 说明 |
|---|---|
| `list` | 点评检索（`--scope {all,valuable,regular}`） |
| `event-list` | 业绩点评专属（`--market-scope {ah,a,hk}` 必填） |
| `detail` | 点评详情 |

`list` 独有筛选：`--subject-code/name`（**题材**编码/名称）、`--stock-symbol/name`、`--industry-code/name`
`event-list` 独有：`--event-type {performance_forecast,performance_flash,performance_report}`

---

## 2. Alpha派特色数据

### 2.1 机构热议 `hot-topics board-list`
返回按交易日聚合的机构榜（公募/私募/外资等），字段：`tradingDay`、`instType`、`rank`、`hotCount`、`comboSymbol`、`hcode`

### 2.2 蓝宝书 `bluebook`

| 子命令 | 说明 |
|---|---|
| `batch-list` | 批次标题列表（`--batch-type {morning,noon,evening,all}` + `--batch-scope {domestic,global,all}` 均必填） |
| `topic-list` | 热门话题搜索（支持 `--stock-symbol`、`--stock-name`、`--min-heat`） |
| `topic-detail` | 话题详情（`--topic-id` + `--batch-scope {domestic,global}`） |

### 2.3 自选股 `watchlist`

| 子命令 | 说明 |
|---|---|
| `list` | 关注列表（`--group-code`、`--check-group`） |
| `exist` | 是否已自选 |
| `num` | 关注数量（股票×分组记录数） |
| `follow` / `unfollow` | 批量关注/取关（`--codes`、`--group-code`） |
| `group-list` / `group-add` / `group-delete` | 分组管理（`group-delete` 有 CLI 二次确认，`--confirm yes`） |
| `legacy-list` | 旧版接口 |

---

## 3. AI 功能（消耗额度）

### 3.1 投研知识检索 `recall`
`--query`、`--type`（逗号分隔）、`--start/--end`、`--no-cutoff`

`recallType` 可取值：`roadShow`（内资纪要）、`roadShow_ir`（官方路演）、`roadShow_us`（美股 earnings）、`report`（内资研报）、`foreign_report`、`third_report`、`ann`（公告库）、`comment`（点评）、`vps`（基金定期报告）、`social_media`（社媒）、`edb`（指标库）

返回字段：`id`、`type`（**可能返回枚举外值**，如 `us_announcement` 混入 `ann`）、`contextInfo`、`chunks`、`sentenceIds`

### 3.2 PaiPai 问答 `qa`
`--question`、`--mode {Flash,Think}`（Flash=一问一搜一答；Think=Wide Search 一问多搜）、`--context`（多轮）、`--web-search`、`--deep-reasoning`、`--auto-route/--no-auto-route`、`--start/--end`

长任务可靠性参数：`--connect-timeout`、`--read-timeout`（默认120s）、`--progress-timeout`、`--max-duration`、`--checkpoint`、`--checkpoint-interval`、`--output`、`--output-only`、`--retry-on-429`（默认2，只重试明确 42900）

SSE 聚合为：`code`、`message`、`questionId`、`answer`、`references`

### 3.3 PaiPai Agent `agent --mode N`
`1`=业绩点评 `2`=公司一页纸 `3`=调研大纲 `5`=主题选股 `7`=投资逻辑 `8`=可比公司 `9`=观点 Challenge `11`=行业一页纸 `12`=个股选基 `13`=主题选基 `15`=画图

（4/6/10/14 未在 CLI 枚举中）

各模式必填参数：

| 模式 | 必填/关键参数 |
|---|---|
| 1 业绩点评 | `--stock CODE:NAME`、`--report-type`、`--report-id`、`--report-title`、`--report-period` |
| 2 公司一页纸 | `--stock`；美股可加 `--language {中文,英文}` |
| 3 调研大纲 | `--stock` |
| 5 主题选股 | `--question` |
| 7 投资逻辑 | `--stock`；`--only-answer` 仅返回最终答案 |
| 8 可比公司 | `--stock` |
| 9 观点 Challenge | `--stock`；`--concern` 选填 |
| 11 行业一页纸 | `--industry`（如「白酒」） |
| 12 个股选基 | `--stock-list CODE:NAME ...`、`--report-date`、`--fund-type {全部,主动,指数,ETF}` |
| 13 主题选基 | `--report-date`、`--fund-type` |
| 15 画图 | `--picture-color HEX...`、`--picture-style {PPT风格,科普风格}`、`--source {0=仅图片,1=图文}` |

公共：`--template {0=alpha派模板,1=用户模板}`、`--template-text`（template=1 必填）、`--start/--end`

### 3.4 金融数据自然语言查询 `da-query`
`--question`（如「比亚迪的近期财报」）

---

## 4. 社媒订阅 `social`

| 子命令 | 说明 |
|---|---|
| `account-search` | 搜公众号（`--word`、`--page-num/--page-size`） |
| `account-list` / `account-detail` | 可订阅列表 / 详情 |
| `subscribe` / `unsubscribe` / `subscribe-list` | 订阅管理 |
| `article-list` | 文章主列表（`--word`、`--industry`、`--stock`、`--institution`、`--source-name`、`--psn-write {1,2,3}`、`--exclude-content`） |
| `article-detail` | 文章详情（`--id`、`--supplier-id`） |

---

## 5. 辅助工具

### 5.1 搜图表 `image`
`--query`、`--files-range`（`3`=内资研报 `8`=外资研报 `6`=公告 `9`=三方研报）、`--topk`（1–100，默认50）、`--recall-mode {both,vector_only,es_only}`、`--llm-rank`、`--start/--end`

### 5.2 个股公告期列表 `report`
`--code`（如 `603380.SH`）——供业绩点评查 `report-id`

---

## 6. 录音与会议

### 6.1 录音转记 `recording`

| 子命令 | 说明 |
|---|---|
| `submit-url` | 提交链接音视频 |
| `upload` → `submit-file` | 先传文件拿 `fileId`，再建任务 |
| `query` | 任务列表（`--status`：0进行中/1成功/2失败/3超时/4取消/5等待/6费用不足/8录音中/9传输中/10转码中/11文件过大/12时长过大） |
| `newest` / `detail` | 最新一条 / 任务详情 |
| `download` / `ai-summary-download` | 下载产物 zip（含 AI 纪要、逐字稿） |
| `rename` / `delete` | 重命名 / 删除 |

### 6.2 预约会议 `meeting-booking`

| 子命令 | 说明 |
|---|---|
| `list` | `--scope {my,organization}`、`--status {success,fail}`、`--meeting-time-from/to`、`--reserved-time-from/to` |
| `generated-list` | 已生成会议 |
| `summary-detail` | 纪要详情（`summaryId` + 必填 `noteType`，仅 SaaS 应用） |
| `delete` | 删除预约记录 |

---

## 数据可信度优先级（skill 内置口径）

`公告/定期报告 > 结构化指标 > 官方路演 > 其他纪要 > 研报 > 点评 > 社媒`

权威性与时效性分开判断：二手源可用于发现线索，结论尽量回到一手来源。

## 已知限制

- `recall` 的 `--no-cutoff`（`isCutOff`）当前无观测效果。
- 加密 ID（公告/研报/点评）可能过期，不复用旧示例 ID。
- CLI 无额度查询命令。
- 三类纪要（meeting-minutes / recording / meeting-booking）标识不通用，`roadshowId`/`taskId`/`msgId` 不可混用。
