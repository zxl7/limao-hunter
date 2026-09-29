# 学习层目标函数改造 · 收益导向（L1+L2+L4 已落地 · L5~L8 待拍板）

> **立项**：2026-09-18 用户原话 ——「命中率降低这不重要，因为市场行情是动态变化的，我们的规则，规则是最重要的，**成功率和收益才是我们需要提高的**，而不是说命中的越多对我们的帮助并没有任何作用。」
> **状态**：**最小可用闭环 L1 + L2 + L4 已实施完毕**（用户 2026-09-18「可以 先做124！」）；L5~L8 仍为设计态、待逐条拍板。
> **落地实测**：`applied=[]`（零回写）｜`audit_window` 出账本旁证｜审计报告 5 档口径出齐｜全量测试 **502 passed / 1 skipped**｜新锁变异验证 **6/6 被杀**。
> **关联**：`config/shared_rules.md`【📊 推测回测 + 自我学习层】🔁-1~10、`config/design_人机混合决策.md`、`config/upgrade_plan.md`、skill `财神-回测`。


---

## 一、诊断：目标函数错配（本次改造的全部理由）

系统当前有 3 个被追踪的指标，但**样本量与经济意义的分布恰好是反的**：

| 指标 | 定义 | 经济意义 | 样本供给（近 15 交易日） | 当前驱动 |
|---|---|---|---|---|
| `range_hit` | 实际竞价 gap 是否落在预期区间内 | ⛔ **无**（命中≠赚钱） | **15/15 日可用**（近5日 58 只可评） | **R1 / R4 自动回写** |
| `candidate_hit` | nav 账本中有成交票的盈利占比 + 均收益 | ✅ 有（本身就是胜率） | **6/15 日可用**（9 日 unavailable） | R2（样本<3 常降级） |
| `sentiment_diff` | AI 情绪预判 vs 实际情绪分差 | 间接（影响买卖时点） | 9/15 日可用 | R3（仅建议） |

**结论**：学习层长期在「**样本充足但无经济意义**」的指标上调参（R1/R4），同时放任「**有经济意义但样本饥荒**」的指标饿死（R2）。这不是参数没调好，是**优化目标选错了**。

### 证据链

1. **近 5 交易日区间明细（58 只可评）**：命中 **12（20.7%）**｜**上沿突破 26**｜**下沿击穿 20** —— 偏离**近乎对称**。对称分布下平移中枢几乎不改变命中率，只会把 26 个上破推得更远。
2. **唯一一次生产级自动回写的实测效果**：09-17 21:52 将 `base_range` 由 `[-0.3,2.2]` 下移至 `[-0.6,1.9]`；PDCA 自评 09-17 21:54 `improved=True` → **09-18 17:47 `improved=False`**（区间命中 19.6% → 19.4%）。**改动无收益、且指标未改善。**
3. **真实的收益侧数据早就存在，只是没接进学习层**：反事实账本 `cache/decision_audit.json`（2026-09-17 立项）覆盖 **20 个执行日 / 286 条 / 159 只**，且是**全决策样本**（非仅成交票）：

   | 池档 | 样本 | 可执行率 | 可执行收益均值 | 胜率 |
   |---|---|---|---|---|
   | relay | 87 | 70.1% | **+1.77%** | **60.7%** |
   | watch | 199 | 83.4% | +0.20% | 46.8% |
   | AI 参与 | 3 ⚠️ | 66.7% | **+4.58%** | 100% |
   | AI 观察 | 38 | 89.5% | +1.05% | 50.0% |
   | AI 放弃 | 17 ⚠️ | 82.4% | +0.76% | 50.0% |

   **relay 与 watch 的收益差 +1.57pt / 胜率差 13.9pt，n 分别为 87/199 —— 这是全系统统计功效最高的一条可用结论，目前却只被 R6 当作"建议"输出、没有任何机制去执行它。**

---

## 二、北极星指标（改造后学习层唯一裁决依据）

**EV（期望值）驱动，而非命中率驱动。**

```
单笔 EV(pt) = 胜率 × 平均盈利 − (1 − 胜率) × 平均亏损   ≡ 收益均值（数学恒等）
超额 EV     = 单笔 EV − 同日基准（见口径 Q3）
```

> 注：EV 与「收益均值」是同一个量 —— 现有 `decision_audit._stats.ret_exec` **即为 EV**，无需另建指标，改造只是**把它接进学习层并加上样本门与基准**。

四条口径铁律：

- **Q1 收益口径多周期化**（当前只有「竞价成交价 → 当日收盘」单日口径，与用户「3 天无改善离场」的持有周期不匹配）：
  `ret_t0_close`（现口径）／`ret_t1_open`（隔夜溢价）／`ret_t1_close`／`ret_t3_close`（对齐纪律）／`mdd_t0_t3`（区间最大回撤）。
