# 财神与量化技能执行契约

本文件统一技能职责、输入输出和验收方式，不新增策略，不替代 `shared_rules.md` 的业务规范，也不授予修改参数、调度或对外推送的权限。源码核验基准：本次本地工作树。旧文档中的未来日期、任务数量、测试数量不作为运行状态证据。

## 规则与事实

- 用户已确认需求决定目标；`shared_rules.md` 承载公共业务约束；模块技能描述执行步骤；源码与配置说明当前实现。需求与实现不一致时分别报告，不能以“代码如此”宣称需求已满足。
- 参数取源码或配置，不在技能复制第二套参数表。历史案例、设计稿、计划任务和测试通过记录都不等于当前已实现、已运行。
- 技能正文的「唯一源词」词频由 `scripts/check_skill_drift.py` 看守（基线 `config/skill_drift_baseline.json`，棘轮：只许降不许升）。要新增副本，先改 `config/shared_rules.md`；确需写死在技能里时 `--update` 并说明理由。
- 修改技能只改文档。运行抓取、写生产缓存、结算、推送、调度修改均是另外的动作，按用户任务范围执行。
- 自动化仅使用平台管理接口；不得访问内部数据库或目录。`scripts/system_health.py` 当前包含 sqlite 自动化查询，不作为默认体检入口。改用授权范围内的平台查询，加项目日志、文件业务日期及守护现场状态分项核验。

## 模块关系与单一来源

| 层 | 主责技能 | 实现与交接 |
|---|---|---|
| 总控 | 财神-中枢 | 分诊、证据索引、跨模块定位；不产买卖判断 |
| 结构化数据 | zxl-stock-realtime / westockdata / hithink-finance / a-stock-short-term | 按当前接口能力取数；保留来源、单位、时间、缺失状态 |
| 事件与方法论 | stock-catalyst-hunter / a-stock-sentiment / 龙头战法-交易体系 | 催化证据、市场情绪、板块周期分别输出；不直接写交易终态 |
| 决策研究 | 龙头战法交易员（原 AI操盘手） | 融合环境、方向、股性；盘后给预案，竞价结论经选股执行链 |
| 候选与竞价 | 财神-选股 | pipeline_eod → ai_ledger record → pipeline_verify / pool_call / ai_caliber → build_cards 与账本 |
| 盘中观测 | 财神-盯盘 | pulse_fetcher.sh → 快照/情绪/异动/时间轴；与盘后候选文件区分 |
| 复盘与机构卡 | 财神-复盘 / 财神-机构调研 | 消费真实证据生成卡片；复盘骨架读 shared_rules，机构原文为主体 |
| 评估与学习 | 财神-回测 | backtest_engine / decision_audit / learning / behavior_attribution；报告与提案不直接授权改策略 |
| 发布 | 财神-客户推送 | push_card 单一出口、卡片校验、镜像白名单与审计 |
| 展示 | 狸猫终端 | React 只读已落盘数据，是唯一展示端 |

技能目录名不总等于调用名：`a-stock-realtime` 的 name 是 `zxl-stock-realtime`；`ticai-lieshou` 的 name 是 `stock-catalyst-hunter`；`hithink-finance__skillhub` 的 name 是 `hithink-finance`。先按当前可用清单核实依赖，缺失时报告并使用已有合法能力，不能假装调用。第三方数据技能的工具说明不自动改变项目生产数据源。

## 2026-11-03 展示更新（覆盖旧盘后独立预案要求）

盘后只渲染天梯、原两池逐票数据/风险/简评、可选题材方向点评；重点仅显式bias=focus，观察重点不可买，池外旧计划不泄漏。普通direction不代表低位；现无明确低位题材事实源则整段省略，不猜字段、不产第三池。观察全池仅作板块参照，observe_only或一字观察不显示无效交易预期/零额/可信度，显式focus仍逐票区分。移除独立预案/在册/战绩/历史基因与机制冗余展示，但后台登记和竞价不变。render不改输入、不补算、不调用metrics和周期持久化。细节唯一源见shared_rules同日最新展示约定；验收只写/tmp、pytest8隔离、card_lint不skip，不推群。

## 2026-11-03 两池更新

真实分池新授权优先于旧观察收容、低位/新方向第三池和预测三档行动约定，业务唯一源为 shared_rules 同日「真实两池契约」。观察显式资格 `participation_eligibility=observe_only` 不授予交易权，09:25回暖不可解冻观察锚；既有5%、量能、个股及情绪风控保持。旧缺字段候选兼容原链，不改历史冻结或账本。条件晋级统计与股性历史分开，不自造概率/门槛。回归非盈利验证。

## 数据与时序契约

1. 每次明确：业务交易日、采集时刻、市场/代码、数据源、单位、复权、缺失与过期状态。文件 mtime 不能替代内容日期；缺失不填零，不用旧快照冒充实时。
2. 情绪分的生产来源为 `cache/feedback_matrix.json`。a-stock-short-term 六维分、理论阈值与生产分不能混用；`promote_rate` 与 `lb_promote_rate` 须说明分母。
3. 盘后 `bias=focus/normal/avoid` 是预案。标签转换 `_plan_label/_is_focus` 不是交易裁决。09:25 池内 `view` 经 `pool_call.call_pool`、`pipeline_verify._inject_pool_views` 注入记录，冻结文件为 `wealth_code/cache/pool_call-<trade_date>.json`，记录读 `open_check.ai_view`。
4. 当前 `ai_ledger._effective_view` 在缺冻结结论时仍由 bias 回退生成 view；无 AI 预案时 verify 仍可能 `mechanical_fallback`。这是实现边界，不能宣称“所有路径彻底不派”。本轮不修改回退策略。
5. 入场判断复用 `ai_caliber.decide/gap_zone`：5%是竞价高开上限，不是止损。还有预期下沿、量能及池内门控；不得简化成“参与且≤5%就买”。AI 自报上限只留痕。
6. 冻结结果不可用后来行情改写。`run_verify(persist=False, out_nav=False)` 不读写冻结裁决并用传入报价重算，但仍可能读生产输入；外围调用是否落盘须另查。
7. 账本当前竞价到同日收盘的收益属于决策评估口径，不能当成满足A股T+1的可执行往返收益或券商真实成交。真实成交须有成交回报；本次核验链路未证明券商下单接入。
8. 历史地位、同线关系和核心延续证据保留业务日期、来源、观察窗口、完整性与反例；结构参照和交易准入分开。业务条款读 `shared_rules.md`「2026-09-30 补充：板块核心与历史地位关联」，执行清单读财神-选股同日章节，不复制第二套排名或固定个股映射。
9. 竞价有效性须核对报价源日期与交易阶段；文件存在、今日mtime、正价条数或高覆盖率均不能代替此校验。采集时间不等于行情源时间，时间缺失不以本机时刻掩盖；盘中累计成交额不得冒充09:25竞价额。来源异常单列问题，文档维护不授权重抓、改缓存、重算裁决或账本。
10. 人工重点观察不等于系统交易终态。昨日预案、今日09:25冻结结论、盘中新增观察分别保留时点与证据；池外复核对象不自动入池或参与裁决。封单快照净变化不等于真实撤单事件，同涨与先后顺序不证明因果带动；不得用后验走势改写事前判断。

