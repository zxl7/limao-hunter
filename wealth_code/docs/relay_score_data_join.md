# 数据对接方案：明日接力评分 × 现有多维度指标融合（P2-13 配套 · 2026-08-17 设计）

> **目标**：让新增的「明日接力评分」（core/relay_score.py）与现有系统的晋级率/情绪分/资金流/消息面等
> 多维度指标实现有效关联与融合，避免重复计算、口径冲突，形成一条可回测、可追溯的数据链。
> 本文档为设计基准，实现以本文档为准（改动同步 upgrade_plan.md）。

---

## 一、关联逻辑说明

### 1.1 关联所依据的维度字段与关联规则

**核心关联键（粒度对齐的关键）**：

| 关联键 | 类型 | 说明 | 粒度 |
|---|---|---|---|
| `trade_date` | 日期主键 | 交易日（YYYY-MM-DD） | 日级 |
| `code` | 个股主键 | 6 位证券代码（去后缀，如 `002172`） | 个股级 |
| `theme` / `sector` | 板块键 | 申万二级题材名（如 `医疗服务`） | 板块级 |
| `ts` | 时间戳 | 盘中快照时点（HH:MM:SS） | 时点级（仅盘中数据用） |

**关联规则（粒度一致性约束）**：
1. **日级指标**（晋级率/情绪分/涨跌停数）→ 只能关联到 `trade_date` 维度，禁止下钻到个股时点；
2. **个股级指标**（评分/连板数/成交额）→ 主键 `(trade_date, code)` 唯一，一对一关联；
3. **板块级指标**（板块资金/涨停广度）→ 主键 `(trade_date, theme)` 唯一，`code → theme` 为多对一外键；
4. **时点级指标**（盘中消息/快照）→ 关联到 `(trade_date, code)` 时取当日最新值（`ts` 最大者），保证"当日终态"口径一致。

### 1.2 关键指标数据来源与统计口径（防重复计算/口径冲突）

| 指标 | 权威来源（唯一真相源） | 统计口径 | 禁冲突点 |
|---|---|---|---|
| **晋级率 promote_rate** | `cache/feedback_matrix.json`（9:26 守卫生成） | 昨涨停今继续涨停家数 / 昨日涨停家数（全市场） | ⛔ 其他脚本不得自行重算晋级率；relay_score 不计算，只读取 |
| **连板晋级率 lb_promote_rate** | 同上 | 昨日连板（≥2板）今继续晋级 / 昨日连板家数 | 同上 |
| **情绪分 sentiment_score** | 同上（打分系统 v2 · 行情强度） | 10 因子模型，`{score}/10·{label}` | ⛔ 盘中 latest.json 的 5 级粗分仅兜底，展示以 feedback 为准 |
| **核按钮 nuke_count / 真破位 break_count** | 同上 | 昨涨停今跌 >5% / 同时破 MA5+MA10 | 口径以 feedback_matrix 的 break_count 分级为准 |
| **炸板率 broken_rate** | 同上 | 炸板家数 / 触及涨停家数 | 与 pulse 的 broken 数组区分（数组是明细） |
| **板块资金流 flow_yi** | `cache/sector_flow_history/sector_flow_{date}.json`（**全量**·优先）→ 兜底 `theme_rotation.json market_context.inflow_top/outflow_top`（**Top10**·2026-08-17 由 Top5 扩） | 东财板块主力净流入（亿）；全量源覆盖 359 板块，latest.json.sector_risk 抓取端保留 Top10（展示层只显 Top5） | ⛔ relay_score 的板块聚合必须经 `build_sector_agg_from_theme_rotation`/`_load_sector_flow_map` 统一取数，禁止各模块自抓。**2026-08-17 优化实证**：Top5 源导致 15 候选板块仅 1 个有资金数据（覆盖率 7%），切全量源后 11/15（73%）——共进(通信设备+59.6亿)因此升入首选档 |
| **板块涨停广度 zt_count** | `cache/yesterday_zt.json zt_list`（涨停池唯一权威） | 全市场涨停池按 sector 计数 | ⛔ 不可以用 relay/watch 候选池计数冒充全市场广度；⛔ 申万细分板块名不同但同属一大方向时（医疗服务 vs 医疗器械）默认精确匹配，语义聚合（如同一级行业前缀）为可选增强，未启用时诚实按精确名计数 |
| **梯队健康 ladder_health** | `core/leader.py build_sector_ladder`（pipeline_eod 计算） | 龙一/龙二/龙三/补涨加权 0~10 | 由 pipeline_eod 算好写入候选，relay_score 只消费 |
| **个股收益/净值 nav** | `wealth_code/cache/account_nav_history.jsonl` | 每行=已结算交易日，等权次日收盘 | 回测校验的唯一真相源 |
| **候选评分 relay_score** | `core/relay_score.py`（本方案新增） | 五维加权 × 环境调制 → 分档 | 新增指标，唯一计算源 |

