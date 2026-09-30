# 代码架构审查与重构规划（2026-08-17 · 中枢架构师视角）

> 审查对象：狸猫猎手全系统（scripts/ + wealth_code/ + common/ + config/）。本文中旧 HTML 相关结论为历史审查记录；HTML 展示层已于 2026-09-29 退役，终端 React 页面为唯一展示端。
> 审查原则：①复用优先 ②文件结构 ③解耦封装 ④一致性
> 本文件为规划基准，落地改造逐步执行（盘中链路改动须收盘后+次日验证）。

## ✅ 已落地（2026-08-17 P0 阶段）

| 项 | 内容 | 验证 |
|---|---|---|
| P0-1 | 新建 `common/http_client.py`（统一 HTTP：超时/UA/指数退避重试/并发取数） | 单测 ✓ |
| P0-2 | 新建 `common/json_io.py`（统一 JSON/JSONL 读写·原子写）+ `common/market_time.py`（BJ_TZ/now/in_trading/is_trading_day 唯一源） | 单测 ✓ |
| P0-3 | 接入 3 高频脚本：push_card（网络层→http_client，保留业务码重试）/ ma5_check（fetch_kline+时间+json 全接入）/ feedback_matrix（http_get 带 Referer + fetch_ma510 + 13 处时间 + 8 处 json 全接入） | dry-run/eod 回归 ✓ |
| P0-4 | biying_client 归并：adapters 版 `_open_json` 底层网络改走 common.http_client（保留 HttpClient.api/get_json 接口与 404/5xx 语义），消除第 4 套 urllib 实现 | biying_pools 导入链 ✓ |
| P0-5 | 全量回归：pytest 22/22、validate_schema 全绿、eod dry-run 正常 | ✓ |
| P1-1 | build_cards 拆分：新建 `pipelines/card_style.py`（12 个通用组件 + 颜色枚举），build_cards 改 import；eod/verify/panqian 三卡渲染回归 + 推测试群检查（真实数据） | ✓ 已推测试群 |

**⚠️ P1 落地教训（2026-08-17）**：
1. push_card 改造后从非项目根目录跑需加 sys.path 引导（common 导入失败）；
2. feedback_matrix 内部旧遗留 `def load_json`（无限自递归）遮蔽 common import——批量替换前必须 grep 同名函数遮蔽；
3. 覆盖型守卫（no_prev_snapshot/snapshot_is_today）已加「已有有效缓存不覆盖」保护（无 error 且 promote_rate 存在→保留盘中真实数据）。

**⚠️ 教训（2026-08-17 落地踩坑）**：手动跑 `feedback_matrix.py` 在非交易时段会触发守卫**覆盖有效数据**（昨日快照缺失守卫）。此后手动跑守卫脚本前必须先 `cp cache/feedback_matrix.json /tmp/` 备份，跑完恢复。

## 待办（后续 P1/P2）

| 优先级 | 动作 | 风险 |
|---|---|---|
| P1 | S1 拆 intraday_pulse（只拆不删：抓取→common/http_client + adapters/em_board；保留 in_trading+编排+推送） | ⛔ 盘中链路·收盘后做+次日验证 |
| P1 | S2 拆 build_cards（card_style.py 配色常量 + card_components.py 通用组件，三卡各自模块） | 中·card_lint+全卡渲染回归 |
| P2 | adapter 数据入口统一（build_cards/gen_dashboard 禁直读 cache，走 load_daily_metrics/load_sector_flow/load_candidates） | 中·validate_schema+dry-run |
| P2 | 剩余脚本接入 common（theme_rotation/capture_daily/intraday_pulse/gen_sentiment_summary 等 9 文件 JSON 读写、13 文件时间） | 低-中·逐个回归 |
| P2 | 规范统一：命名（load_/read_ 分界）、日志（全接 logging_setup）、返回契约（None/{} 混用治理） | 低·静态扫描 |

---

## 一、现状总览（审查时点）

| 层 | 目录 | 职责 | 文件数 |
|---|---|---|---|
| 数据采集/守护 | scripts/ | 盘中抓取/盘后沉淀/守卫/推送 | 21 |
| 选股算法核心 | wealth_code/core/ | 龙头/评分/期望/信号/账本 | 12 |
| 流水线 | wealth_code/pipelines/ | eod/verify/卡片/策略指标 | 6 |
| 适配层 | wealth_code/adapters/ | 数据源适配(zt/auction/verdict) | 6 |
| 公共层 | common/ | 审计/日志/mtime/必盈client | 4 |
| 展示 | 狸猫终端 | React 只读渲染 | — |