11. 前置地位比较使用显式asof/cutoff、完整当日池、逐票历史和一次冻结题材；`ENABLE_PRESELECT_STATUS`关闭或上下文不可用时精确退回旧分池及后置处理。输入不得原位改变；旧新诊断仅随未来正常候选产出，不补写生产历史。09-29缺全量冻结题材及历史特征注入，只能做注明缺口的离线重建，不能宣称严格复现或收益有效。

12. 09:25 池内裁决的防守闸门（`core/defense_gate.py`）**只读、不产生 view**，裁决权仍在 `pool_call`。输入四类且各自时点固定：环境＝昨收 `candidates.environment` ＋ 今开 `core.market_open` 双腿；方向资金＝`cache/sector_flow_history/sector_flow_<上一交易日>.json`（题材取 `core_theme` 优先，兜底 `theme_name`→`sector`）；位置＝前一交易日为止的 `cache/zt_history/zt_<交易日>.json` 逐票涨停序列（断板间隔＝交易日历索引差，**空文件＝节假日占位，不进日历**）＋ 候选的 `dev_10d_pct`/`dev_30d_pct`；派发特征＝前一交易日收盘的 `hs`/`seal_amt`/`lt_mktcap`/`fbt`。两类归档**都有缺口**（板块资金实测 09-23/24/25/29 缺；涨停历史 09-04 才起）：缺失、匹配不到、题材名过泛（命中板块 >8）或该票序列不足 2 条一律**不裁决**，不以邻近日期或更大范围代替。裁决链顺序＝宏观到微观（⑧方向 → ⑨位置 → ⑩派发），位置排在派发之前，⛔ 不得调换（否则卡面只说症状、说不出真原因）。门槛与开关单一源在该模块顶部；`pool_call.POOL_CALL_DEFENSE_GATE=False` 逐字回退，子开关 `_DISTRIBUTION`/`_SECTOR_DRAIN`/`_POSITION`/`_ENV` 分别可关。`call_pool` 新增 `zt_series`/`zt_days` 入参（显式传即不读盘，测试隔离用）。10-08 单日样本只够做定向校准（拦下两只亏损票、未误伤当日涨停票），**不构成收益有效性证明**，真伪待后续交易日累计。
13. **板块联动发布契约**：发布阶段读取 `THEME_LINKAGE_ROLLOUT_STAGE`（`sidecar_record/card_explain/candidate_prefer/auction_gate/strict`），交易纪律读取 `THEME_LINKAGE_CANDIDATE_MODE`（`off/annotate/soft/strict`），两者正交。盘后 `pipeline_eod` 仅写 `linkage_facts_v1`、`linkage_eod_hint_v1` 与展示优先级；09:25 `signal_eval/pool_call` 是唯一可写 `linkage_discipline_v1`、冻结 `pool_call-<交易日>.json` 与影响终态的入口。`candidate_prefer` 不得修改 raw_score、relay/watch、排序、名额、账本或终态。
14. 同题材成员报价缺失必须写 `unknown`，不等同于无支持。映射、报价、联动门异常默认 fail-open 并留痕，不能阻断既有候选或裁决；仅“完整池确认独苗 + 有效同题材报价均未红开”可拦截。盘中联动展示不得写入或改写次日竞价纪律；复盘不得以收盘后信息回填09:25冻结结论；回测仅按阶段/模式/映射指纹切片评估，不能自动切开关。
15. 板块趋势共振的候选字段为 `theme_resonance_facts`，其 `schema` 值为 `theme_resonance_facts_v1`，EOD 标注应在最终 enrich 分支之后。趋势因果链仅为待验证的分析框架，非唯一已证因果；逻辑/催化、板块资金、完整池结构、独立价格技术证据分列，单项不等于趋势，也不识别资金身份。当前板块资金主链尚未真实接通，不声称已有；单日净流出只提示当日流出/背离，不称持续退潮。梯队与 `carry` 只归 `structure_status`；当前独立技术未接入，`technical_status=unknown`，当前不生成 `confirmed`。它只可作预案、卡面与复盘解释，禁止改 raw_score、relay/watch、排序、名额、账本或竞价终态；09:25仍仅由 `signal_eval/pool_call` 基于有效报价裁决。缺失或异常标 `unknown` + fail-open，仅提示研究信心不足，不自动降低真实交易 `view` 或删池；资金背离风险不被单股热度或未经核实消息抵消。
16. 调度角色不因本契约改变时刻：竞价 automation 只负责旁白、准备、兜底或告警；launchd verify 链负责最终裁决、冻结与正式推送。若任务注册表、平台 automation、launchd 现场存在漂移，先核验并由用户确认真源，禁止文档维护时静默改 rrule 或重启服务。

### RSI 研究证据交接（2026-10-27）