### 1.3 消息面（newsflash）关联

**现状**：`latest.json.newsflash`（当日覆盖，含硬信号快讯）；`cache/pushed_newsflash.json` 仅存 id 去重；`capture_daily.py` 已实现日归档 `cache/newsflash_history/daily_{date}.json` 但当日为空（链路待触发）。

**关联规则**：
- 消息面主键：`(trade_date, news_id)`，字段：`title / content / ts / code_hits[] / theme_hits[]`；
- `code_hits`：消息中提到的个股代码（经 stock-catalyst-hunter 事件→个股映射，或标题正则）→ 与候选 `(trade_date, code)` 关联，供「个股是否有消息催化」标注；
- `theme_hits`：消息映射到的题材 → 与板块键 `(trade_date, theme)` 关联，供「板块消息催化密度」聚合（当日该板块相关快讯条数）；
- 融合呈现：候选卡「个股点评」可引用消息面（`催化：xxx`），但**消息面不参与评分计算**（数据/AI 边界：消息→题材→个股映射为 AI 层，评分=规则层，规则不因消息改变，仅 AI 点评引用）。

---

## 二、数据模型设计

### 2.1 逻辑模型（三表一账本）

```
【表1】daily_metrics（日级指标 · 每日一行）          ← 唯一真相源：feedback_matrix + 指数
  PK  trade_date (DATE)
  ── promote_rate, lb_promote_rate, sentiment_score, sentiment_label,
     nuke_count, break_count, broken_rate, up_rate, avg_pct,
     zt_count, prev_zt_count, idx_avg_pct, regime

【表2】sector_daily（板块日级指标 · 每板块每日一行）   ← theme_rotation + 涨停池 + 资金
  PK (trade_date, theme)
  ── zt_count, flow_yi, ladder_health, ladder_grade, promote_rate(板块级·可空),
     main_theme_flag, verdict_flag
  FK (trade_date) → daily_metrics(trade_date)

【表3】candidate_relay（个股接力候选 · 每日每候选一行） ← pipeline_eod + relay_score
  PK (trade_date, code)
  ── name, theme, boards, zbc, fbt, hs, cje_yi, zsz_yi, lt_mktcap, seal_amt,
     cap_score, is_sector_dragon, ladder_grade, ladder_health,
     raw_score, relay_score, band, parts_json,
     bucket(relay/watch), watch_group, expectation_json
  FK (trade_date) → daily_metrics(trade_date)
  FK (trade_date, theme) → sector_daily(trade_date, theme)

【表4】news_events（消息面 · 每快讯一行）
  PK (trade_date, news_id)
  ── title, content, ts, code_hits[], theme_hits[], signal_words[]
  FK (trade_date) → daily_metrics(trade_date)
  （code_hits → candidate_relay.code、theme_hits → sector_daily.theme 为软关联·存代码/题材名数组，不建硬外键）

【账本】nav_ledger（净值结算 · 每交易日一行）         ← account_nav_history.jsonl（不改结构）
  PK trade_date
  ── nav, avg_return_pct, stock_count, recommendation_date
```

### 2.2 物理实现（贴合现有 JSON/JSONL 文件体系 · 不引入数据库）

| 文件 | 对应表 | 写入方 | 读取方 |
|---|---|---|---|
| `cache/feedback_matrix.json` | daily_metrics（部分字段） | scripts/feedback_matrix.py | 所有卡 |
| `cache/theme_rotation.json` | sector_daily（+market_context） | scripts/theme_rotation.py | relay_score/看板 |
| `cache/yesterday_zt.json` | sector_daily.zt_count 源 | record_zt_snapshot.py | pipeline_eod |
| `wealth_code/cache/candidates-{date}.json` | candidate_relay（+relay_score 扩展） | pipeline_eod + relay_score | eod 卡 |
| `cache/newsflash_history/daily_{date}.json` | news_events | capture_daily.py | 复盘/5日汇总 |
| `wealth_code/cache/account_nav_history.jsonl` | nav_ledger | pipeline_verify/eod | 狸猫终端交易战绩页 |

### 2.3 关联后的统一视图（relay_score 输出扩展 · 实现落点）

```jsonc
{
  "schema": "relay_score_result_v1",
  "trade_date": "2026-08-17",
  "env": {"regime": "risk_on", "sentiment_score": 9.0, "promote_rate": 23.8,
          "lb_promote_rate": 45.5, "nuke_count": 2, "max_boards": 4},
  "ranked": [
    {
      "code": "002172", "name": "澳洋健康", "theme": "医疗服务",
      "boards": 4, "relay_score": 64.8, "band": "⭐ 观察",
      "parts": {"leadership": 85, "capacity": 60, "sector_momentum": 5,
                "ladder_position": 90, "capital_flow": 20},
      "sector_ref": {"zt_count": 4, "flow_yi": null, "ladder_grade": "C"},
      "news_ref": {"hit_count": 1, "top_titles": ["基药目录..."]},
      "nav_ref": {"prev_trade": "2026-08-14", "batch_return": 3.09}
    }
  ]
}
```