- **Q2 只有"可执行"样本才进 EV**：`buyable = gap_pct ≤ 5.0`（`decision_audit.ENTRY_GAP_MAX`）。被硬线挡掉的样本单列「错失成本」，⛔ 不得混入 EV 稀释结论。
- **Q3 必须做基准归一**：单日口径收益受大盘 beta 支配（09-14~09-18 大盘连红，任何选股规则都会"看起来有效"）。基准取**同日全市场个股涨幅均值**，进 EV 的必须是**超额**。
- **Q4 样本门**：分组 `n ≥ 30` 且 bootstrap 95% 置信下界 > 0，方可进入"可裁决"；`n < 30` 一律「只呈现不裁决」（复用 `decision_audit.LOW_SAMPLE=30` 与 `small_sample` 字段）。

---

## 三、改造清单（逐条拍板 · 回「L1 做 / L2 不做」即可）

### L1 学习样本源切换到反事实账本 ★核心 —— ✅ **2026-09-18 已落地**

- **现状**：`learning.py` 读 `cache/backtest_history.jsonl`（每日 1 行），主指标 `candidate_hit` 依赖 nav 账本 → 只在有成交时写行（1~2 只/日）→ 9/15 日 unavailable。
- **改动（实做）**：新增 `AUDIT_JSON` + `load_audit_window()`（读 `cache/decision_audit.json`，输出 `pools` 各池 EV/胜率/`exec_ret_n` + `usable` 采信门 `AUDIT_MIN_N=30`）；`detect_biases(hist, cfg, audit=None)` 新增 `audit` 参数，把一行对照旁证（`_audit_ref()`）附到 **R1 / R2** 的 evidence；`run_learning()` 落 `result["audit_window"]`。
- **边界（比设计收窄）**：本轮**只做旁证呈现**——不改阈值、不新增规则、不影响 severity/step。收益类新规则属 L3（未拍板）。
- **措辞约束**：旁证串会经 `learning_suggestions.json` 流进飞书卡 → ⛔ 禁用卡面 Tier-1 阻断词（「样本」），统一「条/只」。
- **验收（已实测）**：`learning_suggestions.json` 出现 `audit_window`（19 日·278 条·157 只；relay EV +1.77%/61 条、watch EV +0.34%/161 条、`usable=true`）；整卡递归扫 `card_copy_rules.scan()` **0 FAIL / 0 WARN**。
- **风险**：低。纯读已落盘文件。
- **默认建议**：**做** → 已做。

### L2 冻结 R1/R4 的自动回写权限 —— ✅ **2026-09-18 已落地**

- **现状**：R1（区间命中<40% → `base_range` 下移 0.3）与 R4（>80% → `max_range_width` 收紧 0.3）曾是 `RULE_RISK` 中**仅有的两个 `low` 风险项**，在 `tier_policy.low=true` 下唯一具备自动回写资格。
- **改动（实做·比设计加严）**：`RULE_RISK` 把 `R1_range_low` / `R4_range_high` 由 `low` 升为 **`high`**；并新增 **`_writable(sug)` 总闸**（`RULE_RISK != high` **且** `step is not None`），在 tiered 分支、legacy `high_only` 分支、`apply_suggestion()` 入口**三处**同时拦截。
  - ⚠️ 为何必须加总闸：legacy `high_only` 分支按 `severity` 筛选，而 R1 的 `severity` 恰是 `high` → 只在 tiered 过滤会被绕过（真实漏洞，已由 `test_frozen_rules_never_writable_in_any_mode` 锁死）。
  - `step` 保留 0.3（不作 `null`）：让**人工拍板时**能看到「若改会改多少」；回写资格由总闸而非 step 决定。
- **理由**：R1/R4 优化的 `range_hit` 与收益无因果关系（见第一节证据 1、2）。
- **验收（已实测）**：`learning.py` 跑完 `applied == []`；`expectation_rules.json` 未被触碰；3 条变异验证全部被杀。
- **默认建议**：**做** → 已做。

### L3 新增四条收益导向规则（替代 R1/R4 的回写预算）—— ⛔ **未拍板·未做**