- `common/rsi_evidence.py` 为 `rsi_evidence_v1` 计算与质量契约；20日未复权、Wilder、断档重新预热，RSI24缺样本保持空。`asof`只截断交易日期，不证明历史采集时已知，`historical_availability=not_verified`；公司行为未核验，不能直接解释跳变。
- EOD 在最终候选补强之后追加 `rsi_evidence`，`ENABLE_RSI_EVIDENCE=0` 可关闭。只追加研究字段，不改变排序、池子、仓位、共振状态或竞价终态，不补写旧候选。
- 操盘手只读入口：`wealth_code/scripts/ai_ledger.py research-input --decision-date YYYY-MM-DD`，读取已落盘候选，校验RSI代码与asof，输出 `operator_research_input_v1`。这不代表定时Agent已调用；缺字段须标未接通，不自动补算或record/settle。
- 数据底座单票接口返回相同RSI契约；终端优先使用后端正序序列，旧接口回退明确标本地预览。日K `status/recent_bars` 使用严格只读连接，缺库不创建，研究查询支持asof。

### ABC 题材家数同源契约（2026-11-04）

- 公共实现为 `common/theme_base.py`：`live_theme_counts/parse_zt_count` 供盘中峰值 B 与路径归因 C 共用；`snapshot_theme_members/count_member_limitups` 供历史相位 A 使用。非法/缺失家数保留 None，显式 0 才是零家；不做族归并。
- 实时 Top5 报告数、逐日 plates 归属与池交集、sector 分组估算分别留来源与 basis，不证明数值完全等价，不将峰值补进相位。上游未给 `zt_count_src` 标 unspecified，不能凭模块名称追认官方来源；日频未保留源时刻则为空。
- B 退榜记录保留最后观察时刻并标 `in_current_top5=False`，不冒充当前值；C 家数未知不判独走/无人跟随，独立板数/价格事实仍按原优先级归因。`today_codes` 仍用于 A 当日未归档目标日补点，不是死参数。
- 只改未来正常产出；不重写旧缓存、相位、账本、冻结裁决，不改评分、仓位、调度或推送。

### 情绪同源契约（2026-11-04）

- 评分唯一源 `feedback_matrix.sentiment_score`，`continuous_v3`连续因子/软收敛，回退开关 `ENABLE_SMOOTH_SENTIMENT=0`。原风险事实与执行纪律保留，不将平滑误解为解除防守。
- pulse采集阶段的score为None；feedback完成后仅对同日同一 `source_snapshot_ts/latest.ts` 回填，携带model/source_at/status。外层环境适配与题材序列不把旧有符号分当10分制。短暂pending是未知，不补0或上一轮值。
- 新日频归档保留模型、质量、缺失清单和评分因子；旧历史不改。摘要跨模型不做变化归因或连续拐点。完整历史因子缺失不能宣称已经做过全历史新旧重算。
- 环境输入缺评分保持None，不补中性5；缺核按钮/炸板输入不能因默认0判risk_on。前端反馈解析保留模型/质量/来源时刻，partial提示为部分评分；空分潮位待确认，不按JavaScript空值比较判冰点。此项不是全系统默认值迁移完成的声明。
- 深层消费：relay_score 缺失/非法情绪不补5、不当0扣罚，独立regime乘数保留，并输出情绪调制可用性与质量。expectation_ai 缺分为未知，不授予依赖情绪的溢价、不贡献情绪置信权重，卡面标情绪待确认；量价区间仅作形态参考，不宣称环境放行。有效评分的既有四阶段映射暂留，偏强/退潮标签映射尚待统一。
- 评分结构（2026-11-04 细化）：`FACTOR_REGISTRY` 是因子单一数据源（12 因子：输入/维度/点表/方向/来源只声明一次），`sentiment_score` 与 `sentiment_breakdown` 共用同一装配入口 `_factor_values`，`factor_set()` 按模型返回因子集（legacy_v2 返回空，`decomposable=false`，不伪造等价注册表）。⛔ 调因子或改点表只改注册表，且元组顺序=浮点累加顺序，改动须重跑等值回归。
- 分维度诊断 `sentiment_breakdown` 只解释「分被谁撑起、被谁拖住」，⛔ 不参与排序、名额、买卖裁决；落 `feedback_matrix.sentiment_breakdown`，与主分必须同值，不一致则整段落空而非落矛盾数据（`ENABLE_SENTIMENT_BREAKDOWN=0` 可关）。
- 覆盖率 `coverage/coverage_grade`（high≥0.85 / medium≥0.6 / low / none）只描述「有多少观测支撑」，⛔ 不设停发阈值——覆盖不足是否停发需自然样本校准，未校准前不得把缺几个因子判成行情不可知。
- 可选参数默认 `None` = 未提供观测，⛔ 不写 `0`：写 0 会让诊断把「没取到」渲染成「已确认无风险」。两者对强度贡献同为 0，但 `observed` 标记与缺失清单必须能区分。
- 新公式随守护自然产出；禁止为验证主动抓行情、补跑、推卡或改冻结账本。代码/测试/自然产出验收分别报告。

### 单票事实画像契约（2026-11-04）

- 唯一读入口 `common/stock_profile.py`：`build_profile(code)` / `build_profiles(codes)`（`ENABLE_STOCK_PROFILE=0` 关闭）。8 维声明式注册表 `DIMS` + 工厂 `dims()`，单个与批量共用同一份声明，⛔ 禁「单个一套、批量另一套」。
- 只读：registry 以 `mode=ro` 打开（不建表、不抢写锁），board_track 索引进程内按 mtime 失效；⛔ 不写任何文件、不参与候选/排序/名额/裁决/推送/调度。
- 每维独立 `status` = `ok / stale / missing / error`，并带 `asof` 与来源。三种「没有」必须分清：`missing`＝未观测；`stale`＝台账落后于最新交易日；`is_current=False`＝该票不在最新台账（断板/降首板，是状态不是数据坏）。⛔ 后两者混报会反过来污染判断。
- ⛔ 缺股性档案 = 不知道，不得渲染成「难连板」；不在连板台账（boards<2 不入册）= 未进入跟踪，不得渲染成「今天没涨停」；数值缺失返回 `None` 而非 0。
- 时间性：连板台账由盘后生成（实测 15:37），此前 board 维恒为 `stale`，这是正确状态而非故障。
- ⛔ 与 `core/snapshot.stock_view(date, code)` 不是一回事：后者答「今天决策链各环节怎么说」（单日 section），本模块答「这票是什么、走到哪了」（跨日事实）。不再新增第三套同名能力。
- 查询边界：`common/data_gateway.stock_profile` → `GET /api/data/stock-profile?code=` → `scripts/data_gateway.py stock-profile --code X`；仅接受 6 位代码，不返回数据库路径或 SQL。
- 消费方迁移（snapshot.stock_view / board_track.lifecycle / daily_screen / 卡面）尚未进行，属待拍板事项，本契约不构成「已接入决策链」的声明。

