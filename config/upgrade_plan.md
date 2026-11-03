# 狸猫猎手 · 系统升级规划基准（团队统一参考）

> **用途**：本文件是系统升级目标的**单一参考基准**。所有卡片生成任务（盘前/盘中猎手（含原快照时点·2026-10-14 并入）/盘后/财富密码 eod+verify）在启动后除 Read `shared_rules.md` 外，**必须 Read 本文件**，将已落地/待办方案与注意事项纳入当卡判断，避免重复说明或遗漏关键要点。
> **维护者**：主助手（Claw）会话负责增改；定时任务只读不写。
> **最近更新**：2026-10-22（全市场轻量日频底库：20 个交易日旁路数据层）

---

## 一、升级总目标（2026-08-16 用户架构原则）

1. **数据/AI 边界**：数据获取固定、客观、可自动执行（脚本）；买入/卖出/评价/情绪分析由 AI 介入并注重环节联动。脚本信号=客观输入，AI=综合研判，三层分离。
2. **情绪联动与动态演变**：当天各卡片情绪互相关联、随时间渐进演变（转好/转差）；每卡承接上一卡情绪（memory_lib market_state.sentiment），输出演变行，禁止孤立判断。
3. **语义色板全局统一**：改善=红、恶化=绿（涨红跌绿推广）；恶化数据严禁红。
4. **数据真实性**：无数据不推信号、不编置信度、涨停标注必须 zt_list 核验（用户底层要求）。

---

## 二、已落地方案（2026-08-16 · 全部验证通过）

