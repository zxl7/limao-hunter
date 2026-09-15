# 人机混合决策体系设计（量化因子 × 人工判断 · 2026-09-09 用户立项）

> 设计权威（DESIGN）· 落地前用户逐段拍板；执行细则在实现后并入 `config/shared_rules.md`。
> 相关既有设计：`config/design_连板晋级量化分析.md`（基因/晋级/板块核心票=本体系的 L0 因子）。

## 0. 背景与要解决的问题

现状：量化链已成熟——盘后 eod（涨停池→龙头分→relay/watch→竞价期望区间）→ 次日 09:25 `signal_eval.evaluate_realtime_signal` 按**固定 Python 规则**输出 buy/pending/reject 三档 + regime/ice 门控。今天又叠了历史涨停基因/晋级矩阵/板块核心票/板块资金池多因子，但**都只进排序与点评，不进竞价决策**。

问题（用户 09-09 定版诉求）：
1. 推荐 = 规则二值输出，无"证据链/置信度"可解释，用户看不到"为什么推"；
2. **无人工介入点**——用户实战判断（题材背离/板块周期/盘感）无法在任何环节确认、覆盖、调整；
3. 无人工经验回填通道——用户"放弃某票/改区间"的经验不会影响次日推荐、也不进回测；
4. 冰点等特殊档是机器一刀切（哪怕已解冻产池，买侧仍 hit 硬拦），人没有显式入口表达"我要"。

**目标**：把"量化因子 → 推荐 → 执行"升级为**量化因子 + 人工判断的混合决策流**——机器汇总多因子给「带证据链的分档建议」，人工在 4 个关键节点可确认/覆盖/放弃/调参，人工输入全审计回填，反哺次日推荐与回测对比。**量化不越位、人工有入口、经验可沉淀。**

## 1. 总体架构：推荐引擎四层

```
L0 因子层（已存在·客观数据）   hist基因(250窗) · promo晋级矩阵 · board_core核心票 · 板块资金池
                              · leader龙头分 · relay/capacity · regime情绪 · 期望区间 · 竞价量能/承接
      ↓
L1 因子汇总层（新增 signal_scorer.py·纯量化）  每候选 → 证据链{因子项值+方向+权重} → conf + 四档建议
      ↓
L2 人工决策层（新增 decision_layer.py + cache/human_decisions/）  状态机：机器建议 → 人工 采纳/覆盖/放弃/调区间
      ↓
L3 执行与回填（verify 卡/账本/回测）  终态写 holdings/nav · 人工样本记审计 → 次日加权 + 回测人工对比
```

**设计原则**：L1 是纯函数可回测（回填回测体系）；L2 是薄状态层（不藏逻辑）；**纯量化路径永远可旁路人工**（主开关关掉=现状直通，双轨 diff 审计兼容）。

## 2. 竞价推荐触发条件与时间节点（新时间轴）

| 时点 | 环节 | 产出 | 人工介入（可选） |
|---|---|---|---|
| T-1 盘后 15:3x | eod 选股 | candidates + 期望区间 + **证据链摘要**上卡 | 人工批注：重点/剔除/调区间（介入点①） |
| T-1 晚 20:00 前 | （无自动） | — | 复核 eod 卡，human_sync 微调 |
| 竞价日 09:15-09:20 | 预抓竞价（auction --verify-aux 风向标已有） | 每票「竞价预判」：预计高开档 | 人工可预放弃高开诱多票（介入点②） |
| 09:24:30-09:25:00 | 终局竞价快照 → `signal_scorer` 汇总 | 每票 conf + 建议档 | — |
| **09:25:00** | verify 卡推送 | 含每票{建议档+证据链top3+状态} | — |
| **09:25:00-09:26:30 人工闸门窗口** | 人工对机器建议回填 | 采纳→自动执行 / 覆盖→改档执行 / 放弃 | **介入点③（核心闸门）** |
| 收盘 eod 结算 | 账本结算（幂等） | nav/命中 | — |
| 晚间复盘（17:18/晚间人工） | 信号打标：对/错/跳过+原因 | human_decisions 追加 | 介入点④ |

**闸门机制**：窗口内人工未回填 = 按策略默认执行（`decision_config.json default_policy`：follow=跟机器 / skip=全放弃，默认 follow 兼容现状；建议生产默认 skip 防误跟）。错过闸门=当天无人工痕迹，不阻塞（人工是增量层，不是必需前置）。

## 3. 多因子汇总（L1 · signal_scorer.py）

每候选产出证据链后加权：
```
conf(0-100) = clamp(50 + Σ w_i × vote_i × 10, 0, 100)
vote_i ∈ {-2..-1, 0, +1..+2}   ← 每因子按真实值映射方向分
建议档 = conf + regime 联合：≥72 强推(red) / ≥58 可推(red) / ≥45 观察(purple) / <45 回避(grey)
        regime=ice → 封顶「冰点观察」档（机器永不推 buy·人工闸门是唯一进场口——衔接 09-09 解冻语义）
```