### 竞价一字 → 方向线索契约（2026-11-09）

用户原话：「竞价的时候如果有一字涨停，需要留意他的炒作方向，比如今天的西部材料带动商业航天逻辑！！」→ 拍板「可以 没问题！！先看见」。

- **只做解释层**：`core/auction_seal.py` 产出的清单进竞价卡一段（🧲 竞价一字 → 方向），⛔ 不进 `pool_call` 名额、不进仓位、不改 `can_enter`/`view`/评分排序。一字板的隔壁票⛔ 不因此成为买入理由（11-06 实证：同题材龙头一字封死时隔壁票 T+1 −1.49%、胜率 40.4%，虹吸不是外溢）。
- **判据（三态必须分开）**：窗口 09:15~09:30，涨停线容差 0.2pct（与 `core.ai_caliber.PHYSICAL_LIMIT_TOL` 同源，测试有等值断言）。
  - `sealed` 全程 pct 均 ≥ 涨停线−0.2 **且** 末条观测 ≥ 09:25（不可撤单期结束仍封）；
  - `unconfirmed` 全程未下探但末条 < 09:25 → **不知道**，⛔ 不得当一字；
  - `wobbled` 中途下探过（含 09:42 回封）→ 撤单虚顶。
  - 技术基础：`stock_observations` 是 `INSERT OR IGNORE` + `dedup_hash`（同日同源同值只留一条），因此**「中间没有记录」=「值没变过」**。
- **⛔ 不许拿 09:15 涨幅当一字**：11-09 实测 09:15:09 那批 pct≥9.9 有 259 只，绝大多数是虚顶（阳光照明 09:15 +10.0% → 09:24 +5.56%；润贝航科 09:15 +9.99% → 09:23 +7.33%）。
- **方向取题材标签，⛔ 不读行业名**：002149 行业=小金属、题材=商业航天；002889 行业=物流、题材=半导体。读 `stocks.industry` 会把方向整个丢掉。标签经 `theme_base.is_attr_plate` 过滤（业绩增长/ST/重组…），全属性 → `None`，⛔ 不编「其他」。
- **缺失语义**：无观测 → `found=False`，⛔ 不写「无一字」；卡面「取不到」写「未取到（原因）」；sealed=0 但 unconfirmed>0 时写「尚未确认」，⛔ 不许报成「没有一字」（卡片可能生成在 09:25 后、09:26 快照前）。
- **只读边界**：`common.stock_registry.load_auction_window`（`mode=ro`，缺库不建库）→ `common.data_gateway.auction_seal` → `scripts/data_gateway.py auction-seal --date`。`ENABLE_AUCTION_SEAL=0` 关闭；卡面 `VERIFY_CARD_HIDE=seal` 隐藏该段。
- **已知覆盖缺口（未修）**：竞价快照只覆盖「昨日涨停池 + 异动池」，采集来源仍是"往上走"的名单。11-09 实测 411 只里 168 只无法确认。这会让部分真一字落进 `unconfirmed`，但⛔ 不因此放宽判据。⚠️ **"09:20~09:25 采样稀疏"这个说法 11-10 已证伪**：`fetch.log` 显示竞价段每分钟一轮、共 30 轮，稀疏的是**去重后的记录条数**（同值只留一条），不是采集轮次。

### 观察池行情快照 / 竞价弱转强（2026-11-09）

用户原话：「竞价的时候，昨天涨停，今天开盘竞价跌停，然后慢慢的居然红开了。我靠，这种竞价弱转强，我也是非常想要买的呀」＋「不用补全量，但是可以将我们观察池里面的所有票全部都打上市场快照，因为大概也就只有二三十个票」。

- **病根＝采集视野只有上涨端**：`intraday_pulse` 写底库的三个来源（选股宝异动池 / 昨日涨停池 / 东财涨幅榜）全是「往上走」的名单。一只票竞价从跌停往红开走时，**跌的那一段它不在任何名单里**；等它涨起来进异动池，序列起点已经是红的。11-09 实测竞价段 411 只票 740 条观测，**涨幅曾 ≤ −5% 的 0 只** —— 不是市场没有，是看不见。
- **范围**：当日 `pool_call` 池 ∪ 最近一期 `candidates` 的 relay+watch（实测 15 只，上限 `MAX_CODES=60`）。⛔ 不扩全市场。
- **⛔ 只写行情事实**：`pct`/`price`/`name`。**不写 `limit_up_now` / `limit_up_touched`** —— 涨停闩锁是选股宝的强证据，不能被行情快照污染。
- **两处落盘，各答一个问题**：
  - `cache/watch_pool/<date>.jsonl` 每轮一行（**心跳**）。`stock_observations` 按「同日同源同值只留一条」去重，一只票全程趴在 −10% 不动只会留 09:15 一条，读出来会误判成「不知道 09:25 还在不在跌停」；心跳必须单独留。
  - `stock_observations`（`source=intraday:watchpool`）有变化才留痕，进入统一个股事实主干。