| # | 方案 | 位置 | 状态 |
|---|---|---|---|
| P0-1 | feedback_matrix 非交易日守卫（守卫0 is_trading_day + 守卫3 涨停池空/自交拦截），根治周末假数据（promote 100%/亢奋10 事故） | scripts/feedback_matrix.py | ✅ |
| P0-2 | 情绪口径统一：卡面情绪一律 feedback 10 分制（sentiment_score/label），弃 latest.json 5 级粗分 | build_cards._real_env_block | ✅ |
| P0-3 | 盘中事件兜底扫盘 automation-1786859887557（交易日 10 点仅跑 pulse_fetcher --force 触发 live_signal），防 8/13 式守护停摆断供 | automation | ✅ |
| 三层分离 | 规则信号（signal_eval buy/pending 唯一决策）+ AI 风险打标（_narrative_risk_tag 提取 narrative 的 ⚠️风险）+ AI narrative 展示（只说话不投票） | build_cards | ✅ |
| 情绪联动 | memory_lib record --sentiment 写回情绪快照；所有 AI 卡强制「📉 情绪演变」行；骨架 A/B/C + 3 快照卡已含 | memory_lib.py + shared_rules | ✅ |
| 语义色板 | 改善红/恶化绿核心映射表固化；build_cards 7 处 + gen_dashboard 3 处 + live_signal 2 处修复 | shared_rules + 代码 | ✅ |
| 数据边界 | shared_rules【数据获取与 AI 决策边界】+【情绪联动与动态演变】两章宪法 | shared_rules.md | ✅ |
| 当日题材时间线 | 按时间排序的「从早到晚在炒什么」：**不新建文件**，gen_daily_timeline.py 解析 daily_YYYY-MM-DD.md 自身推送记录（兼容 主线/TOP3题材/突发题材Top3-5 三字段）→ 原位刷新「📅 当日题材时间线」章节（幂等），挂 capture_daily.py 盘后链第 4 步 | scripts/gen_daily_timeline.py + capture_daily.py | ✅ |
| 归档交易日守卫 | capture_daily 生成侧加 `_guard_trading_day()`：读 feedback_matrix 的 error 标记（non_trading_day/zt_pool_unreliable）→ 非交易日跳过归档，防周末/节假日假数据污染 5 日窗口（P0-1 同款漏洞补齐） | scripts/capture_daily.py | ✅ |
| 轮动交易日窗口 | theme_rotation `_recent_files()` 按**文件存在的交易日计数**取最近 5 个（eruption）/3 个（flow），替代原日历日回看（LOOKBACK_DAYS=6）——长假后 6 日历日覆盖不到 5 交易日，回流模式失效 | scripts/theme_rotation.py | ✅ |
| 情绪数值日快照 | capture_daily 第 2.6 步归档 feedback_matrix 数值 → short_term/daily/emotion_YYYY-MM-DD.json（结构化情绪历史，替代 AI 手工抄表；8/10–8/14 为 seed 补录） | scripts/capture_daily.py | ✅ |
| 5日情绪汇总自动化 | gen_sentiment_summary.py（capture_daily 第 5 步）：聚合 5 交易日 emotion 快照 → summary_5d.json（情绪/晋级率/核按钮序列+主线+周期提示）→ 幂等刷新 短期情绪周期判断.md 的「5日情绪演变表」（标注🤖自动生成）；骨架 A/B/C 启动链已加 Read summary_5d.json | scripts/gen_sentiment_summary.py | ✅ |
| 外部新闻日归档 | capture_daily 第 2.7 步把 latest.json 的 newsflash 沉淀为 newsflash_history/daily_YYYY-MM-DD.json（事件/催化维度，供 5 日汇总与复盘引用） | scripts/capture_daily.py | ✅ |
| 打分系统·行情强度 v2 | **2026-08-17 用户定**：弃「退潮温度计」（旧模型晋级率<25% 一票-2 → 08-17 涨停63→110翻倍只打 3.0 分·严重失真），改 10 因子行情强度打分——涨停扩张 zt_expand（核心）/上涨广度 up_rate/赚钱效应 avg_pct/晋级率（仅接力质量·不再一票扣2）/连板晋级/核按钮/炸板率/指数强度 idx_avg/最高板趋势/真破位；main 新增 zt_expand/idx_avg 落盘（复用 latest.json 指数零新增抓取）；label ≥9 亢奋/≥7 偏强/≥4.5 修复/≥2.5 分歧/else 退潮；语义色板情绪分 ≥7 红/3-6 蓝/≤2 绿；今日实况 3.0→9.0 亢奋。量能（放量）=强加分信号由 AI 定性引用 | scripts/feedback_matrix.py + shared_rules | ✅ |
| P1-6 | regime 冰点档 + 禁买硬开关 **2026-08-17 落地**：①input_zt 情绪分≤2.0 判 ice（新档）；②signal_eval regime=ice 竞价达标也 reject 冰点禁买；③quant_score freeze 模式 relay 置空全转观察；④build_cards 冰点文案。验证：risk_on 不误伤/ice relay=0/buy→reject/pytest 22/22。**2026-08-19 用户确认生效**：8/19 收盘 1.0 分 → 当晚 eod 自动产 candidates regime=ice/relay=0/watch=8；**同日用户澄清「命中照算·禁买不进场」**：signal_eval 不再提前全 reject，_build_record 将 buy 打标 hit（can_enter=False 不写持仓），summary 增 hit_signals，build_cards 冰点卡照常展示命中票+⛔禁买标注；pytest 28/28 含 ice 命中黄金用例；宪法 shared_rules【冰点期禁买·空仓策略】条款已按新语义更新。连续亏损熔断未做（待 P2-14） | wealth_code/adapters/input_zt.py + core/signal_eval.py + core/quant_score.py + core/dimension_score.py + pipelines/build_cards.py | ✅ |
| 数据孤岛打通·情绪传递链 | **2026-08-17 用户「盘面是情绪整体」审计落地**：①newsflash 消息面接口结构变化（新 baoer-api 返回 data.messages 而非 newsflash.data.messages）导致整链为空 → parse_newsflash 兼容双结构修复（实测 14 条/12 条板块匹配）；②early 盘中强势→盘后候选零传递 → load_pulse 透传 early + run_eod 注入 early_ambush（蓝盾/钢研纳克/名家汇/江西铜业 4 条）+ build_cards 早鸟埋伏区渲染；③em_expect 东财次日预期纯孤岛（抓了没人用）→ load_environment 透传 + expectation_ai 命中上浮+催化标注（共进→CPO 英伟达量产等 4 命中）+ 申万→题材别名映射；④ma5_status 业务零消费 → 抽 common/tencent_kline.py 统一 K 线（消 fetch_kline/fetch_ma510 重复）+ relay_score 回填 ma5_broken 标注 | scripts/intraday_pulse.py + adapters/input_env.py + adapters/input_zt.py + pipelines/pipeline_eod.py + core/expectation_ai.py + core/relay_score.py + common/tencent_kline.py + pipelines/build_cards.py | ✅ |
| P0-量能广度·情绪分12因子 | **2026-08-17 晚落地（盘面情绪整体·量能/广度维度补齐）**：①抓取 intraday_pulse 新增 em_market（东财 push2 f6 成交额+f104/f105/f106 涨跌家数，与 feedback_matrix 同源）→ latest.json 落 `market`/`breadth`；②打分 feedback_matrix v2 扩为 **12 因子**：量能(两市成交额)与全市场涨跌广度以**修正系数**作用于原分（放量×1.04/缩量×0.92；普涨≥3:1 ×1.04/普跌≤1:2 ×0.88），避免强普涨原分近满分时叠加被上限吞（区分度实证：量能±0.4、广度±1.0，放量但普跌 10→9.0 偏强）；③卡片 build_cards 新增「量能广度」行（3.02万亿🔴放量｜涨1777/跌491 语义色板）；④pytest 新增 2 黄金用例（广度修正/量能区分）→ 24/24。数据源已验证：今日 3.02 万亿/涨跌 1777:491 | scripts/intraday_pulse.py + scripts/feedback_matrix.py + wealth_code/pipelines/build_cards.py + adapters/input_env.py + tests/test_golden.py | ✅ |
| 盘后候选三点优化 | **2026-08-17 晚用户定**：①预期区间收窄（expectation_rules 加 max_range_width=2.0，引擎最终 hi=min(hi, lo+max_w, 涨停阈值9.9/19.9)——实测敞口 3.7~4.8→2.0 整，pytest 新增宽度黄金用例）；②字段澄清（「早盘埋伏」=early_ambush 盘中未封板强势透传盘后·只展示不参与分池；「涨多少跌多少」=verify 卡 gap_pct 实际竞价涨幅 vs 预期区间=预测vs实测对照不冗余，shared_rules 核心原则+财神-选股数据契约补定义）；③数据覆盖沪深全市场（parse_em_market 涨跌家数上证+深证相加=5281家·旧仅上证2351漏深证·创业板是深市子集不重复计；成交额3.02万亿不变；latest.json 加 data_scope 口径标注+卡片渲染；surge_stock 返回结构迁移 parse_stocks 兼容双结构修复·早鸟埋伏恢复） | wealth_code/config/expectation_rules.json + core/expectation_ai.py + scripts/intraday_pulse.py + scripts/feedback_matrix.py + wealth_code/pipelines/build_cards.py + tests/test_golden.py | ✅ |
| 核心炒作题材识别 | **2026-08-17 用户「识别市场真正炒作的核心题材·非静态行业」**：新增 `common/stock_theme.py`（ThemeFetcher 抓 surge_stock/stocks description+plates·40+ 关键词规则提炼题材名·热度H/M/L按板块涨停数）；pipeline_eod 候选回填 industry_sector(行业)/core_theme(炒作题材)/core_theme_logic/core_theme_heat 双字段；build_cards 渲染「🏷 行业通用设备｜🔥 炒作金刚石/培育钻石🔴H热」。实证：黄河旋风(主业通用设备→炒金刚石-碳化硅半导体散热·H热)、共进→CPO光模块H、沃格→CPO(英伟达量产)。抓取失败降级空标注不编造 | common/stock_theme.py(新) + wealth_code/pipelines/pipeline_eod.py + pipelines/build_cards.py + config/shared_rules.md | ✅ |
| P2-8 | 资金因子加权 **2026-08-17 落地**（scripts/theme_rotation.py _confidence）：资金 0.2→**0.35**（回流核心证据）、大盘 0.3→0.15（环境分）、催化 0.3 保持、相位 0.2 保持。实证：CPO 资金递增(26→115→130) conf0.9、稀土无资金趋势 0.8→0.75、无资金候选(电磁屏蔽/粮食) 0.55 诚实降权——对齐「资金持续净流出不构成回流」铁律，无资金证据不再靠催化虚高 | scripts/theme_rotation.py | ✅ |
| P2-9 | 分池校准 **2026-08-17 用户"情绪核心"思路回标**（改 core/quant_score.py，非 pipeline_eod._rank_and_split 死代码）：①breakRisk 炸板惩罚按高度区分——高位(≥3板)烂板=人气分歧后封死=辨识度(5.0/次)，低位烂板=封板质量差(12.0/次)；②relay 档 breakRisk 上限放宽（高度突破 68→80 / 高标龙头 68→85 / 主线接力 76→85）；③炸板次数 open 上限放宽（高标龙头 2→8 / 主线接力 8→12）；④watch_group 高标(≥3板)优先归「高标/题材核心」（旧容量标签优先埋没 5 板空间龙）。实证：8/14 买入 3 只全来自观察池、大赢家共进+7.33%/蓝盾+11.32% 均被旧门槛拒于 relay 外；校准后 08-17 共进升 relay#1、金螳螂(4板)升 relay#5。⛔ 全部阈值集中为 ZBC_PENALTY_*/BREAK_RISK_MAX_*/OPEN_MAX_* 常量（quant_score.py 顶部），可一键回滚 | wealth_code/core/quant_score.py | ✅ |
| 同花顺专业数据接入 | **2026-08-26 用户「非常酷·明天开始正式使用」**：REST 通道激活（统一 Key 入凭据文件 0600）+ 新增 `common/hithink_api.py`（统一客户端，X-api-key 头）+ `scripts/hithink_fetch.py`（7 子命令 limitup/lhb/ladder/auction/anomaly/valuation/dump，独立落盘 `cache/hithink_*.json` + 交叉校验 + 可逆开关 `--apply`，零侵入核心链路）。**P0**：涨停池双源校验（同花顺 vs 必盈 重合 50/51·reason 题材归因可注入必盈 50 只）+ 龙虎榜机构净额/游资绰号交叉校验（机构净额与东财同值实证）+ 连板天梯 30 日（与必盈 ladder 完全一致·最高板校验 5=5）。**P1**：竞价双源辅助（benchmark 风向标 + snapshot 竞价量比/未匹配量/昨量比，verify 辅助 9/9）+ 异动原因落盘（213→过滤 688/ST 后 173）+ 估值 enrich（字段 pe_ttm/pb_mrq/ps_ttm，候选注入 16 只）。**P1-4 dump（全市场 Parquet）用户确认搁置**。骨架 C 启动链已加 4.5 步沉淀；pytest 55/55 零破坏 | common/hithink_api.py(新) + scripts/hithink_fetch.py(新) + config/shared_rules.md | ✅ |
| 中枢体检 09-02 优化落地 | **2026-09-02 用户「检查系统+html 控制面板→出优化方案→全部修改」**：①**P0-1 快照写入原子化**——根因实证：latest.json 被并发写坏（双进程各自 O_TRUNC 顺序写 → 新内容+旧尾 → `JSONDecodeError: Extra data: line 1697`，watch.log 09-02 有 4 次实录）；intraday_pulse 改走 common.json_io.save_json（tmp+fsync+os.replace），gen_watch/gen_dashboard 容错读（坏文件保留上一版页面不产出半截）并新增 write_html_atomic（回读校验内嵌 JSON + 内容最小长度护栏）。②**P0-2 质量门修复**——根因：调度器 `python3`=系统解释器缺 PyYAML/pytest，与装依赖的隔离环境不是同一解释器；新增 `scripts/quality_gate.py`（自动挑选可用解释器，跑 配置/任务/契约/单测 四项统一 PASS/FAIL/exit），system_health 新增第 5 维 quality_gate（`--skip-gate` 可跳过）。③**P1-1 日志降噪**——http_client 同主机+同异常类型 10min 窗口合并（首条必落+窗口末补汇总含次数，不静默吞真实异常）。④**P1-2 测试补强**——tests/conftest.py 固定 basetemp 到项目内 .pytest_tmp（受限环境 tmp_path 不再 ERROR）；新增 test_html_atomic（8 用例·原子写/容错读/降噪）、test_schema_guard（4 用例）；修 test_golden first_seen_ordering 盘前文件缺失脆弱性；78→**86 passed**。⑤**契约守卫态豁免**——feedback_matrix pre_auction/non_trading_day 等 error 占位不再被契约误报缺字段（AI 任务仍按 shared_rules 读 error 跳块），非守卫态真缺字段仍 FAIL。⑥**任务台账校准**——tasks.yaml 以 DB 运行时为准回写（竞价验证 9:20/尾盘潜伏 14:30·名含 AI研判/盘后复盘 17:18/回测-每日 17:50/回测-周五 17:40），盘中调度哨兵已删（yaml retired 留档）；⚠️周五 17:40 优化方案早于每日 17:50 回测，次序待用户拍板。⑦**HTML 健康条**——dashboard 顶部新增系统健康条（快照新鲜度·仅交易时段判定不误报/渲染心跳/今日推送成败/今日日志异常数·日期过滤防旧错误显），守护 30s 自动生效 | scripts/intraday_pulse.py + common/http_client.py + scripts/quality_gate.py(新) + scripts/validate_schema.py + HTML/gen_dashboard.py + HTML/gen_watch.py + config/tasks.yaml + tests/* | ✅ |
| P2-18 | CapitalPulse 资金流分层吸收（2026-09-09 落地·设计权威=config/design_资金流分层对接_CapitalPulse.md）：em_flow 分层批量/分钟曲线/日线30日/分级校验；榜单分层注入；dashboard 资金Tab 分层行 subrow；tests 7 例 | common/em_flow.py + scripts/intraday_pulse.py + HTML/gen_dashboard.py | ✅ |
| P2-19 | 腾讯 westock 深化：风险事件/风险公告/两融（2026-09-10 落地）：tx_query 新增 risk_events/notices/margin（单次批量调用·[Batch] 分票解析）；capture_daily 步骤 8 高标风险面归档 → cache/risk_watch_{date}.json；骨架 C 启动链 8.6 + 风险段数据源铁律 | wealth_code/scripts/tx_query.py + scripts/capture_daily.py + config/shared_rules.md | ✅ |
| P2-20 | 情绪因果链 + 轮动反哺（2026-09-10 落地）：_rotation_digest 轮动状态量化（回流候选/新爆发/相位分布）+ _cycle_why 机械归因（delta/drivers/text）→ summary_5d 增 rotation/cycle_why 键 + md 注脚；骨架 A/C 注明字段用途 | scripts/gen_sentiment_summary.py + config/shared_rules.md | ✅ |
| P2-12 | 5日汇总反填自动化（核实为 2026-08-17 已落地·待办条目过时已归档）：宪法【情绪联动】「📈 五日评分轨迹」行（骨架 A 盘前/骨架 C 盘后强制·纯数字序列红升绿降） | config/shared_rules.md | ✅ |
| P1-5 | 退潮/冰点聚合预警卡 + 核按钮限频（2026-09-11 落地）：核按钮限频 09-04 已落地（每日每档1次+恶化升级）；新增 ENABLE_EBB_ALERT + _ebb_dims 七类恶化维度聚合 + build_ebb_card（turquoise·每日1次）；审计 ebb_push/ebb_eval | scripts/live_signal.py | ✅ |
| P2-7 | 涨停播报降噪（2026-09-11 量化核实·已缓解归档）：26只/日 → 实测 14 张批量卡/日（28 audit = 14 卡 + 14 客户镜像），叠加 09-07 当下性硬门 + 每轮≤10 + 批量合并 + 当天去重；是否再叠加高度/封单门槛待拍板 | scripts/short_spirit.py（无需改动） | ✅ |
| P2-10 | reflow/ebb 触发审计日志（2026-09-11 落地·含判定面修正）：REFLOW_PHASE_WIDEN 放宽 phase 判定（分歧/cooling/watch 纳入·boom/dead 排除）；每日首轮漏斗审计 reflow_eval（skip_phase/skip_pool/skip_cool/hits）+ ebb_push/ebb_eval | scripts/live_signal.py | ✅ |
| P2-14 | 避险反向抽取观察档（2026-09-11 引擎侧落地）：_avoidance_sweep（退潮门=情绪≤2或核按钮≥3 ＋ 5日资金≤-50亿 ＋ 当日异动）→ theme_rotation.avoidance_sweep 独立档（⛔不进买点池）；宪法⑥轮动复盘加避险档措辞铁律（禁写回流确认）。实证：国产芯片5日-252.4亿+当日题材活跃入档；更深流出的人工智能因无当日异动被正确过滤 | scripts/theme_rotation.py + config/shared_rules.md | ✅ |
| P2-17 | 机构票实时拉升事件层 L2（2026-09-11 落地）：早盘窗口(09:25-09:55)机构池(73只)批量查行情（em_flow.fetch_quotes 一次请求+60s缓存）→ 拉升未封板(≥3%且<涨停线)即时卡；双闸门=每票每日1次+当日总量6张。实证：73只全返回(双源回落)·命中国际复材+4.32%/东方电缆+3.36%；L1 标注层 09-04 已在骨架B生效 | scripts/live_signal.py + common/em_flow.py | ✅ |
| 📈 历史涨停基因层 | **2026-09-09 用户「股性很重要·要的是历史涨停基因」**：东财涨停池仅存近 1 月≠历史 → 改**不复权日K自算**（涨停价精确判定·ST 5% 天然排除）：近250/500日涨停次数/晋级率/首板次日溢价/历史最长板 → `cache/zt_genes/hist_{code}.json`；**数据源同花顺 REST 优先→新浪回退**（09-09 用户「用同花顺双重验证」·行级一致性已验证 603308）；**统计窗口统一 250 交易日**（双源验证抓出旧口径混用 bug：应流首板高开 1.35% 全历史 vs 0.63% 250窗）；eod 卡常驻渲染「📈 历史基因」行 + cmd_eod 自动预生成档案 | scripts/zt_genes.py + build_cards._hist_gene_line + run_wealth_code cmd_eod | ✅ |
| 📐 连板晋级量化 P1-P3 | **2026-09-09 用户立项·分步交付**：P1 晋级事件层（promo_{date}.json·1进2/2进3/3进4/高标 成功率+次日溢价+失败组亏损·capture step7.5 滚动）+ P2 回测 CLI（--stage/--board/--group/--min-n→报告）+ P3 board_core 板块核心票（时效/封板硬度/连板领先/带动四维·实证对上龙版传媒/亚盛/汉森）。权威=config/design_连板晋级量化分析.md | scripts/promo_quant.py + scripts/board_core.py | ✅ |
| ❄️➡️🌀 冰点产池解冻 | **2026-09-09 用户「冰点也要给我推接力池·买不买由人来决定」**：解除 P1-6 ice freeze——`ICE_FREEZE_RELAY=False`（可逆）ice 跳过 strict 走 relaxed→broad→emergency 收严级联产池，选出多少推多少；买侧 signal_eval hit 语义保留（用户声明买入规则后续单独优化）；连带修复 `_regime_label` 缺 ice 键致标题矛盾（补「冰点防守·观察为主」） | wealth_code/core/quant_score.py + pipelines/card_style.py + build_cards.py | ✅ |
| 🧬 卡片优化铁律·模板增量 | **2026-09-09 用户「以之前的为模版增量叠加·不是重新推卡」**：宪法新增【🧬 卡片优化铁律】🔄-1 原卡模板深拷贝增量（禁重构骨架）/🔄-2 测试版必须 `--webhook feishu_webhook_test`（dry-run 默认 prod 勿照推）/🔄-3 叠加数据读 cache 真值 | config/shared_rules.md | ✅ |
| 🤖 AI 操盘手 + 机械决策层 | **2026-09-09 用户「构建 AI 操盘手 Agent」**：skill `~/.workbuddy/skills/龙头战法交易员/`（2026-11-02 由 AI操盘手 更名）（三数据融合=环境定攻守→梯队定方向→股性定选谁→竞价仓位定执行·攻守状态机 环境×梯队矩阵·仓位风控表·盘前盘中模板）+ 机械内核 `wealth_code/core/decision_outlook.py`（五档门控 hard_block/wait_ice/wait/selective/buy·读 feedback+账本自动决策）→ eod 卡「🎯 明日执行判定」段自动渲染；test_decision_outlook 6 例 | ~/.workbuddy/skills/龙头战法交易员/ + wealth_code/core/decision_outlook.py + build_cards.build_eod_card | ✅ |
| 📋 人机混合决策设计 | **2026-09-09 用户「量化因子+人工判断混合决策」**：四层架构（L0 因子→L1 signal_scorer 汇总→L2 decision_layer 人工闸门→L3 回填）+ 4 人工介入点 + 回填三层；**P0-P3 待用户拍板开工**（设计权威=config/design_人机混合决策.md） | config/design_人机混合决策.md | 📋 设计态 |
| 🌡️ 情绪评估窗口分层 | **2026-09-10 用户「几个交易日合适」→实测拍板**：23 交易日情绪序列实测——当日值→次日涨停溢价 r=0.458（最强·执行层）/3日窗 std 1.57·方向翻转仅 5 次·拐点滞后 1 日（方向层·最佳平衡）/5日窗=MA5 纪律层/10日窗 std 0.53 贴平（仅周期定位·不可做买卖触发）。落地：gen_sentiment_summary 新增 `ma3()`+`turn_signal()`（repair/ebb/flat + **背离修正 ebb_warn/repair_warn**·防单日脉冲误判修复·09-09 实证）+ summary_5d.trajectory.sentiment_ma3/turn_signal + MD 表加「3日窗」列与拐点注脚；test_sentiment_window 6 例 | scripts/gen_sentiment_summary.py + tests/test_sentiment_window.py | ✅ |
| 🤖📒 AI 判断平行台账 | **2026-09-10 用户「能否把你的判断记到真实台账·和 Python 固定脚本对比」**：开**AI 判断轨**——`wealth_code/scripts/ai_ledger.py`（record 记录 AI 每日决策含理由/因素/入场规则·append-only；settle 用**与 Python 轨同源行情口径**结算：T+1 竞价价买入 → T+1 收盘 `return_pct=(close−auction)/auction`·等权·nav 递推；compare 双轨对比胜率/日均/净值；status 看待结算）。存储 `wealth_code/cache/ai_decisions.jsonl`（决策+结算态）+ `ai_nav_history.jsonl`（AI 轨账本·strategy=ai_judgement_next_day_close）。铁律：reason 必填、禁演示数据、竞价缺数据诚实 skip、幂等防重复记账。test_ai_ledger 6 例。⛔ 影子轨不自动下单（验证 AI 收益用） | wealth_code/scripts/ai_ledger.py + tests/test_ai_ledger.py | ✅ |
| P2-15 | 复盘卡冗余重构 · R3 龙虎榜独立速览卡（**2026-09-03 方向 A 用户拍板落地**） | 复盘 17:18 不再渲染 🏦 模块、不再 18:00 后补推整版（8/25 双卡作古）；新建「【盘后】龙虎榜速览卡」automation-1145135d-7ef4-4100-bc95-977462d615cd 每交易日 18:20 触发（lhb_fetch→机构 Top5+游资 Top5+资金性质·hithink 交叉·空窗跳过不硬拼）；复盘精简 9 模块（①删无源"全天轨迹"改定调+五日轨迹行·⑩延续并入⑨·⑥.5只答核心生死·R5/R6）；复盘 prompt/財神-复盘 SKILL/卡片总览 9.6/tasks.yaml/中枢台账同步；18:20 首推验收 | ✅ |
| 🔁 学习层收益导向（L1+L2+L4） | **2026-09-18 用户「命中率降低这不重要…成功率和收益才是我们需要提高的」→ 拍板「可以 先做124！」**：①**L2 冻结 R1/R4 自动回写**（`RULE_RISK` low→high + 新增 `_writable()` 总闸，在 tiered / legacy `high_only` / `apply_suggestion()` 入口**三处**拦截 —— 堵死「legacy 分支按 severity 筛选绕过冻结」的真实漏洞）②**L1 反事实账本接入学习层**（`decision_audit.json` → `audit_window`：各池 EV/胜率/`exec_ret_n` + 采信门 n≥30；R1/R2 evidence 附对照旁证·措辞避卡面 Tier-1 词）③**L4 收益口径多周期**（`common.tencent_kline.fetch_kline_ohlc` 同源补开盘价；t0收/t+1开/t+1收/t+3收/最差收盘 5 档·每档自带可用条数·缺一律 None；交易日历改取**日K实际日期**防 t+1 静默位移）④**L4b 回滚快照修正**（原实现无条件备份 expectation_rules.json → 改 relay 时「备份 A 改 B」、`--rollback` 还原错文件；现按真实目标备份 + 快照名携带原文件名）。实测：`applied=[]` 零回写、审计报告 5 档出齐、全量 **502 passed/1 skipped**、新锁变异 **6/6 被杀**。⛔ 未做 L3 收益规则与 L5~L8（待拍板） | scripts/learning.py + scripts/decision_audit.py + common/tencent_kline.py + tests/test_learning_tiered.py + tests/test_decision_audit.py + tests/test_backtest.py | ✅ |
| 09:25 池内裁决·买入前防守闸门（含位置维度） | **2026-10-08 落地**：四判据（方向资金 / 位置高位二波 / 派发三特征 / 环境降档）接 `core/pool_call.call_pool` 裁决链，顺序宏观→微观（⑧方向→⑨位置→⑩派发→⑪候选）；位置闸=位置高(dev_10d≥45%/dev_30d≥50%) **且** zt_history 断板≥3交易日重启∧上一波最高板≥3 → 不占名额（只有高位只留痕）；⛔ 位置闸≠禁N字(断1~2天快速回封仍加分)；29 例防守测试 + 全量 **894 passed**（现 959）；15:30 接力卡 prompt 步骤5.2 已同步「派发三特征+高位二波」；3 个 commit 分批落地。详见 skill 财神-选股 / MEMORY | wealth_code/core/defense_gate.py + wealth_code/core/pool_call.py + wealth_code/pipelines/build_cards.py + wealth_code/pipelines/pipeline_verify.py + tests/test_defense_gate.py(29) | ✅ |
| 交易日历单源重构（周末恒休市） | **2026-10-09 落地（全仓清扫）**：`common/trading_calendar.py` 定为「某天是否交易日」唯一真源——周末硬规则(`weekday()>=5` 直接 False·不读配置) + 法定休市表 `closed_dates`；**废弃 `open_dates`**（旧把 6 个周末补班日列开市→`next_a_share_trading_day('2026-10-09')` 错返 10-10）；`test_trading_day_single_source.py` 静态扫描拦截仓库内 `weekday()` 自行判定（白名单除外）；同步修 09-25 中秋漏列→已 `closed_dates` 补录 + 清 09-25 幽灵日归档。27 例扫描全过 | common/trading_calendar.py + config/a_share_calendar.json + tests/test_trading_day_single_source.py(27) | ✅ |
| 尾盘潜伏口径收严 | **2026-10-13 用户「只买阴线…前期的热门题材…靠近10日线…10日线必须向上」+「不接飞刀」**：审计先证「4 条机械条件代码里都有」（亏损不是条件没实现），再收 3 处口径——① `LOOKBACK_DAYS` 20→**5**（前期＝近 5 个交易日内爆发过；原 20 日＝僵尸题材：精达/古越龙山/大金重工的热点距今 20 天上下）② `REQUIRE_CLOSE_DOWN`（阴线须**收跌**，跳空高开收阴不算）③ `MAX_DROP_PCT=5.0`（跌幅超限＝**砸盘**非回踩）。真实回放：10-09 3→1 只、10-12 6→2 只、10-13 大金重工剔除（避 −9.36%）。commit `cabef6f` | wealth_code/core/ambush_pullback.py + tests/test_ambush_pullback.py | ✅ |
| 尾盘潜伏·环境读数→仓位建议 | **2026-10-13 用户「退潮期我们控制的是**仓位**，而不是通知票，这些东西还是按照逻辑推」**：`env_read()` 只读 `cache/pulse_snapshots/latest.json`（**当日实时**·T-1 环境判不出当天普跌）四判据（跌多于涨/跌停≥10/涨停<40/炸板率≥35%·命中≥2→weak）；`position_advice()` weak→最多推 2 只、每只≤50%（平摊），normal→不限只数。⛔ **绝不裁候选**（回归锁钉死「退潮 vs 常态 candidates 逐字相同」）。commit `5a7d101` | wealth_code/core/ambush_pullback.py + tests/test_ambush_pullback.py(+3) | ✅ |
| 题材 top5 长期归档 + 题材库沉淀 | **2026-10-13 用户「题材是可以积累和沉淀的毕竟有上限」**：新增 `scripts/top5_history.py` —— 把 `cache/market_memory_<日期>.json` 的 `prev_top5`（**选股宝 top5**·之前被当日快照覆盖、只剩 7 天）逐日落成 `cache/top5_history/top5_<日期>.json` + 重建 `theme_index.json`（题材名→首见/最近/出现天数/最高与累计涨停家数/成分股并集/龙头）；CLI `archive/backfill/index/show`；挂 `capture_daily` 盘后链第 11 步（`ENABLE_TOP5_HISTORY` 可逆·失败降级）。⛔ 幽灵产物闸：非交易日不落盘（10-05/06/07 实测被拦）。首跑：4 日 / 24 题材。6 例回归锁 | scripts/top5_history.py + scripts/capture_daily.py + tests/test_top5_history.py(6) | ✅ |
| 全量个股沉淀二期：技术派生队列 | **2026-10-15 用户确认「所有命中到系统、路过系统的股票都继续迭代」**：盘中命中即写 `stocks/stock_daily` 并入 `stock_technical_queue`；盘后 `capture_daily` 末段对当天全部命中代码统一拉取腾讯前复权日K，写 `stock_technical_daily`（MA5/10/20、3/5/10日收益、20/60/120日阶段新高、均线多头、5日量比）；K线缺失只记队列 error，不填零、不阻断盘后链；CLI `technical --date` 可重跑。回归 49 例 | common/stock_registry.py + scripts/capture_daily.py + scripts/stock_registry.py + tests/test_stock_registry.py | ✅ |
| 个股核心读源三期首批 | **2026-10-15 继续迭代**：新增 `wealth_code/adapters/stock_registry_reader.py` 统一只读适配；`defense_gate.load_zt_series()` 生产默认以 `stock_daily.limit_up_close` 为主源；`adapters/input_zt._load_xuangubao()` 以底库个股事实为主、旧 `yesterday_zt.json` 仅补未结构化字段；`pipeline_verify._load_full_zt_rows()` 同步底库优先，保留旧归档降级。读取开关 `STOCK_REGISTRY_READ_MODE=registry_fallback|registry|legacy`、`WEALTH_USE_STOCK_REGISTRY=0`；定向回归 141 passed，旧 JSON 不删除。 | wealth_code/adapters/stock_registry_reader.py + wealth_code/adapters/input_zt.py + wealth_code/core/defense_gate.py + wealth_code/pipelines/pipeline_verify.py + tests/test_stock_registry_read_adapter.py | ✅ |
| 全市场轻量日频底库（旁路） | **2026-10-22 用户拍板：先建底层、后续慢慢接入。** 新建独立 DuckDB，仅留最近 **20 个实际交易日**的全市场未复权 OHLCV 原始事实；输入为同花顺 `daily-k-10d` Parquet，按 `(code, trade_date)` UPSERT，按交易日裁剪。与 `stock_registry`（系统可见个股事实）及题材快照/Top5历史（时点题材关系）严格分源；日线表不写题材、行业或交易结论，未来只能按 `code + trade_date` 关联当天快照。当前仅显式 CLI 维护，未接入候选、买卖、卡片、推送或自动化；`MARKET_DAILY_ENABLED=0` 可关闭。真实首批导入 55,623 行 / 10 个交易日（2026-10-07 至 2026-10-20）；不为补足 20 天自动拉十年全量包。 | common/market_daily_store.py + scripts/market_daily_store.py + scripts/hithink_fetch.py (`dump --recent`) + tests/test_market_daily_store.py | ✅ 旁路底座 |

---

## 三、待办方案（按优先级 · 未落地）

> ⚠️ **待办清单校准（2026-10-10）**：本节下表内标记「✅ …已落地」的行**实际已落地**（权威记录见【二、已落地方案】对应行：P1-6/P2-8/P2-9/❄️➡️🌀/P1-8/回流引擎ABC/🔁学习层）；**真正未落地 = 未标 ✅ 的项**（P1-7 / P2-11 / P2-13 / P2-16 / L0-体检④）。新增的 2026-10 观察项见【三.5】。

| 优先级 | 方案 | 要点 |
|---|---|---|
| P1-6 | regime 加冰点档 + 禁买硬开关 + 连续亏损熔断 | ✅ 2026-08-17 已落地（ice 档：input_zt 情绪分≤2.0 判 ice；signal_eval regime=ice 竞价达标也 reject 冰点禁买；quant_score freeze 模式 relay 置空全转观察；build_cards 冰点渲染文案；连续亏损熔断暂未做·见 P2-14 评估） |
| P2-8 | theme_rotation 资金因子 0.2→0.35 | ✅ 2026-08-17 已落地（scripts/theme_rotation.py _confidence：资金 0.2→0.35 核心证据、大盘 0.3→0.15 环境分、催化 0.3 保持；实证：CPO 资金递增 conf0.9、稀土无资金趋势 0.8→0.75、无资金候选 0.55 诚实降权；对齐「资金持续净流出不构成回流」铁律） |
| P1-8 | 监管打击·情绪拐点信号 **2026-08-19 用户定·落地**：8/18 晚监管集中出手（爱丽 13天12板+214.8% 暂停账户交易/一鸣 28日+202.8% 严重异常波动/蓝盾 7天+197.2% 停牌核查）→ 8/19 高标集体一字跌停/核按钮 = 消息面缺口实锤（8/19 盘前卡只捕捉隔夜美股未捕捉当晚监管）。①新建 scripts/regulatory_watch.py（扫描涨停池算 10日≥100%/30日≥200% 红线→沉淀 cache/regulatory_events.json 含标的/规则/时间点/压力分）；②capture_daily 盘后链第6步接入；③feedback_matrix 情绪打分第⑬因子（clamp 后减法 -0.2~-1.0，只扣不奖）；④pytest 29/29（含监管因子黄金用例）；⑤宪法 shared_rules 已补【监管打击·情绪拐点信号】条款（含置信度/适用边界/验证方案）。回测：8/18 命中爱丽家居+136.2%/中石科技+115.7% 2只·压力 2.5/10 | scripts/regulatory_watch.py + scripts/feedback_matrix.py + scripts/capture_daily.py + tests/test_golden.py | ✅ |
| P2-9 | relay/watch 分池校准 | ✅ 2026-08-17 已落地（quant_score.py：breakRisk/open 按高度区分+阈值放宽+watch_group 高标前置；实证共进/金螳螂升入 relay；阈值集中常量可回滚） |
| P2-13 | 明日接力权重评分系统（**2026-09-11 影子模式已启·待对照切换**） | ✅ 评分模块 v0.1（五维加权+分档）已建；**影子模式已落地**：scripts/relay_shadow.py 并行评分 → cache/shadow/relay_shadow_{date}.json（对照 eod 原序·⛔ 不改产池·两套刻度字段分开命名 score_eod / score）；⚠️ 实测冰点环境全落「不入池」（最高 43 < 观察线 50）= 环境未归一化观察点（非缺陷·待对照）；切换主流程 = 高风险改动，须数日对照后另行拍板 | scripts/relay_shadow.py + wealth_code/core/relay_score.py | ⏳ 影子已启·待切换 |
| 回流引擎ABC三档补强 | **2026-08-27 用户拍板落地**（尾盘潜伏卡准确性审查产出·六氟化钨连推/农业被挤掉/资金标签失真三问题）：A档 build_cards.ambush 回流候选 = re-entry 全显 + 分歧/cooling 置信度 top2（粮食概念不再被挤掉）；B档 theme_rotation re-entry 悬挂超时 COOL_STALE_DAYS=4——冷却满4天且资金未回流(flow_trend!=inflow_rising)移出候选（资金转流入自动回归）；C档 新增 inflow_fading(流入转弱)——窗口内有净流入但最近2日连续净流出不再误标 inflow_rising（贵金属实证[33.8,34.2,5.4,-7.8,-13.9]），资金 neutral/转弱 conf 封顶 0.6（六氟化钨 0.85→0.6·催化不再单点拉满）。验证：pytest 55 全过 + build() 全链路 + ambush dry-run 结构 4 板块；shared_rules【潜伏卡骨架】已同步。生效：次日盘后链重跑 theme_rotation 后全面生效 | scripts/theme_rotation.py + wealth_code/pipelines/build_cards.py + config/shared_rules.md + tests/test_golden.py | ✅ |
| P1-7 | 外部新闻源扩展（事件驱动增强） | 当前 newsflash 仅选股宝单源；接入财联社/金十 API，按「事件→题材→个股」落库，供情绪判断的事件维度引用（2026-08-16 评估） |
| P2-11 | newsflash 情绪化处理 | 5 日新闻归档已有，但"新闻→情绪影响"未量化；对硬信号快讯做情绪极性标注（利好/利空/中性），融入 5 日汇总（2026-08-16 评估） |
| P2-16 | 盘前预测卡模块梳理（**2026-09-03 用户「明天把开盘预测推送模块也拉出来梳理」**） | 仿 09-03 复盘 R1-R6 同款逐模块审查：骨架 A（盘前预测 06:06）逐模块核对 ①数据源真实性（隔夜外盘映射/消息催化速览/今日主线预判/风险提示——无源即删·编造红线同复盘"全天轨迹"）②凑数/重复（模块5 持仓应对·holdings 为空整模块省略；与机构研报早盘卡 08:50 同日"主线方向"靠口径铁律隔离；与昨日盘后复盘"明日展望"的隔夜衔接是否有冗余）③每模块主题紧扣"今天会发生什么"。⛔ **09-04 执行**（避开 06:06 运行窗口±15min·建议 09:00 后/中午）；权威=財神-选股 SKILL 盘前章节 + shared_rules 骨架 A + 实际卡样本 | 財神-选股 SKILL + shared_rules 骨架 A | ⏳ 待办（09-04） |
| L0-体检 | 调度台账对齐（**2026-09-09 体检处置**） | ①yaml 残留午间卡已清 ✅ ②rrule 漂移 2 处=用户自改（09-09 确认）→ tasks.yaml 已随 DB 对齐 ✅ ③盘前预测 automation 09-04 起断供（update 后 nextRunAt 未重算·runs 停 09-03 但产物/推送靠会话手动补）→ **PAUSED→ACTIVE 刷新 ✅ nextRunAt=09-10 06:06**；⛔ 今日 09-09 盘前卡仅推 test 未进 prod（用户确认环节缺失）——修复后明晨恢复 ④launchd 双守护未加载=域损坏已知态（08-28 起·猎手 --force 兜底维持） | config/tasks.yaml + automation DB | ✅ 已处置（④维持已知态） |

---

### N 批次：口径诊断与黑匣子（2026-09-11 用户拍板「做一批 N1+N2+N3」）

**N1-A（结构性·最严重）供给 < 需求 → 盘中形态 / 题材回流长期缺料**
- `scripts/capture_daily.py`：`RETAIN_DAYS = 5` + `prune_history()` 用 `p.unlink()` **删除** 5 个交易日前的 `cache/zt_history/zt_*.json`
- `scripts/live_signal.py`：`N_SHAPE_HIST_DAYS = 12` —— 需要回看 **12** 个交易日
- → **需求 12 > 供给 5，结构性永久缺 7 天**；`第一波/N字/二波启动` 检测与 `scripts/theme_rotation.py` 的 zt_series 同被压到 5 天
- 修复：`RETAIN_DAYS` 提至 ≥15（建议 20 留 buffer）。⛔ 收盘后改（复盘链 17:18 执行）

**N1-B（口径分裂）两份 zt_history 并存，消费方各读一份**

| 目录 | 天数 | 剪枝 | 写入方 | 读取方 |
|---|---|---|---|---|
| `cache/zt_history/` | 5 | ✅ `RETAIN_DAYS=5` | `capture_daily.py` | `live_signal.py`、`theme_rotation.py` |
| `wealth_code/data/zt_history/` | 23（08-11 起） | ❌ 未见剪枝 | **未定位到写入方（待查·N1-C）** | `pipeline_eod.py`（历史晋级先验）、`theme_roll.py` |

- 另：`cache/zt_history` 的 09-04 / 09-07 两日文件**缺 `zt_list`**（写入时是盘中快照，盘后未沉淀）

**N1-C（待查·风险）** `wealth_code/data/zt_history` 的写入方未在代码中定位到 → 若该链停更，
`historical_relay`（现 174 样本的晋级先验）与 `theme_roll` 会**静默饿死**。须查明并纳入守护。

**N2（已落地前半）决策黑匣子**
- ✅ 新增 `scripts/archive_decision.py`：按日归档 候选快照（含每票「当时依据」）/ 竞价报价 / AI 判断 / 涨停池 / 当日全部卡片（标注调试卡）→
  `cache/decision_log/YYYY-MM-DD/` + `manifest.json`（源路径+sha256+摘要+缺口）+ `index.jsonl`；支持 `--all` 回填、`--verify` 哈希校验
- ✅ 已回填 **24 天**（08-10 ~ 09-10·6.1MB·哈希校验全过）；唯一缺口=`ai_view`（历史本就缺失）
- ⏳ **待接入（20:00 后）**：①接力卡任务（`automation-1786429073866`）增写 `cache/ai_view_{date}.json`（schema：`{"date","views":{code:{"view":"participate|watch|skip","reason"}}}`）
  ②同任务末尾调用 `archive_decision.py`。**根因**：`ai_view` 全仓**只有读、没有写**（`build_cards._load_ai_view` 是唯一消费方），故 AI 判断从未留档

**N3（已完成）因子归因** `scripts/factor_attribution.py`
- 用已有 24 天 candidates + 14 行净值账本，输出胜单/败单因子差异 + 分池净值对照；`--json` / `--min-cov` / `--show-samples`
- 关键观察（样本仅 12 日·**非统计结论**）：接力池胜率 70.6%（n=17）vs 观察池 32.0%（n=25）；
  重建净值 relay-only **+17.2%** / watch-only **−19.6%**（覆盖账本 12/14 天·与官方 nav 不同源，仅作池间相对比较）
- 池内稳健同向因子（relay）：综合分↑、成交额↑、炸板次数↓、同题材家数↓（越拥挤越亏）、连板位↓

### ✅ N1/N2/N3 执行结果（2026-09-12 周六·已落地并验收）

**N1 口径单源 · 已修复（3 处代码 + 1 次回填）**
| 动作 | 内容 |
|---|---|
| 供给修复 | `scripts/capture_daily.py`：`RETAIN_DAYS` **5 → 20**（需求 `N_SHAPE_HIST_DAYS=12`）·附耦合注释 |
| 单一源 | `pipeline_eod.py` 新增常量 `ZT_HIST_DIR = ROOT.parent/"cache"/"zt_history"`；**第 648 行（弱转强）+ 第 663 行（历史晋级）** 改读它；`theme_roll.py` 的 `HISTORY_DIR` 同改 |
| 历史回填 | `cache/zt_history` ← `wealth_code/data/zt_history`：新增 19 天 + 补齐 2 天（09-07/09-11 原缺 `zt_list`）→ **24 天全含 `zt_list`** |
| 守卫 | 新增 `tests/test_history_window.py` 4 例（供给≥需求+缓冲 / 单一源 / 禁幽灵路径 / 残日体检） |

**N1 追加查出的 2 个真缺陷（原诊断未发现）**
- 🐛 **弱转强标注从未生效**：第 648 行原读 `wealth_code/cache/zt_history`（**该目录不存在**）→ `_build_weak2strong_map` 异常被 `except` 吞掉 → 静默返回空。实证：候选文件里「弱转强/weak2strong」**一次都没出现过**。修复后 09-11 立即命中 1 只（000980 断板回封·prev_boards=2·prev_date=09-09）。**此功能自 2026-08-20 上线起 3 周零产出。**
- 🐛 **沉淀链不稳定**：09-04 / 09-07 / 09-11 三日的 `cache/zt_history` 文件只有盘中快照（`intraday_only`）无 `zt_list`。`capture_zt()` 本身会无条件重写并合并 `intraday_points`（已实测可自愈），故根因是**该步骤未在 `yesterday_zt.json` 就绪后执行**（09-11 的 `yesterday_zt.json` 写入时间 = 17:19:40，晚于 17:18 的链启动）。→ 已加**输出告警**：`capture_daily` 落盘后校验 `zt_list`，为空则在链路输出打 `⚠️缺zt_list`。**根治待定**（建议复盘链把 capture_daily 放在 record_zt_snapshot 完成之后，或盘后二次补跑）。

**N1 验收（实测通过）**：`live_signal._load_zt_history()` 取到 **12 个交易日**（原 3 天有效）✅

**N2 决策黑匣子 · 已落地（归档器 + 25 天回填）**
- `scripts/archive_decision.py`：按日归档 候选快照（含每票「当时依据」）/ 竞价报价 / AI 判断 / 涨停池 / 当日全部卡片（标注 debug）→ `cache/decision_log/YYYY-MM-DD/` + `manifest.json`（源+sha256+摘要+缺口）+ `index.jsonl`；`--all` 回填 / `--verify` 哈希校验 / `--dry-run`
- 已回填 **25 天**（08-10 ~ 09-11·哈希校验全过）；近 3 日（09-09/10/11）**缺口=0**
- ⚠️ **更正原判断**：先前写「`ai_view` 只有读没有写」**是错的**。写入方存在 = `wealth_code/scripts/ai_ledger.py record`（接力卡任务步骤 5·见 财神-选股 SKILL L324）；且命名口径为「**判断适用的交易日**」（= candidates 日的下一交易日，`trade_date10=_next_day(date10)`），与消费端一致。周五 16:21 已产出 `ai_view_2026-09-14.json`（decision_date=09-11·11 条）。
- 归档器据此修正日期口径：`available_dates()` **只认 candidates 日期**；ai_view 按 `decision_date` 归属（回退文件名）。曾因并集导致幽灵归档 `2026-09-14`（已清理）。
- ⏳ **待拍板**：是否把 `archive_decision.py` 挂进复盘链（17:18）实现**自动每日归档**。⛔ 未改任何 automation（尊重用户对任务配置的最终决定权）。

**N3 因子归因 · 已落地**
- `scripts/factor_attribution.py`（`--json`/`--min-cov`/`--show-samples`；含分池净值对照）
- 观察（12 日/42 笔·**非统计结论**）：接力池胜率 **70.6%**(n=17) vs 观察池 **32.0%**(n=25)；重建净值 relay-only **+17.2%** / watch-only **−19.6%**（覆盖账本 12/14 天·与官方 nav 0.9328 不同源，仅作池间相对比较）
- 池内稳健同向（relay n=17）：综合分↑ / 成交额↑ / 炸板次数↓ / **同题材家数↓（越拥挤越亏）** / 连板位↓；「板块龙头」标签反向(-6.4pp)；「题材档位」完全无区分(2.0 vs 2.0)
- ⚠️ 附带发现：`nav_ledger` 仅按 `open_check.can_enter` + `next_day.status=="covered"` 纳入 → **relay 与 watch 等权计入同一净值**，净值被观察池拖累（待拍板是否拆池）

---



### 三.6 2026-10 非交易时段系统梳理·遗留观察项（2026-10-10 盘点）

> 本轮（2026-10-10 周六·非交易日）对全系统做了盘点 + 回归基线 + 安全升级，详见下方「本轮交付」。
> 以下按风险分层（含**已落地** / **待拍板** / **已作废**）：2026-10-10 第一批已落地的低风险投资见「低·已落地」行。

| 风险 | 项 | 当前状态 / 处置建议 |
|---|---|---|
| 低·已落地 | **sector_official 映射表刷新归属任务（D2）** | ✅ **2026-10-10 第一批已落地**：挂 `capture_daily` 盘后链末步（`ENABLE_SECTOR_OFFICIAL_REFRESH` 可逆开关，240s 超时 + 失败降级，仅新 code 才联网），次日新板块自动补官方 BK 码；⛔ 关开关退回「不刷新」旧行为（仅影响卡面展示字段，不阻断主链路） |
| 中·已落地 | **展示层统一（B2·撤平行表）** | ✅ **2026-10-11 已落地**：核心动作=消除 `sector_official.ALIAS` 与 `sector_family.SECTOR_ALIAS` 的**平行表**——`sector_family.SECTOR_ALIAS` 升为【聚合名→东财板块】唯一权威源（并入资产重组/智能电网/机器人/国产芯片 4 项），`sector_official.py` 改为 `from sector_family import SECTOR_ALIAS`（带兜底），59 例回归全过。⚠️ 原"BK 码/信号 sector 归一化"两点经核查**不建议动**：撤码违背用户"改东财官方板块名"硬需求（BK 码即官方标识，`official_tag` 已极克制）；归一化会打穿全下游链路（违反 D2"只增字段不改 name"铁律） |
| 高·阻断（功能缺失） | **relay_shadow 切换主流程（P2-13 / C1）** | ⚠️ **2026-10-11 核查：无法出对照报告**——核心依赖 `wealth_code/core/relay_score.py`（五维评分 `rank_relay_candidates`）**不存在**；全仓库无 candidates 历史文件、`cache/shadow/` 不存在 → 影子模式**从未运行、零数据**。"已跑数日"为误判。C1 实为**未完成半成品**：要谈切主流程，须先补建 relay_score + 让 eod 产出 candidates + 挂 relay_shadow 影子积累数日，之后才有对照可拍板。**待用户决策：补建 C1 链路，或暂时搁置** |
| 低·已落地 | **archive_decision 挂复盘链（D1）** | ✅ **2026-10-10 第一批已落地**：挂 `guard_eod_chain` 17:50 末步（`ENABLE_DECISION_ARCHIVE_HOOK` 可逆开关，best-effort 120s 超时），覆盖含 17:18 复盘卡的当日全量决策产物；仅脚本级挂钩、未改任何 automation（尊重用户对任务配置的最终决定权） |
| 低·已验证 | **sector_family.SECTOR_ALIAS 键命中（D3）** | ✅ **2026-10-10 验证完成**：SECTOR_ALIAS 键（大消费/大农业/大金融/闪存/油服/影视/数字人民币）设计正确 = 选股宝名键（与 `live_signal._alias_members` 消费口径一致），非死表；样本 3/7 命中（大农业/大消费/影视），其余未出现 = 行情依赖非缺陷；真实盲区（锂电池/医药/网络安全等）由 D2 每日 `--build` 累积官方映射逐步覆盖；**扩键方案并入 B2**，本轮未改生产 |
| 低·已落地 | **盘前预测卡模块梳理（B1）** | ✅ **2026-10-10 第一批已落地**：仿复盘 R1-R6 审查盘前预测卡模块 → ①宪法骨架 A 模块⑤明确「持仓为空→整模块省略·不写凑数」（与【隐私铁律】对齐）；②新增【P2-16 审查铁律】三段（数据源真实性 / 去凑数去重复 / 每模块紧扣"今天会发生什么"）。纯宪法级文案增量，零行为改动 |
| 低·测试卫生 | **测试套件 I/O 隔离** | 部分测试直写真实 cache 目录 / 发起网络请求（非全量 tmp 隔离），在受限沙箱环境无法跑全量（235 ERROR 为沙箱 I/O 拦截伪像，非代码缺陷）；生产无沙箱环境全量 **959 passed**。建议逐步加 `_guard_test_write` 式守卫，但非紧急 |
| 已作废 | **10-10 周六补班开市日 rrule** | 旧「24 任务 rrule 仅 MO~FR→周六不触发」待办项，已被 2026-10-09 交易日历重构作废——A股周末恒休市，今日(10-10)正确判非交易日，无需改 rrule |
| 低·系统级（待用户操作） | **恢复 launchd 双守护（D5）** | 🔍 **2026-10-11 诊断**：plist 完好（`com.limao.pulse-fetcher` / `com.limao.wealth-verify` 均 `plutil -lint OK`）、脚本在，但 `launchctl list` 无 `com.limao.*` 项 = 未加载；`launchctl bootstrap/load` 报 `I/O error` 因 agent 执行环境为 `Aqua` 非 `gui/<uid>` session（无 GUI 会话连接）。**非文件损坏**。**恢复方式（二选一）**：①重启 Mac（登录时 launchd 自动 load `~/Library/LaunchAgents/`）；②在**终端.app/iTerm**（GUI 会话内）手动 `launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.limao.pulse-fetcher.plist`；wealth-verify 同样。⛔ agent 环境无法代执行 |

## 三.5 三段式流程校验 + 外部新闻融合评估（2026-08-16 审计）

**三段式**（沉淀基础资料 → 获取轮动数据 → 反推整体情绪）**逻辑成立、方向正确**，是"先数据后结论"的正确分层。审计发现的问题与已修/待修：

| 环节 | 漏洞（审计发现） | 处置 |
|---|---|---|
| ①沉淀（capture_daily） | 生成侧无交易日守卫——周末/节假日触发会写假数据文件，污染 5 日窗口（P0-1 只修了 feedback_matrix） | ✅ 已修 `_guard_trading_day()` |
| ②轮动（theme_rotation） | 按日历日回看 6 天——长假后覆盖不到 5 个交易日文件，回流模式失效 | ✅ 已修 `_recent_files()` 交易日计数 |
| ③反推（5日情绪汇总） | 5 日情绪表由 AI 手工抄表，无结构化历史、口径易漂移 | ✅ 已修 emotion 快照 + summary_5d.json 自动生成 |
| ①沉淀 | 情绪数值无日级结构化留存（只有当天 feedback_matrix.json） | ✅ 已修 emotion_YYYY-MM-DD.json |
| ①②③共同盲区 | **外部新闻维度缺失**：newsflash 只进 latest.json（当日覆盖），不沉淀 → 5 日情绪判断看不到"事件/催化"驱动源 | ✅ 已修 newsflash_history 日归档；待办 P1-7/P2-11 深化 |

**三段式逻辑漏洞（未修·需注意）**：
1. **轮动结果未反哺情绪**：theme_rotation（题材轮动周期）与情绪汇总（市场情绪周期）各自独立计算，轮动数据没有作为情绪判断的输入项——应把「re-entry 候选数/新 boom 数/主线切换频率」纳入周期提示。
2. **情绪汇总无"事件因果"**：当前情绪序列是纯数值（情绪/晋级率/核按钮），与当日新闻催化无显式关联——"情绪为什么变"缺失，AI 定调时需人工补新闻。
3. **滞后性**：情绪分来自 feedback_matrix（T 日收盘后），盘前 06:06 用的是 T-1 数据——隔夜事件（美股/公告/政策）只能靠 catalyst-hunter 搜索补位，未纳入 5 日窗口。

**外部新闻融合落地路径（已开始·P1-7 深化）**：
- **已落地**：newsflash 日归档（newsflash_history/daily_YYYY-MM-DD.json），5 日汇总已预留 `news` 维度（当日硬信号标题 Top8）。
- **下一步（P1-7）**：①接财联社/金十多源（**A1·搁置·2026-10-10 用户确认无 API 凭据**）；②按「事件→题材→个股」落库（**待拍板**）；③情绪极性标注（利好/利空/中性）融入 5 日汇总（**✅ A2·2026-10-10 第一批已落地**：`capture_daily.capture_newsflash` 增量标 `polarity` + `gen_sentiment_summary` 聚合 `news_polarity_total` + md 注脚，语义复用 `newsflash_express._direction` 涨→利好/跌→利空；A2 仅依赖现有快讯源，与 A1 多源解耦，A1 搁置不影响）。
- **卡片落地**：盘前/盘后卡在"情绪演变"行后加 `📰 催化：{当日硬催化标题 1-2 条}`，让事件成为情绪演变的解释变量。

---

## 四、关键节点与触发（卡片生成必须关联）

- **情绪演变链基准**：`memory_lib.py load` 的 market_state.sentiment = 上一卡情绪；首卡标"首卡基准"；盘前=昨日盘后、盘后=全天轨迹。**先定演变方向，再下出手结论**（转差禁"可出手"除非新催化注明）。
- **兜底扫盘**：automation-1786859887557 交易日 09:25/09:55/10:25/10:55/11:25/11:55/13:25/13:55/14:25/14:55 触发，只跑脚本不产卡。
- **事件驱动**：live_signal 涨停/冲板/核按钮/板块强势/回流前兆，in_trading 守卫 + 当天去重。
- **收益结算**：盘后 eod ≥15:00 结算当日（_finalize_prev_batch closing）；次日 09:25 verify 走原链路；nav 账本按 trade_date 去重幂等。
- **反馈矩阵守卫**：9:26 前 pre_auction；非交易日 non_trading_day；涨停池空/自交 zt_pool_unreliable——error 时卡面诚实标"待更新"，禁编数字。

---

## 五、注意事项与不可再犯（教训固化）

1. **narrative 否决教训（2026-08-16 撤销）**：AI narrative【弃·风险】不否决规则信号——8/14 共进被否决排除但次日 +7.33% 唯一正确票。**规则信号=机器决策，AI 观点=参考展示（含风险打标），永不投票**。
2. **周末/非交易日假数据**：东财涨停池接口非交易日返上一交易日数据 → 自交 100% 假象；守卫已修，但新脚本引用 feedback_matrix 前须确认 error 字段。
3. **情绪口径**：feedback 10 分制是唯一权威，latest.json 5 级粗分仅兜底；两处不一致时以 feedback 为准。
4. **语义色板**：恶化禁红（晋级率<25% 绿警、核按钮多绿、资金流出绿、负收益绿）；红仅改善/涨方向/⚠️动作警示（句中恶化数据绿+警示词红）。机会紫/涨停整行红不变。
5. **数据真实性铁律**：无数据不推信号、不编置信度；涨停/封板标注必须 zt_list 核验（8/11、8/12 事故）；资金流≠涨停，分开表述。
6. **推送单入口**：一律 `scripts/push_card.py`；手动推测试群必须显式 `--webhook feishu_webhook_test`（沙箱内联 env 会被丢弃）。
7. **隐私铁律**：群推卡禁持仓/6位代码；持仓应对仅板块级。

---

## 六、卡片自动关联方式（生成时必读链）

```
卡片任务启动 →
  1) Read shared_rules.md（宪法·配色/布局/骨架/联动）
  2) Read config/upgrade_plan.md（本文件·升级基准）
  3) Skill stock-catalyst-hunter + a-stock-sentiment（实时催化+情绪框架）
  4) Bash pulse_fetcher.sh --force（刷新数据）+ memory_lib.py load（上卡情绪）
  → 生成卡片（含📉情绪演变行 + 语义色板 + 三层分离呈现）→ push_card.py 推送
  → memory_lib.py record --sentiment（写回情绪快照）
```