因子与初版权重（全部可逆常量）：

| 因子 | 数据源 | 权重 | 强支持(+)判据 |
|---|---|---|---|
| 板块题材 | 板块资金池 + 涨停梯队 | 0.20 | 板块资金净流入 ∧ 涨停≥3 梯队 |
| 板块周期 | 龙头战法口径（S/A/B/C×阶段·卡面标注） | 0.15 | S级·发酵期（人工体系输入量化） |
| 个股身位 | board_core core_score + 梯队位置 | 0.15 | 板块核心票 ∧ 高标/梯队前排 |
| 股性基因 | hist_{code}.json（250窗） | 0.12 | 晋级率≥40% ∧ 首板高开>0 |
| 个股强度 | leader 龙头分 + 连板纯度 | 0.13 | 龙头分≥80 ∧ 纯连板 |
| 市场环境 | regime + 情绪分 + promo 当日基准 | 0.10 | risk_on；ice 强负 |
| 竞价即时 | gap vs 期望区间 + 量能 + 板块竞价承接 | 0.15 | gap 落区间 ∧ 量能达标 ∧ 非孤军 |

输出：`{code, conf, rec(档), votes[{factor,value,direction,weight}], evidence_top3(人话), regime_note}`。
**回归约束（兼容期双轨）**：L1 结果与原 `signal_eval` 三档**并行跑 N 日 diff 审计**（同票不同档=记录待校准），差异率收敛后再切 L1 为主、signal_eval 降为基准触发器（防一把梭）。

## 4. 人工介入点清单（明确：在哪些环节允许人工做什么）

| # | 环节 | 人工动作 | 允许覆盖什么 | 影响范围 |
|---|---|---|---|---|
| ① | eod 后 | 标「重点/剔除」、**调期望区间**（如冰点看低高开、题材背离日放弃）、备注 | relay 池成员、期望区间、档位 | 次日该票 conf ±15、区间用人工版、卡面👤标注 |
| ② | 09:15-09:20 | 预放弃（防高开诱多/临时利空） | 候选信号集成员 | 该票 09:25 不进闸门信号集 |
| ③ | **09:25-09:26:30 闸门** | 对机器建议：**采纳**（确认→自动执行写 holdings）/ **覆盖**（改档，如 pending→买）/ **放弃** | 机器最终动作 | 真实成交入口（唯一写 holdings 的人工通道） |
| ④ | 晚间复盘 | 当日信号打标 对/错/跳过 + 一句话原因 | 回测样本标签 | 次日权重建议 + 周提案 |

**⛔ 边界（人工 ≠ 免检）**：人工输入全审计（操作时间/原建议/新值/原因），禁止改历史账本；人工区间 override 只作用于当日候选，不改 expectation_rules.json（防止手滑污染全局）——想要全局改走 learning/周提案。

## 5. 人工输入如何回填并影响最终推荐

存储：`cache/human_decisions/{trade_date}.json`（append-only 每票一条）：
```json
{ "code","name","ts","stage":("post_eod"|"pre_open"|"gate"|"review"),
  "action":("mark_focus"|"drop"|"adjust_range"|"adopt"|"override"|"abandon"|"verdict"),
  "value": {...}, "reason": "...", "orig_suggestion": "强推", "src": "human" }
```

回填路径（三层）：
1. **当日即时**：verify 卡渲染「👤 人工：已放弃/已改区间/确认采纳」标记；gate 采纳 → 与机器建议合并进终态 → holdings/nav；
2. **次日加权**：`signal_scorer` 读近 60 日 human_decisions → ①某票被人工 drop ≥3 次 → 该 code 权重 -1 档（经验票）；②人工 adjust_range 与机器区间的偏差均值 → 期望引擎 calibration 提示（learning 白名单）；③人工 verdict 对/错分布 → 归因报告（哪类票人工正确率高=系统盲区）；
3. **周回测对比**：`backtest_engine` 双轨记账「机器建议 vs 人工修正后」——人工修正率高且修正后多赚的环节 = 提案（O 清单），建议把该环节因子权重调高或加人工前置确认。

## 6. 对现有代码/数据流/回测的影响（改造清单）

**新增文件（不改动既有主链即可验收）**：
- `wealth_code/core/signal_scorer.py` —— L1 纯量化汇总（因子表可逆常量 + evidence 生成）
- `wealth_code/core/decision_layer.py` —— L2 状态机 + human_decisions 读写
- `wealth_code/scripts/human_sync.py` —— CLI 人工输入入口（`--stage gate --action adopt --codes ...`）
- `config/decision_config.json` —— 主开关 `human_gate`(False=现状直通) / 权重 / default_policy / 闸门时长