- **弱转强四态**（`watch_pool.auction_paths`，窗口 09:15~09:30）：`reversal` 曾 ≤ −5% 且末条 > 0；`pending` 末条早于 09:25 → **不知道**，⛔ 不得当弱转强；`flat` 走了全程但无深水反转；`no_data` 窗口内没抓到。
- **回落语义**：心跳在窗口内一条都没有（例如快照是当天中午才上线）→ 回落 `stock_observations`，返回里 `source` 如实标 `registry`。⛔ 只在「窗口内全空」时回落，绝不把两条来源混成同一段路径。
- **陈旧价**：行情时间戳落后超过 `STALE_SECONDS=180`（竞价窗口放宽到 `AUCTION_STALE_SECONDS=900`，否则 09:15 的价到 09:24 会被判成旧价、整段丢掉）→ 不写、不评，记入 `stale`。
- **✅ 该假设 11-10 已验证成立**：东财 `ulist` 在集合竞价时段**确实刷新**。当日观察池 25 只、心跳 30 轮、`no_data=0` / `pending=0` / `source=heartbeat`（不是观测表兜底）、全局末采样 09:30 —— 竞价段数据首次全程可用。⛔ 陈旧阈值仍保持 `AUCTION_STALE_SECONDS=900`，不放宽。
- **只读出口**：`common.data_gateway.watch_pool_paths` → `scripts/data_gateway.py watch-paths --date`；`python3 common/watch_pool.py {pool|snapshot|paths}`。`ENABLE_WATCH_POOL_SNAPSHOT=0` 关闭。
- **⛔ 不做裁决**：本层只产「路径事实」，不进名额 / 仓位 / 买入 / 评分 / 排序。

### 竞价卡 · 观察池弱转强段（2026-11-09 当日追加）

用户原话：「弱转强写进竞价卡里面」＋「只关注我们当天收盘锁定的那些票，不需要看场外的那些票」＋「根据时间戳来更新，记录一次时间再记录一次时间，对比价格变化就能看出来趋势变化了呀。哪怕没数据也没关系，做好数据的兼容就好」。

- **接线**：`pipeline_verify._watch_reversal_payload(trade_date10)` → `payload.watch_reversal` → `build_cards._watch_reversal_block` → 竞价卡 **🧲 竞价一字之后、🤖 今日买入之前**（一字答「哪条线被点亮」，弱转强答「我锁定的票里谁被砸后爬回来」）。
- **池子边界**：只看观察池（昨日盘后 `candidates` 的 relay+watch+low_pool，09:25 后并上当日 `pool_call`）。⛔ 不扩全市场，⛔ 不看场外票。
- **三时点轨迹**：每只 reversal 票给出「首笔 → 最低 → 末条」，各带 `HH:MM` 时点与涨幅，另附拉升 pp。这正是用户说的「记录一次再一次看趋势」——只给两端看不出过程。
- **⏱️ 确认判据按来源分开（11-09 实测踩到，务必不要合并）**：
  - `heartbeat`：每轮全量写 → **票级末条 ≥09:25** 才算确认；
  - `registry`（兜底）：同源同值只留一条 → 票级末条 = 最后一次**变化**时点，一只票 09:16 后没动过就永远停在 09:16。⇒ 改用**全局最后采样时点 ≥09:25** 判确认（全局覆盖了 09:25 且该票无新记录 = 值没变、延续到末刻）。
  - 合并两套判据会让「稳住没动」的票全被判 `pending`（11-09 实测 15 只里多数如此）。
- **`rounds` 有两套含义**：`heartbeat` = 采样轮次；`registry` = **记录条数**（去重后一票可能只有 1 条）。⛔ 兜底时不许在卡面写「N 轮」——740 条记录不是 740 轮采样。
- **数据兼容四态**（用户明确「哪怕没数据也没关系」）：
  | 情形 | payload | 卡面 |
  |---|---|---|
  | 有反转 | `available=True` | 逐票三点轨迹，最多 4 只 |
  | 无反转但有未确认/没抓到 | `available=True` | 「没有深水转红；N 只终态未确认；M 只没抓到」——⛔ 不得笼统写「没有弱转强」 |
  | 一条都没取到 | `available=False` + reason | 一行灰字「未取到观察池竞价路径（原因）」，⛔ 段不静默消失 |
  | 旧 payload 无该字段 | — | 整段不渲染（向后兼容，不报错） |
- **⛔ `pending`（末条/全局末采样早于 09:25）不得当弱转强**；`no_data` 不得当「没反转」。
- **回退**：`ENABLE_WATCH_POOL_SNAPSHOT=0` 停采集，`VERIFY_CARD_HIDE=reversal` 隐藏该段，两者互相独立。

### 竞价变数 · 双向读法 + 全池扫描（2026-11-09 已落地）

用户原话（两段，同一天）：
1. 「**记录此功能，明天退票的时候降低的也要考虑上竞价的变数**」
2. 「**吸收到量化脚本，让备选池中的全部股票明天都按同一套竞价逻辑进行观察筛选。脚本需明确竞价逻辑的判断条件与参数配置，对备选池内每只股票逐一应用该逻辑，并输出可供交易员参考的观察结果与判断依据，便于交易员据此比较并识别其中表现最好的标的。竞价是基于原有选股逻辑上的进一步维度优化**」

**一、一句话原则**：竞价路径是一条线，**两个方向都要读**——往上读是「谁被砸完爬回来」（`reversal`，买点线索），往下读是「谁站在高处被砸下去」（`fade`，退点线索）。⛔ 不许只用它找买点，却在退票时回去看昨收的静态强弱。

**二、判断条件与参数配置（唯一源 `watch_pool.params()` · 全部可 env 覆盖）**：

| 参数 | 默认 | 环境变量 | 含义 |
|---|---|---|---|
| `window_start` / `window_end` | 09:15 / 09:30 | `WP_AUCTION_START` / `WP_AUCTION_END` | 竞价窗口 |
| `confirm` | 09:25 | `WP_CONFIRM` | 不可撤单期结束；末条到此之后才承认终态 |
| `reversal_deep` | −5.0% | `WP_REVERSAL_DEEP` | 「深水」线，曾跌穿才算弱转强候选 |
| `reversal_red` | 0.0% | `WP_REVERSAL_RED` | 「转红」线 |
| `fade_start` | **09:20** | `WP_FADE_START` | **转弱高点起算时刻（不可撤单期）** |
| `fade_high` | +3.0% | `WP_FADE_HIGH` | 高点至少到这里才算「强过」 |
| `fade_drop` | −3.0pp | `WP_FADE_DROP` | 从该高点回撤到这里以下 = 转弱 |