**关联字段说明**：
- `sector_ref`：join sector_daily（板块广度/资金/梯队）——供复核评分合理性；
- `news_ref`：join news_events 按 code_hits 命中该股的消息条数+标题（消息面融合，AI 点评用）；
- `nav_ref`：join nav_ledger 最近已结算批次的收益（回测战绩引用）。

### 2.4 索引建议（文件体系下的等效物）

| 文件 | 检索模式 | 等效索引 |
|---|---|---|
| feedback_matrix.json | 按 trade_date 读当日 | 文件名即日期索引（无需改动） |
| theme_rotation.json | 按 theme 查资金/广度 | 内存 dict：`theme → agg`（build_sector_agg 已实现） |
| candidates-{date}.json | 按 code 查候选 | 内存 dict：`code → candidate`（一次性加载） |
| newsflash_history/daily_{date}.json | 按 date 读、按 code_hits 过滤 | 日文件+code_hits 数组（内存扫描，量小） |
| account_nav_history.jsonl | 按 trade_date 查净值 | 追加式 jsonl，倒序读末行（现有实现） |

> ⛔ 数据量级（日几十~百条）无需数据库索引；「索引」= 加载时的内存 dict 建键，全部在 relay_score 的
> `build_sector_agg_from_theme_rotation` 与新增的 `build_news_index` 中实现。

---

## 三、可落地实现步骤

### 步骤 1：relay_score 输出补关联字段（核心落地）
在 `core/relay_score.py` 新增 `build_news_index(newsflash_history_dir)`（读当日 newsflash_history → `code → [titles]`），
`rank_relay_candidates` 增加 `news_ref`/`nav_ref` 回填（news 可空则 `hit_count:0`，诚实标注）。

### 步骤 2：sector_daily 统一取数（防口径漂移）
以 `build_sector_agg_from_theme_rotation` 为唯一板块聚合入口：
- zt_count ← yesterday_zt.json zt_list（全市场）
- flow_yi ← theme_rotation.market_context（东财 Top5）
- ladder ← pipeline_eod 候选内嵌（ladder_grade/health）
禁止任何新模块绕过此函数自算板块数据。

### 步骤 3：晋级率等日级指标统一入口
新增 `load_daily_metrics()`（读 feedback_matrix.json 单点封装）：
- 输出 promote_rate/lb_promote_rate/sentiment/nuke/break/broken/regime/zt_count；
- relay_score 只调用此函数，**不自行计算任何晋级率**（防重复计算）。

### 步骤 4：消息面归档补跑 + 关联
- 确认 `capture_daily.py` 的 `capture_newsflash` 在盘后链正常产出 `cache/newsflash_history/daily_{date}.json`；
- 消息→个股 code_hits 用标题正则（`(\d{6})`）先落地，事件级映射（stock-catalyst-hunter）后续 P1-7 深化；
- relay_score 结果 `news_ref` 在无消息数据时 `hit_count:0`，不编造。

### 步骤 5：回测框架（P2-13 后续）
- 输入：历史 `candidates-{date}.json`（含 raw_score）→ relay_score 重算 → 与 `account_nav_history.jsonl` 实际收益 join；
- 输出：各 band 的次日胜率/平均收益（首选/次选/观察分组回测），用于调 `relay_score_config.json` 权重；
- 目标：验证「首选档转化率 > 次选 > 观察」成立，权重才可定稿接入主流程。

### 步骤 6：接入 pipeline_eod（待回测通过后·用户拍板）
- eod 流程在 select_relay_watch 之后调用 relay_score 排序，输出 band 作为候选卡新字段；
- 卡片展示：接力池按 band 分组（首选⭐⭐⭐/次选⭐⭐），原 bucket 逻辑保留兼容；
- ⛔ 接入前先跑 pytest + 全量 dry-run，验证不破坏现有契约。

---

## 四、口径冲突清单（不可再犯）

1. 晋级率**唯一真相源** = feedback_matrix.json；relay_score/任何新模块**不得重算**；
2. 板块广度**必须**全市场涨停池计数，禁止用候选池计数冒充；
3. 板块资金**必须**经 build_sector_agg_from_theme_rotation 取东财数据，禁止各模块自抓（防接口差异）；
4. 情绪分展示以 feedback 10 分制为准，latest.json 5 级仅兜底；
5. 消息面**不参与评分计算**（数据/AI 边界），仅作 AI 点评引用；
6. 新增指标 relay_score 唯一计算源 = core/relay_score.py，回测调参只改 relay_score_config.json。