**修改（向后兼容·加字段不删字段）**：
- `signal_eval.evaluate_realtime_signal`：保留（作为 L1 基准触发器之一）；regime/ice 语义不变
- `pipeline_verify._build_record` / `run_verify`：record 透传 `scorer_evidence / human_decision`
- `build_cards.build_verify_card`：每票加「🧩 建议{档}｜{conf}」证据链 top1 行 + 👤 人工标记
- `run_wealth_code.py verify`：合并 `--human` 输入（读 human_decisions）→ 终态判定
- `scripts/backtest_engine.py`：三类指标不变，**新增 ④ 人工对比维**（机器/人工/实际 三轨）
- 审计：human 动作全部 `common/audit_log.py` 落 `cache/audit.jsonl`（event=human_decision）

**数据流**：
```
candidates ─→ signal_scorer(证据链) ─→ auction 竞价 ─→ 建议集 ─→ human gate ─→ 终态 ─→ holdings/nav
                    ↑ 因子：hist/promo/board_core/资金池/regime/期望          ↓
              human_decisions(近60日·回填加权) ←──────── 人工回填(①②③④)
```

**对回测/学习体系**：纯量化 L1 走既有 🔁-1~8（learning 只回写白名单 config·样本≥3·幅度钳制）；人工数据**只作标注与提案输入，永不 auto_apply**（人工=用户主权，系统只提示不代改）。

## 7. 落地步骤（分步·每步独立可验收可停）

| 步 | 内容 | 验收标准 | 改动面 |
|---|---|---|---|
| **P0 基建** | human_decisions schema + decision_layer + human_sync CLI + 审计接入 | CLI 写入/读取/覆盖状态闭环；schema_guard 过 | 全新增 |
| **P1 因子汇总** | signal_scorer + 证据链 + 双轨 diff（与原三档 N 日并行） | 今日候选回放：diff 不一致率 <15% 且差异可解释 | signal_scorer 新 + build_cards 只读 |
| **P2 闸门上线** | verify 卡人机状态 + 09:25-09:26:30 gate（首期 CLI·二期飞书按钮） | 实测 1 交易日：人工放弃/采纳正确写终态+审计 | pipeline_verify/build_cards/run_wealth_code |
| **P3 回填闭环** | 次日加权 + 回测人工对比维 + 周提案接 O 清单 | 近 60 日人工样本回放：drop 经验票降权生效 | backtest_engine + signal_scorer 权重 |
| 每步后 | 宪法/中枢/测试同步 | pytest 全绿 | — |

**建议节奏**：P0+P1 一起（纯新增+只读，安全，可在近期晚间做）→ P2 需要一个真实交易日实测（等次日 09:25 窗口）→ P3 需积累人工样本（≥2 周后才有统计意义，先跑通机制）。

## 8. 铁律与边界（先立规矩）

1. **量化不越位**：机器永远只给"建议档+证据链"，不直接替人决定大额动作（default_policy=skip 为推荐生产默认）；
2. **人工有主权**：gate 的人工最终态是真实成交唯一来源；系统对人工动作只审计不否决（但越权改账本/改全局配置会被 schema_guard 拦）；
3. **冰点语义延续**：ice 机器封顶「冰点观察」，人工 gate 可进场（衔接 09-09「买不买由人决定」）——verify 卡需把 ice hit 票也纳入 gate 信号集（标「机器不推·人工自决」）；
4. **数据即事实**：人工输入必须有时间戳+原因字段（reason 为空则 action 不生效），防误触；
5. **可逆**：`human_gate=False` 一键回退现状；权重/阈值全在 decision_config.json；测试 test_signal_scorer/test_decision_layer 覆盖状态机全分支。

## 9. 与既有体系的关系

- **龙头战法体系（用户实战）**：L2 人工判断 = 该体系在决策层的落地接口——卡面证据链含「板块级别×周期×梯队」口径，人工 gate 动作建议按体系术语填 reason（分歧低吸/高潮不接力/退潮观望）；
- **今日迭代全接入 L0**：hist 基因、promo 晋级矩阵、board_core、板块资金池 作为 L1 因子直接消费；
- **财神-回测**：P3 人工对比维挂在既有三类指标旁，周提案 O 清单输出「人工修正率高环节」；
- **宪法【🧬 卡片优化铁律】**：verify 卡改造走模板增量，先推测试群再定稿。

## 10. 待用户拍板清单（落地前）

1. 整体方向是否认可（四层架构 + 4 介入点 + 闸门窗口）？
2. P0+P1 是否现在开工（纯新增 + 只读双轨，晚间安全）？
3. 闸门首期形式：CLI（human_sync.py）还是直接上飞书按钮卡（card.action 交互）？——建议 CLI 先行闭环，按钮二期
4. default_policy 生产默认：follow（机器建议自动执行）还是 skip（无人工=不动作）？——建议 skip 更符合「买不买由人」哲学
5. 因子初版权重表是否需要先拿历史回放看效果再定（P1 双轨 diff 期间调）？
