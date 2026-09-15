# A股连板行情量化分析 · 设计方案（2026-09-09 用户立项·待开发）

> 定位：把今日已落地的股性数据底座（`zt_genes`：东财涨停池逐日 pool_*.json + 6年历史基因 hist_*.json）升级为两条量化主线：
> **① 连板晋级统计与回测（一进二/二进三/三进四 成功率+次日溢价·可区间/板块筛选）**
> **② 板块核心股性票识别（用涨停时间/封板强度/连板高度/带动性/联动 量化"一拉即知"的板块核心票）**
> 状态：DESIGN 阶段（待用户拍板后按 P1→P4 开发）；数据铁律：只读本地沉淀真值，禁编造。

---

## 〇、数据底座（全部复用·零新抓取成本）

| 层 | 文件 | 关键字段 | 现状 |
|---|---|---|---|
| 涨停池逐日 | `cache/zt_genes/pool_{YYYYMMDD}.json` | code/zt_boards(连板)/seal_min(fbt 首封)/last_seal/lbt/broken(zbc 炸板)/seal_fund_yi(封单亿)/industry(hybk)/zt_days/zt_times | ✅ 8/20 起每日沉淀(capture step7) |
| 历史基因 | `cache/zt_genes/hist_{code}.json` | 近250/500涨停次数·最长连板·晋级率·首板次日溢价 | ✅ 按需生成(新浪6年K) |
| 候选股性档案 | `cache/zt_genes/gene_{code}.json` | 短窗频率/首封/溢价/股性分 | ✅ |
| 腾讯/新浪日K | kl_hist_*.json 缓存 | 前复权/不复权·open/close | ✅ |
| 题材归属 | latest.json top5 + `sector_family.py` | top5 成分 zt_stocks/家族聚合 | ✅ |
| 板块资金 | `sector_fund_pool.json` | 板块级 f62 | ✅ |

**缺的只有"派生层"——本文档定义派生层全部由 pool 序列 + 日K 算，无新增外部依赖。**

---

## 一、主线① 连板晋级统计与回测

### 1.1 口径定义（写死·防歧义）
- **梯次**：1进2（昨连板=1 今=2）/2进3/3进4/4进5+（合并 4→5、5→6…为"高标晋级"）。
- **晋级事件**：相邻两交易日 (D 日连板 n、D+1 日连板 n+1 且同票) = 一次 `n进n+1` 成功事件；D+1 不再涨停 = 失败事件（晋级池含 D 日所有连板≥1 票的次日结局）。
- **次日溢价（晋级成功后的收益）**：以晋级日 T（即 D+1）收盘价为基准 → T+1 开盘溢价 / T+1 收盘溢价 / T+2 收盘溢价（打板者持有到晋级日的次日卖出的平均收益）。由日K join（hist 同源）。
- **成功溢价 vs 失败代价**：统计时同时给"成功组的次日溢价均值/中位"与"失败组(D+1 断板)当日开盘跳水均值（打板亏损）"——完整风险收益画像。

### 1.2 派生文件
```
cache/promo/promo_{YYYYMMDD}.json      # 每日晋级事件表（生成：新 pool 归档后对前一 pool 日跑）
  [{date, prev_date, code, name, prev_boards, boards(结局), stage:"1to2|2to3|3to4|4to5+",
    ok: bool, sector:hybk行业, theme:top5题材归属(若有), premium_open/+1d, premium_close/+1d, premium_close/+2d,
    zbc:当日炸板, seal_min, seal_fund_yi}]
cache/promo/promo_summary.json         # 汇总矩阵：梯次×区间×板块 的成功率/样本/次日溢价均/中位/失败代价
cache/promo/backtest_{tag}.md|json     # 回测报告（--filter 产出）
```

### 1.3 统计/回测 CLI（已实现 P1 于 `scripts/promo_quant.py`·与 zt_genes 同层共享日K缓存）
```
python3 scripts/promo_quant.py build                          # capture step7 每日增量（对昨日→今日对）
python3 scripts/promo_quant.py build --all                    # 回填全部历史相邻对（已完成 8/20 起 13 日）
python3 scripts/promo_quant.py enrich --all | --date X        # 补次日溢价（新浪日K·滞后 2 交易日自动滚动）
python3 scripts/promo_quant.py stat                           # 汇总矩阵（全量）
python3 scripts/promo_quant.py stat --stage 1to2 --board 食品加工 --start 20260801 --end 20260909
# stat 输出：成功率(%)/样本数/成功组次日开盘·收盘溢价均/失败组次日开盘(打板亏损)
```

### 1.4 板块/题材筛选口径
- 行业维度：pool.industry（东财 hybk·天然一致）；
- 题材维度：**历史题材归属需回填**——pool 只有行业。方案：晋级票若命中「当日 top5 题材成分」（有归档的日期）打 theme 标；未命中保持行业口径；回测过滤用"行业 OR 题材命中的票集"。⚠️ 诚实边界：题材归属只有 pool+top5 双归档之日可标（09-08 起），早期晋级事件仅行业维度。

### 1.5 产品化输出
- 复盘卡模块：今日"一进二成功 X 家/失败 Y 家" + 当前梯次环境（1进2 成功率连续 N 日下行=情绪退潮先行指标）；
- 财神-回测提案源：晋级率分层进情绪分/regime（若证明有增量，走提案模式）。

---

## 二、主线② 板块核心股性票识别

### 2.1 "一拉即知"拆成 4 个可算维度（对每票×所属板块打分）

