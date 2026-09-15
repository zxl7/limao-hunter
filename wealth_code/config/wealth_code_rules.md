# 财富密码模块 · 规则中枢（wealth_code_rules）

> 本文件是 wealth_code 模块的"宪法"，集中记录阈值、口径与判定逻辑。
> 改这里一处，core/ 与 pipelines/ 的判定全局生效（呼应狸猫猎手三层架构：
> 规则中枢 → 任务编排 → 脚本能力）。

## 一、龙头因子（core/leader.py，忠实移植）

总分 = 点火(0~40) + 爆发(0~40) + 先发(0~20) + 题材加分(0~4)
- **点火**：一字/强封(24/14) + 早封(≤8) + 封单金额log归一(≤20) + 封单相对强度(≤12) + 封板时长(≤6) + 空间高度(≤8) − 开板惩罚(≤14)
- **爆发**：题材涨停规模 + 龙头封板后跟随数(follow≥60s) + 扩散持续性；一级题材(光模块/商业航天/锂电/储能) ×1.10
- **先发**：同题材内封板越早越高（30分钟内线性衰减）
- **题材加分**：光模块+4 / 其他一级+2.5 / 二级0
- **龙头唯一性**：每题材只保留分最高 1 只

## 二、竞价验证（core/signal_eval.py，CAUTION_GAP_PCT）

| 状态 | 含义 | 是否买入(can_enter) |
|---|---|---|
| super | 竞价涨幅 ≥ super_gap_min 且 量能达标 | 买入 |
| expected | 竞价涨幅落在 expected_range | 买入 |
| pending | 涨幅达标但 gap > 5%（高开过猛）| 不直接买入，观察承接 |
| reject | 涨幅/量能不达标 | 不买入 |
| unavailable | 无 9:25 报价 | 不纳入 |

**高开谨慎阈值 CAUTION_GAP_PCT = 5.0**（与线上一致）

## 三、接力/观察池门控（pipelines/pipeline_eod.py，轻量版）

取代原版 zt_analysis 写死的潮汐魔法数字，改用可解释的环境代理 + 连板：

- **情绪代理**：来自狸猫猎手 `feedback_matrix.json`（sentiment_score / nuke_count / broken_rate）
  - risk_on：+6；neutral：0；risk_off：−8；核按钮>2 每只再 −1.5
- **连板高度**：≥5板+10 / ≥3板+6 / ≥2板+3 / 首板0
- **主板/创业板对称**：主板倾斜已撤销（2026-08-13 创业板开放）。主板与创业板(300/301)按同一套封板质量→竞价溢价情绪周期参与，无板块属性加分/减分；科创板(688) 硬剔除（无权限）。
- **接力池容量**：risk_on 8 / neutral 6 / risk_off 4
- **ST / 北交所 / 科创板688**：直接剔除（与用户"不看ST"及"688无权限"纪律一致）

## 四、预期区间（AI 期望层 · 双维度规则引擎，core/expectation_ai.py）

> ⚠️ 2026-08-12 重构：由 **双维度规则引擎 `build_ai_expectation`** 取代旧「按板数给区间」刚性映射。
> 综合 **① 短线表现维度（涨停形态）** + **② 板块情况维度（梯队位置 × 板块情绪）** 产出差异化竞价预期。
> 所有阈值集中在 `config/expectation_rules.json`，**改参数不动代码**，引擎读取后全任务（盘后候选卡 + 次日验证卡）生效。
> 输出：`expected_range / super_gap_min / auction_amount_* / auction_boom_yi / gap_strength_hint / seal_form / sector_tier / sector_sentiment / confidence / *_text / ai_note`。

### 4.1 统一基础区间（不再按板数分级）

所有票统一从 **基础区间 1.5%~5.0%** 起步，再层层调整。理由：6板票放量低开可能是健康换手，不应被板数阈值拒之门外。

```
lo, hi = 1.5, 5.0
+ 情绪相位：亢奋 +1.0/+2.0 | 修复 +0.5/+1.0 | 分歧 −1.0(只降上沿) | 冰点 −1.0/−2.0
+ 龙头身份：真龙头(主升) → lo 收到 (lo+hi)/2（向中上沿收拢）；跟风/补涨 → 整体 −1.0
+ 题材动量：处于 boom/re-entry-candidate → 上沿 +0.5
super_gap_min = hi + 1.0（冰点/分歧 +2.0，更挑剔）
```

### 4.2 维度① 短线表现（涨停形态 → form_adjust 差值）

`classify_seal_form` 按 `fbt(首封) / oneWord(一字标记) / zbc(炸板数) / hs(换手率)` 识别形态，
套 `form_adjust.gap` 对基础区间做加减（单位 %）：