| 规则 | 判据（基于 decision_audit） | 目标参数 | 建议幅度 | 风险 |
|---|---|---|---|---|
| **R8_pool_ev** | `relay.EV − watch.EV ≥ +1.0pt` 且两者 `n≥30` → watch 池收严 | `relay_score_config.json`（watch 入池门槛 / 降权系数） | 一次一档 | medium |
| **R9_label_ev** | 按竞价 `gap` 分档（0~1/1~2/2~3/3~5/5~7/>7）× EV，若某档 EV 显著低于相邻档 → 该档降级为观察 | `expectation_rules.json` 档位映射（`gap_labels` / `form_adjust`） | 一次一档 | medium |
| **R10_factor_ic** | 因子值与 `ret_t0..t3` 的 IC / 反向因子（现 R6 已算 `diff`，缺收益维度） | `factor_weight` | 单因子 ≤0.05 | medium |
| **R11_ai_tier_ev** | AI 三档 EV 单调性 + `participate` 样本量（现 R5 只看"次日涨停率"，不是收益） | `ai_judge` | 语义·仅建议 | low |

- **改动点**：`learning.py` `detect_biases` 追加 R8~R11 分支；`config/learning_config.json` `rules` 增补；`tl` 目标仅限白名单 `expectation_rules.json` / `relay_score_config.json`（铁律 🔁-3 不变）。
- **验收**：R8~R11 各有 1 个正例 + 1 个反例回归锁（`tests/test_learning_tiered.py` 扩容）。
- **默认建议**：**做**（R8 优先，它的证据最硬）。

### L4 收益口径多周期落地 —— ✅ **2026-09-18 已落地**（加做：回滚快照修正）

- **改动（实做）**：
  - `common/tencent_kline.py` 抽出 `_raw_klines()`，新增 **`fetch_kline_ohlc()`**（含开盘价）—— 同 host 同 URL，**未新开数据源**；`fetch_kline()` 行为不变（仅改走同一原始行）。
  - `decision_audit.py` 缓存升级为 `{code: {YYYYMMDD: {"o": 开, "c": 收}}}`，**兼容旧格式**（裸 float=仅收盘 → `open=None`，联网时自动升级）；`close_of()/open_of()/_bar()`。
  - 新增 `attach_returns(row, cache, cal)` → `ret_pct`（t0收·主口径不变）/`ret_t1_open`/`ret_t1_close`/`ret_t3_close`/`mdd_t0_t3`；`_stats()` 每档配 `n_<key>` 条数；报告池档/AI/门槛三表加列并加口径图例。
  - 交易日历改用 **`kline_calendar()`（日K实际日期）**，⛔ 不用 zt 归档日——归档缺一天会静默把 t+1 挪成 t+2（多周期口径最怕的位移）。
- **验收（已实测）**：报告 A/B/C 三表出齐 5 档 + 每档条数；旧缓存自动升级（168 票中 158 票拿到开收双字段，10 票在窗口外保持旧格式·不报错）；13 条新回归锁 + 6 条变异验证全部被杀。
- **⚠️ 口径漂移核查（重要）**：本次改动前后对同一窗口对比 —— **relay 池完全一致**（n=87 / exec_n=61 / EV +1.77% / 胜率 60.7%），证明 **t0 主口径未被改动**。watch 池从 n=199 变 191 系**窗口位移**（`cache/zt_history/zt_2026-08-21.json` 在 16:27~17:19 之间被移除 → 回看窗口 20 日→19 日），与本改动无关。
- **风险**：中（已在验收中处置：旧缓存兼容 + 备份）。
- **默认建议**：**做** → 已做。

### L4b 回滚快照修正（实施 L2 时发现的阻塞级缺陷·一并修）—— ✅ **已修**

- **缺陷**：`learning.py backup_snapshot()` 无条件 `copy2(EXPECT_RULES)` → 当回写目标是 **relay 配置**时，快照里存的却是 `expectation_rules.json` ⇒「备份了 A、改了 B」，`--rollback` 会还原错文件 → **可逆性名存实亡**（用户铁律）。
- **影响面**：L2 冻结后 R1/R4 退出回写，`R2_cand_low`（relay 门槛）成为**唯一**可自动回写的目标 → 该缺陷从"理论问题"升级为"当前唯一回写路径不可回滚"。
- **修法**：快照改为备份**真实目标文件**并把原文件名编入快照名（`learning_rollback_{ts}_{tag}__{原文件名}.json`）；`rollback()` 按快照名尾部复原到同名目标文件（旧格式快照仍按 expectation_rules 处理，向后兼容）。回归锁 `test_learning_apply_backup_targets_real_file` + `test_learning_rollback_restores_correct_file`。

### L5 基准归一（超额 EV）

- **改动**：`decision_audit.py` 新增同日全市场基准（数据源优先 `latest.json` 的 `breadth`/`market` 已落盘字段；不足时用当日全市场个股涨幅均值）。
- **验收**：报告新增「超额 EV」列，且给出基准口径来源标注。
- **默认建议**：**做**。

### L6 提案去重 / 结案机制（治 14 项 draft 堆积）

