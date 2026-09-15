# 资金流分层对接设计 · CapitalPulse 吸收（2026-09-09 定案）

> 定位：本文件 = 本次「资金流分层对接」设计的唯一权威（对应 design_连板晋级量化分析.md 同级惯例）。
> 对接方：github.com/Dr-Liu23/CapitalPulse（东财资金流采集，已克隆 /tmp/CapitalPulse 拆解 + 实测字段口径）。
> 实施原则：⛔ 改动盘中链路须收盘后做 + 次日验证；长任务运行窗口（复盘/eod）20:00 前不动（中枢运维铁律）。
> 状态：✅ **P2-18 已实施（2026-09-09 晚·盘后窗口）**：common/em_flow.py + intraday_pulse 分层注入 + dashboard 资金Tab 分层行 + tests 7 用例(全量 177 过)。次日 09:25 盘中真实卡验证。
> ⚠️ 实施修正 2 处（真实数据校准）：①A5 落地形态=**clist 保全市场排名 + ulist.np 对榜单 Top/focus 批量补分层**（非替换 clist——ulist 需代码池，盘中动态 360 板块即池）；②校验分级=恒等1(主力=超大+大·强·丢弃) + 恒等2(守恒·软·概念板块 1~10% 破缺打标 flow_consistent=False 保留，>10% 丢弃)——强校验曾误杀 24 条概念板块(实测破缺 1~5%)。

---

## 一、对接方案（总览）

### 1.1 判断：吸收数据能力，不吸收架构

| 对接方组件 | 决策 | 理由 |
|---|---|---|
| f66/f72/f78/f84 资金结构分层字段 | ✅ 吸收 | 现只有 f62 主力净额单字段，无法判断超大单(机构)vs大单(游资)结构质量 |
| f124 源时间戳 | ✅ 吸收 | 数据新鲜度可校验（现无源时间，无法识别东财侧停更） |
| fflow/kline 分钟累计曲线 | ✅ 吸收 | 盘中板块/个股分时资金演变，验证「拉升是否带主力资金」 |
| daykline 资金日线(30日) | ✅ 吸收 | 东财口径资金趋势，与腾讯 MainNetFlow5D/10D 交叉对账 |
| ulist.np 批量快照 | ✅ 吸收 | 单请求多标的全字段快照，替代 clist 逐页抓取（省请求） |
| SQLite + 3s 轮询 + WebSocket | ⛔ 不吸收 | 与本系统 launchd 30s 守护 + 定时卡架构冲突，实时到秒非需求 |
| 申万2021二级白名单 Top30 板块池 | ⛔ 不吸收 | 板块体系以选股宝 plates + sector_family.py 为权威，不引入第二套板块分类 |
| Next.js 前端 / FastAPI REST | ⛔ 不吸收 | 已有 dashboard/watch 自包含 HTML 双页 |
| 双域名回落(push2→push2delay) | ✅ 沿用 | 与本系统 EM_BOARD_HOSTS 模式一致，直接合并 |

### 1.2 落点与数据流（新增仅一处 common 层）

```
东财 push2/push2delay（REST·ut=7eea3edcaed734bea9telecast + Referer）
  → common/em_flow.py   ← 新增·唯一东财资金流请求/解析/校验层（收口现有散装 _em_board_get）
      ├ fetch_board_flow_snapshot(secids)   ulist.np  f12,f14,f62,f66,f72,f78,f84,f124
      ├ fetch_minute_curve(secid, klt=1)    fflow/kline  ← CapitalPulse A 级函数移植
      ├ fetch_daily_flow(secid, lmt=30)     daykline    ← 同上
      ├ parse_flow(守恒校验)                内部契约→语义字段
      └ sector_flow_cache 60s（复用现有 sector_risk_cache 模式·提升为公共缓存）
  → 盘中：intraday_pulse.build_sector_risk 升级（f62榜 + 分层字段透传 + 结构标签）
  → 盘后：capture_daily step → sector_flow_history_{date}.json 增补分层字段
  → 选股：pipeline_eod 候选资金回填（东财分层 + 腾讯总额 双源对账）
  → 展示：latest.json sector_risk.* → 盘中卡💰区块 / dashboard 资金Tab / watch.html
  → 审计：audit_log.py 记录每次上游快照 ts/条数/守恒异常（资金数据可追溯）
```