| 维度 | 指标 | 计算（池/日K·已具备字段） | 权重 |
|---|---|---|---|
| **时效** | seal_time_score | 板块内该票平均首封序位（板块涨停日该票 fbt 排名）+ 绝对早（fbt≤09:45 加分） | 0.25 |
| **封板硬度** | strength_score | 封单资金 seal_fund_yi / 流通市值、炸板率（1-zbc）、最后封板 lbt 不再开 | 0.20 |
| **连板领先** | lead_score | 板块内最高连板持有时长：该票 zt_boards 达板块峰值的次数占比、首个 2 板次数 | 0.25 |
| **带动+参与** | drive_score | ①带动：板块 T 日该票涨停后，同板块其余票 fbt>T 首封（该票身后跟封数）均值——**用 fbt 时间序反推**；②参与率：该板块有涨停的日子，该票也在涨停列的比例 | 0.30 |

核心票分 = 0.25时效+0.20硬度+0.25领先+0.30带动参与（**带动最重**——"一拉即知"的本质=它动了别人跟）。

### 2.2 派生文件
```
cache/board_core/board_core_{board}.json   # 每板块核心票榜：[{code,name,score_4d,seal_avg_order,drive_avg,join_rate,max_lbc_lead,...}]
cache/board_core/summary.json              # 全板块核心票 Top（复盘/盘前可用）
```

### 2.3 CLI（同仓 `board_core.py`）
```
python3 board_core.py build --days 60      # 用最近 N 交易日 pool 计算每板块核心票（industry 口径·家族聚合沿用 sector_family）
python3 board_core.py show --board 食品加工
python3 board_core.py top --n 20           # 全市场核心票榜（供候选池/打板前置过滤）
```
- **用途**：盘中/选股时某板块异动 → 先看该板块 core 票是不是在动（在动=真板块行情；核心票不动只有杂毛涨=假启动）；可对接财富密码候选的板块校验。

### 2.4 联动关系（进阶·P4）
- 板块内两票"同日同板块涨停次数"共现矩阵 → 联动边；核心票 = 与其他票共现次数最高的节点（图中心性近似）——初期用"共现 TopN"近似，不进图算法库。

---

## 三、存储与字段设计汇总（新增文件清单）

| 文件 | 内容 | 生成时机 |
|---|---|---|
| `cache/promo/promo_{date}.json` | 每日晋级事件表（1.2） | 每 pool 归档后（capture step7 加 build --prev） |
| `cache/promo/promo_summary.json` | 梯次×区间成功率+溢价矩阵 | stat 时增量/每日 |
| `cache/promo/backtest_*.md` | 回测报告 | 手动 --filter |
| `cache/board_core/board_core_{board}.json` | 板块核心票 4 维分 | 每日（盘后） |
| `cache/board_core/summary.json` | 全市场核心票榜 | 每日 |

**回滚性**：全部为新文件+新 CLI，不动现有打分/推送；挂接复盘/选股均有开关。

---

## 四、开发落地路径（P1→P4·每期独立可交付）

| 期 | 内容 | 验收 | 挂接 |
|---|---|---|---|
| **P1** | promo 事件层：`promo_quant.py build/stat`（一进二~四进五 成功率+次日溢价矩阵·全量+按行业） | 用 8/20 起真实数据出首份矩阵·抽样人工核对（如"海欣 3进4 成功/溢价"） | capture step7 自动 build |
| **P2** | 回测 CLI：--stage/--board/--start/--end/--window + 报告 md | 出「2进3·种植业·近30日」类报告与全量对照 | 财神-回测风格·独立 |
| **P3** | 板块核心票：`board_core.py build/show/top`（4 维分） | 抽查 3-5 个板块核心票与"一拉即知"直觉一致（大农业→亚盛? 消费→海欣?） | 复盘/选股引用 core summary |
| **P4** | 联动共现 + 复盘/盘中挂接 + 宪法沉淀 + 回测提案 | 复盘卡出「晋级环境」行与核心票参考 | 财神-复盘/盯盘 |

**依赖与约束**：全链只依赖本地 pool/日K（已稳）；pool 历史从 8/20 起——样本逐日增长，**P1/P2 首月为"冷启动期"（样本<20 交易日·结论标注小样本）**；60 日后样本才丰沛；题材维度回填依赖 top5 归档。

**进度**：P1 ✅ / P2 ✅ / **P3 ✅（board_core 板块核心票·实证对上龙版传媒/亚盛/汉森等真核心）** / P4 ⏳（联动共现 + 复盘/选股挂接 + 今日 17:18 复盘首秀核对）。

---

## 五、代码规划（骨架签名·实现时落 `wealth_code/promo/`）

```
promo_quant.py
  def build_prev_day(prev_date) -> list[PromoEvent]      # 读 pool[prev]/pool[next]→晋级事件
  def _next_date_of(date, pool_dates)                    # 交易日推进
  def _premiums(code, t_close_date) -> (o1,c1,c2)        # 复用 kl_hist 日K
  def stat(events, stage=None, board=None, start=None, end=None) -> dict
  def backtest(events, **filters) -> dict + write md
board_core.py
  def board_events(pools, board, days)                   # 板块涨停日×票
  def seal_rank_scores(evs) / strength(evs) / lead(evs) / drive(evs)
  def core_rank(board, days) -> list[dict]               # 4维加权
  def all_boards_top(n)
# 共用：zt_genes 已提供 pool 读/日K 缓存/_is_zt_row；sector_family 提供家族聚合
```

**文档落点**：本文件 = 设计权威（config/design_连板晋级量化分析.md）；实现后规则并入 shared_rules + 中枢索引；pytest 每个纯函数配对。
