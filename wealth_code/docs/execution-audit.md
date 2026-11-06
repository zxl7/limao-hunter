# 财富密码执行审计

执行审计把“选中了什么”和“实际形成了什么收益”分开。它不改候选评分、AI 预案、账户净值或终端现有接口，只读取既有冻结件并生成独立报告。

## 2026-11-04 晚间续办核验（多日调整，当前实际状态）

- 续办起点 `git status --short` 已有7个修改文件；不将上一节“起始干净”误作本轮现场事实。本轮实读代码、候选和备份，确认此前已写入002275 conditional，并非完全未执行。以下结论覆盖上一节单日识别边界，历史记录不改。
- 本轮修改 `pipeline_eod._build_second_leg_map`：原最近40个历史文件窗口内，完整逐日核实最近一轮至少2连板、调整期、当前重启；调整期可含零散首板但不得缺完整交易日。新增 adjustment_dates/adjustment_sessions/interim_limitup_dates，不宣称价量反包/N形已确认。改为对 `_scored_all` 识别，被前置拒绝者仅补观察。
- 本地归档证据（cache/zt_history，同日收盘generated_ts）：002275于10-29/10-30/11-02为1/2/3板，11-03不在完整池，11-04为1板，调整1交易日；000530于10-13至10-16为1/2/3/4板，10-19至11-03调整12交易日，期间10-22/10-29/11-02有零散首板，11-04再次首板。后者不能说“连续12日没涨停”。
- 隔离目录 `/tmp/repair-1105-current-b714d54y/`；pytest 8.4.2，准入/两池/call_pool/defense_gate共131 passed，新增多日/零散首板/缺日拒绝测试。正常run_eod显式out_path，仅在完整隔离副本执行，socket连接封禁、聚合关闭；未调用cmd_eod。初次副本复制被沙箱拦截后获提权完成，不把失败算成功。
- 正常重算识别11只历史修复：000530、002058、002275、003021、600371、600391、601123、603248、605287、605488、605577；不是代码白名单。完整重算会将原观察601599升级relay，故不整体覆盖，按上述通用历史修复集合同步，旧14只资格保持。接力0→0，观察14→21；新增000530、003021、600371、600391、603248、605287、605488，均observe_only。此前13→14的变更及名单见上一节。
- 生产本轮确已更新 `wealth_code/cache/candidates-2026-11-04.json` 与 `cache/ai_view_2026-11-05.json`。改前备份 `wealth_code/cache/ledger_repairs/20261104_historical_repair_multiday_1105/`，manifest记录前后SHA256及30个账本/净值/既有冻结文件哈希；更新后全部保护文件不变。未改ai_decisions.jsonl，不将历史决策改成今日预案；新增仅观察票在11-05未来预案记avoid，池内裁决仍以资格为准，无需追加历史账本行。11-05冻结文件仍不存在。
- 实际双loader均读11-04候选：pipeline_verify._latest_candidates与run_wealth_code._load_latest_candidates；_load_plan('2026-11-05')读项目根cache/ai_view_2026-11-05.json，decision_date=11-04。002275 normal，000530 avoid。只核验本机，未部署或证明远端执行端。
- 真实未来候选与预案、隔离真实call_pool、合成报价：002275在+3.5%/1亿时watch（1板对市场3板、身位差2）；+3.5%/0.01亿watch（需0.34亿）；+6% skip；-2% skip。000530四种场景均watch，observe_only先短路，不能将它包装成“超5%已执行skip”；缺前置准入（同题材1家未成线）和题材龙。无硬伤合成conditional样本的participate、弱量watch、超5%skip由pytest真实call_pool验证，模拟没有生产落盘。
- 防守独立核验注入截至11-04真实序列：两票最近涨停间隔均1交易日，所以老龙与高位二波均未命中；dev10/dev30缺失，不声称低位已证实。000530换手48.69%、封单/流通0.25%、10:27封板，distribution_verdict三项组合block（即使上游解除，仍有此闸）；002275仅薄封1项不拦。上述是缓存事实与规则结果，不证明资金身份，不复述引擎既有胜率作为本轮收益证据。
- 验收边界：历史修复类保留conditional路径，未全体observe_only解锁；本日002275仍受身位硬闸、000530仍仅观察，不能声称两票明早均可命中。未改ST/688/5%/量能/单票50%或其他个股闸，未抓行情、推送、改调度、结算或部署。未更新聚合EOD快照；明早真实决策loader直接读候选JSON。git diff --check通过；不宣称全量测试或收益验证。

## 2026-11-04 晚间历史修复实施日志