判定顺序固定（⛔ 不可调换）：**未确认 → `reversal` → `fade` → `flat`**。坏值/空值回落默认，⛔ 不静默变 0。

**三、每票档位（`grade`）**：

| 档 | 含义 | 条件 |
|---|---|---|
| `S` | 深水转红 | `low ≤ reversal_deep` ∧ `last > reversal_red` |
| `A` | 竞价走强 | `last ≥ fade_high` ∧ 未跌破 `fade_drop` |
| `B` | 一般 | 已确认、无深水反转、未转弱、末条 < +3% |
| `C` | 转弱 | 09:20 后高点 ≥ `fade_high` ∧ `drop_pp ≤ fade_drop` |
| `D` | 深水未回 | `low ≤ reversal_deep` ∧ `last ≤ reversal_red`（**比 C 更差**，不可调换） |
| `N` | 无结论 | `pending`（终态未到）/ `no_data`（没抓到）——⛔ 不排名、不当任何一边 |

附加 `flags`：`翻绿`（fade 且末条 <0）／`深水未回`／`虚顶回落 X.XXpp（09:15 可撤单期挂单）`。
⚠️ **`虚顶回落` 不是 fade**：09:15–09:20 是可撤单期，首笔涨幅多是撤单前虚顶。

**四、逐票输出字段**：`rank / grade / grade_label / basis / flags` ＋ `first_pct·first_ts / high_pct·high_ts / low_pct·low_ts / last_pct·last_ts / span_pct（last−low）/ drop_pp（last−high）`。
- `basis` 必须带数字与时点（例：`09:20 后高 +9.93%（09:22）→ 末 +5.50%（09:24），回撤 -4.43pp（转弱线 -3.00pp）`），⛔ 不许空口结论。
- 缺失一律 `None` + `—`，⛔ 不写 0；`high` 在 registry 兜底下取末条值（同源同值只留一条 = 值没变）。

**五、排序口径**（写死在 `_scan_sort_key`，便于复核）：档位 → 末条涨幅降序 → 回撤（越接近 0 越好）→ 拉升。`best` 取 `rank=1`。⛔ 这只回答「谁竞价最强」，不含可买性（买不到由 `ai_caliber` 的 5% 空间闸管）。

**六、命令与出口**：
```
python3 common/watch_pool.py scan --date 2026-11-10          # 文本表
python3 common/watch_pool.py scan --date 2026-11-10 --json   # 结构化
python3 common/watch_pool.py scan --date 2026-11-10 --out cache/auction_scan/2026-11-10.json  # 显式落盘
python3 scripts/data_gateway.py auction-scan --date 2026-11-10   # 网关投影 auction_scan
```
⛔ 默认**不落盘**；`common/data_gateway.auction_scan()` 为只读投影。

**七、边界（一条都不能越）**：
- ⛔ 不进名额 / 仓位 / 买入 / 评分 / `pool_call` 裁决；⛔ 不做自动卖出。
- ⛔ **不得用「昨收弱」当退票的唯一依据**：昨收难看今早可能弱转强，昨收很强今早可能高开低走，退票看**当刻竞价路径**。
- ⛔ 不得反向推论：弱转强 ≠ 可追高，fade ≠ 必跌。
- 数据缺失一律写「没抓到 / 未确认」，⛔ 不当 0、不当「没反转」、不当「没走弱」。
- 卡面（竞价卡 📉→📈 段）**仍只渲染 `reversal`**；`fade` 只在 `scan` 输出里可见，要进卡需另改 `build_cards`。

**八、11-09 实测（15 只观察池，仅证明判据能算出东西，⛔ 不作阈值校准）**：
- 档位分布：`S` 0 / `A` 9 / `B` 0 / `C` 4 / `D` 0 / `N` 2（鲁阳节能、上海电影没抓到）；状态计数 `reversal 0 / fade 4 / flat 9 / pending 0 / no_data 2`。
- 转弱 4 只：永臻 −4.43pp、红太阳 −3.98pp、万向德农 −3.31pp、贝特利 −3.01pp。
- ⚠️ 09:15 虚顶必须剔除：贝特利用首笔算会得 −13.53pp（假），用 09:20 算 −3.01pp。

**九、退票的三类对象（先说退什么，再说怎么退）**：

| # | 退的对象 | 竞价走低的含义 | 处置 |
|---|---|---|---|
| ① | 盘后 `candidates` 里明天准备接的票 | 今早没接住 = 不接 | 已有 `pool_call` ⑨⑩ 闸，本层不重复判 |
| ② | 观察池里只是盯着的票 | 只降关注度，**不是交易动作** | 无动作 |
| ③ | 已有持仓 / 昨日已买 | 这才是真正的「退票」 | ⚠️ **卖出侧仍零代码**，本层只提供 `fade` 事实 |

### 强基因豁免 · 量能闸（2026-11-10 用户拍板）

用户原话：「**量能可以增加强基因豁免，但是我这边说的历史连板，是指类似于冰山冷热这种，近一个月有连板，昨天涨停**」。

**一、口径纠正（这是本节存在的唯一理由）**：`max_boards_hist` 是**永久峰值**，会把「很多年前猛过一次」算成强基因。实测反证：

| 票 | `max_boards_hist` | 近一月最高连板 | 近一月涨停 | 真实状态 |
|---|---|---|---|---|
| 冰山冷热 000530 | 6 板 | **4 板**（10-13~10-16 四连） | 9 次 | 一个月内反复被资金用 |
| 锋龙股份 002931 | **18 板** | **2 板** | 3 次 | 18 板是老黄历，最近只在试探 |

⇒ 判据换成**窗口内**（`GENE_RECENT_DAYS=31` 自然日）最高连板 + 涨停次数。

