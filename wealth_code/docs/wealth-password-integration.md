# 财富密码 × 狸猫猎手 · 方法论整合文档

> 整合对象：`quant-review/docs/wealth-password-reproduction-spec.md`（量化选股方案）＋ 技能「财神-选股」＋ 狸猫猎手真实数据源（选股宝/脉冲快照/情绪矩阵）。
> 目标：把"严格贴合原量化系统"的十一维评分 + 真实大盘/板块环境 + AI 自身研判，融合成一套可无人值守实跑的 A股短线选股系统。
> 状态：2026-08-13 已实跑（必盈主池），盘后候选卡已推送测试群。

---

## 1. 融合架构（三层）

| 层 | 来源 | 职责 | 代码 |
|---|---|---|---|
| 评分层 | spec §4.3 十一维 `raw_score` | 环境/板块/接力/龙头/容量/风险/机会 + identity/height/market_gate/tide | `core/quant_score.py` |
| 选择层 | spec §4.4 四档接力 | 高度突破/高标龙头/核心接力/1进2 + strict→relaxed→broad→emergency 级联 | `core/quant_score.py::select_relay_watch` |
| 真实环境层 | 脉冲快照 + 情绪矩阵 | 三大指数/情绪/板块风险/弱板块/强势热点（真实派生 environment/板块/风险/潮汐维） | `adapters/input_env.py` + `adapters/input_zt.py` |
| AI 研判层 | AI 写入 `ai_env.json` | 作为 spec 自有维度的**输入**折叠（环境/板块/风险/潮汐调制），不另立平行打分 | `pipeline_eod.run_eod(ai_env=)` |
| AI 竞价预期覆盖 | AI 写入 `ai_env.expectations` | 逐只推断合理竞价区间，覆盖退潮期确定性引擎基线坍缩 | `pipeline_eod` + `build_cards` 🤖AI 标记 |

**铁律（不可再犯）**：ST 排除、一字板排除（无参与机会）、成交额<5亿剔除（`MIN_TURNOVER_YI`）、溢价门控（expectation 层）、合成不落盘、推送单入口 `scripts/push_card.py`。

---

## 2. 数据源策略（两者相结合）

- **必盈（biyingapi）主池**：`adapters/biying_pools.fetch_ztgc(date)` 取 spec §3.1 涨停池，字段映射 `dm/mc/zf/lbc/fbt/zbc/hs/hy/cje/lt/zsz/zj/price`。
  - token 经环境变量 `BIYING_TOKEN`（本地 `wealth_code/.env`，不提交）；`WEALTH_USE_BIYING=1` 开关。
  - **实测 2026-08-13**：返回 59 只，字段齐全；`price` 字段为空（biying 不返回现价）→ 优雅降级为 None，卡片不显示现价。
- **选股宝（yesterday_zt.json）降级**：必盈缺失/失败自动降级，保证系统永不断流。
- 切换判定：`input_zt.load_zt_pool` 优先必盈，失败 catch 后降级；`payload.data_source` 诚实标注 `biying` / `xuangubao`。

---

## 3. 环境双源冲突调和（本系统最关键判断）

同一交易日两数据源给出相反标签：

| 源 | 判定 | 关键信号 |
|---|---|---|
| `feedback_matrix.json` | **退潮 / 2.0** | 核按钮 13 家、破板率 37.4%、晋级率 22.8%、三大指数全绿 |
| `pulse_snapshots/latest.json` | **修复 / 2.0** | 指数红开、医药净流入+45亿、电力+36亿、算力+46亿 |

**AI 调和结论**：弱反弹掩盖强分歧——37% 破板率 + 13 家核按钮是典型退潮/分歧特征，非真修复。环境按「**分歧偏退潮**」处置：接力只做容量核心与干净封板，高位放量烂板控仓或放弃。`ai_env.market_regime=neutral / ai_env_score=4.5`。

---

## 4. AI 竞价预期覆盖通道（解决退潮期基线坍缩）

**根因**：确定性引擎 `core/expectation_ai.py` 的 `sentiment_phase` 不识别"退潮"→静默退化为冰点阈值，叠加 follower 在 `sector_adjust` 与 `leadership_premium` 两处双重惩罚 → 接力池区间坍缩至 `[0.0, 0.5]`，验证卡四桶失效。

**解决（用户指定：AI 干预而非配置修补）**：`ai_env.expectations{code: {expected_range, reason}}` 由 AI 逐只读取涨停封板质量（封单额/换手率/炸板次数/封板时间/板块地位/容量）推断合理区间，覆盖引擎基线；`build_cards` 对覆盖项标注 `🤖AI`。真一字板（移远通信 603236）维持无参与机会，不覆盖。

**推断原则（退潮期）**：容量核心+干净封板 → +1~3.5%；中等封板 → +0.5~3%；高位放量烂板 → 0~2%（闷杀风险）；极致烂板(zbc≥9) → 0~1.5%。

---

## 5. 实跑结果（2026-08-13，必盈主池）

- `data_source=biying`，`regime=neutral`，strict 模式。
- **接力池 TOP5（全主板）**：中鼎股份(000887,汽车零部,容量核心)、北京文化(000802,影视院线4板烂板)、誉衡药业(002437,化学制药)、亚泰集团(600881,综合Ⅱ3板)、德龙汇能(000593,燃气Ⅱ干净封板)。
- **观察池 TOP8**：移远通信(一字板·无参与)、皇氏集团、华电能源、金开新能、一鸣食品、良信股份、共进股份、风语筑。
- AI 覆盖 12 只（移远一字板跳过）；叙事 13 只全覆盖。
- 卡片推送 ✓OK 至测试群（`feishu_webhook_test`）。
- **板块主线缺席说明**：AI/算力/CPO 因当日炸板潮（紫光/剑桥/浪潮等37家破板）被模型正确轮动剔除，转独立强势题材——退潮期特征。

---

## 6. 后续（明确暂缓）

- **竞价验证卡（verify / 净值结算闭环）**：按用户要求本次不做。次日 09:25 可独立跑 `verify --fetch --push` 结算上一交易日收益并验证竞价预期区间命中。
- 必盈 token 已落地 `wealth_code/.env`（本地，需确认 `.gitignore` 屏蔽，勿提交）。

---

## 7. 关键文件索引

- `wealth_code/.env` — 必盈 token + 开关（本地密钥）
- `wealth_code/ai_env.json` — AI 研判 + 竞价预期覆盖（每交易日重算）
- `wealth_code/narratives.json` — 逐只点评 + 全局旁白（每交易日重算）
- `wealth_code/adapters/{biying_client,biying_pools,input_zt,input_env}.py` — 数据源适配
- `wealth_code/core/{quant_score,expectation_ai,leader,dimension_score}.py` — 评分/预期引擎
- `wealth_code/pipelines/{pipeline_eod,build_cards}.py` — 流水线与卡片
- `wealth_code/scripts/run_wealth_code.py` — CLI（已接 `--webhook` 透传 + `.env` 加载）
- `scripts/push_card.py` + `config/settings.json` — 飞书推送单入口（测试群 `feishu_webhook_test`）