- 用户授权仅更新11-04盘后输入供11-05竞价；工作树起始干净。先读 shared_rules / skill_contract，未触碰 automations 文件系统、调度、远端、推送、行情抓取或旧冻结结算。
- 改动：pipeline_eod 复用完整收盘池逐日 N+M 证据并留日期；pool_admit 只对证据有效的修复免除首板带动删除；最终补观察并区分 conditional / observe_only；pool_call 对 conditional 校验同日证据后继续全部原闸。回退 ENABLE_HISTORICAL_REPAIR=0；数据回退使用下述备份。单票50%、5%、量能、题材龙、身位及名额未放宽。
- 本地证据：cache/zt_history/zt_2026-10-29、10-30、11-02分别有002275的1/2/3板；11-03完整收盘池无该票；11-04再涨停。只认序列修复，不宣称价量N形确认。目前只支持断板1日，多日调整未扩展。
- 隔离目录 `/tmp/repair-1105-1_fqneae`，pytest 8.4.2：首轮88通过，最终115通过（准入、两池、call_pool、四条件，含新增保留/回退/缺证据测试，以及条件资格在无身位硬伤的合成样本可真实participate、弱量/超5%仍拦截）。正常 run_eod 在临时副本封禁网络执行，聚合快照关闭，所有重算副作用留临时副本。
- 完整重算将原观察601599重新变为relay，违反本次不升级其他观察票边界，未整体应用。采用正常重算后定向同步：原relay空、13只watch逐字段不变，仅追加002275桂林三金到watch，资格conditional，blocked_by空。原13只为601599、600892、605577、002827、000679、001339、002058、002578、002088、002652、002992、601123、603381，均仍observe_only。
- 更新前备份目录 `wealth_code/cache/ledger_repairs/20261104_historical_repair_1105/`，manifest带原文件SHA256；备份及更新三个文件：wealth_code/cache/candidates-2026-11-04.json、wealth_code/cache/ai_decisions.jsonl、cache/ai_view_2026-11-05.json。正常ai_ledger record先在副本登记，核对旧账本行逐行不变、仅新增11-04未settled行、视图执行日11-05，再原子同步；未用replace，不改既有仓位申报。
- 实际双loader `_latest_candidates` 与 `run_wealth_code._load_latest_candidates` 均读更新文件，next_day=11-05；_load_plan能读新增normal预案。11-05 pool_call冻结件不存在，没有提前冻结模拟结论。
- 实际最终候选+预案在隔离真实call_pool模拟：+3.5%/1亿→watch（当前1板比市场3板低2板，既有后排闸）；+3.5%/0.01亿→watch（量能需0.34亿）；+6%→skip（5%上限）；-2%→skip（低于预期）。原13只所有情景均无participate。报价为合成测试，不是行情、不写生产。引擎旧文案中的胜率数字未经本轮验证，不作为本次结论。
- 遗留：本票仍被当前身位闸拦截，未擅自恢复旧板数或开豁免；因此“条件资格已接入”不等于“明早可实际命中”。若要允许历史高度参与该闸，需单独明确授权与校准。当前只验证本机loader，未证明远端为实际执行端，未部署。聚合eod快照未更新，明早裁决直接读候选文件；既有预案601599资格不一致保持原状，不借本次修复扩权。未运行全量测试，不宣称收益有效。

## 数据血缘

```text
盘中采集 / 涨停池 / 题材与资金
        │
        ├─ feedback_matrix.json ──> input_zt.load_environment
        │                                  │
        │                                  ├─ pipeline_eod / quant_score / select_relay_watch
        │                                  │       └─ wealth_code/cache/candidates-<推荐日>.json
        │                                  │
        │                                  └─ pipeline_verify 的机械竞价参考
        │
AI 盘后预案 ──> ai_decisions.jsonl ──> 9:25 pool_call-<执行日>.json
竞价冻结     ──> auction_quotes-<执行日>.json
        │
        └─ pipeline_verify / ai_caliber ──> account_nav_history.jsonl
                                             └─ account_strategy_metrics.json
```

终端只读猎手产物：`src/shared/api/routes.ts` 定义文件路由，TanStack Query 拉取后交给页面渲染。终端没有下单接口，不能把盘中候选或卡片分数显示成真实成交。

## 评分与裁决边界

| 层 | 真实输入 | 是否决定收益账本 |
| --- | --- | --- |
| `feedback_matrix` | 晋级、溢价、核按钮、断板、指数、广度等 | 间接：提供 `regime` 与环境因子 |
| `gen_sentiment_summary` | 日级情绪序列、MA3/MA5/MA10 | 否，周期归纳与展示 |
| `leader` / `quant_score` | 龙头、容量、梯队、题材、环境、风险、历史晋级 | 决定候选排序与接力/观察池 |
| `dimension_score` / `relay_score` | 现有字段派生的评分面板 | 当前不是 `pipeline_eod` 的生产入池裁决 |
| `signal_eval` | 竞价涨幅、量能、孤军承接、冰点 | 机械参考；AI 口径开启时不直接决定账户收益 |
| `ai_caliber` / `pool_call` | 盘后预案、池内 9:25 裁决、预期区间、5%硬线、量能 | 是，当前 AI 口径下决定实际入场 |
| `nav_ledger` | `pipeline_verify` 的已入场且收益已覆盖记录 | 是，唯一实际成交与收益来源 |

冰点期机械买点会被标为 `hit`；AI 若仍放行，审计报告会逐票标记，不能被普通候选命中统计掩盖。

## 运行

```bash
# 只读核验，不落盘
python3 wealth_code/scripts/execution_audit.py --dry-run

# 生成独立审计产物
python3 wealth_code/scripts/execution_audit.py
```

产物位于 `wealth_code/cache/execution_audit.json` 与 `execution_audit.md`。每个批次含候选冻结时间、执行日、AI/机械结果、池内裁决、竞价价格与时间、账户入场、毛收益及缺失原因。

执行日只从已落盘的竞价、账户和完整涨停归档组成的交易日历推导；没有证据时保留为空，绝不以“跳过周末”猜测法定节假日后的交易日。

收益标签固定为“竞价至当日收盘毛收益”。现有账本未冻结收盘价格，审计不会根据收益反推价格；手续费、滑点和未成交冲击也不计入该指标。