**健康度**：pytest 22/22、validate_schema/config/tasks 三绿、审计日志/备份/哨兵已落地。
**总规模**：约 13.7k 行 Python。

---

## 二、发现的问题（按四原则归类）

### 原则 1「复用优先」——重复实现（最严重）

| # | 重复项 | 涉及文件 | 风险 |
|---|---|---|---|
| R1 | **http 请求各自实现** | intraday_pulse(10处)/feedback_matrix(5)/ma5_check(3)/push_card(3) 各自写 urllib+超时+重试 | 改一处漏一处；超时/UA 口径漂移 |
| R2 | **JSON 读写各自实现** | 9 个文件各自 `json.load(open)`/`Path.read_text`，无统一容错/编码约定 | 异常处理不一致，空文件崩溃行为各异 |
| R3 | **交易日/时区判断各自实现** | 13 个文件各自 `BJ_TZ`/`is_trading_day`/`_today` | 时区常量漂移 → 推送时点错乱（曾踩坑） |
| R4 | **板块资金流读取双实现** | relay_score.`_load_sector_flow_map` vs theme_rotation.`_load_flow_series` 都读 sector_flow_history | 解析逻辑漂移，改一处不同步 |
| R5 | **同名 biying_client 两处** | `common/biying_client.py`(7.4K) vs `wealth_code/adapters/biying_client.py`(3.1K) **内容不一致** | 漂移已发生，谁为准不明 |
| R6 | **环境/regime 判定** | relay_score 曾自写（今日已改委托 load_environment，✅ 已修）；其余模块仍各自判断 regime 使用场景 | 已修一处，需确认无其他残留 |

### 原则 2「文件结构」——职责边界

| # | 问题 | 说明 |
|---|---|---|
| S1 | `intraday_pulse.py` 1666 行 | 抓取+守卫+推送+多模式混杂，违背单一职责；应拆 抓取(adapter)/守卫(util)/编排(script) |
| S2 | `build_cards.py` 1345 行 | 三张卡(eod/verify/panqian)+卡片组件+配色常量全在一个文件 |
| S3 | `common/biying_client.py` 与 adapters 同名 | 归属不清（见 R5） |
| S4 | `wealth_code/config/` 目录存在但无 .py | 配置层分散在 config/(根) 与 wealth_code/config/ 两处，期望引擎参数在 wealth_code/config，webhook 在 config/（根） |

### 原则 3「解耦封装」——耦合点

| # | 耦合 | 说明 |
|---|---|---|
| D1 | 卡片渲染直读 cache 文件 | build_cards/gen_dashboard 直接 `json.load(cache/...)`，绕过统一数据入口 → 数据源变更需改所有渲染点 |
| D2 | pipeline_eod 与 input_zt 强绑定 | `load_zt_pool` 返回裸 dict，无类型/契约校验层，字段漏传曾导致 price 被涨幅顶替（已修但靠人肉） |
| D3 | common/ 接入面窄 | audit_log(2)/logging_setup(3)/mtime_cache(1)/biying(1)——公共层建了但多数脚本未接入 |

### 原则 4「一致性」——规范统一

| # | 不一致 | 说明 |
|---|---|---|
| C1 | 命名混用 | `_load`/`_load_json`/`load_`/`_read` 四种读文件命名；`CACHE`/`CACHE_DIR`/`ROOT` 路径常量命名不一 |
| C2 | 日志口径 | 有的用 logging_setup、有的 print、有的写独立 .log——排查链路不统一 |
| C3 | 返回契约 | 有的函数失败返 None、有的返 {}、有的抛异常——下游防御逻辑各异 |

---

## 三、重构组织方案（目标结构）