**二、⛔ 与 `gene_height.prev_streak_boards` 不是一回事（已在模块 docstring 写死）**：
- `prev_streak_boards` = 最近一次**已结束**连板段的最高板数 → 中间夹了单板就断。冰山 10-16 四连之后又打 5 次单板，它返回 **1**（答错）。
- `recent_gene` = **窗口内出现过的最高连板**，不被打断。冰山返回 **4**（答对）。
- 两者各有用途：**龙一认定用 `prev_streak_boards`**（比"上一波谁硬"），**量能豁免用 `recent_gene`**（比"最近还在不在动"）。⛔ 不互相替换。

**三、缺失三态（⛔ 不许压成"弱"）**：无档案 `known=False, max_boards=None`（不知道）／有档案但窗口内没涨停 `max_boards=0`（确实没有）／有涨停但无连板 `has_streak=False`。

**四、豁免范围（只让开一处，不是拆闸）**：

| 闸 | 原行为 | 豁免后 |
|---|---|---|
| 「复核票须 full 量才算推翻盘后判断」（`POOL_CALL_RECHECK_STATE_FULL`） | soft/dry 一律 watch | **强基因 + soft → 放行**，仓位仍走 `volume_scale`（0.75/0.5），不加满 |
| 量能硬地板 `dry < 0.35` | 拦 | ⛔ **照旧拦**（31 日回放：拆了日均 2.79%→1.30%） |
| zone / 5% / 预期 / 名额 / 排序 / stance | — | ⛔ **一律不动** |

- 强基因判定：`gene_recent.is_strong`，默认 **`近月最高 ≥3 板`**（`POOL_CALL_GENE_MIN_BOARDS`，2026-11-10 10:13 用户拍板「3」）∧ 涨停 ≥0 次（`POOL_CALL_GENE_MIN_ZT`）。⛔ 硬地板 2：无论怎么配，**单板永不算强基因**。
- **为什么是 3 不是 2**：按字面「近月 ≥2 板」，11-10 池内 **22/25 命中（88%）** —— 池子本身就是「昨日涨停候选」，人人都刚涨停过、近月几乎都有两连板 ⇒ 豁免接近拆掉量能闸。收紧到 3 → **8/25（32%）**；`=3` 且 `GENE_MIN_ZT=5` → 7/25。
- 3 档留下的 8 只（11-10）：闽发铝业 6板·7次、上海九百 5板·6次、冰山冷热 4板·9次、澳洋健康 4板·6次、光洋股份 4板·5次、文投控股 3板·8次、东方嘉盛 3板·6次、大晟文化 3板·4次。
- 2 档多出、3 档踢掉的 14 只全是「近月只到 2 板」：龙江交通、顺灏股份、红太阳、大东方、日出东方、泰尔股份、**锋龙股份**、超声电子、盛洋科技、安泰集团、胜通能源、东方新能、润贝航科、东方创业。
- 开关 `POOL_CALL_GENE_EXEMPT=0` 逐字回退（soft 一律拦）；`POOL_CALL_GENE_MIN_BOARDS=2` 可回退到旧门槛做对照。

**五、11-10 真实回放（冻结件反推 quotes，只读）**：
- 门槛 2 → 让开 3 只：锋龙股份 002931（仍被 ⑨b 老票复燃闸拦）、红太阳 000525（仍被派发闸拦）、**文投控股 600715 真买到**（当日封板 +10.04%）。
- 门槛 3 → 只让开 **文投控股**（3板·8次），`participate` 仍是「东方创业 + 文投控股」2 只，quota 2/2。
- ⇒ **今天两档实际成交结果相同**，一天样本证不了优劣；改 3 的理由是「2 在候选池里 88% 命中＝没有区分度」，不是今天的收益差。

**六、留痕**：每票 `recent_gene` 字段（事实透传）＋`gene_exempt` / `gene_exempt_reason`（带"近月 X 板 · 涨停 Y 次 · 量 Z%"数字）；`meta.recheck.gene_exempt` / `gene_exempt_bought` 答「今天几只靠股性让开量能闸、其中几只真买到」。

**七、⚠️ 窗口覆盖必须自证（11-10 修的一处真错）**：
- 基因窗口是 **31 自然日 ≈ 22 交易日**，而共享 `zt_series` 只回看 `POSITION_LOOKBACK_DAYS=20` 交易日（≈28 自然日）。实测 11-09：20 日序列最早只到 **10-13**，窗口起点 **10-09** ⇒ 前 2 个交易日被吃掉，**安泰集团 600408 被算成 1 板（真实 2 板）、文投 600715 涨停次数少 1**。
- 修法：`pool_call` 为基因**单独补一份 `days=max(POOL_CALL_GENE_LOOKBACK_DAYS=30, 22)` 的序列**，⛔ 不改共享 `zt_series`（位置闸/老龙闸的 20 日口径不能跟着变）。调用方显式传 `zt_series` 时原样用，保证测试隔离。
- `recent_gene` 新增两字段：`avail_since` 入参（**数据源**覆盖最早日）＋`truncated` 出参（True 截断／False 完整／**None 不知道**）。
- ⛔ **不许拿「该票第一条涨停日」判截断**：那会把「十几天没涨停的正常票」误报成数据缺失（冰山 000530 首条是 10-13、锋龙 002931 是 10-23，都是正常轨迹，不是截断）。
- ⛔ 截断也不许谎报 `window` 起点：真截断时 `window[0]` 写 `avail_since`，label 加「（序列仅覆盖到 X，次数可能少算）」。
- 截断**只影响 `zt_n`**，不影响 `max_boards`（当日 `boards` 是累计板数，截头不改变段内峰值）—— 但当 `GENE_MIN_ZT>0` 时就会影响判定，所以必须修。

**八、只读边界**：`core/gene_recent.py` 纯函数 + `load_map` 复用 `defense_gate.load_zt_series`（⛔ 不另读 zt_history）。测试 `tests/test_gene_exempt.py`（30 项，全隔离，显式传 `zt_series`）。

## 副作用边界