- **现状**：27 项提案 = **3 个根因的日更副本**（`P2_candidate_low`×5 + `P4_data_gap`×5 + `P1_sentiment_bias`×4 + `P3_range_low` 展示型×13），无去重/失效/结案 → 卡上 O2..O15 十四行实为三条。
- **改动**：`gen_proposals.py` 增加指纹去重（`rule + evidence 归一化`）；同指纹已存在 `draft` → 只更新 `last_seen` 不重复生成；已处置的置 `closed`；`gen_optimization_plan.py` 决策清单只列 `open` 项。
- **验收**：下一周决策清单条目数 ≤ 8（当前 20）。
- **默认建议**：**做**。

### L7 区间命中率的新定位（不删，降级）

- **定位**：`range_hit` 保留为**预期引擎的校准诊断**，退出"学习目标"。理由：卡面「✅符合预期 / 🔺超预期 / ⬇低于预期」标签会经 `signal_eval` 影响买不买，标签失真会**间接伤收益**，所以不能一刀切删掉。
- **改动**：R1/R4 的判据从「命中率 < 40%」改为「**标签分组收益无区分度**」——即按三档标签分组看 `ret_t1`，若三档超额 EV 差异不显著 → 才提示校准区间。**直接连到收益，而不是连到命中率数值。**
- **默认建议**：**做**（与 L2 配套；仅改判据，不改回写权限）。

### L8 自动回写的准入门（一次一变量 + 事后验证）

- **改动**：`learning.py` `apply_suggestion` 前置校验：①分组 `n≥30` 且置信下界 >0；②单次只允许一条规则回写（现 `max_apply_per_run=2`）；③冷却 3 日保留；④回写后第 5 个交易日自动生成「改动前 5 日 vs 改动后 5 日 EV 对比」，未改善则**自动置为待回滚**并在周五卡置顶提示。
- **验收**：`cache/learning_history.jsonl` 每次回写带 `ev_before / ev_after / improved` 三字段（当前 `effect` 段已有雏形，缺 EV）。
- **默认建议**：**做**。

---

## 四、明确不改（边界）

1. ⛔ 不碰 `config/shared_rules.md` 宪法 / 骨架 / 任务 prompt（中央规则原则）。
2. ⛔ 学习回写白名单仍限 `expectation_rules.json` / `relay_score_config.json`（铁律 🔁-3）。
3. ⛔ 不自动修改 skill（语义类永远走 `gen_proposals` 提案 + 人拍板，铁律 🔁-8）。
4. ⛔ 不在本轮开启 `auto_apply`；`auto_apply_mode` 维持用户拍板结果（当前 `tiered`，主开关 `auto_apply=false`）。
5. ⛔ 改买入口径必查四处同值（`ai_caliber` / `ai_ledger` / `build_cards` / 接力卡 prompt）—— 铁律【🤖-1c】。

---

## 五、实施顺序（依赖关系）

```
L1（样本源）──┬─→ L4（多周期口径）─→ L5（基准归一）─→ L3（EV 规则）
              └─→ L2（冻结 R1/R4）─→ L7（命中率降级为标签诊断）
L6（提案去重）独立
L8（准入门）依赖 L1 + L3
```

**最小可用闭环**＝L1 + L2 + L4 —— ✅ **2026-09-18 已完成**。做完这三条，"系统自动迭代"才第一次作用在**与钱有关的指标**上；其余条目是精度与秩序提升。

## 六、落地记录（2026-09-18）

| 条目 | 状态 | 触及文件 | 回归锁 |
|---|---|---|---|
| L1 样本源 | ✅ 完成 | `scripts/learning.py` | `tests/test_learning_tiered.py` +5 |
| L2 冻结 R1/R4 | ✅ 完成 | `scripts/learning.py`（`RULE_RISK` + `_writable()`） | 同上 +2；`tests/test_backtest.py` 改写 1 + 新增 2 |
| L4 多周期收益 | ✅ 完成 | `scripts/decision_audit.py`、`common/tencent_kline.py` | `tests/test_decision_audit.py` +8 |
| L4b 回滚快照修正 | ✅ 完成（实施中发现） | `scripts/learning.py`（`backup_snapshot`/`rollback`） | `tests/test_backtest.py` +2 |

**未做（本轮范围外）**：L3 收益类规则、L5 基准归一、L6 提案去重、L7 命中率降级、L8 回写准入门。
**副作用（需知悉）**：L2 冻结后 `tier_policy.low` **已无成员** → `tiered` 模式下实际只剩 `R2_cand_low` 具备回写资格，而 R2 常被样本守卫（`min_samples=3`）拦成仅建议 ⇒ **当前自动回写实质处于停机状态**（符合用户「改不改由我拍板」的意图）。

> 免责：本文基于系统落盘数据的分析，投资需谨慎。