### 1.3 跨系统变更说明（本方案涉及，实施前确认）

1. `scripts/intraday_pulse.py build_sector_risk/_em_board_get`：请求收口到 common/em_flow.py（行为等价迁移，60s 缓存保留）→ **影响盘中卡💰区块 + 看板资金 Tab**，改动须收盘后 + 次日 09:25 后验证一次真实卡
2. `cache/sector_flow_history/sector_flow_{date}.json`：boards 条目增补 `main_net/超/大/中/小` 分层（**向后兼容**：旧字段 flow_yi/tx_* 不动，新增只追加）
3. `latest.json sector_risk.inflow_top/outflow_top`：条目增补可选分层字段（旧消费方不受影响，新增为空时降级显示）
4. pipeline_eod 候选回填：新增东财分层查询为盘中源，腾讯总额保持盘后 cross-check（分工铁律 2026-08-25 不变）
5. ⛔ 不新增 automation / launchd 任务——复用现有守护链 30s 节奏与盘中猎手触发（避免调度面扩大）

---

## 二、待吸收功能清单（逐项：价值 / 实现 / 吸收或不吸收）

| # | 功能 | 现状缺口 | 价值 | 实现载体 | 吸收 |
|---|---|---|---|---|---|
| A1 | 资金结构分层(超大/大/中/小) | 仅 f62 主力总额，结构盲区 | 区分机构(超大)vs游资(大)驱动；f62流入+f84小单流出=健康承接，反之警惕派发 | ulist.np 批量快照字段 f66/f72/f78/f84 | ✅ 盘中+盘后 |
| A2 | 源时间戳 f124 | 无法验证东财侧停更/冻结 | 新鲜度门禁：快照 ts<交易时段预期 → 降级/告警，杜绝陈旧数据当实时 | ulist.np 字段 | ✅ |
| A3 | 分钟资金累计曲线 | 无盘中资金分时 | 拉升是否带资金、板块资金拐点(10:30/14:00 脉冲验证) | fflow/kline klt=1 lmt=240，lmt 控制请求量(现抓≤30根) | ✅ 按需(猎手/快照卡注入) |
| A4 | 资金日线 30 日 | 腾讯5d/10d 单口径 | 东财 30 日趋势 vs 腾讯 5d/10d 对账；theme_rotation flow_trend 第二口径 | daykline klt=101 lmt=30 | ✅ 盘后沉淀 |
| A5 | 批量快照协议 | 逐板块 clist 翻页(10min×2源×2榜) | 一次请求拉全板块池+候选股，请求量↓、字段一致 | ulist.np secids 逗号拼 | ✅ 替换 clist 资金榜 |
| A6 | 守恒+新鲜度校验 | 无 | 资金数据准确可追溯的硬校验 | sum≈0 / f62=f66+f72 / f124 vs 本地 | ✅ 入 common 层 |
| A7 | 双源对账(东财vs腾讯) | 各说各话 | 候选股资金验证层置信度 | 偏差>20% 打标「口径分歧」 | ✅ 盘后 |
| B1 | 3s轮询+SQLite+WS | — | — | — | ⛔ 架构不匹配 |
| B2 | 申万白名单板块池 | — | — | — | ⛔ 板块权威不重叠 |
| B3 | 前端/API 层 | — | — | — | ⛔ 重复建设 |

---

## 三、全局统一规范（本系统资金流新契约·唯一权威）

### 3.1 字段命名（内部契约·语义化，东财字段映射只存在于 common/em_flow.py 一处）