```
狸猫猎手/
├── common/                      # 【公共层·唯一实现】——原则1核心落点
│   ├── http_client.py           # 统一 http：超时/重试/UA/日志（合并 R1）
│   ├── json_io.py               # 统一 JSON 读写：容错/编码/原子写（合并 R2）
│   ├── market_time.py           # BJ_TZ/is_trading_day/_today 唯一源（合并 R3）
│   ├── sector_flow.py           # 板块资金流唯一读取（合并 R4）
│   ├── audit_log.py / logging_setup.py / mtime_cache.py   # 已建，扩大接入
│   └── biying_client.py         # 唯一保留，删除 adapters 同名（合并 R5）
├── scripts/                     # 【业务编排层】薄脚本：定时入口+调用公共层
│   ├── intraday_pulse.py        # 瘦身：仅 in_trading 守卫+编排，抓取移 common/http_client + adapters
│   ├── feedback_matrix.py / ma5_check.py / push_card.py   # http 统一走 common
│   └── ...（其余保持）
├── wealth_code/
│   ├── core/                    # 【算法层】纯算法无 IO：leader/relay_score/expectation/signal
│   ├── adapters/                # 【数据适配层】唯一数据源入口：input_zt/input_auction/input_env
│   │   └── __init__.py          # 统一 re-export（对外只暴露 adapter 层）
│   ├── pipelines/               # 【流程层】build_cards 拆：card_components.py(组件) + card_style.py(配色) + 3 卡各自
│   └── config/                  # 【配置层】期望引擎参数（与根 config/ 分工明确：根=系统配置，wealth=算法参数）
└── config/                      # 系统级配置：settings/webhook/tasks/app_config
```

### 关键拆解动作

**动作 A（R1+R2+R3 落地）**：新建 `common/http_client.py`、`common/json_io.py`、`common/market_time.py`，先接入 3 个最高频脚本（feedback_matrix/ma5_check/push_card），再逐月扩散。

**动作 B（S1 拆 intraday_pulse）**：
- `common/http_client.py` ← 10 处 urllib（抓取）
- `wealth_code/adapters/em_board.py` ← 东财板块/资金抓取（build_sector_risk/snapshot）
- `scripts/intraday_pulse.py` ← 保留 in_trading 守卫 + 编排 + 推送（薄层）

**动作 C（S2 拆 build_cards）**：
- `wealth_code/pipelines/card_style.py` ← text_tag 枚举/配色常量/_div/_hr/_hl（目前被 card_lint/多个渲染点引用）
- `wealth_code/pipelines/card_components.py` ← _flow_columns/_stock_hl 等通用组件
- 三张卡（eod/verify/panqian）各自模块

**动作 D（R4/R5 归并）**：
- `common/sector_flow.py` ← 统一读 sector_flow_history（relay_score 与 theme_rotation 都改调它）
- 删 `wealth_code/adapters/biying_client.py`，统一 `common/biying_client.py`

**动作 E（D1 解耦）**：adapter 层提供 `load_daily_metrics()`/`load_sector_flow()`/`load_candidates(date)` 统一入口，build_cards/gen_dashboard 改为调用 adapter，禁止直读 cache 文件。

---

## 四、实施优先级与风险控制

| 优先级 | 动作 | 风险 | 验证 |
|---|---|---|---|
| P0 | R1/R2/R3 建 common 三件套 + 接入 3 高频脚本 | 中（改抓取链路） | 次日 09:25 验证抓取/推送；pytest |
| P0 | R5 删 adapters/biying_client（先比对两处差异合并） | 低（选股链路） | run_wealth_code eod --dry |
| P1 | S1 拆 intraday_pulse（只拆不删，逐步迁移） | 高（盘中链路） | ⛔ 收盘后做+次日全天验证 |
| P1 | S2 拆 build_cards（组件先抽，卡逻辑后移） | 中 | card_lint + 全卡渲染回归 |
| P2 | E 统一 adapter 数据入口（D1/D2） | 中 | validate_schema + 全链路 dry-run |
| P2 | C1/C2/C3 规范统一（命名/日志/契约） | 低 | 静态扫描（grep 命名清单） |

**⛔ 铁律**：
1. 盘中链路（intraday_pulse/pulse_fetcher/live_signal）改动**必须收盘后做+次日验证**；
2. 每次重构后跑 pytest 22/22 + validate_schema/config/tasks 三绿；
3. 重构只改结构不改算法数值（打分/权重/阈值冻结），算法变更走 upgrade_plan；
4. 公共层新增函数必须先搜现有实现（复用优先），禁止为单脚本造私有工具。

---

## 五、一致性规范（原则4·落地清单）

1. **命名**：文件读取统一 `load_xxx()`（返回对象）/`read_xxx()`（返回原始）；路径常量统一 `CACHE_DIR`（scripts）/`ROOT`（wealth_code 用 ROOT.parent）；
2. **日志**：全脚本接入 `common.logging_setup.get_logger`，print 仅限 CLI 入口；
3. **返回契约**：数据缺失统一返回 `None`（调用方防御），禁止混用 None/{} /空列表；
4. **配置**：系统配置（webhook/阈值/守护）→ config/（根）；算法参数（期望/权重）→ wealth_code/config/；分工写进 README。
