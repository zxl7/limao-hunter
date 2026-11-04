# 财神与量化技能执行契约

本文件统一技能职责、输入输出和验收方式，不新增策略，不替代 `shared_rules.md` 的业务规范，也不授予修改参数、调度或对外推送的权限。源码核验基准：本次本地工作树。旧文档中的未来日期、任务数量、测试数量不作为运行状态证据。

## 规则与事实

- 用户已确认需求决定目标；`shared_rules.md` 承载公共业务约束；模块技能描述执行步骤；源码与配置说明当前实现。需求与实现不一致时分别报告，不能以“代码如此”宣称需求已满足。
- 参数取源码或配置，不在技能复制第二套参数表。历史案例、设计稿、计划任务和测试通过记录都不等于当前已实现、已运行。
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

## 评估解释约束

- 回测需报告窗口、样本量、缺失、基准、费用、滑点、涨跌停买不到、停牌与T+1假设；分开候选收益、账本评估和真实成交。
- 覆盖率需结合区间宽度、校准及基准分析，不能单凭覆盖率高低断言预测有效或无效，更不能直接推导收益提升。
- “清仓距今”窗口因卖出日不同而不同；卖后涨幅均值除以平均年数不是有效年化。卖后上涨不能证明卖错，少量长期持有盈利不能证明长期持有有效。
- 成交时刻不能证明追涨，金额不能证明满仓，多笔成交不能排除拆单。行为归因给待验证假设，不自动确诊交易纪律。
- behavior_attribution 当前 profit_factor 为百分比求和之比；净投入收益不是时间加权净值；日级净现金流峰值是仓位代理。CSV量纲启发式和缺失收益处理须核对原始数据后解释。GATE_* 是诊断阈值，不等于已接入交易硬门。
- target_pct / stop_pct / hold_horizon 为待接入字段，本次源码检索未发现账本实现；不能以已排期任务声称完成。

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