| 语义名(契约) | 东财源字段 | 单位 | 说明 |
|---|---|---|---|
| `main_net` | f62 | 元 | 主力净流入 = super_large_net + large_net（实测精确成立） |
| `super_large_net` | f66 | 元 | 超大单净流入 |
| `large_net` | f72 | 元 | 大单净流入 |
| `mid_net` | f78 | 元 | 中单净流入 |
| `small_net` | f84 | 元 | 小单净流入 |
| `source_ts` | f124 | Unix秒 | 上游快照时间（新鲜度门禁） |
| `{field}_yi` | — | 亿元 | **仅展示层**换算（渲染 ÷1e8，禁各消费方重复实现） |

**存量兼容**：历史字段 `main_inflow`(intraday_pulse) / `main_flow_yi`(tx_query 腾讯) / `flow_yi`(sector_flow_history) **不改名不删除**；新产出用 `*_net` 契约。消费方读取优先 `main_net`，缺则回落历史字段（兼容读，规范写）。

**校验恒等式**（parse 层分级，2026-09-09 真实数据校准）：
- 恒等1 `main_net = super_large_net + large_net`：**强校验**（定义性恒等·实测全量精确）→ 违反丢弃+日志
- 恒等2 `main_net + mid_net + small_net ≈ 0`：**软校验**（行业板块元级成立；概念板块因成分重叠/分档舍入 1~10% 破缺为正常态）→ 打标 flow_consistent=False 保留，>10% 才丢弃
- 缓存键含解析版本戳 _PARSE_VER（契约演进自动失效，防校验规则调整致旧缓存污染——2026-09-09 实踩修复）

### 3.2 接口层（common/em_flow.py 唯一出口）

```
fetch_snapshot(secids:list[str]) -> list[FlowSnapshot]      # ulist.np
fetch_minute_curve(secid:str, limit=30) -> list[MinuteFlow] # fflow/kline klt=1（默认30根控量）
fetch_daily_flows(secid:str, limit=30) -> list[DailyFlow]   # daykline klt=101
```
- secid 规则：板块 `90.{BK代码}`；沪 `1.{6位}`；深/创 `0.{6位}`（与 CapitalPulse QUOTE_ID 一致）
- 双源回落：push2 优先 → push2delay 兜底（统一 EM_FLOW_HOSTS）
- 公共参数：`ut=7eea3edcaed734bea9telecast&fltt=2&invt=2&np=1&_={ms}`，Header 必须 `Referer: https://data.eastmoney.com/`
- 60s 进程内缓存（键=secids+fields，复用 sector_risk_cache 模式上提），失败不落缓存

### 3.3 错误处理与可追溯

| 场景 | 策略 |
|---|---|
| push2 + push2delay 双挂 | 返回空 → 消费方降级（榜单显示「数据暂缺」）→ notify_alert 蓝卡测试群（30min 去重） |
| 守恒校验失败 | 丢弃该条 + `cache/audit.jsonl` 记 `event=flow_inconsistent` |
| f124 过期（>5min 无刷新且盘中） | 打标 stale，卡面显示「延迟」，禁止当实时结论 |
| 部分板块缺失 | 正常返回，缺失者标注，不整批失败 |
| 单位 | 契约=元；展示=亿（_yi 助手统一），禁消费方自算 |

### 3.4 配置策略（config/app_config.yaml 追加登记）

| 键 | 值 | 说明 |
|---|---|---|
| `EM_FLOW_ENABLED` | true(默认)/false | **总开关·可逆**（对接落地后仍可一键回退旧逻辑） |
| `EM_FLOW_CACHE_SECONDS` | 60 | 快照缓存秒数 |
| `EM_FLOW_MINUTE_LIMIT` | 30 | 分钟曲线默认根数（控请求量） |
| `EM_FLOW_CONSISTENCY_TOLERANCE` | 0.01 | 守恒容差 |
| `EM_FLOW_STALE_SECONDS` | 300 | f124 新鲜度门禁 |
| `TX_FLOW_CROSS_CHECK` | true | 盘后东财vs腾讯对账开关（偏差阈值 0.20） |

落地顺序：common 层新增（纯增量）→ intraday_pulse 收口（收盘后）→ capture_daily 增补 → eod 对账 → 次日盘中验证。

---