| 操作 | 需明确的副作用 |
|---|---|
| eod / verify / ai_ledger record、settle | 候选、报价、冻结结果、指标、账本可能写盘；不可当只读诊断 |
| decision_audit | 写报告和日K缓存 |
| reflow_backtest | 写报告，可能追加前向信号记录 |
| ai_self_review | 写 cache/ai_self_review.json；不改阈值不等于文件只读 |
| learning / gen_proposals | 写建议与历史；当前 RULE_RISK 七项全 high，_writable 阻断自动改参 |
| behavior_attribution | 默认不持久化；--persist 写输出，具体路径先查参数 |
| push_card --dry-run | 不发送，但当前输出可包含完整 webhook URL；必须脱敏，不回传原始输出 |
| core/defense_gate（由 pool_call 调用） | 纯只读，不写缓存/账本/冻结文件；结论经 pool_call 落 `pool_call-<date>.json` 的 `meta.defense` 与逐票 `defense_gate` 留痕 |
| common/watch_pool.snapshot（intraday_pulse 每轮调用） | **会写盘**：`cache/watch_pool/<date>.jsonl` 追加一行 + `stock_observations` 新增 `intraday:watchpool` 观测。只写 pct/price/name，不碰涨停闩锁与裁决 |
| common/watch_pool.scan / data_gateway.auction_scan | **默认纯只读**，不写任何文件；只有显式 `--out <path>` 才落一份 JSON，目录不存在会创建 |
| core/gene_recent（由 pool_call 调用） | 纯函数；`load_map` 复用 `defense_gate.load_zt_series`，只读 `stock_registry` 涨停序列（降级读 zt_history），⛔ 不写盘、不建库 |
| scripts/gene_exempt_replay | 只读回放（复用 `pool_call_replay` 口径）；唯一落盘 `cache/relay_review/gene_exempt_replay.json` 诊断报告。⛔ 不碰账本/冻结件/生产卡 |

## 评估解释约束

- 回测需报告窗口、样本量、缺失、基准、费用、滑点、涨跌停买不到、停牌与T+1假设；分开候选收益、账本评估和真实成交。
- 覆盖率需结合区间宽度、校准及基准分析，不能单凭覆盖率高低断言预测有效或无效，更不能直接推导收益提升。
- “清仓距今”窗口因卖出日不同而不同；卖后涨幅均值除以平均年数不是有效年化。卖后上涨不能证明卖错，少量长期持有盈利不能证明长期持有有效。
- 成交时刻不能证明追涨，金额不能证明满仓，多笔成交不能排除拆单。行为归因给待验证假设，不自动确诊交易纪律。
- behavior_attribution 当前 profit_factor 为百分比求和之比；净投入收益不是时间加权净值；日级净现金流峰值是仓位代理。CSV量纲启发式和缺失收益处理须核对原始数据后解释。GATE_* 是诊断阈值，不等于已接入交易硬门。
- target_pct / stop_pct / hold_horizon 为待接入字段，本次源码检索未发现账本实现；不能以已排期任务声称完成。

### 短线精灵重复收敛契约（2026-11-05）

- 三类重复分别治理，⛔ 不用一个「总去重」糊过去：①同一轮同 code（涨幅榜＋涨停池＋leaders 兜底三路拼接）由 `_dedup_signals` 在 up/down 合并后的唯一装配点收敛，`GROUP_VIEW_CACHE` 同源去重，避免联动家数虚高；②后排候选（跟风／今日可能涨停）当日已展示不重展，`ENABLE_SPIRIT_BACKSEAT_DELTA` 可回退；③接力方向 `_relay_identity` 只认「板块／前排／梯队／可参与成员」，涨幅与家数抖动属状态，⛔ 不得绕过 `RELAY_COOLDOWN_MIN`；`ENABLE_SPIRIT_RELAY_IDENTITY_DELTA=0` 回退旧指纹判定。
- 三个「已展示」状态（`linkage_sent`／`backseat_shown`／`relay_state`）一律**仅成功送达才提交**；relay_state 按日切分，跨日旧档作废。发送失败下一轮可重试，不把未送达记成已推。
- ⛔ 不改推送节奏：`--force` 早盘高频窗口「有信号就立刻推」是用户纪律；`batch_threshold`／`batch_timeout_min`／`max_signals`／`top_per_sector`／排序／评分／调度一律不动；`--send` 统一出口的 45s 内容 hash 防抖也不改。**卡片密度未收敛，跨通道（`live_signal` 透传卡 vs 主卡）未做全局节流**，需用户拍板后再动。
- 只改未来正常产出；不重写旧事件流、卡文件或账本，不手动补跑、不推送测试卡。事件流缺 front／ladder／pct，回放压降为近似估算，不得当精确值或收益证明。

## 验证标准

- 文档变更：元信息可读、链接和路径存在、参数/函数锚点可核实、无重复权威声明；只报告实际做过的验证。
- 代码变更：按影响面跑隔离测试；注入临时输出目录，禁止测试写生产。dry-run后检查生产路径未新增或改变文件。
- 卡片变更：对真正渲染的卡JSON做结构、全量文字、日期和隐私检查，词表唯一源 `scripts/card_copy_rules.py`，门禁 `scripts/card_lint.py`；不能以plan数据替代卡片验收，不能默认 --skip-lint。
- 推送：用户已授权时才实发，测试群也属于外发。明确通道，检查镜像命中；不得在报告、命令回显或技能中暴露凭据。失败不能记成成功或先提交去重状态。
- 调度与守护：文档里的时刻只是角色说明，当前状态现场核验。保留既有触发前后15分钟不改任务、长任务调整放收盘后的约束；不因加载技能自动重启、补推或变更计划。
- 前端代码变更执行狸猫终端规定的门禁，文档维护不冒称通过运行或视觉验收。

## 保留待确认的策略冲突

- 龙头战法交易员“至少1个participate”与允许空仓、09:25才裁决的关系。
- 周/月持有新需求与既有短线退出表如何衔接；5%入场线不得代替止损参数。
- gen_candidates 中创业板0.85系数与用户主板/创业板对称参与要求的关系。
- 缺冻结结果时是否允许bias回退、无预案是否允许机械回退。

这些冲突不靠本文件改参数解决；在获得具体实现任务后，分别设计、测试并验证真实产物。