| 形态 | gap 调节 | 置信调节 | 语义 |
|---|---|---|---|
| 一字板 | +1.5 / +2.5 | +0.15 | 一字锁筹·高开溢价强 |
| 缩量板（hs<5） | +1.0 / +1.5 | +0.10 | 缩量惜售·溢价强 |
| 快速板（fbt≤0935） | +0.5 / +1.0 | +0.05 | 早盘共识·溢价中上 |
| 温和放量 | 0 / 0 | 0 | 健康换手 |
| 放量板（zbc=0, hs≥15） | −0.5 / −1.0 | −0.05 | 放量换手·略谨慎 |
| 炸板回封（zbc≥1） | −1.0 / −1.5 | −0.10 | 盘中分歧·下调预期 |
| 放量烂板（hs≥12 且 zbc≥1） | −2.0 / −2.5 | −0.15 | 放量派发·谨慎对待 |
| 炸板王（zbc≥5） | 额外 −1.0 | 额外 −0.10 | 分歧极端 |

> 形态识别优先级：一字 > 炸板(回封/烂板) > 快速 > 缩量 > 放量 > 温和。卡片形态标签形如 `[一字板]` / `[炸板回封×2]` / `[放量烂板×7]`（×N=炸板次数）。

### 4.3 维度② 板块情况（梯队位置 × 板块情绪 → sector_adjust）

- **梯队位置** `tier_position`：板块龙（is_sector_dragon / 龙头且 relay≤2）/ 容量核心（cap≥60）/ 跟风补涨。
- **板块情绪** `sector_sentiment`：由 `theme_ctx` 题材相位推导 —— boom/re-entry-candidate→强；stable→中；cooling→弱；无数据→中。
- 二者交叉得 9 档 `sector_adjust.gap`（单位 %）：板块龙+强 +1.0 / 龙+中 0 / 龙+弱 −0.5；容量+强 +0.5 / 容量+弱 −0.5；跟风+强 −0.5 / 跟风+中 −1.0 / 跟风+弱 −1.5。

### 4.4 爆量高开线（auction_boom，次日验证判定阈值）

- `auction_boom_yi = max(0.20, 昨成交额 × 15%)` —— 竞价额 ≥ 此线 **且** 涨幅 ≥ 预期上沿 → `boom_open=True`（🔺爆量高开，抢筹/承接强）。
- 仅涨幅达标、量能不达此线 → 高开但量不足（不判爆量）。卡片用 `boom_label`「🔺爆量高开」标注。
- 量能下限：竞价量能预期线仍保留 QR 口径 `max(0.15亿, 昨量×比例)`，比例随情绪/龙头/形态浮动（5%~10% 基准）。

### 4.5 高开强度标签 & 置信度（卡片输出）

- **高开强度** `gap_strength_hint`：形态强(一字/缩量/快速) + 板块强 + 龙头 → 🔺高开溢价强；形态强 或 (板块强+龙头) → ⬆高开预期中上；炸板/放量/板块弱 → ⚠️高开谨慎/低开风险；其余 → ⬆正常高开预期。
- **置信度** `confidence`（0.05~0.98）= 情绪0.3 + 龙头0.3 + 形态0.2 + 板块0.2 + 调节项。≥0.8 高置信，卡片标「置信0.96」。

### 4.6 卡片输出形式

- **盘后候选卡（接力/观察池）**：第1行 `预期{lo}~{hi}% {高开强度}`（红）；第2行 `[形态]龙头分x｜题材｜板数｜量昨x~y%｜梯队｜置信`。
- **次日验证卡**：第1行 `信号 竞价{gap} {实际vs预期判定}`（✅符合预期 / 🔺超预期 / ⚠️低于预期）；第2行 `预期{lo}~{hi}%{高开强度} [形态] 置信x｜额x/y亿｜板块｜龙头分`。
- 参数开关：`output.show_form_tag / show_boom_line / show_confidence` 控制是否渲染形态/爆量线/置信度。

> 详情见 `core/expectation_ai.py` 与 `config/expectation_rules.json`。

### 卡片量价展示口径（build_cards.py，2026-08-12 立）

- **量昨x~y%**：竞价量能预期 = 昨日成交额(cje_yi) 的 5%~10%（情绪/龙头/20cm 联动），
  但当预期线被 0.15 亿下限兜底（昨量×比例 < 0.15亿，多为缩量/一字票）时，卡片**显式标注下限真实值**：
  `量昨5~10%(下限0.15亿)`；一字板再加「一字·」前缀（`量昨5~10%(一字·下限0.15亿)`）。
  原因：比例显示与实际执行线（下限）存在偏差，不标会误导（2026-08-12 用户拍板处理）。