## 四、资金流入流出优化建议（本地管道 + 数据口径双视角）

### 4.1 流入侧（数据进系统的采集管道）——现状 3 处冗余/风险

| 问题 | 位置 | 优化 | 收益 |
|---|---|---|---|
| 资金榜逐板块 clist 翻页×双源×10min 重复抓 | intraday_pulse build_sector_risk | ulist.np 批量(secids 拼接) + 公共 60s 缓存上提 common | 请求量↓ ~80%；字段一次拉齐(f62-f84+f124) |
| 东财请求封装散装 | intraday_pulse._em_board_get 函数级 | 收口 common/em_flow.py 唯一出口 | 一处改、全链路生效；CapitalPulse 契约坑不再重复踩 |
| 无源时间校验 | 全部 | f124 门禁入解析层 | 陈旧/冻结数据不再冒充实时（今日 15:00 后 f124 冻结=收盘正常态，需按 in_trading 判定，勿误报） |
| 无资金结构 | 全部 | 分层字段 | 「主力流入」升级为「超大单主导 or 大单主导」，供资金背离研判 |

### 4.2 存储/中继侧（延迟与追溯）

- sector_risk_cache 60s（函数级、进程内）→ 提升为 common 公共缓存，消除 intraday_pulse 与下游各持缓存副本
- 盘中分钟资金曲线：**按需拉取不落库**（猎手卡/快照卡注入用），避免与 pulse 快照链抢 I/O；盘后统一由 capture_daily 沉淀日线+分钟末值 → 单日文件版本化（sector_flow_history_{date}.json 已具备 date 真值，直接增补字段）
- 快照与产物写入前过 `common/json_io.py`（原子写），半截文件不产生

### 4.3 校验/对账（资金准确可追溯）

1. **守恒校验**（A6）每批快照强制；异常审计 `flow_inconsistent`，30min 同源去重告警
2. **双源对账**（盘后·东财分层 vs 腾讯 MainNetFlow）：`|em_main_net_yi - tx_main_flow_yi| / max(|..|,1) > 0.20` → 打标「⚠️口径分歧」，复盘卡/候选卡不再单源断言资金方向；口径一致 → 可标注「双源确认」
3. **当日闭环**：盘中 latest.json 资金榜 → 收盘 capture_daily 冻结 → 盘后复盘卡引用同一份 sector_flow_history 数据源（单一真值，杜绝盘中盘后数字打架）
4. **audit 时间线**：每次上游快照落 `cache/audit.jsonl`（ts/源/条数/守恒异常/降级标记）——资金数据全程可追溯

### 4.4 流出侧（展示/推送/下游消费）——口径混乱三处已识

| 现状 | 问题 | 统一后 |
|---|---|---|
| main_inflow(元,盘中) / flow_yi(亿,盘后沉淀) / main_flow_yi(亿,腾讯) | 同名不同单位、同义不同名 | 契约 `*_net`(元)+`*_yi`(亿·仅展示)；存量只读兼容 |
| 渲染层各自 ÷1e8 | 溢出/单位错风险 | common 助手 `to_yi()` 统一 |
| 资金方向词（净流入/净流出/主力流出）随卡自由发挥 | 语义不一致 | 方向=符号判定（>0 流入/ <0 流出/ 0 持平）；措辞遵守语义色板（流入=红=改善/流出=绿=恶化·08-16 铁律映射不变） |

---

## 五、验收口径（落地后自检）

1. pytest：common/em_flow 解析黄金用例（守恒成立样例/违反丢弃/双源回落/单位换算）+ 全量回归
2. 实测：盘中 09:35+ 手动跑一次猎手卡 --dry-run → 💰区块出现「超大单主导」分层标注；看板资金 Tab 字段展示正常
3. 对账样例：当日板块 Top5 东财 vs 腾讯偏差 <20% 无「口径分歧」打标（或打标内容与盘面事实一致）
4. 审计：cache/audit.jsonl 出现 em_flow 快照事件
5. 回退演练：EM_FLOW_ENABLED=false → 旧逻辑照常出卡（可逆铁律）