- **观察池按 watch_group 分区**：容量核心 / 高标·题材核心(≥3板) / 补充观察，卡片以 `▸` 分组渲染。
- **20cm 标注**：接力池/观察池个股行对创业板/科创板 20cm 票标 `⚠️20cm`（紧止损提醒），与盘中卡一致。

## 五、净值与指标口径（core/nav_ledger.py / strategy_metrics.py）

- **策略**：equal_weight_next_day_close（每日等权买入可入场候选，隔日收盘卖出）
- **入池条件**：open_check.can_enter=True 且 next_day.status=covered（与原版一致）
- **净值**：nav_t = nav_{t-1} × (1 + 当日等权平均收益/100)，首日 base_nav=1.0
- **指标**：按推荐日聚合 next_day / hold_2d / hold_3d 的覆盖度、胜率、平均收益；
  并按开盘状态(super/expected/reject)拆分
- **口径约定（用户指定·真实交易战绩·长期统计）**：
  - **不计交易成本**（等权隔日收益即最终战绩展示值）
  - hold_2d/hold_3d 默认置 pending（不在主口径计算），不影响 next_day 主口径
  - 净值连续性依赖每日实跑；单日缺失会产生采样缺口（周末已自动跳过，法定节假日缺口已知且可接受）
  - ⚠️ **成绩数据永久维护**：`cache/` 下 metrics/nav/candidates/auction_quotes 为长期统计唯一依据，**禁止清空**
  - ⛔ **禁止污染真实战绩（2026-08-12 铁律）**：`verify --synthesize` 是 dry-run 工具路径，
    **只计算不落盘**（`out_nav=False, persist=False`，见 pipeline_verify.run_verify），
    严禁用合成数据写入 nav/metrics——真实战绩文件只允许 09:25 真实竞价结算写入。
    曾发生 08-11 22:06 `verify --synthesize` 把合成结算(胜率62.5%假象)写进 cache，已回滚+加锁。

## 六、数据源与 Token 安全约定（必读）

### 竞价 / 涨停池数据源：必盈 API（biyingapi）
- **涨停池（连板天梯源）**：`hslt/ztgc/{YYYY-MM-DD}/{token}` —— 由 `scripts/record_zt_snapshot.py` 调用，写入 `cache/yesterday_zt.json`
- **次日 9:25 竞价**：`hsrl/ssjy_more/{token}?stock_codes=...` —— 由 `wealth_code/adapters/input_auction.py::fetch_realtime_auction` 调用
- **统一客户端**：`common/biying_client.py`（全项目共享，token 零硬编码）

### 🔐 Token 安全（铁律）
- **biyingapi Token 绝不写死在任何项目文件里。** 读取优先级：
  1. 环境变量 `BIYING_TOKEN`（推荐：在 `~/.zshrc` 中 `export BIYING_TOKEN=xxx`，只在本机 shell，永不入库）
  2. 兜底：本机已安装的 a-stock-realtime 技能配置（位于 `~/.workbuddy/skills/...`，**不在项目目录、不会被提交**）
- 项目根 `.gitignore` 已屏蔽 `.env` / `*.local` / `config/settings.json` / `cache/` / `.workbuddy/`，
  确保实时数据与任何密钥都不会被 `git add` 进远端仓库。
- 飞书 webhook 同样视为密钥：`config/settings.json`（本地保留、不提交，仓库用 `config/settings.example.json` 占位）；
  `scripts/intraday_pulse.py` 的兜底已改为读环境变量 `FEISHU_WEBHOOK`，不再硬编码。
- **提交前自查**：`grep -rn "hook/\|BIYING_TOKEN\|[0-9A-Fa-f]\{8\}-" . --include=*.py --include=*.json --include=*.md` 应只命中占位/环境变量，无真实密钥。

### 🤖 拟人旁白（由 agent 直接产出，不调用外部 LLM）
- 设计原则：**agent 本身就是大模型**，个股/全局拟人点评由 agent 在分析时直接写，
  经 `run_eod(narratives=..., global_narrative=...)` 注入候选与 payload，再由 `build_cards` 渲染 `🗣️` 行。
- `pipeline_eod.run_eod` 接收两个可选参数：
  - `narratives: dict[code, str]` —— 个股点评（接力池 + 观察池）
  - `global_narrative: str` —— 全局点评（账户胜率/收益/情绪基调）
- 未传入 → 候选/全局均无旁白，卡片照常（纯数据），保证无人值守自动化稳定。
- 已删除原 `core/narrative_ai.py`（外部 LLM 客户端）；无需任何 LLM 密钥。

