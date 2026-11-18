#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""9:25 池内裁决（Pool Call）—— 2026-09-21 用户定调「接力池＝提前缩范围，不是提前拍板」。

背景（用户原话）：
  「你可以提前做好预测和明天的思路，然后再根据明天瞬间开出来的行情做出判断就好了。
   不一定要跑全量呀，全量你可以提前跑。因为我们这个接力池，你不就是已经提前跑好了的吗？
   所以说，你明天只需要看接力池这里面的哪个能命中就好了。我们不应该这样子吗？
   不然这个接力池的意义是什么呢？」
  「我没有让你的过去决定未来，我是让你在 9:25 再做决定，拿到数据之后再做决定。」

旧口径的问题：盘后（T-1 日）AI 就把 `view = participate / watch / skip` **终态**定死，
次日 09:25 的卡只做「按 5% 硬线判能不能成交」——接力池缩了半天范围，**买哪只却是昨天定的**，
池子的意义被提前消耗掉。

新口径（本模块＝唯一判据源）：
  · 盘后（T-1）＝跑全量 + 出池子 + 写**预案**（攻守基调 / 方向 / 逐票倾向 bias）→ ⛔ 不定"买哪只"。
  · 次日 09:25＝竞价落地后，**只在池内（relay + watch）** 用当日实况判命中 → 定买哪只。

裁决链（对池内每只票，按序短路）：
  ① 无竞价数据          → 观察（诚实·不是放弃）
  ② 竞价 > 5% 用户硬线  → 放弃（买不到）
  ③ 盘后预案 = 回避     → 放弃（昨日的预案就是不看它）
  ④ 低于预期区间下沿    → 放弃（不及预期）        ← 🎯 最高板低开>3% 例外放行（2026-11-02）
  ⑤ 没红开（gap ≤ 0）   → 观察                   ← 🎯 同上唯一例外
  ⑥ 红开但量能不足      → 观察（无量的高开＝诱多）  ← 🎯 未破地板时降半仓放行（2026-11-02）
  ⑦ 同线核心已断板的跟风 → 观察（跟风闸·不占名额）
  ⑧ 所属方向资金失血     → 观察（方向净流出 ≥30 亿·不占名额）        ← 土壤
  ⑨ 位置高 + 二波反抽    → 观察（断板 ≥3 个交易日后重启 ∧ 上一波最高板 ≥3·不占名额）← 位置
  ⑨b 老龙复燃           → 观察（炒过的老票曾 ≥2 连板 ∧ 断板 ≥5 交易日 ∧ 复燃首日仅首板·不占名额）← 老龙
  ⑩ 前一日派发特征       → 观察（换手大 ∧ 封单薄 ∧ 封得晚·三条全中·不占名额）← 盘口
  ⑪ 其余                → 候选
  候选先按「**全市场最高板** → 地位 → 带动 → 连续性 → 竞争身位」，再沿用同线核心、预案/机械强龙
  等旧排序键，取前 N 只 → 参与；其余候选 → 观察（附「池内今天只买前 N 只」）。
  🎯 身位档 2026-11-02 起**参与排序**（此前只拦错不改选）：31 个交易日 / 218 个可执行样本
     实测，只改这一个排序键，验证段日胜率 46.2%→61.5%、日均 −0.09%→+2.25%。
  ⛔ ⑦~⑩ 的判据全部来自**前一交易日收盘**（09:25 时点已可得），不看今日盘中——所以它们能
     在竞价落地的那一刻就生效，而不是收盘后才后悔（2026-10-08 回撤教训）。
  ⚠️ ⑦~⑩ 的先后＝「宏观到微观」：板块有没有钱（⑧）→ 这票在什么位置（⑨ 高位二波 / ⑨b 老龙复燃）
     → 它昨天封得脏不脏（⑩）。位置与老龙必须排在派发前面，否则先被「换手大/封单薄」命中，
     卡面只说症状、说不出真原因（10-08 南华＝位置是主因；10-12 国芳＝老龙是主因）。

融合边界（2026-09-24 用户确认）：盘后 AI 的 `normal` 不能用软理由压过已核实的
机械强龙事实。满足“接力池第1或09:30前封板”，并且没有一字/ST/成交额不足5亿/纯跟风
四类硬伤的 `normal` 候选，进入 09:25 排序时获得“机械强龙优先”；AI 的原始预案和理由
照常保留。它仍须通过当日竞价、5%上限、预期下沿、量能、跟风闸和名额，不会被直接买入。
  N 由 stance 决定（attack 3 / selective 2 / defend 1 / hibernate 0）。
  hibernate 日全部置「观察」，把唯一名额让给既有逃生阀（`ai_caliber.pick_hibernate_valve`）。

⛔ 边界（不可违反）：
  1. **可逆**：`POOL_CALL_ENABLED = False` → 逐字回退旧口径（读盘后 view 终态）。
  2. **判据唯一源**：区间门走 `ai_caliber.gap_zone`、量能门走同一 `VOLUME_WEAK_RATIO`、
     硬线走同一 `USER_ENTRY_MAX_GAP_PCT` —— ⛔ 禁在此另写一套（防卡面/账本双轨脱节）。
  3. **池外不看**：只读 `candidates.relay + candidates.watch`；early_ambush 不参与裁决。
  4. **不写账本**：本模块纯计算，成交与否仍由 `ai_caliber.decide` 落在同一口径上。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent          # wealth_code/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import ai_caliber as _ac                      # noqa: E402
from core import defense_gate as _dg                    # noqa: E402
# 前排/后排身位（2026-10-21 用户贴 92科比 定调·「后排接力严格要谨慎」）：
# 只做「同线谁更靠前」的纯判定，⛔ 不打分、不改阈值。
from core import front_core as _fc                      # noqa: E402
# 锚点判读（2026-10-21）：昨日大将军的今日竞价态度 → 这条线能不能出手。
from core import anchor_call as _ac2                    # noqa: E402
# 周期四阶段 + 中位股（2026-10-21 学 92科比）：只判「这只票的位置该不该碰」。
from core import cycle_phase as _cp                     # noqa: E402
# 早盘接力·动态仓位（2026-10-13 用户需求）：名额的最终向下裁断唯一判据源。
# ⛔ 不在此处另写一套强弱/仓位逻辑——只消费 relay_position 的方案（与卡面/决策层同源）。
from core import relay_position as _rp                   # noqa: E402
# 单票仓位硬顶/百分比文案的唯一源（2026-11-04 单票场景：本模块 ⛔ 不写 0.5 字面量）
from core import risk_limits as _rl                     # noqa: E402
# 🎯 9:25 竞价三态（2026-11-05 用户「系统只吃竞价、直接买；其余人工不记录」）：
#    只用竞价开幅 + 竞价额 + 昨全天成交额 + 逐票质量预期，纯计算、⛔ 不读分时。
from core import auction_state as _ast                  # noqa: E402
# 🎯 逐票「昨日涨停质量 → 今天该开多少」（2026-11-05 用户纠偏：禁固定涨幅区间）
from core import expected_gap as _eg                    # noqa: E402
# 🎯 9:25 三环联动影子上下文（2026-11-05 用户拍板 A 档）：只留痕，⛔ 不裁决。
from core import sector_gap_context as _sgc             # noqa: E402
# 🧩 板块打分（2026-11-10 用户「短线接力的时候，每一个个股其实也是有板块的，这个板块进行一个打分挺好」）：
#    把散在 row_tier / sector_support / theme_linkage / theme_phase 的板块信号收成一个 0~100 的数。
#    第一版**只留痕**（写 views[code].theme_score），⛔ 不改 view / 仓位 / 名额 / 任何闸。
from core import theme_score as _ts                     # noqa: E402

# ── 开关（架构改动必留可逆开关）─────────────────────────────────────────────
# True  = 9:25 池内裁决（本模块接管「买哪只」）
# False = 一键回退旧口径（读盘后 ai_view 的 view 终态，行为与 2026-09-21 前逐字一致）
POOL_CALL_ENABLED = True

# 🚪 隔壁通道（2026-11-04 用户 BBB「今天龙一买不到，必须考虑龙二能不能买」）
# 龙一因一字/ST 买不进时，第 2 身位可进接力池参与竞价；仓位按单票硬顶的一半。
# `POOL_CALL_NEXT_TO_LEADER=False` 回退「龙一买不到＝整条线不做」。
POOL_CALL_NEXT_TO_LEADER = True
NEXT_TO_LEADER_POS_FRAC = 0.5        # 半仓：× 单票硬顶（0.5 → 实际 25%）

# 预案档（盘后写·**不是**终态）：
#   focus  重点看（昨日最看好）
#   normal 一般候选
#   avoid  回避（有硬伤或明确不看好）
BIAS_FOCUS = "focus"
BIAS_NORMAL = "normal"
BIAS_AVOID = "avoid"
_BIAS_LABEL = {BIAS_FOCUS: "重点看", BIAS_NORMAL: "一般", BIAS_AVOID: "回避"}

# 每日可买只数上限（按盘后攻守基调）——hibernate 不给名额，交给逃生阀
POOL_CALL_QUOTA: dict[str, int] = {
    "attack": 3, "selective": 2, "defend": 1, "hibernate": 0,
}
DEFAULT_QUOTA = 2

# ── 🎯 名额目标下限 2（2026-11-04 用户「每日尽量推两个个股」）────────────────────
# 起因：大盘情绪因子（弱档 / 防守降档 / 动态仓位弱档）会把名额压到 1 只 → 单票
#   仓位与择时风险集中，用户要求「每天尽量推两个」以摊薄情绪因子的影响。
# 规则（⛔ 只放宽名额，不放宽任何单票准入）：
#   · 只在 **允许买**（effective_quota >= 1）时生效；effective_quota == 0
#     （冰点 / 环境 block 无回暖 / 方案禁买）**绝不放宽**，仍空仓。
#   · 目标 = min(2, 原 stance 名额)：attack 的 3 只不动，defend 的 1 只也不动
#     （原 stance 就是上限），selective/attack 被情绪压到 1 时回到 2。
#   · ⛔ 5% 硬线 / 量能门 / 预期区间 / 跟风闸 / 身位闸 / 龙一闸 / 薄封闸 /
#     四条件合取 / 两池资格**一律原样**；凑不出第二只合格票就如实只有 1 只。
# 开关可逆：`POOL_CALL_MIN_TWO_SLOTS=False` → 逐字回退旧名额（被压到 1 就只买 1 只）。
POOL_CALL_MIN_TWO_SLOTS = True
POOL_CALL_MIN_TWO_SLOTS_TARGET = 2
# 2026-11-05 用户「轻大盘重个股·买入限制别卡那么死·尽量推 2 个」：
#   defend 档原名额 1 会恒定压死「双票优先」开关（min(2,1)=1）。开启后名额目标
#   不再受 stance 上限压制，但仍要求 effective_quota>=1（冰点/环境 block=0 绝不放宽），
#   且**只放宽名额**——5% / 量能 / 身位 / 龙一 / 薄封 / 观察禁买等个股闸一律不动。
#   凑不出第二只合格票就如实只有 1 只。回退：POOL_CALL_STOCK_FIRST_SLOTS=False。
POOL_CALL_STOCK_FIRST_SLOTS = os.environ.get("POOL_CALL_STOCK_FIRST_SLOTS", "1") != "0"

# ── 🎯 不限制只数 · 平均分配仓位（2026-11-05 用户明确授权）────────────────────
# 用户原话：「能买几个股，我们是不限制的，只要达标了，我们就按照单票最多50%，
#   平均分配仓位买入」。
#   · 名额：`effective_quota >= 1` 时**不再按数字截断**，所有过完个股闸的票都参与；
#     ⛔ `effective_quota == 0`（冰点 / 环境 block 无回暖 / 方案禁买）仍全池观察。
#   · 仓位：N 只均分，每票 `min(单票硬顶 50%, 100%/N)`——N=1 → 50%（不打满 100%），
#     N≥2 → 均分 100% 总仓，单票自动 ≤50%。⛔ 硬顶走 risk_limits 单一源。
#   · 「平均分配」覆盖账户档位 per_pick_cap/total_cap（用户「轻大盘重个股」），
#     但**不放宽任何个股闸**：5% 硬线 / 量能 / 身位 / 龙一 / 薄封 / 四条件照旧。
# 三个开关各自可逆，任一置 0 逐字回退对应旧行为。
POOL_CALL_UNLIMITED_SLOTS = os.environ.get("POOL_CALL_UNLIMITED_SLOTS", "1") != "0"
POOL_CALL_EVEN_ALLOCATION = os.environ.get("POOL_CALL_EVEN_ALLOCATION", "1") != "0"
# =1 时平均分配后仍叠加账户回撤压仓（保守档，默认关）
POOL_CALL_EVEN_RESPECT_DRAWDOWN = os.environ.get("POOL_CALL_EVEN_RESPECT_DRAWDOWN", "0") == "1"
# =1 时量能 soft 档在平均分配基础上再按档降仓（默认关：用户「只要达标了就平均分配」）
POOL_CALL_VOLUME_SCALE_POSITION = os.environ.get("POOL_CALL_VOLUME_SCALE_POSITION", "0") == "1"
# ── 🎯 高开接近涨停降权（11-05 21:45 用户「大于 6、7 个点要非常小心，降低买入权重」）──
#   ⚠️ 与上面量能档**默认关**不同，本项默认开：这是用户 21:45 的新指令，
#      「直接上最新的策略」。量能档仍按旧约定默认关，两者互不覆盖。
#   ⛔ 只降 `position_cap`（买多重），不改 zone/view/participate/quota —— 不是硬闸，
#      不会把扬子新材那类票变成「不买」，只是从满仓降到试错仓。
#   与量能档**取小**不相乘（同一笔风险不折两次）；唯一实现 `ai_caliber.gap_weight`。
#   回退：`POOL_CALL_GAP_WEIGHT_POSITION=0`（或 `AI_GAP_WEIGHT=0`）→ 逐字回到旧均分。
POOL_CALL_GAP_WEIGHT_POSITION = os.environ.get("POOL_CALL_GAP_WEIGHT_POSITION", "1") != "0"

# ── 🔁 观察票竞价复核（11-05 用户拍板 A：「观察肯定也要再通过竞价这里再判一遍，
#    而不是直接 pass 掉」；起因＝盘后 20 只 observe_only 在 09:25 一步 continue，
#    「0 买入」连竞价都不看就定了）──────────────────────────────────────────
#   · 全部 observe_only 票都先读今早真实开盘/竞价额/量能档，逐票留 `auction_recheck`；
#   · `state`（盘后状态类）才进入完整竞价裁决链，且必须量能 full 档才算「拿真金白银
#     推翻了盘后状态」；其余闸（5%/预期/预案/身位/龙一/薄封/四条件）一律原样；
#   · `hard`/`restricted` 复核后仍观察，理由写今早实测，⛔ 不再用一句固定话术跳过；
#   · 身份不变：`participation_eligibility` 仍是 observe_only、buyable=False。
# 两个开关各自可逆：POOL_CALL_RECHECK_OBSERVE=0 逐字回到旧一步短路。
POOL_CALL_RECHECK_OBSERVE = os.environ.get("POOL_CALL_RECHECK_OBSERVE", "1") != "0"
POOL_CALL_RECHECK_STATE_FULL = os.environ.get("POOL_CALL_RECHECK_STATE_FULL", "1") != "0"

# ── 🧬 强基因豁免（2026-11-10 09:50 用户「量能可以增加强基因豁免，但是我这边说的
#    历史连板，是指类似于冰山冷热这种，近一个月有连板，昨天涨停」）──────────────
# 只豁免**一处**：上面那条「复核票须 full 量才算推翻盘后判断」的加严条款。
#   实测当日（11-10）：锋龙股份 002931 09:25 开 +4.88%（5% 线内可买）、量 62% →
#   被该条判 watch，随后封板 +10.01%；日盈电子 603286 量 67% → watch，收 +6.79%。
# ⛔ 不拆量能硬地板（`ai_caliber.volume_band` 的 dry<0.35 仍拦，31 日回放：
#   放进真没量的票，日均 2.79%→1.30%）。⇒ 只放行 `soft`（0.35~0.70），不放行 `dry`。
# ⛔ 不进 zone / view / 名额 / 排序 / stance，只把「这条加严闸」让开。
POOL_CALL_GENE_EXEMPT = os.environ.get("POOL_CALL_GENE_EXEMPT", "1") != "0"
# 基因窗口回看天数（交易日）。基因判定窗口是 31 **自然日** ≈ 22 交易日，
#   而共享 `zt_series` 只回看 `POSITION_LOOKBACK_DAYS=20` 交易日（≈28 自然日）
#   ⇒ 会截掉窗口最前面 2~3 天。实测 11-09：20 日序列最早到 10-13，
#   窗口起点 10-09，安泰集团 600408 因此被算成「1 板」（真实 2 板）、
#   文投控股 600715 涨停次数少 1。⇒ 基因单独补一份长序列，⛔ 不改共享 zt_series
#   （位置闸/老龙闸的 20 日口径不能跟着变）。
POOL_CALL_GENE_LOOKBACK_DAYS = int(
    os.environ.get("POOL_CALL_GENE_LOOKBACK_DAYS", "30"))

# ── 🚫 顶部空间硬闸（2026-11-06 14:45 用户「一定要避开这种开的特别高又没有资格和
#    行情秒板的票呀（扬子新材 +8.93%）」）──────────────────────────────────────
# 用户要的不是「高开降权」（已有 `ai_caliber.gap_weight`，只降到 0.2 仓），是**不买**。
#
# 数据（`scripts/research_open_gap_band.py`，270 票次 / 28 交易日 09-21~11-05，
# 全为可成交票、当日 close/open−1）：
#   · 开盘幅度单调恶化：0~2% +0.25%/胜55% → 2~5% −1.65% → 5~8% −2.30% → ≥8% −5.38%/胜23%
#   · 归一到涨停幅度 gap/limit：≤20% +0.06%、20~50% −1.48%、50~80% −2.62%、≥80% −7.19%
#   · 事后真相：开盘≥5% 的 33 只里，T日真封板 8 只 +3.42%/胜100%，
#     没封板 25 只 **−5.73%/胜 8%** —— 「开得高又封不住」就是用户点名的那一类。
#     而这 8 只事前**没有任何指标能筛出来**（题材家数 / 封单比 / 量能档 / 环境 level /
#     板数 / 昨日首封时间 / DEV / line_is_core 全试过，每一档都是负期望）；
#     ⚠️ 反直觉：昨日首封 ≤10min（秒板）次日高开更差（n=20 −4.01%/胜20%），
#     ≥60min 才勉强不亏（n=9 −1.16%/胜55.6%）——「强票开得高」是共识过热，不是优势。
# ⇒ 既然档内不可分辨，只能**整档砍**，不做「有资格就放行」的例外。
#
# 判据（赔率口径，不是绝对百分比，20cm 自动等价）：
#   剩余到涨停的空间 `room = 涨停幅度 − 今开幅度`，要求 `room ≥ 涨停幅度 × 阈值`。
#   默认阈值 0.5 → 10cm 需 ≥5 个点、20cm 需 ≥10 个点。即「半路上车」。
#   回测：砍掉 29 只 −3.88%/胜27.6%（T+1 −4.43%），留下 241 只 −0.32%/胜43.6%。
#   阈值放宽到 0.4 会多砍 14 只（43 只合计 −3.76%），留下 227 只 −0.12%——方向同。
# ⛔ 可逆：`POOL_CALL_HIGH_GAP_REJECT=0` → 逐字回到「只降权不否决」的 11-05 行为；
#   `POOL_CALL_HIGH_GAP_ROOM` 调阈值（0.4 / 0.6 可现场改，不用改代码）。
# ⛔ 不覆盖：竞价封死（zone=above，买不进是物理事实，走 ② 的文案）、
#   `_lo_top` 低开例外（低开 room 恒 > 涨停一半，天然不触发）。
POOL_CALL_HIGH_GAP_REJECT = os.environ.get("POOL_CALL_HIGH_GAP_REJECT", "1") != "0"
try:
    HIGH_GAP_ROOM_MIN = float(os.environ.get("POOL_CALL_HIGH_GAP_ROOM", "0.4"))
except ValueError:
    HIGH_GAP_ROOM_MIN = 0.4
# 🔓 2026-11-12 用户「稍微倾斜优化，不用卡这么死」：0.5 → 0.4
#    即「离涨停不足 4 个点才拒」≈ 允许高开到 +6%（原来 +5%）。
#    实证：日出东方昨日一字、今日开 +5.26% 差 0.26 点被拒，随后涨停。
#    回退：`POOL_CALL_HIGH_GAP_ROOM=0.5`（原口径）或 `1.0`（关闭）。

# ── 🧮 当日整体开盘偏移修正 DEV（2026-11-06 用户「今天受外盘影响…低开是可以理解的」）──
# 病根：`DEV = gap − E` 是**绝对**判据。外盘砸下来那天池内中位开幅 −1.5%，每只票的
#   DEV 都被同一个负偏移压低 → 逐票质量判据集体误杀（11-06 被 DEV 门砍掉的 5 只事后
#   均值 **+1.11%**，其中博纳影业 +5.24%、模塑科技 +2.34%）。
# 修法：把池内**中位开幅**当作当日「环境基准」扣掉，DEV 改判「比同行强多少」。
#   ⛔ 只在整体明显低开（中位 ≤ `DEV_ENV_TRIGGER_PCT`）时启用，平时恒 0（零回归）。
#   ⛔ 偏移量上限 `DEV_ENV_MAX_OFFSET_PCT`，防单日极端把闸整个废掉。
#   ⛔ 样本 < `DEV_ENV_MIN_SAMPLES` 不判（缺数据不裁决）。
# 可逆：`POOL_CALL_DEV_ENV_OFFSET=1` 打开。
# ⚠️ 2026-11-06 实测（35 日 A/B，`scripts/research_new_caliber_ab.py`）：开偏移只新增 2 笔、
#    均 −3.43%（胜 0%），把日均从 +0.33% 压到 +0.22%。中位数偏移量只有 1 个点，
#    而被误杀的票 DEV 在 −5 以下 —— **幅度不够**，改由下面的「深低开豁免」处理。
#    本开关**默认关**，仅留作对照与极端日人工复核。
POOL_CALL_DEV_ENV_OFFSET = os.environ.get("POOL_CALL_DEV_ENV_OFFSET", "0") != "0"
try:
    DEV_ENV_TRIGGER_PCT = float(os.environ.get("POOL_CALL_DEV_ENV_TRIGGER", "-1.0"))
except ValueError:
    DEV_ENV_TRIGGER_PCT = -1.0
try:
    DEV_ENV_MAX_OFFSET_PCT = abs(float(os.environ.get("POOL_CALL_DEV_ENV_MAX", "3.0")))
except ValueError:
    DEV_ENV_MAX_OFFSET_PCT = 3.0
DEV_ENV_MIN_SAMPLES = 8

# ── 🕳️ 深低开豁免 DEV（2026-11-06 用户「今天低开是可以理解的」的**真解**）──────────
# 病根不是「环境整体压低 DEV」，而是 **DEV 的收益曲线是 U 形**（35 日 · 366 票次实测）：
#
#   DEV 档          n    均值     胜率   中位
#   < −6           40  +2.69%    60%   +2.02%   ← 砸出来的错杀，最好的一档
#   −6 ~ −4        24  −0.48%    38%   −1.65%
#   −4 ~ −2        65  −0.92%    35%   −1.50%   ← 最差的一档
#   −2 ~ 0         83  +0.04%    48%   −0.44%
#   0 ~ +3         87  −0.21%    45%   −0.70%
#   ≥ +3           67  −2.87%    30%   −2.58%   ← 高开透支（另由顶部空间闸/降权管）
#
# 旧规则「DEV < −2 一律放弃」把**最差档和最好档一起砍**。故改为**带豁免**：
#   DEV ≤ `DEEP_LOW_OPEN_DEV` → 不按「走弱」处理，放行（标记 `deep_low_rescue`）。
#   `DEEP_LOW_OPEN_DEV < DEV < EXPECTED_GAP_DEV_MIN` → 仍放弃（那才是真走弱）。
# ⛔ 只放宽「④ 明显低于预期」这一道门：量能/顶部空间/身位/名额/仓位一律原样。
# ⛔ 一字板、竞价封死仍在其之前被判 `above`，进不来。
# 可逆：`POOL_CALL_DEEP_LOW_RESCUE=0` 逐字回到「DEV<−2 全放弃」。
POOL_CALL_DEEP_LOW_RESCUE = os.environ.get("POOL_CALL_DEEP_LOW_RESCUE", "1") != "0"
# 🧵 2026-11-16 用户「弱线的深低开不是错杀，是走弱」：
#    错杀档必须是**成线**的票（该线昨日 ≥ N 家涨停）才救，孤线/弱线不救。
#    实证：11-16 金辰股份（光伏仅 2 家、昨日 3 板炸 1）开 −7.45% 被救 → 与「只做最强线」冲突。
#    回退：`POOL_CALL_DEEP_LOW_MIN_MATES=0`。
try:
    DEEP_LOW_MIN_MATES = int(os.environ.get("POOL_CALL_DEEP_LOW_MIN_MATES", "3") or 0)
except ValueError:
    DEEP_LOW_MIN_MATES = 3
# 🥇 更硬的一条：错杀档只救**当日最强的前 N 条线**（按昨日涨停家数，取不同家数档）。
#    11-16 实证：传媒 7 家、汽车零部 6 家，绿电 3 家 → 金辰不该被救。
try:
    DEEP_LOW_TOP_LINES = int(os.environ.get("POOL_CALL_DEEP_LOW_TOP_LINES", "2") or 0)
except ValueError:
    DEEP_LOW_TOP_LINES = 2

# 🧲 2026-11-16 用户定调（主线题材为核心逻辑）：
#   「不能看单个个股，整个板块和老龙的共振，预示着一个题材强度。」
#   共振三项各 1 分：① 同线出现一字/大幅高开 ② 同线老龙红开 ③ 昨日家数成线（≥3）。
#   强共振（≥2）且是当日最强线 → 该线的**非核心可买票**允许参与（半仓），
#   ⛔ 不是「核心买不到就推二排」，是「这条线整体在走强」。
#   回退：`POOL_CALL_SECTOR_RESONANCE=0`。
POOL_CALL_SECTOR_RESONANCE = os.environ.get("POOL_CALL_SECTOR_RESONANCE", "1") != "0"
# 🌱 2026-11-17 用户：「瀛通通讯连续两天错过」——首板走「平开→再封」这段我们没有任何进场规则。
#    新增「首板平开确认」档：昨日首板 + 今早平开(-1%~+1.5%) + 线内成线(≥3家)
#    + 昨日封得早或零炸板 → 允许**25% 试仓**参与（不受「低于预期」与「量能地板」一票否决）。
#    回退：`POOL_CALL_FIRST_BOARD_FLAT=0`。
POOL_CALL_FIRST_BOARD_FLAT = os.environ.get("POOL_CALL_FIRST_BOARD_FLAT", "1") != "0"
FB_FLAT_LO = float(os.environ.get("POOL_CALL_FB_FLAT_LO", "-1.0"))
FB_FLAT_HI = float(os.environ.get("POOL_CALL_FB_FLAT_HI", "1.5"))
FB_FLAT_MIN_MATES = int(os.environ.get("POOL_CALL_FB_FLAT_MATES", "3") or 3)
# 🧬 股性替代（用户 2026-11-17 选 B）：孤票首板不看线内家数，只要股性别太差
FB_FLAT_GENE_MIN = float(os.environ.get("POOL_CALL_FB_FLAT_GENE", "20"))
FB_FLAT_POSITION = float(os.environ.get("POOL_CALL_FB_FLAT_POSITION", "0.25"))
RESONANCE_STRONG_GAP = float(os.environ.get("POOL_CALL_RESONANCE_GAP", "7.0"))   # 一字/大幅高开线
RESONANCE_MIN = int(os.environ.get("POOL_CALL_RESONANCE_MIN", "2") or 2)
RESONANCE_SECOND_CAP = float(os.environ.get("POOL_CALL_RESONANCE_CAP", "0.5"))
# ⛔ 2026-11-17：接近跌停的开盘不是「错杀」，是崩（文投 −9.89% 被当错杀买入的教训）。
#    下限默认 −8.5%：比它更低的直接不救。
DEEP_LOW_GAP_FLOOR = float(os.environ.get("POOL_CALL_DEEP_LOW_FLOOR", "-8.5"))
try:
    DEEP_LOW_OPEN_DEV = float(os.environ.get("POOL_CALL_DEEP_LOW_DEV", "-6.0"))
except ValueError:
    DEEP_LOW_OPEN_DEV = -6.0

# ── 🧬 龙一基因复核（2026-11-06 用户「东方海洋上次四连板，皇台只有三连板」）──────────
# 盘后 `sector_leader` 已知用「板数 → 上一波连板高度 → 封板时段…」排龙一；但**旧候选
#   文件**里的 `dragon_rank` 是按旧序（板数 → 封板时段）写死的。09:25 是最终裁决，
#   故在此做一次基因复核：同板数、且本票上一波**严格更硬** → 不按「跟风」否决。
#   ⛔ 只放宽「龙一身位」这一件事：⑩d 之外的所有闸（量能/预期/身位/顶部空间/名额）
#      一律原样，仓位仍按既有 cap。`POOL_CALL_DRAGON_GENE=0` 逐字回退。
POOL_CALL_DRAGON_GENE = os.environ.get("POOL_CALL_DRAGON_GENE", "1") != "0"
# 换位：认定挑战者更强时，原龙一**降为跟风**（⑩d 会拦），而不是两个都买。
#   理由：⑩d 的语义是「只做龙一、跟风不追」；两个都留等于同一套判据下自相矛盾。
#   11-06 实证：皇台 −10.66% + 东方海洋 +8.17% 一对仓位对冲成 −1.25%，只买对的那只 +8.17%。
#   `POOL_CALL_DRAGON_GENE_SWAP=0` → 只放行不顶替（两个都买，旧的「只放开」行为）。
POOL_CALL_DRAGON_GENE_SWAP = os.environ.get("POOL_CALL_DRAGON_GENE_SWAP", "1") != "0"

# ── 📋 盘后预案「回避」是否仍是硬否决（2026-11-06 用户「盘后预案这个功能关了」）──────
# 用户原话：「盘后预案这个功能关了，根本不需要，全网全系统关闭这个盘后预案。」
#   病根：盘后 LLM 给的 `bias=avoid` 在 09:25 是**一步否决**，且理由无代码层校验
#   （09-22 已踩过一次：真龙被软理由判 avoid）。11-06 当天 7 只被 avoid 砍掉，
#   事后均值 **+0.42%**，其中新亚制程 **+16.00%** 是当日第二强。
# ⛔ 关闭后：`avoid` 只留痕（`bias_avoided=True`），不再否决；买不买交给竞价链自己判。
#   预案文案与卡面展示一律保留 —— 关的是「硬否决」这一个动作。
# 可逆：`POOL_CALL_BIAS_AVOID=1` 逐字回到「盘后回避 → 今天不买」。
POOL_CALL_BIAS_AVOID_VETO = os.environ.get("POOL_CALL_BIAS_AVOID", "0") == "1"

# ── 🚫 盘后预案是否进入 09:25 裁决（2026-11-06 用户「盘后预案全系统关闭·根本不需要」）──
# 用户原话：「盘后预案这个功能关了，根本不需要，全网全系统关闭这个盘后预案！！」
# 关的是**整条消费链**，不只是「回避否决」：
#   · `views[code].bias`（重点看/一般/回避）→ 不再参与任何判断，只留 `bias` 字段供追溯；
#   · `plan.stance`（攻守基调）→ 不再兜底名额，缺省走 `DEFAULT_QUOTA`。
# 📊 35 日实测（`scripts/research_cycle_ab.py` → `cache/research/cycle_ab_bt.json`）：
#    读预案 20天/67笔/日胜70.0%/日均+1.73%/复利+39.99%
#    → 不读   25天/83笔/日胜72.0%/日均+1.97%/**复利+61.05%**（回撤同为 −4.70%）
#    新增 16 笔均 +1.70%，**全部**落在「盘后 LLM 判 hibernate（空仓）」的那 5 天。
#    ⇒ LLM 的「今天别做」在本窗口是**负价值**，关掉它是净收益。
# ⚠️ 这 35 日是强势窗口，⛔ 不能据此说「永远该做」。真正的熔断是环境防守腿
#    （`env level=block → effective_quota=0` 照旧生效），与 stance 无关。
# 回退：`POOL_CALL_EOD_PLAN=1` 逐字恢复「读盘后预案」。
POOL_CALL_EOD_PLAN = os.environ.get("POOL_CALL_EOD_PLAN", "0") == "1"

# ── 🌅 09:25 机械定调（关预案后**可选**的定调来源）──────────────────────────
# 只用**今晨竞价开出来那一刻**就能看到的四个数：昨涨停红开率 / 连板红开率 /
#   连板竞价量比 / 覆盖率（`core.market_open`）。⛔ 不看指数、不看 LLM 主观。
# 档位沿用既有 `POOL_CALL_QUOTA`：attack 3 / selective 2 / defend 1 / hibernate 0。
# 📊 35 日实测（同上脚本 P2）：24天/81笔/日胜70.8%/日均+1.96%/复利+57.73%
#    —— **略差于「不设定调·名额恒 2」（+61.05%）**：它的 `hibernate` 档又在重复
#    「今早很弱 → 空仓」，而本窗口里「空仓」整体站在错的那一边。
#    ⚠️ 阈值 0.60/0.45/0.30 扫到 0.50/0.35/0.20，日均几乎不动（平台非尖峰）——
#       它本身没坏，只是在这个窗口里不如「无条件给名额 + 环境腿兜底」。
# ⇒ 默认关。留作弱市窗口的备选，⛔ 不得据此宣称提高胜率。
POOL_CALL_STANCE_INTRADAY = os.environ.get("POOL_CALL_STANCE_INTRADAY", "0") == "1"

# 回看天数（找「同一条线的核心」——含已断板的核心）
LINE_CORE_LOOKBACK_DAYS = 4

# ── AI × 机械强龙融合（2026-09-24 用户确认）────────────────────────────────────
# 盘后 AI 的 bias 是预案，不可用软理由覆盖候选已验证的强龙事实。融合只影响 09:25
# 名额排序：强龙 normal 与 focus 同档优先；avoid 仍由既有硬伤守卫和短路规则处理。
# 开关关掉可逐字回退 09-24 前的排序（同线核心 → focus → 板数 → 同线身位）。
ENABLE_MECH_LEADER_FUSION = True
MECH_LEADER_SECOND_BOARD_MAX_FBT = 930
MECH_LEADER_MIN_CARRY = 4
MECH_LEADER_MIN_TURNOVER_YI = 0.05   # 🔴 2026-11-11 用户「5 亿全系统清除」→ 仅留数据防呆（原 5.0）

# ── 跟风闸（2026-09-21 用户当场抓包·最高优先级）──────────────────────────────
# 用户原话：「它也是风电，明显就是闽东电力的跟风。它虽然涨得多，但是它依然是小弟的地位，
#   闽东电力的地位在风电里面依然是绝对的核心！」
# 实证：闽东电力(000993) 09-16 六连板 → 09-17 断板 → 不在 09-18 候选池；池内只剩
#   09-15 才首板的锡华科技(603248·4板)。旧逻辑按单票得分排序 → 把跟风当核心买入。
# 规则：同一条线里存在**板数高出 ≥ CORE_GATE_MIN_GAP 的核心**、且该核心**已断板**
#   （＝核心不在池内，系统无法靠排序表达"核心优先"）时 → 该跟风票**不占当日买入名额**，
#   降级「观察」并在理由里点名核心是谁（让读者看到身位差，而不是盲追小弟）。
# ⛔ 可逆：`POOL_CALL_CORE_GATE = False` → 逐字回到「只看单票强度」的旧行为。
# ⛔ 只拦「核心已断板」这一种：核心**仍在板且仍在池内**时，靠排序（同线核心优先）即可
#   表达，不必硬拦——否则会误杀正常的低位接力（用户战法本就有 2 进 3 倾斜）。
POOL_CALL_CORE_GATE = True

# ── 🧮 竞价广度（2026-11-11 用户「前五好看但后排大面积水下，是风险」）────────────
# 只看身位前五会漏掉整条线的承接质量。广度 = 这条线**昨日全部涨停票**今早的竞价分布。
# ⛔ 只上卡面一行说明，不进任何判据、不改名额/仓位/账本；无数据就不写（不猜）。
# 回退：`POOL_CALL_BREADTH=0`。
POOL_CALL_BREADTH = os.environ.get("POOL_CALL_BREADTH", "1") != "0"
# 🐉 2026-11-11 晚 用户：「应当直接买入龙头股，仅当它直接高开涨停时才放弃」。
#    开启后：**龙一/这条线核心**不适用「顶部空间不足就放弃」——高开也买，
#    只有竞价封死/一字（物理买不到）才放弃。⛔ 默认关，先跑回放再决定。
POOL_CALL_LEADER_IGNORE_ROOM = os.environ.get("POOL_CALL_LEADER_IGNORE_ROOM", "0") == "1"
# 🧠 智能评分层（2026-11-11 计划 P2）：只在**机械允许集内**做排序微调/弃权。
#    off    = 完全不加载（逐字节回退旧行为）
#    shadow = 只记录「如果听了会怎样」，⛔ 不改任何 view / 顺序
#    live   = 应用 tie-break（仅同键组内）；弃权另受 AI_TILT_ABSTAIN 控制
AI_TILT_MODE = os.environ.get("AI_TILT_MODE", "shadow").strip().lower()
AI_TILT_ABSTAIN = os.environ.get("AI_TILT_ABSTAIN", "0") == "1"
AI_TILT_ABSTAIN_P = float(os.environ.get("AI_TILT_ABSTAIN_P", "0.28") or 0.28)
# 🧪 2026-11-11 用户「挑着做题材催化强度」：LLM 只给题材催化分，⛔ 不给买卖结论。
#    shadow（默认）= 只记录每只参与票所属题材的催化等级；live = 低于门槛不做。
CATALYST_MODE = os.environ.get("CATALYST_MODE", "shadow").strip().lower()
CATALYST_MIN_LEVEL = os.environ.get("CATALYST_MIN_LEVEL", "normal").strip().lower()
# ⚖️ 2026-11-12 用户选 A：催化分**进权重**（排序 + 仓位），默认开。
#    · 排序：同档内 strong 优先（⛔ 只做同分键，不推翻板数/身位/核心）
#    · 仓位：strong 1.0 / normal 0.85 / weak 0.6 / unknown 1.0（不罚"不知道"）
CATALYST_SORT_WEIGHT = os.environ.get("CATALYST_SORT_WEIGHT", "1") != "0"
CATALYST_POSITION_WEIGHT = os.environ.get("CATALYST_POSITION_WEIGHT", "1") != "0"
CATALYST_RANK = {"strong": 2.0, "normal": 1.0, "unknown": 1.0, "weak": 0.0}
CATALYST_SIZE = {"strong": 1.0, "normal": 0.85, "unknown": 1.0, "weak": 0.6}
# 🔓 2026-11-12 用户「稍微倾斜优化，不用卡这么死」：
#    同线二排比核心高开 ≥5 个点（核心明显转弱）→ 允许二排进裁决（半仓）。
#    ⛔ 不是「核心买不到就推二排」；二排仍须自己量能/空间达标。
POOL_CALL_CORE_WEAK_SECOND = os.environ.get("POOL_CALL_CORE_WEAK_SECOND", "1") != "0"
CORE_WEAK_SECOND_DIFF = float(os.environ.get("POOL_CALL_CORE_WEAK_SECOND_DIFF", "5") or 5)
# 🧊 2026-11-11 用户 D：一字板允许进接力池，但「没有换手」→ 明天买点要观察 + 降权。
#    shadow 只标注；live（默认开）在分配仓位时**减半**并写进理由。
POOL_CALL_ONE_WORD_DISCOUNT = os.environ.get("POOL_CALL_ONE_WORD_DISCOUNT", "1") == "1"
# 🚫 首板只看不买（2026-11-12 用户：「三钢闽光明明写的是只观察，结果被买了」）。
#    判据：`watch_group == "首板观察"` 或（`boards==1` 且不是新方向首板槽）。
#    回退：`POOL_CALL_FIRST_BOARD_BUY=1`。
POOL_CALL_FIRST_BOARD_BUY = os.environ.get("POOL_CALL_FIRST_BOARD_BUY", "0") == "1"

# 🚫 题材缺失不裁决（2026-11-12 用户：「缺题材归属的直接 pass，不该进裁决池」）。
#    判据：`core_theme` 缺失或占位（"", "-", "—", "其他", "无"）。
#    ⛔ 只挡「没有题材归属」的票，行业兜底（theme=普钢 这类）不算真题材。
POOL_CALL_REQUIRE_THEME = os.environ.get("POOL_CALL_REQUIRE_THEME", "1") == "1"
# 🧓 老票高位节点（2026-11-12 用户：「像光洋这种老票，3 板是一个节点…这个位置只观察不要买」）。
#    判据：近一年涨停 ≥8 次（股性老练）∧ 当前 ≥3 板 → 只观察，不买。
#    ⛔ 只挡「老票的高位节点」，不挡新股/次新首板连板（它们没有历史包袱）。
#    数据 `cache/zt_genes/gene_<code>.json`。
# ⛔ 2026-11-12 10:30 用户纠正：「近一年跨度太大了」——
#    正确口径是**最近一个月**：第一波已经走完，现在走第二波，3 板就是危险节点。
#    判据：当前连续涨停 ≥3 天 ∧ 这波之前 20 个交易日内已有 ≥3 次涨停（第一波走完）。
POOL_CALL_SECOND_WAVE_MIN_BOARDS = int(os.environ.get("POOL_CALL_SECOND_WAVE_MIN_BOARDS", "3") or 3)
POOL_CALL_SECOND_WAVE_PRIOR_ZT = int(os.environ.get("POOL_CALL_SECOND_WAVE_PRIOR_ZT", "3") or 3)
POOL_CALL_SECOND_WAVE_WINDOW = int(os.environ.get("POOL_CALL_SECOND_WAVE_WINDOW", "20") or 20)
POOL_CALL_OLD_STOCK_OBSERVE = os.environ.get("POOL_CALL_OLD_STOCK_OBSERVE", "1") != "0"
_THEME_MISSING = {"", "-", "—", "其他", "无", "未知", "none", "null"}
BREADTH_MIN_SIZE = 8          # 少于这个家数的线不判广度（百分比无意义）
BREADTH_DIVERGE_UNDER = 0.40  # 水下占比 ≥40% → 分化
CORE_GATE_MIN_GAP = 2
# ── 归并一致性守卫（2026-10-12 传媒事故结构性兜底）────────────────────────────
# 跟风闸拦票前，校验候选 `core_theme` 归线 与 `sector` 归线是否**矛盾**——
# 矛盾（如 core_theme 被误标「AI算力」而 sector=数字媒体→传媒）说明题材归并可疑
# → ⛔ 不硬拦（防「一次归并错误就整条误杀」，10-12 中文在线/掌阅被误杀即此）。
# ⛔ 可逆：False → 回到只看「板数差 ≥2 + 核心已断板」。
POOL_CALL_LINE_CONSISTENCY_GATE = True

# ── 🚧 后排接力闸（2026-10-21 用户「后排接力严格要谨慎！很有可能没有溢价！」+ 92科比贴图）──
# 用户原话：「选票，往前排核心选」「看高做低，做高看低」「后排接力严格要谨慎！很有可能没有溢价！」
# 与上面「跟风闸」的分工（⛔ 不是重复）：
#   · 跟风闸：只在「同线核心**已断板**」时拦 —— 治的是「核心不在池内、系统只能按单票分排」；
#   · 后排闸：**不等核心断板**。同线只要还有比它靠前的票在，它就是后排 —— 治的是
#     「溢价先被前排吃掉，后排竞价看着达标其实接的是最后一棒」。
# 分档（池内板数排名 `line_rank`，见下方 _line_rank）：
#   · 前排（本条线最靠前 / line_is_core）→ 放行，正常参与名额竞争；
#   · 次排（line_rank == 2，或池外核心只高 1 板）→ 需**量能确认**（竞价额 ≥ 计划需求）；
#   · 后排（line_rank ≥ 3，或池外核心高出 ≥2 板）→ 同样必须量能确认，且**排序永远让给前排**
#     （见 `_key` 的首位身位档）—— 名额有限时先给前排，只有前排填不满、且它自己放量，
#     才轮得到后排。
# ⛔ 不另设 gap 上限：5% 是用户唯一硬线（`ai_caliber.USER_ENTRY_MAX_GAP_PCT`，卡面/账本同源），
#   后排闸再加一条 3% 会造成「卡面说没买到、账本记买到」的三方打架（09-14 闽东电力教训）。
# ⛔ 可逆：`POOL_CALL_BACKROW_GATE = False` → 逐字回到「跟风闸单闸」的旧行为。
POOL_CALL_BACKROW_GATE = True
BACKROW_MIN_VOL_RATIO = 1.0       # 次排/后排要求 竞价额 / 计划需求 ≥ 该值（＝放量确认）

# ── 🧩 板块打分（2026-11-10）：只留痕，不裁决 ───────────────────────────────
# 用户原话：「短线接力的时候，每一个个股其实也是有板块的，这个板块进行一个打分挺好」
# 把规模（同板块家数）/ 高度（板块最高板）/ 卡位（本票身位）/ 承接（今早红开率）
# 四维等权合成 0~100，写进 `views[code].theme_score`。
# ⛔ 第一版**不参与任何判断**：不改 view、不改 position_cap、不改名额、不进任何闸。
#    要进权重必须先用 `scripts/research_theme_score_ab.py` 证明分档单调（分越高收益越高），
#    否则它只是个好看的数 —— 那比没有更糟，因为它会让人以为系统看了板块。
# 回退：`POOL_CALL_THEME_SCORE=0` → 完全不计算、不写字段。
POOL_CALL_THEME_SCORE = os.environ.get("POOL_CALL_THEME_SCORE", "1") != "0"

# ── 🌊 题材动量（2026-11-10 第二轮）：也先只留痕，等验证 ─────────────────────
# 热度分验证下来**分越高反而越差**（35 日 ≥70 强 −0.57% vs 30~49 弱 +0.24%），
# 说明「线有多热」已被身位/板数覆盖。改打**变化**：家数环比 / 位置(÷5日峰值) / 连续天数。
# 数据来自 `cache/theme_momentum/<day>.json`（由 `scripts/build_theme_momentum.py` 预生成，
# 底座是 zt_history + theme_snapshot，⛔ 不用只有一份的 theme_rotation）。
# ⛔ 同样只写 `views[code].theme_momentum`，不改任何裁决。
# 回退：`POOL_CALL_THEME_MOMENTUM=0`。
POOL_CALL_THEME_MOMENTUM = os.environ.get("POOL_CALL_THEME_MOMENTUM", "1") != "0"


# ── ⚓ 锚点闸（2026-10-21 用户口述「次日只需做好锚点的对比与观察」）────────────────
# 体系：昨天收盘用**涨停时间**定出每条线的大将军（`core/anchor_call.pick_general`）→
#   今天 09:25 只看这只怎么开（一字/大高开/强开/平开/破预期）→ 决定这条线今天能不能出手。
# 本闸只做**最强否决**一件事：
#   · 锚点破预期（低开超 1% 或低于昨日预期下沿）→ 该线其它票一律不占名额；
#   · 锚点只是低开（0~-1%）→ 同样先看，等它回红盘。
#   · 锚点一字/大高开（买不到）→ **不拦**，那是「打隔壁」场景（与后排闸同一口径）。
# ⛔ 缺锚点数据（核心不在涨停池 / 已断板 / 没报价）→ **不裁决**，诚实降级不猜。
POOL_CALL_ANCHOR_GATE = True
POOL_CALL_ANCHOR_MIN_GAP = -1.0

# ── 🎯 中位股闸（2026-10-21 学 92科比 §5.1「要么做高，要么做低，中间不要碰」）──────────
# 中位 = 既有不了最高板的地位，也没低位的性价比。只在市场高度 ≥5 板时才存在中位区。
# ⛔ 可逆：`POOL_CALL_MIDBOARD_GATE=False` → 恢复旧行为。
POOL_CALL_MIDBOARD_GATE = True
# ── 🛡️ 买入前防守闸门（2026-10-08 大幅回撤经验固化·见 core/defense_gate.py）──────
# 实证：10-08 只买两只（襄阳轴承 −5.85% / 南华生物 −8.18%，平均 −7.02%，净值 1.058→0.984）。
# 两只票在**前一日收盘**就同时具备「换手 >15% + 封单/流通 <2% + 10:00 后才封板」三条特征，
# 而同日走出来的票三条命中 0~2 条。→ 09:25 竞价达标**不再等于**可以买：
#   · 派发特征（三条全中）→ 降级观察、**不占当日名额**（名额让给封板质量好的票）
#   · 方向资金失血（所属方向昨日净流出 ≥30 亿）→ 同上
#   · 位置高 + 二波反抽（断板 ≥3 个交易日后重启）→ 同上（2026-10-08 补·南华案例）
#   · 老龙复燃（炒过的老票曾 ≥2 连板、断板 ≥5 日后又拉首板）→ 同上（2026-10-12 补·国芳案例）
#   · 环境差（昨收 + 今开双腿判据命中 ≥2/≥3 条）→ 名额压到 1 只 / 当日不主动买
# ⛔ 可逆：`POOL_CALL_DEFENSE_GATE=False` → 逐字回退 10-08 前的裁决行为。
POOL_CALL_DEFENSE_GATE = True
POOL_CALL_DEFENSE_DISTRIBUTION = True
POOL_CALL_DEFENSE_SECTOR_DRAIN = True
POOL_CALL_DEFENSE_POSITION = True
POOL_CALL_DEFENSE_OLD_DRAGON = True
POOL_CALL_DEFENSE_ENV = True
# 🧊 2026-10-27 用户定（原话「只观察，行情差没有必要强行接力」）：
#    **全市场最高板 + 封单极薄 → 只观察、不占名额**（能看不能买）。
#    与盘后 `pipeline_eod.demote_thin_seal_top` 同一判据、同一阈值（封单/流通 <2%，
#    复用 `defense_gate.DIST_SEAL_RATIO_MAX`）：盘后先把它从接力池降进观察池，
#    这里做第二道保险——防它被别的路径（旧数据/手工重排）又捡回买单队列。
#    `POOL_CALL_THIN_SEAL_TOP=False` 单独回退本闸（不影响其余防守判据）。
POOL_CALL_THIN_SEAL_TOP = True

# 🐉 2026-10-27 用户定（原话「推的票 必须是板块龙一」）：
#    **只有板块龙一能买**；龙二/跟风一律 watch（不占名额、不下买单）。
#    判据来源 = 盘后 `core.sector_leader` 回填的 `dragon_rank`（单一源），
#    ⛔ 本闸不自己重算强弱 —— 9:25 只认盘后定好的身位，防「盘后漏标 / 旧候选文件」漏过去。
#    与 ⑪ 后排闸的分工：⑪ 只在「前排还在板且买得到」时拦；本闸**无条件拦非龙一**
#    （前排买不到也不拿跟风顶替 —— 用户「行情差没必要强行接力」）。
#    `POOL_CALL_DRAGON_GATE=False` 单独回退本闸。
POOL_CALL_DRAGON_GATE = True

# ⑩e 四条件闸的裁决层拦截（2026-10-27 用户「一定要题材龙+情绪OK+量能+梯队…没有就空！！」）。
# 盘后 `apply_high_win_gate` 已把四条件不全的票移出接力池并打 `high_win_gate=[...]` 标；
# 这里二次拦截，⛔ 防「同日重跑 / 旧存档 / 人工塞票」绕回买入。
# `POOL_CALL_HIGH_WIN_GATE=False` 单独回退本闸。
POOL_CALL_HIGH_WIN_GATE = True

# ⑪b 🥇 身位闸（2026-11-02「高胜率优先」立项·默认关·等回放验收）
#   依据＝红开可买口径（0<gap≤5%，现行「不接低开」纪律下真正能下单的子集）按身位分组：
#       最高板  n=9  胜率 66.7% / 均 +1.83%
#       差1板   n=10 胜率 60.0% / 均 +0.14%
#       差≥2板  n=58 胜率 39.7% / 均 −1.37%
#   ⛔ 关键：**封板质量救不了后排**。差≥2板里封板强（≥3 因子）那批红开可买胜率只有
#      28.6% / 均 −2.69%（n=7）——封得好换来的高开被市场砍掉，能成交的本身就是弱样本。
#   ⛔ 与 ⑪ 后排闸分工：⑪ 管「有前排可买却买小弟」；本闸管「这条线连前排都没有，它自己就是后排」。
#   ⛔ 空池是合法结果（用户「没有就空」）。缺 `market_max_boards` → 不裁决（不猜）。
# ⚠️ 证据强度（务必如实看）：31 日真实链路回放里它只砍掉 **1 笔**（11-02 中电港 1 板
#    +0.05% 开 → −0.44%），日胜率 63.6%→70.0%、日均 +2.79%→+3.11%、中位 +2.01%→+3.40%。
#    **增量仅 1 天，不足以单独称「已验证」**；真正的证据基础是纸面 58 个后排可成交样本
#    （胜率 39.7%）。两处方向一致才开，单看回放不敢开。
POOL_CALL_LEADER_ONLY = True
LEADER_ONLY_MAX_GAP = 2        # 身位差 ≥ 此值判后排（0/1 板内仍可买）

# 🔄 2026-11-06 用户拍板 A：**分母改同题材 + 由硬闸改降权**
#   用户原话：「身位差我好像没有强制要求百分之百限制吧……你传媒板块你已经涨了五板龙头了，
#   但是光伏板块才一个板，这种身位差其实是没有影响的呀，它是走一个新的逻辑，
#   甚至说可以做一个资金的承接方向，你这种身位差估计只能在同一个板块里面适用，
#   不同板块之间它是没有关系的」
#   依据（35 日 / 717 真题材可买票次 / T+1）：旧闸零区分度且方向反（剔除批 −0.93%
#   反而优于保留批 −1.05%）；换同题材分母后前排 −0.68% vs 后排 −1.62%。
#   详见 `core.ai_caliber.position_weight` 顶部证据表与 `scripts/research_position_gap.py`。
#   ⛔ 默认只降 `position_cap`，不再把票剔出裁决（旧行为 = `POOL_CALL_POSITION_GAP_HARD=1`）。
POOL_CALL_POSITION_GAP_HARD = os.environ.get("POOL_CALL_POSITION_GAP_HARD", "0") == "1"
POOL_CALL_POSITION_WEIGHT_POSITION = (
    os.environ.get("POOL_CALL_POSITION_WEIGHT_POSITION", "1") != "0")

# ⑪c 🔮 预测档闸（2026-11-02 与选股卡共用同一模型 `core.relay_forecast`·默认关）
#   盘后选股卡算好 `forecast` 落进候选文件，本层只透传并在买入理由里带上「这档历史胜率」。
#   本闸只额外拦一类**历史崖位**：boards ≥ 6 且该档晋级率 < 25%（6进7 实测 12.5%、n=8）。
#   ⛔ 不拦「放弃」档 —— 那等于身位后排，⑪b 已经拦过，同一件事不判两次。
#   ⛔ 不拦「只观察」的非崖位情形 —— 那多半是「样本不足」，是不知道，不是不行。
#   ⛔ 缺 `forecast`（旧候选文件 / 模型样本缺失）→ 一律放行，不裁决。
# ⚠️ 默认关的理由：31 日回放里崖位日是稀有事件（6 板一年出现几次），
#    **现状样本不足以证明放开或收紧**，故只留代码 + 逐笔留痕，等真实触发 ≥3 次再复议。
POOL_CALL_FORECAST_GATE = False
FORECAST_CLIFF_BOARDS = 6
FORECAST_CLIFF_PROMO_MAX = 25.0

# 🌊 独苗高开闸（2026-11-04 用户「独苗高开可以立马设，适合跑路」）。
#   判据：自己竞价高开 ≥ LONE_GAP_MIN_PCT，而同**炒作题材**其余票红开率 < LONE_GAP_PEER_RED_MAX。
#   「独苗高开 + 同板块集体绿开」= 派发信号，不是强势信号。
#   依据见 call_pool ⑪d 注释（33 日实测：独苗组晋级 57.1%/胜率 28.6%，
#   其余 20.8%/43.7% —— 晋级率高 36pp、胜率低 15pp）。
#   ⛔ 只降观察、不占名额；缺题材/同块票<3/缺竞价 → 不裁决。
#   `POOL_CALL_LONE_GAP_GATE=False` 回退。
POOL_CALL_LONE_GAP_GATE = True
LONE_GAP_MIN_PCT = 3.0          # 自己高开幅度门槛（%）
# 同方向**其余**票红开率上限。
# ⚠️ 2026-11-04 实测把 0.30 放宽到 0.40：0.30 时样本 21 个、0.40 时 30 个，
#    晋级率 57.1%→60.0%、胜率 28.6%→33.3%（对照组恒为 43.7%）—— 信号同向且样本更大。
#    11-04 东方海洋所在「洋」簇红开 1/3=33%，按 0.30 会漏掉，按 0.40 才拦得住。
LONE_GAP_PEER_RED_MAX = 0.40
LONE_GAP_MIN_PEERS = 2          # 同方向其余票至少几只才裁决（不够=独苗本身，无参照）
# 名字炒作簇是否也参与「同方向」判定（⛔ 只认客观共字，见 core/name_cluster.py）
POOL_CALL_NAME_CLUSTER = True

# 🧊 冰点解冻（2026-10-28 用户「冰点日若开盘后情绪转好 可以出手」）。
#   盘后 `apply_high_win_gate` 把冰点日的票**冻结**（`frozen` + `unfreeze_on`）而非判死；
#   这里在 09:25 用 `core.market_open` 的竞价回暖读数决定是否放行。
#
#   ⛔ 只解冻「市场级条件」缺失（= 情绪）。缺失项里只要掺一个个股级硬伤
#      （题材龙 / 量能 / 梯队）就**不解冻** —— 集合竞价回暖救不了量能不足或身位不对的票。
POOL_CALL_ICE_UNFREEZE = True
# `high_win_gate` 里视为「市场级」的条件名，必须与
# `pipelines.pipeline_eod.HIGH_WIN_MARKET_LEVEL` 保持一致（此处不 import 管道层：
# core 不得反向依赖 pipelines）。
POOL_CALL_MARKET_LEVEL = ("情绪",)

# 🧊 回暖豁免名额：`stance=hibernate` 时名额为 0，`open_relief` 本已把它放宽到 1，
#   但紧随其后的环境防守腿 `level=block` 又会把名额压回 0 —— 回暖信号被自己的防守腿掐死
#   （10-08 实证：market_open_relief=True、defense 5 条命中 block → effective_quota=0）。
#   本开关让「竞价回暖」在 block 时仍保底 1 只。
#   `POOL_CALL_OPEN_RELIEF_OVERRIDE=False` 单独回退。
POOL_CALL_OPEN_RELIEF_OVERRIDE = True

# ── 🎯 高胜率优先三开关（2026-11-02 用户「打开！高胜率优先！可以自己迭代，有序优化」）──
# 依据：`scripts/relay_winrate.py`（31 个交易日 · 218 个**可执行**样本口径）。
# ⛔ 口径铁律：统计必须排除「高开>5%」（用户 5% 硬线，实际不成交）。混进去会把
#    「封板质量好」算成亏钱（那批 −2.64% 全是**买不到**的样本在记账），
#    得出「封板越差越好」的错误结论。三条开关全部可逆，改一个常量即可回退。
#
# ① 身位优先（唯一在验证段同向且为正的排序改动）
#   同一批样本、同样名额=2，只改排序：
#     板数优先（原）：训练段 72.2%/日均+1.93% → 验证段 46.2%/日均 −0.09%
#     身位优先（新）：训练段 83.3%/日均+3.15% → 验证段 61.5%/日均 +2.25%
#   单看「最高板」这一档本身：n=20 胜率 65.0%（下界 43.3%）、验证段 63.6%。
#   `POOL_CALL_TOP_BOARD_PRIORITY=False` 回退（排序立即恢复「地位→预案→板数」）。
POOL_CALL_TOP_BOARD_PRIORITY = True

# ② 低开例外（**只对最高板**，⛔ 绝不可推广）—— ⚠️ 默认**关闭**
#   纸面统计：「最高板 ∧ 低开>3%」n=7 胜率 71.4%/均 +5.02%（验证段 n=5 胜 60%/均 +3.94%）。
#   但真实链路回放（`scripts/pool_call_replay.py`·31 个交易日）只触发 **1 天**：
#   10-22 东安动力 4 板低开 −5.5% → 当日 −4.73%（旧口径当天不买）。
#   → 纸面 n=7 与实盘 n=1 冲突时，信实盘。⛔ 保持 False，等真实触发样本 ≥10 次再复议。
#   三道锁不变（只认最高板 / 半仓 / 不额外放号），开之前先改下面这个常量。
#   ⛔ 反例（永远不覆盖）：放宽到「身位差≥2板」的同类规则 n=45 全样本胜率 51.1%
#      但**验证段只有 29.2%** —— 典型过拟合。
POOL_CALL_LOW_OPEN_TOP_EXCEPTION = False
LOW_OPEN_TOP_GAP_MAX = -3.0        # 低开幅度上限（%）＝跌超 3% 才算
LOW_OPEN_TOP_POSITION = 0.5        # 该场景仓位上限（半仓试错）

# ③ 量能门软化：竞价量不足从「直接否决」降为「压仓位放行」—— ⚠️ 默认**关闭**
#   纸面统计：被量能门跳过的机会若照买平均 +0.53%/胜率 61.5%（n=13）。
#   但真实链路回放更硬：开它 → 4 天名单被改，日均 **2.79% → 1.30%**。最典型 10-21：
#   量能不足的 2 板科森科技被放进来，按板数顶掉 1 板南威软件（当日 +9.27%），换成 −2.04%。
#   → 「量不够」这个门在实盘是在救钱，不是在挡机会。⛔ 保持 False。
#   `POOL_CALL_VOLUME_SOFT=False` 即恢复「量不够即观察」（现行默认）。
POOL_CALL_VOLUME_SOFT = False
VOLUME_HARD_RATIO = 0.35           # 地板：低于计划需求的 35% 才算真没量
VOLUME_SOFT_POSITION = 0.5         # 量能不足时的仓位上限

# 🎯 11-05 用户删除「必须红开」闸后的**可选**低开收紧（默认关，不违背删除指令）。
#   反证：26 日 / 1001 票次里，删线后新放进的 gap≤0 票仅 6/130＝4.6% 晋级。
#   默认只留 `low_open_expected` 影子标记，不否决；逐日复核后若要收紧再置 1。
#   ⛔ 与 `POOL_CALL_LOW_OPEN_TOP_EXCEPTION`（最高板低开半仓试错）互不依赖。
POOL_CALL_LOW_OPEN_GUARD = os.environ.get("POOL_CALL_LOW_OPEN_GUARD", "0") == "1"
LOW_OPEN_GUARD_MAX_GAP = 0.0

# 🐾 低开买入口径（2026-11-11 用户「可以在低开的时候买」）。
#   放开的是**中间档**：`−6 < DEV < −2`（开得比它自己的质量预期低 2~6 个点）。
#   · 砸穿档 `DEV ≤ −6` 早已由 `POOL_CALL_DEEP_LOW_RESCUE` 放行，本开关与它不重叠；
#   · 低开但仍在自身质量预期内（DEV ≥ −2）本来就可买，本开关也不涉及。
#   ⚠️ 已知冲突：35 日 / 366 票次实测该档 **n=65 均 −0.92%、胜率 35%，是全表最差**；
#      11-11 局部复跑（n=10，其中 5 胜 5 负、均值 +2.40% 几乎全来自同一只票两天）不足以推翻。
#      ⇒ 只作**受控试验**：半仓、不额外占名额、量能/一字/题材/身位门全部照旧。
#   ⛔ 回退：`POOL_CALL_LOW_OPEN_BUY=0` → 逐字恢复 11-11 之前「中间档一律放弃」。
POOL_CALL_LOW_OPEN_BUY = os.environ.get("POOL_CALL_LOW_OPEN_BUY", "1") == "1"
LOW_OPEN_BUY_POSITION = 0.5        # 中间档低开的仓位上限（受控试验，不满仓）

MISSING = "—"


def bias_label(bias: Any) -> str:
    """预案档白话标签（卡面共用·禁机器黑话上卡）。"""
    return _BIAS_LABEL.get(str(bias or ""), "一般")


def quota_for(stance: Any) -> int:
    """按盘后攻守基调取当日可买只数上限。缺省/未知 → DEFAULT_QUOTA。"""
    s = str(stance or "").strip()
    return POOL_CALL_QUOTA.get(s, DEFAULT_QUOTA)


# ─────────────────────────────────────────────────────────────────────────────
# 线族归并（治「同一条线被拆成两个板块」——用户 2026-09-21 当场抓包：
# 闽东电力(电力) 与 锡华科技(风电设备) 是**同一条风电线**，一个 6 板一个是跟风补涨，
# 系统却把两者归到不同板块 → 池内比不出「核心 vs 跟风」）
# ─────────────────────────────────────────────────────────────────────────────
def _gene_score(code: str) -> float | None:
    """读股性基因分（`cache/zt_genes/gene_<code>.json`）；缺失返回 None（⛔ 不当 0）。"""
    try:
        p = ROOT.parent / "cache" / "zt_genes" / f"gene_{str(code).zfill(6)}.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        v = d.get("gene_score")
        return float(v) if isinstance(v, (int, float)) else None
    except Exception:  # noqa: BLE001
        return None


def _lk_with_theme(it: dict[str, Any], code_theme: dict[str, str] | None) -> str:
    """行 → 线键；涨停池行没有 core_theme 时用 `code_theme`（调用方给的 code→题材）补。

    ⛔ 2026-11-17 实证：昨日涨停池行只有 sector（消费电子），候选行有 core_theme
    （智能眼镜/MR头显）—— 两边算出来的线键不同，家数永远查不到（返回 None）。
    """
    if not str(it.get("core_theme") or "").strip() and code_theme:
        code = str(it.get("code") or it.get("dm") or "").zfill(6)
        t = code_theme.get(code)
        if t:
            it = {**it, "core_theme": t}
    return _line_key(it)


def _line_key(it: dict[str, Any]) -> str:
    """共振匹配用的「线键」：先取线族名，没有族就用基座名。

    ⚠️ 为什么不直接用 `line_family`：候选行带 `core_theme`（短剧/互动影游），
    涨停池行走 sector_support 归并（传媒）——两边名字对不上，共振永远匹配不到。
    2026-11-16 实测：横店影视 fam=短剧/互动影游，最强线算出来是「传媒」。
    """
    base = line_family(it)
    try:
        from common.theme_base import family_of
        return str(family_of(base) or base)
    except Exception:  # noqa: BLE001
        return base


def line_family(it: dict[str, Any]) -> str:
    """取个股所属**线族**（题材层·经 sector_support 归并表）。

    与孤军判定/带动性统计同口径（`sector_support._theme_key`），保证：
      · 池内排序看得到同线比分（核心优先）
      · 卡面标注的「同线还有 N 家」与孤军判定不打架
    失败兜底 = 原有的 theme_name / sector / theme / "其他"（诚实降级，不编造）。
    """
    try:
        from core import sector_support as ss
        return str(ss._theme_key(it) or "").strip() or MISSING
    except Exception:
        for k in ("theme_name", "sector", "theme", "hy"):
            v = str(it.get(k) or "").strip()
            if v:
                return v
        return "其他"


def _zt_dir(root: Path | None = None) -> Path:
    return (Path(root) if root else ROOT.parent) / "cache" / "zt_history"


def _read_zt(path: Path) -> dict[str, Any] | None:
    """只读有效完整收盘池；None 表示未知，显式空名单仍是有效证据。"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or d.get("intraday_only") not in (None, False):
            return None
        day = path.stem[3:]
        if datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d") != day:
            return None
        if d.get("date", day) != day:
            return None
        rows = d.get("zt_list")
        if not isinstance(rows, list):
            return None
        codes = set()
        for row in rows:
            if not isinstance(row, dict):
                return None
            code = str(row.get("code") or row.get("dm") or "").strip()
            if len(code) != 6 or not code.isascii() or not code.isdigit() or code in codes:
                return None
            codes.add(code)
            for key in ("boards", "lbc", "lianban"):
                if key in row:
                    value = row[key]
                    if (isinstance(value, bool)
                            or not (str(value).isdigit()
                                    or isinstance(value, float) and value.is_integer())
                            or int(value) < 1):
                        return None
        # 旧归档可以没有计数字段；显式空列表也是有效池，但声明的总数必须吻合。
        for key in ("zt_count", "tc"):
            if key in d:
                value = d[key]
                if isinstance(value, bool) or not str(value).isdigit() or int(value) != len(rows):
                    return None
        return d
    except Exception:
        return None


def _zt_rows(z: dict[str, Any]) -> list[dict[str, Any]]:
    rows = z.get("zt_list") or []
    return [r for r in rows if isinstance(r, dict)]


LINE_CORE_THEME_KEY_ENV = "ENABLE_LINE_CORE_THEME_KEY"



def load_breadth(date10: str, quotes: dict[str, Any], line_cores: dict | None = None,
                 *, root: Path | None = None) -> dict[str, dict]:
    """最近**完整收盘**归档 → 按「题材」算竞价广度，并挂到各线族名下（卡面说明用）。

    ⛔ 为什么按题材而不是按线族：线族是把题材再归并（机器人 29 只可能被拆成
       机器人/人形机器人几族），用户要看的是「机器人这条线 30 个票开在哪」。
       所以先用题材算广度，再用 `line_cores[fam].code` 反查它属于哪个题材，
       把该题材的广度挂到这条线下 —— 口径与用户口语一致。

    ⛔ 只读、失败返回 {}（卡面少一行，不影响裁决）。
    """
    if not POOL_CALL_BREADTH or not quotes:
        return {}
    d = _zt_dir(root)
    try:
        files = sorted(d.glob("zt_*.json"))
    except Exception:
        return {}
    rows: list[dict[str, Any]] | None = None
    day = ""
    for f in reversed(files):
        cand = f.stem[3:]
        # ⛔ 必须**严格早于执行日**：广度问的是「昨日涨停池今天开在哪」，
        #    回放/补算时当天归档已完整，若用 `<=` 会拿今天的池子算今天（前视）。
        if len(cand) != 10 or cand >= str(date10):
            continue
        z = _read_zt(f)
        if z is None:                       # 盘中档/半成品 → 跳过，继续往前找
            continue
        rows = _zt_rows(z)
        day = cand
        break
    if not rows or not day:
        return {}

    from core.auction_breadth import theme_breadth
    from core.theme_strength import theme_of_factory

    theme_of, _src = theme_of_factory(day)
    groups: dict[str, list[dict[str, Any]]] = {}
    theme_by_code: dict[str, str] = {}
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        th = theme_of(code, r)
        theme_by_code[code] = th
        groups.setdefault(th, []).append(r)

    def _b(row: dict[str, Any]) -> int:
        for k in ("boards", "lbc", "lianban"):
            v = row.get(k)
            if v not in (None, ""):
                try:
                    return int(v)
                except (TypeError, ValueError):
                    pass
        return 0

    theme_b: dict[str, dict] = {}
    for th, arr in groups.items():
        front = [str(x.get("code") or "") for x in sorted(
            arr, key=lambda x: (-_b(x), str(x.get("fbt") or "999999"),
                                str(x.get("code") or "")))[:5]]
        theme_b[th] = theme_breadth(arr, quotes, front)

    # 线族 → 它核心所属的题材 → 该题材的广度
    out: dict[str, dict] = {}
    for fam, lc in (line_cores or {}).items():
        if not isinstance(lc, dict):
            continue
        th = theme_by_code.get(str(lc.get("code") or ""))
        if th and th in theme_b:
            out[fam] = theme_b[th]
    return out


def line_core_theme_key_enabled() -> bool:
    """风向标（线锚）是否按**炒作题材**归组（默认开，`=0` 回退按申万行业）。

    2026-11-04 用户：「这样还是利于我们看盘和观察的，因为这样子我们知道这个风向标
    它到底是一个什么情况，基于这个风向标，就像我们上一次做新华传媒和新华文轩一样联动！」
    """
    return os.environ.get(LINE_CORE_THEME_KEY_ENV, "1") not in (
        "0", "false", "False", "")


def _theme_map_for(day: str, root: Path | None = None) -> dict[str, str]:
    """该交易日题材快照的 `{code: 炒作题材}`。缺失/损坏 → {}（诚实降级）。"""
    p = (Path(root) if root else ROOT.parent) / "cache" / "theme_snapshot" / f"theme_{day}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    mp = d.get("mapping") if isinstance(d, dict) else None
    if not isinstance(mp, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in mp.items():
        th = str(((v or {}) if isinstance(v, dict) else {}).get("theme") or "").strip()
        if th:
            out[str(k)] = th
    return out


def load_theme_momentum(day: str, root: Path | None = None) -> dict[str, dict[str, Any]]:
    """该交易日 09:25 可用的「题材动量」特征（`cache/theme_momentum/<day>.json`）。

    2026-11-10 用户：「短线接力…这个板块进行一个打分挺好」→ 热度分验证下来不单调
    （分越高反而越差），所以改打**变化**：家数环比 / 位置 / 连续天数。

    ⛔ 快照里的数据**严格早于 day**（由 `scripts/build_theme_momentum.py` 保证），
       把 day 自己写进去就是未来函数。
    ⛔ 不用 `theme_rotation.json`：它只有一份、覆盖写，回放会读到未来数据。
       底座是 `zt_history` + `theme_snapshot`（都按日归档，可回放）。
    缺失/损坏 → {}（诚实降级，不裁决）。
    """
    p = ((Path(root) if root else ROOT.parent) / "cache" / "theme_momentum"
         / f"{day}.json")
    try:
        d = json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    themes = d.get("themes") if isinstance(d, dict) else None
    if not isinstance(themes, dict):
        return {}
    return {str(k): v for k, v in themes.items() if isinstance(v, dict)}


def load_line_cores(date10: str, *, days: int = LINE_CORE_LOOKBACK_DAYS,
                    root: Path | None = None) -> dict[str, dict[str, Any]]:
    """近 N 个交易日涨停池 → {线族: 该线**最高板**那只}（含已断板的核心）。

    为什么需要它（2026-09-21 用户实证）：候选池＝涨停池派生 → **断板的核心进不了池**。
    闽东电力 09-16 六连板、09-17 断板、09-18 无涨停 → 不在 09-18 候选里；池内只剩
    09-15 才首板的跟风票锡华科技(4板)。没有这层回看，9:25 判得再准也会把跟风当核心。

    返回每项：
      {code, name, boards, last_date, last_boards, broken}
      · boards     = 窗口内出现过的**最高**板数
      · last_date  = 该票最近一次涨停日
      · broken     = True 表示「窗口内曾涨停、但最新交易日已不在涨停池」（＝断板）
    窗口内无任何涨停池文件 → {}（诚实降级，卡面不标注线核心）。
    """
    d = _zt_dir(root)
    if not d.exists():
        return {}
    try:
        files = sorted(d.glob("zt_*.json"))
    except Exception:
        return {}
    picked: list[tuple[str, dict[str, Any]]] = []
    for f in reversed(files):
        day = f.stem[3:]                                   # zt_2026-09-18 → 2026-09-18
        if len(day) != 10 or day > str(date10):
            continue
        z = _read_zt(f)
        if z is None:
            continue
        picked.append((day, z))
        if len(picked) >= max(1, int(days)):
            break
    if not picked:
        return {}
    newest_day = picked[0][0]

    def _boards(r: dict[str, Any]) -> int:
        for k in ("boards", "lbc", "lianban"):
            v = r.get(k)
            if isinstance(v, (int, float)):
                return int(v)
            if isinstance(v, str) and v.strip().isdigit():
                return int(v.strip())
        return 0

    # 逐票累积（先按 code 聚合，再按线族选核心）——⛔ 不能在线族层直接更新字段：
    # `broken` 必须看**核心里那只票自己**的最后出现日，否则会被同线其他票的近期涨停
    # 掩盖（2026-09-21 实测 bug：闽东电力 09-16 六板后 09-17 断板，却因 09-18 同线
    # 锡华在板而把线族的 last_date 刷成 09-18 → broken 误判 False）。
    per_code: dict[str, dict[str, Any]] = {}
    for day, z in picked:
        # 🧭 归组键：优先**炒作题材**（题材快照），⛔ 不再只用申万行业。
        #   11-03 实证：「短剧/互动影游」一条线 6 只票被行业拆成 游戏Ⅱ/出版/纺织制造/
        #   文娱用品 4 个键 → 候选（按题材归组）永远匹配不到自己的风向标，
        #   锚点机制等于空转；而拆出来的「传媒」锚点还是文投控股（炸10次/换手23%）
        #   这种最弱票。快照缺失的日期 → 退回 line_family（旧行为）。
        _tmap = _theme_map_for(day, root) if line_core_theme_key_enabled() else {}
        for r in _zt_rows(z):
            code = str(r.get("code") or r.get("dm") or "").strip()
            if len(code) != 6:
                continue
            _th = _tmap.get(code)
            fam = line_family({"core_theme": _th}) if _th else line_family(r)
            b = _boards(r)
            rec = per_code.get(code)
            if rec is None:
                per_code[code] = {
                    "code": code, "name": str(r.get("name") or r.get("mc") or ""),
                    "family": fam, "boards": b, "last_date": day,
                    "last_boards": b, "days": 1, "last_row": dict(r),
                }
                continue
            if b > int(rec["boards"]):                     # 窗口内最高板
                rec["boards"] = b
            if day > str(rec["last_date"]):                # 该票自己最近一次涨停
                rec["last_date"] = day
                rec["last_boards"] = b
                rec["family"] = fam
                rec["last_row"] = dict(r)                  # 最新交易日原始行（判强用）
            rec["days"] = int(rec.get("days") or 1) + 1

    out: dict[str, dict[str, Any]] = {}
    by_fam: dict[str, list[dict[str, Any]]] = {}
    for rec in per_code.values():
        by_fam.setdefault(str(rec.get("family") or ""), []).append(rec)

    from core import sector_leader as _sl
    for fam, recs in by_fam.items():
        # 🧭 返回两组语义，⛔ 不许混（2026-11-04 修）：
        #
        # ① **真核心**（`code`/`name`/`boards`/`broken`）——保持旧语义不变：
        #    窗口内出现过的最高板那只；它今日不在板 → `broken=True`。
        #    ⛔ 09-21 铁律仍成立：闽东电力 6 板断板后，不得因同线 4 板票在板而把 broken 刷掉。
        #
        # ② **风向标 `beacon`**（新增·给盘面联动用）——今日**仍在板**的最强那只：
        #    你要联动它，就得看还在推进的那只。典型：新华传媒断板后新华文轩接力；
        #    11-04 大消费 6 板青岛食品已断，但 4 板东方海洋仍强开 +4.68% → 风向标是东方海洋。
        #    ⛔ 整条线今日无人涨停 → beacon=None（没有可读的当日竞价，老实留空）。
        #
        # ③ **选核心不按「窗口内历史最高板」**（11-03 实证）：文投控股窗口内曾 3 板但已断波重启，
        #    11-03 只是 2 板（10:13 才封、炸 10 次、封单 0.20%）；同日大晟文化 09:25 一字封死
        #    封单 9.26%。旧逻辑按历史最高板选了文投 → 次日低开 −1.22% 判「这条线别碰」，
        #    真正核心开 +9.98%，整条线被误杀。⇒ 真核心也改为「在板者优先，其次才看历史」。
        alive = [x for x in recs if str(x.get("last_date")) == newest_day]
        hist_best = max(recs, key=lambda x: (int(x["boards"]), str(x["last_date"])))
        broken = (str(hist_best.get("last_date")) != newest_day
                  and int(hist_best["boards"]) > max(
                      (int(x["last_boards"]) for x in alive), default=0))

        if alive:
            rows = [x.get("last_row") or {} for x in alive]
            bands = _sl.time_bands(rows)
            best = min(alive, key=lambda x: _sl.leader_key(
                x.get("last_row") or {}, band=bands.get(str(x.get("code")), 999)))
            beacon = {
                "code": best["code"], "name": best["name"],
                "boards": best["last_boards"],
                "last_date": best["last_date"],
            }
        else:
            beacon = None
        # 真核心：今日在板者优先（选当日最强），整线无人涨停才回退历史最高板
        if alive and beacon:
            core = {"code": beacon["code"], "name": beacon["name"],
                    "boards": beacon["boards"],
                    "last_date": beacon["last_date"],
                    "last_boards": beacon["boards"]}
            # ⛔ 2026-11-11 用户当场质问：「昨天涨停，今天竞价刚开、没开一字，你就说人家断板！」
            #    根因：`broken` 是拿 `hist_best`（窗口内最高板，**可能是另一只票**）算的，
            #    而 `core` 取的是 alive 里的今日核心 —— 于是「名字是这只、断板是那只」。
            #    ⇒ 语义拆开，⛔ 不要混：
            #      · `broken`（旧语义，判据用）= 上一波更高的核心已断（闽东 6 板案例要它为 True）
            #      · `core_broken_self`（展示用）= **命名的这只自己**断没断 → 卡面只认这个
            core_broken = bool(broken)
            core_broken_self = bool(str(core.get("last_date")) != newest_day)
            prev_core_broken = bool(
                str(hist_best.get("code")) != str(core["code"])
                and str(hist_best.get("last_date")) != newest_day
                and int(hist_best["boards"]) > int(core["boards"]))
        else:
            core = {"code": hist_best["code"], "name": hist_best["name"],
                    "boards": hist_best["boards"],
                    "last_date": hist_best["last_date"],
                    "last_boards": hist_best["last_boards"]}
            core_broken = bool(broken)
            core_broken_self = True        # 整线今日无人涨停 → 命名的这只也不在板
            prev_core_broken = False
        core["broken"] = core_broken
        core["core_broken_self"] = core_broken_self
        core["prev_core_broken"] = prev_core_broken
        core["in_pool"] = bool(beacon)
        core["beacon"] = beacon
        # ⭐ 2026-11-10：带出锚点那行的**原始涨停数据**（fbt/seal/hs/boards）。
        #    锚点态度已改为按 DEV（相对它自己的质量预期）判 —— 没有这行就算不出预期，
        #    只能退回绝对开幅（旧口径），那正是「卡面说别碰、裁决却买」的根源。
        #    ⛔ 只多带一个字段，不改任何既有语义。
        core["quality_row"] = dict(
            ((best if alive and beacon else hist_best).get("last_row")) or {})
        out[fam] = core
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 池内裁决
# ─────────────────────────────────────────────────────────────────────────────
# 只分离核心身份与开盘买点；=0 完整回退原有隔壁/后排通道。
POOL_CALL_CORE_FIRST = os.environ.get("POOL_CALL_CORE_FIRST", "1") != "0"


def _core_identity_pool(pool, full_rows, code_theme):
    """复用昨日完整池的龙一单源；不用行业归亲，也不靠代码兜底猜核心。"""
    from core import sector_leader as sl

    def theme(row):
        code = sl.code_of(row)
        if isinstance(code_theme, dict):
            return sl.theme_of({"core_theme": code_theme.get(code)})
        return sl.theme_of(row)

    extras = {sl.code_of(c): c for c in pool}
    rows = []
    for raw in full_rows or []:
        row = dict(raw)
        for key, value in extras.get(sl.code_of(row), {}).items():
            if row.get(key) is None:
                row[key] = value
        th = theme(row)
        # 显式映射优先，清除可能与它冲突的旧题材字段。
        for key in ("theme_em_name", "core_theme", "theme_name"):
            row.pop(key, None)
        row["core_theme"] = th
        rows.append(row)
    result = sl.compute(rows)
    known = {}
    for board in result["boards"]:
        if not board["has_sector"]:
            continue
        group = [r for r in rows if sl.theme_of(r) == board["theme"]]
        bands = sl.time_bands(group)
        ordered = sorted(group, key=lambda r: sl.leader_key(
            r, band=bands.get(sl.code_of(r), 999)))
        # 完全同证据仅凭代码排第一不算核心证据，缺板数也不猜。
        if (any(not any(r.get(k) is not None for k in ("boards", "lbc", "lianban"))
                for r in group)
                or sl.leader_key(ordered[0], band=bands.get(sl.code_of(ordered[0]), 999))[:-1]
                == sl.leader_key(ordered[1], band=bands.get(sl.code_of(ordered[1]), 999))[:-1]):
            continue
        for row in group:
            known[sl.code_of(row)] = result["by_code"][sl.code_of(row)]
    out = []
    for original in pool:
        c = dict(original)
        c["core_identity_theme"] = theme(c)
        info = known.get(sl.code_of(c))
        if info and info["theme"] == c["core_identity_theme"]:
            c.update(dragon_rank=info["dragon_rank"], sector_has_mates=True,
                     dragon_leader_code=info["leader_code"],
                     dragon_leader_name=info["leader_name"],
                     dragon_leader_boards=info["leader_boards"],
                     dragon_leader_block=info["leader_block"],
                     core_identity_source="sector_leader_full_pool")
        elif c["core_identity_theme"] and c.get("dragon_leader_code"):
            leader = extras.get(str(c["dragon_leader_code"]))
            if leader is None or theme(leader) == c["core_identity_theme"]:
                c["core_identity_source"] = "sector_leader_candidate"
        out.append(c)
    return out


def _gap_of(q: dict[str, Any] | None) -> float | None:
    """竞价涨幅（%）。数据缺失/异常 → None（诚实·不编造）。"""
    if not isinstance(q, dict):
        return None
    try:
        ap = float(q.get("auction_price"))
        pc = float(q.get("prev_close"))
    except (TypeError, ValueError):
        return None
    if pc <= 0 or ap <= 0:
        return None
    return round((ap / pc - 1.0) * 100.0, 2)


def _amount_of(q: dict[str, Any] | None) -> float | None:
    if not isinstance(q, dict):
        return None
    try:
        return float(q.get("auction_amount_yi"))
    except (TypeError, ValueError):
        return None


def _dip_hint(q: dict[str, Any] | None, c: dict[str, Any], *, is_core: bool = False) -> str:
    """给「开太高不追」的票补一句**低吸提示**（2026-11-11 用户口径）。

    用户原话：「对于这种开得太高的票，我只想要有这样的一句话：如果它够值得买的话，
    我想要有『低吸』这一句话，我自己来决定低吸不低吸，**不需要你机械执行**。」

    ⛔ 只加文案，不改 view / 名额 / 仓位 / 账本 —— 系统**不替用户低吸**。
    ⛔ 只在「够值得」时出现：这条线的龙一（`dragon_rank==1`）、这条线核心、或板块龙。
       跟风票不提示，否则满屏都是「可低吸」，提示就贬值了。
    参考位：竞价价 × (1 − 3%)；没有竞价价就只给一句定性提示。
    """
    worth = (c.get("dragon_rank") == 1) or is_core or bool(c.get("is_sector_dragon"))
    if not worth:
        return ""
    ap = None
    try:
        ap = float((q or {}).get("auction_price"))
    except (TypeError, ValueError):
        ap = None
    if ap and ap > 0:
        # 🐉 2026-11-11 用户：「可以写一个留意光洋的买入机会」。⛔ 不要写成
        #    「今天不做/没机会」——那读起来像退出信号，实际是**等价格**。
        return (f"｜🐉 留意买入机会：高开不追，回踩到 {ap * 0.97:.2f}"
                "（今开 −3%）附近再看（买不买你定）")
    return "｜🐉 留意买入机会：高开不追，等回踩再看（买不买你定）"


def _bias_of(plan: dict | None, code: str) -> str:
    """该票的盘后预案档。缺省 / 缺 view 段 → normal（宽容·不否决）。"""
    if not isinstance(plan, dict):
        return BIAS_NORMAL
    v = (plan.get("views") or {}).get(code)
    if not isinstance(v, dict):
        return BIAS_NORMAL
    raw = str(v.get("bias") or "").strip()
    if raw in (BIAS_FOCUS, BIAS_NORMAL, BIAS_AVOID):
        return raw
    # 向后兼容：旧格式用 view 终态 → 映射成预案档（participate→focus / watch→normal / skip→avoid）
    return {"participate": BIAS_FOCUS, "watch": BIAS_NORMAL, "skip": BIAS_AVOID}.get(
        str(v.get("view") or "").strip(), BIAS_NORMAL)


def _candidate_int(it: dict[str, Any], key: str) -> int:
    try:
        return int(it.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _candidate_float(it: dict[str, Any], key: str) -> float | None:
    try:
        return float(it.get(key))
    except (TypeError, ValueError):
        return None


def _is_mech_leader(it: dict[str, Any]) -> bool:
    """是否为可参与的机械强龙。

    只认可区分的事实：接力池第1或09:30前封板；带动≥4家仅增强证据，
    不单独触发。四类硬伤仍优先，不把机械标签变成绕过风险闸的买入通行证。
    """
    if not isinstance(it, dict):
        return False
    if bool(it.get("is_one_word")) or bool(it.get("is_st")):
        return False
    turnover = _candidate_float(it, "cje_yi")
    if turnover is not None and 0 < turnover < MECH_LEADER_MIN_TURNOVER_YI:
        return False
    lone = (bool(it.get("sector_is_lone"))
            and not bool(it.get("theme_has_sector_effect"))
            and _candidate_int(it, "carry_cnt") == 0)
    if lone:
        return False
    rank1 = _candidate_int(it, "relay_rank") == 1
    fbt = "".join(ch for ch in str(it.get("fbt") or "") if ch.isdigit())
    second_board = len(fbt) >= 4 and int(fbt[:4]) <= MECH_LEADER_SECOND_BOARD_MAX_FBT
    return rank1 or second_board


def _mech_leader_note(it: dict[str, Any]) -> str:
    """机械强龙依据，供冻结结果与展示读取；不使用内部英文枚举。"""
    # ⛔ 只讲事实、不带系统术语（对外产品）：几点封的 / 带动几家
    parts: list[str] = []
    fbt = "".join(ch for ch in str(it.get("fbt") or "") if ch.isdigit())
    if len(fbt) >= 4 and int(fbt[:4]) <= MECH_LEADER_SECOND_BOARD_MAX_FBT:
        parts.append(f"{fbt[:2]}:{fbt[2:4]} 封板")
    carry = _candidate_int(it, "carry_cnt")
    if parts and carry >= MECH_LEADER_MIN_CARRY:
        parts.append(f"带动{carry}家")
    return "·".join(parts)


def _cycle_info() -> dict[str, Any]:
    """周期位置摘要（写进 meta 留痕用）。⛔ 只读、不参与任何裁决；失败返回 {}."""
    try:
        ph = _cp.phase_of()
        if not ph or ph.get("phase") in (None, "", _cp.PHASE_UNKNOWN):
            return {}
        return {"phase": ph.get("phase"), "label": ph.get("label"),
                "confidence": ph.get("confidence"), "date": ph.get("date"),
                "position": (ph.get("position") or {}).get("level"),
                "play": ph.get("play")}
    except Exception:
        return {}


def call_pool(candidates: dict[str, Any] | None,
              quotes: dict[str, dict[str, Any]] | None,
              *,
              plan: dict | None = None,
              stance: str | None = None,
              zt_rows: list[dict[str, Any]] | None = None,
             theme_rows: list[dict[str, Any]] | None = None,
              line_cores: dict[str, dict[str, Any]] | None = None,
              market_open: dict[str, Any] | None = None,
              sector_flow: dict[str, dict[str, Any]] | None = None,
             zt_series: dict[str, list[tuple[str, int]]] | None = None,
             zt_days: list[str] | None = None,
             relay_plan: dict | None = None,
             market_max_boards: int | None = None,
             theme_open_rows: list[dict[str, Any]] | None = None,
             prev_amounts: dict[str, float] | None = None,
             expected_gaps: dict[str, float] | None = None,
             sector_gap: dict[str, dict[str, Any]] | None = None,
             code_theme: dict[str, str] | None = None,
             theme_momentum: dict[str, dict[str, Any]] | None = None,
             advice: dict[str, Any] | None = None,
             catalyst: dict[str, Any] | None = None,
             enabled: bool | None = None) -> dict[str, Any]:
    """9:25 池内裁决 → 今天买哪只（**唯一判据源**）。

    入参：
      candidates = 盘后候选（`cache/candidates-<依据日>.json`）—— 只取 relay + watch
      quotes     = 当日 09:25 竞价快照 {code: {prev_close, auction_price, auction_amount_yi}}
      plan       = 盘后预案（`cache/ai_view_<执行日>.json`）—— 读 stance / views[code].bias
      stance     = 攻守基调（缺省从 plan 取）—— 决定当日可买只数上限
      line_cores = 同线最高板表（`load_line_cores`），缺省自动加载
      market_open = 昨日完整涨停池的09:25竞价读数；环境防守腿用它，空仓日也用它放宽一只名额
      sector_flow = 上一交易日的板块资金表（`defense_gate.load_sector_flow`）；缺省按
                    `candidates.date` 自动加载，缺失则方向闸**不裁决**（诚实降级）
      zt_series / zt_days = 逐票涨停序列 + 交易日历（`defense_gate.load_zt_series`）；二者
                    都缺省（None）才按 `candidates.date` 自动加载——位置闸用。
                    显式传任一（含空）即视为「调用方自带数据」，不再读盘（测试隔离用）
      market_max_boards = 市场最高板（昨日收盘口径·中位股闸的参照系）。
                    缺省 → 读 `candidates.environment.max_boards`，再缺 → 用 `zt_rows` 现算；
                    ⛔ 不从磁盘兜底读（防测试吃生产文件）。三者都缺 → 中位闸不裁决。
      enabled    = 覆盖开关（测试用）；None → 读模块常量 `POOL_CALL_ENABLED`
      prev_amounts = 昨日全天成交额 {6位代码: 亿元}（⛔ 09:25 前已存在的昨收归档，
                    不是当日盘中数据）。显式传 None → 从 `theme_open_rows` 的 cje_yi 取；
                    显式传 {} → 全部 unknown（测试隔离用）。竞价三态 A/B/C 的分母。
      expected_gaps = 逐票质量预期开幅 {6位代码: %}。显式传 None → 由
                    `core/expected_gap.expected_gap_pct(cand)` 从候选的昨日封板质量现算；
                    显式传 {} / 缺该票 → unknown 不裁决（测试隔离用）。
                    ⛔ 不是固定涨幅区间；`POOL_CALL_EXPECTED_GAP=0` 全部不裁决。
      sector_gap  = 09:25 三环联动**影子上下文** {6位代码: fields}
                    （`core/sector_gap_context.build_day_context` 由调用方注入，本函数保持纯函数）。
                    ⛔ 2026-11-05 用户拍板 A 档：只写进 base + meta 留痕，**不参与任何裁决**——
                       不改 zone / view / participate / quota / 仓位 / 账本 / 结算。
                    ⛔ `POOL_CALL_SECTOR_GAP_CONTEXT=0` → base/meta 一个字段都不写（零感知回退）。

    返回：
      {"enabled": bool, "stance", "quota", "views": {code: {...}}, "meta": {...}}
      views[code] = {code, name, view, reason, gap_pct, zone, bias, amount_yi, need_yi,
                     theme_name, boards, line_core, line_is_core, mechanical_leader,
                     mechanical_leader_note, quota_rank, defense_gate, defense_detail,
                     defense_warn, position_warn, seal_quality}
      view ∈ participate / watch / skip（与既有口径同一个枚举·卡面/账本共用）

    ⛔ 只读：不写账本、不改阈值；落盘由调用方（pipeline_verify）负责。
    """
    on = POOL_CALL_ENABLED if enabled is None else bool(enabled)
    if not on:
        return {"enabled": False, "stance": stance, "quota": None,
                "views": {}, "meta": {"reason": "POOL_CALL_DISABLED"}}

    # 🎯 竞价三态分母：昨日全天成交额。⛔ 只从调用方注入的昨收归档取，不读当日行情。
    #    显式 {} 视为「无数据」（测试隔离），只有 None 才从 theme_open_rows 兜底。
    _prev_amounts = (_ast.prev_amounts_from_rows(theme_open_rows)
                     if prev_amounts is None else dict(prev_amounts))
    plan = plan if isinstance(plan, dict) else {}
    if not POOL_CALL_EOD_PLAN:
        plan = {}                      # 🚫 盘后预案整体不消费（2026-11-06 用户拍板）
    _stance = str(stance or plan.get("stance") or "").strip() or None
    quota = quota_for(_stance)
    # 昨日空仓不是绝对封死：只有整批昨涨停股的 09:25 红开、连板红开、竞价量能
    # 同时转强，才放宽为 1 只。缺全市场冻结快照或任何一项不达标，保持原名额。
    open_read = market_open if isinstance(market_open, dict) else {}
    # `_open_warm` = 今晨竞价回暖这个**事实**本身（与 stance 无关）：
    #   昨涨停整体红开 ≥55% + 连板红开 ≥60% + 竞价量能不萎缩，三项同达标才为真。
    #   `open_relief` 只是它在 hibernate 下的名额用法（0 → 1）。
    _open_warm = bool(open_read.get("ready"))
    open_relief = bool(_stance == "hibernate" and _open_warm)
    effective_quota = 1 if open_relief else quota

    # ── 🛡️ 环境防守腿（defense_gate ①·2026-10-08 经验）──────────────────────
    # 昨收环境（candidates.environment＝盘后 feedback 快照）+ 今日开盘读数双腿判据：
    # 命中 ≥2 条 → 名额压到 1 只；≥3 条 → 当日不主动买（全置观察，名额让给逃生阀）。
    _env_date = str((candidates or {}).get("date") or "").strip()
    env_read: dict[str, Any] = {"level": "normal", "hits": [], "hit_count": 0, "notes": []}
    if POOL_CALL_DEFENSE_GATE and POOL_CALL_DEFENSE_ENV:
        try:
            env_read = _dg.env_verdict((candidates or {}).get("environment"), open_read,
                                       prev_date10=_env_date)
        except Exception as e:                      # ⛔ 防守腿失败不得打断裁决
            env_read = {"level": "normal", "hits": [], "hit_count": 0, "notes": [],
                        "error": str(e)}
        if env_read.get("level") == "block":
            # 🧊 回暖豁免（2026-10-28）：昨收环境差是**真结论**（block 不动），但今晨竞价
            #   已集体回暖 → 名额不归零，保底 1 只。⛔ 只覆盖到 1，不恢复原名额。
            #   10-08 实证：relief=True、defense 5 条命中 block → 名额被压回 0，回暖白读。
            if bool(POOL_CALL_OPEN_RELIEF_OVERRIDE and _open_warm):
                effective_quota = 1
                env_read = {**env_read, "relief_override": True,
                            "relief_note": ("环境命中 ≥3 条本应不主动买，"
                                            "但今晨竞价回暖 → 只放 1 只试错")}
            else:
                effective_quota = 0
        elif env_read.get("level") == "defensive":
            effective_quota = min(effective_quota, _dg.DEFENSIVE_QUOTA_CAP)

    # ── 🎯 早盘接力·动态仓位（2026-10-13 用户需求·relay_position 唯一判据源）──────
    # 在 stance 名额 + 环境防守闸之后，用「昨日强势 + 今晨竞价 + 题材沉淀 + 净值回撤」
    # 合成的动态方案**最终向下裁断名额**（行情转弱→名额收紧；回撤破硬线→禁买）。
    # ⛔ 只在允许买时收紧（取 min），绝不放大名额（不越权覆盖 stance / 防守闸）。
    # ⛔ 本函数不读盘：relay_plan 由调用方（pipeline_verify 真实 09:25 读数）注入——
    #    与 market_open / sector_flow / zt_series 同一约定，保证裁决纯函数、可离线复现。
    if relay_plan is not None:
        effective_quota = _rp.refine_effective_quota(effective_quota, relay_plan)

    # ── 🎯 名额目标下限 2（2026-11-04 用户「每日尽量推两个个股」）────────────────
    # ⛔ 位置讲究：必须在**名额最终确定之后**（stance → 环境防守腿 → 动态仓位 refine
    #    全部跑完）才放宽，否则会被后续的 refine 再压回 1；也必须早于下面按名额分配。
    # ⛔ 只放宽「名额」，不动任何单票准入闸门：放宽后仍要自己过 5% 硬线 / 量能 /
    #    预期区间 / 跟风 / 身位 / 龙一 / 薄封 / 四条件，过不了就如实只有 1 只。
    # ⛔ effective_quota == 0 绝不放宽（冰点 / 环境 block 无回暖 / 方案禁买 = 空仓）。
    _requested_quota = quota                       # 原 stance 名额（attack 3 / defend 1 …）
    _target_quota = (POOL_CALL_MIN_TWO_SLOTS_TARGET if POOL_CALL_STOCK_FIRST_SLOTS
                     else min(POOL_CALL_MIN_TWO_SLOTS_TARGET, _requested_quota))
    _min_two_applied = False
    if (POOL_CALL_MIN_TWO_SLOTS and effective_quota >= 1
            and _target_quota > effective_quota):
        effective_quota = _target_quota
        _min_two_applied = True

    cands: list[dict[str, Any]] = []
    # 🐣 低位池（2026-10-27）：与 relay/watch **同池裁决、共享名额**。
    #    ⛔ 板数天然最低 → 排在接力池之后，只有前排被防守闸拦掉/名额空出来才轮到；
    #    ⛔ 不动任何既有闸的门槛（后排闸照常拦「有前排可买的小弟」，这正是用户纪律）。
    for k in ("relay", "watch", "low_pool"):
        cands.extend([c for c in (candidates or {}).get(k) or [] if isinstance(c, dict)])
    _seen: set[str] = set()
    pool: list[dict[str, Any]] = []
    for c in cands:                                        # 去重（relay/watch 理论上不重叠）
        code = str(c.get("code") or "").strip()
        if len(code) == 6 and code not in _seen:
            _seen.add(code)
            pool.append(c)

    qs = quotes or {}
    if line_cores is None:
        _date10 = str((candidates or {}).get("date") or "").strip()
        try:
            line_cores = load_line_cores(_date10) if _date10 else {}
        except Exception:
            line_cores = {}
    line_cores = line_cores or {}

    # 方向资金表（上一交易日盘后归档；文件有缺口 → 缺就**不裁决**，不编造）
    if sector_flow is None:
        try:
            sector_flow = _dg.load_sector_flow(_env_date) if _env_date else {}
        except Exception:
            sector_flow = {}
    sector_flow = sector_flow if isinstance(sector_flow, dict) else {}
    _drain_on = bool(POOL_CALL_DEFENSE_GATE and POOL_CALL_DEFENSE_SECTOR_DRAIN
                     and sector_flow)

    # 涨停序列（位置闸/老龙复燃闸用·截到依据日 T-1；缺失/序列不足 → 不裁决，诚实降级）
    _zt_injected = not (zt_series is None and zt_days is None)
    if not _zt_injected:
        if POOL_CALL_DEFENSE_GATE and (POOL_CALL_DEFENSE_POSITION
                                       or POOL_CALL_DEFENSE_OLD_DRAGON):
            try:
                zt_series, zt_days = _dg.load_zt_series(upto=_env_date)
            except Exception:
                zt_series, zt_days = {}, []
    zt_series = zt_series if isinstance(zt_series, dict) else {}
    zt_days = list(zt_days) if isinstance(zt_days, (list, tuple)) else []
    _pos_on = bool(POOL_CALL_DEFENSE_GATE and POOL_CALL_DEFENSE_POSITION)
    _od_on = bool(POOL_CALL_DEFENSE_GATE and POOL_CALL_DEFENSE_OLD_DRAGON)

    # 🧬 近段连板基因（2026-11-10 用户裁定口径：近一个月有连板 ∧ 昨天涨停）
    #    ⛔ 与 `gene_height.prev_streak_boards`（上一波连板）不是一回事，见 gene_recent docstring。
    #    ⛔ 唯一用途 = 量能门复核票豁免；不进 zone / view / 名额 / 排序 / 仓位。
    _gene_recent: dict[str, dict[str, Any]] = {}
    if POOL_CALL_GENE_EXEMPT:
        # 序列来源：调用方注入（测试/回放）→ 原样用，保证隔离；
        #   未注入 → 单独补一份覆盖完整 31 自然日窗口的长序列（见常量注释）。
        _gene_src = zt_series
        _gene_days = zt_days
        if not _zt_injected:
            try:
                _gene_src, _gene_days = _dg.load_zt_series(
                    days=max(int(POOL_CALL_GENE_LOOKBACK_DAYS), 22),
                    upto=_env_date)
            except Exception:                     # 补序列失败 → 用共享那份，如实标 truncated
                _gene_src, _gene_days = zt_series, zt_days
        _gene_src = _gene_src if isinstance(_gene_src, dict) else {}
        # 数据源覆盖到哪天（⛔ 不是某只票的第一条涨停日）
        _gene_avail = None
        try:
            _days_sorted = sorted(str(d)[:10] for d in (_gene_days or []))
            _gene_avail = _days_sorted[0] if _days_sorted else None
        except Exception:
            _gene_avail = None
        if _gene_src:
            try:
                from core import gene_recent as _gr

                for c in pool:
                    _c0 = str(c.get("code") or "").strip()
                    if not _c0:
                        continue
                    _gene_recent[_c0] = _gr.recent_gene(
                        _gene_src.get(_c0), asof=_env_date,
                        avail_since=_gene_avail)
            except Exception:
                _gene_recent = {}

    if POOL_CALL_CORE_FIRST:
        pool = _core_identity_pool(pool, theme_open_rows, code_theme)

    # 🧬 基因换位预扫（2026-11-06）：同板数下「上一波更硬」的挑战者**顶替**原龙一。
    #   为什么需要顶替而不是只放行：⑩d 的语义是「只做龙一、跟风不追」。若 09:25 认定
    #   东方海洋才是这条线更强的那个，那皇台在**同一套判据下**就是跟风 —— 两个都买
    #   等于自相矛盾（11-06 实证：皇台 −10.66%、东方海洋 +8.17%，一对仓位对冲成 −1.25%）。
    #   ⛔ 只在「板数完全相同」时判（板数是第一序，⛔ 不许跨板数翻案）。
    #   ⛔ 缺序列/缺任一侧 → 不裁决（`gene_stronger` 自带缺数据保护）。
    #   可逆：`POOL_CALL_DRAGON_GENE_SWAP=0` → 只放行不顶替（两个都买）。
    _gene_swap: dict[str, dict[str, Any]] = {}
    if POOL_CALL_DRAGON_GENE and POOL_CALL_DRAGON_GENE_SWAP and zt_series:
        try:
            from core import gene_height as _gh0
            for c in pool:
                _lc0 = str(c.get("dragon_leader_code") or "")
                if not _lc0:
                    continue
                if POOL_CALL_CORE_FIRST and not c.get("core_identity_source"):
                    continue
                _b0, _lb0 = c.get("boards"), c.get("dragon_leader_boards")
                try:
                    _b0 = int(_b0) if _b0 is not None else None
                    _lb0 = int(_lb0) if _lb0 is not None else None
                except (TypeError, ValueError):
                    continue
                if _b0 is None or _lb0 is None or _b0 != _lb0:
                    continue
                _my0 = _gh0.prev_streak_of(zt_series, str(c.get("code") or ""))
                _th0 = _gh0.prev_streak_of(zt_series, _lc0)
                if not _gh0.gene_stronger(_my0, _th0):
                    continue
                _prev0 = _gene_swap.get(_lc0)
                if _prev0 is None or (int(_my0 or 0) > int(_prev0.get("mine_prev_streak") or 0)):
                    _gene_swap[_lc0] = {
                        "by": str(c.get("code") or ""),
                        "by_name": c.get("name"),
                        "mine_prev_streak": _my0,
                        "leader_prev_streak": _th0,
                        "boards": _b0,
                    }
        except Exception:                                # noqa: BLE001
            _gene_swap = {}

    # 同线板数排名（池内）：板数高的排前面（核心优先于跟风）
    _by_line: dict[str, list[dict[str, Any]]] = {}
    for c in pool:
        _by_line.setdefault(line_family(c), []).append(c)
    _line_rank: dict[str, int] = {}
    _line_max_boards: dict[str, int] = {}     # 本线池内最高板（身位判定的参照系之一）
    # 🧩 本线池内票数（板块打分的「规模」维）。⛔ 池内口径，不是全市场涨停池 ——
    #    全市场口径（`theme_open_rows`）只有生产链路传，回放拿不到，两套口径永远对不上。
    _line_count: dict[str, int] = {}
    # 🧩 本线 ≥2 板的票数（板块打分的「厚度」维）：只有一只高板 + 一堆首板＝独角戏，不是梯队。
    _line_depth: dict[str, int] = {}
    for fam, arr in _by_line.items():
        def _bd(x: dict[str, Any]) -> int:
            v = x.get("boards")
            return int(v) if v is not None and str(v).strip().isdigit() else 0
        _line_max_boards[fam] = max([_bd(x) for x in arr] or [0])
        _line_count[fam] = len(arr)
        _line_depth[fam] = sum(1 for x in arr if _bd(x) >= 2)
        for i, c in enumerate(sorted(arr, key=lambda x: -_bd(x))):
            _line_rank[str(c.get("code"))] = i + 1

    # ── 📏 市场高度（中位股闸的参照系）────────────────────────────────────
    # ⛔ 只认「传入的数据」：`market_max_boards` 显式参数 → `candidates.environment.max_boards`
    #    → `zt_rows`（调用方给的涨停池）→ 缺失则不裁决。
    #    ⚠️ 刻意**不从磁盘兜底读** `zt_history`：那会让测试偷偷吃到生产文件
    #    （2026-10-21 实测踩坑：test_defense_gate 5 例因读到真实 zt_2026-09-30.json 而失败），
    #    违反「显式传参即隔离」的既有约定。
    #    口径必须是**昨日收盘**的高度（中位股判的是昨天这只票在板位分布的哪个位置）。
    _market_max_boards: int | None = None
    try:
        if market_max_boards is not None:
            _market_max_boards = int(market_max_boards)
    except (TypeError, ValueError):
        _market_max_boards = None
    if _market_max_boards is None:
        # 🧊 2026-10-27：盘后 eod 落盘的当日最高板（`candidates.market_max_boards`）优先，
        #    与「最高板 + 极薄封单只观察」判据同源同值，⛔ 不让裁决层另算一套高度。
        try:
            _cmb = (candidates or {}).get("market_max_boards")
            if _cmb is not None:
                _market_max_boards = int(_cmb)
        except (TypeError, ValueError):
            _market_max_boards = None
    if _market_max_boards is None:
        _env_mb = (candidates or {}).get("environment") or {}
        try:
            if _env_mb.get("max_boards") is not None:
                _market_max_boards = int(_env_mb["max_boards"])
            elif _env_mb.get("now_max_boards") is not None:
                _market_max_boards = int(_env_mb["now_max_boards"])
        except (TypeError, ValueError, KeyError):
            _market_max_boards = None
    if _market_max_boards is None and zt_rows:
        _bs = []
        for _r in zt_rows:
            try:
                _bs.append(int(_r.get("boards") or _r.get("lbc") or 0))
            except (TypeError, ValueError, AttributeError):
                continue
        _market_max_boards = max(_bs) if _bs else None

    # ── 📏 同题材最高板（身位差的**新分母**·2026-11-06 用户 A）──────────────────
    # 用户：「你这种身位差估计只能在同一个板块里面适用，不同板块之间它是没有关系的」。
    # ⛔ 数据源只认 `theme_open_rows`（昨日**完整涨停池**）：
    #     · 用池内票算会被选股结果高估/低估（同 `sector_gap_context` 的既有告诫）；
    #     · 题材键统一 core_theme → theme → sector，与 `daily_screen._theme` 同序，
    #       保证盘后筛与 09:25 裁决用的是同一套分组；
    #     · 落行业兜底（sector）的**不算题材**：行业口径实测方向相反且无信息量
    #       （同一行业里板数天然一致 → 身位差恒 0），与「缺数据不裁决」一致。
    # 缺失 / 为空 → 映射空 → `position_weight` 返回 basis=unknown、权重 1.0（不裁决）。
    _theme_board_max: dict[str, int] = {}
    _code_theme_pos: dict[str, str] = {}
    # 🧩 同板块家数（板块打分的「规模」维）：这条线昨天有几只涨停。
    #    ⛔ 与 `_theme_board_max` 同源同口径（同一份 theme_open_rows + 同一个 code_theme 映射），
    #    不另起一套分组 —— 两套分组必然有一天打架。
    _theme_count: dict[str, int] = {}
    # ⛔ 题材源优先级：显式 `code_theme`（调用方用选股宝 plates 算好的**炒作题材**）
    #    > 行内 core_theme/theme > sector。**只要显式传入就严格只用它**：
    #    `full_zt_rows` 只带 sector（行业），混用会让分母变成「同行业最高板」，
    #    而行业口径实测无信息量（一个行业里板数天然一致 → 身位差恒 0）。
    _ct_strict = isinstance(code_theme, dict) and bool(code_theme)
    for _r in (theme_open_rows or []):
        if not isinstance(_r, dict):
            continue
        _cd = str(_r.get("code") or "").strip()
        if len(_cd) != 6:
            continue
        if _ct_strict:
            _th = str(code_theme.get(_cd) or "").strip()
            if not _th or _th in ("其他", "未分类", "—", "-", "未知"):
                continue
        else:
            _th = ""
            for _k in ("core_theme", "theme", "sector"):
                _v = str(_r.get(_k) or "").strip()
                if _v and _v not in ("其他", "未分类", "—", "-", "未知"):
                    _th = _v
                    break
            if not _th:
                continue
        try:
            _bd = int(_r.get("boards") or _r.get("lbc") or 0)
        except (TypeError, ValueError):
            continue
        if _bd <= 0:
            continue
        _code_theme_pos[_cd] = _th
        _theme_count[_th] = _theme_count.get(_th, 0) + 1
        if _bd > _theme_board_max.get(_th, 0):
            _theme_board_max[_th] = _bd

    # ── ⚓ 每条线的风向标（同线核心）今日态度 ──────────────────────────────
    # 只在「风向标今日仍在涨停池」时才算：已断板的核心今天不是涨停股，
    # 它今天怎么开说明不了这条线的强弱，硬当锚点会误杀（10-21 传媒线 新华传媒 已断板）。
    # ⛔ 2026-11-04：判据从 `broken` 改为 `in_pool` —— `broken` 现在表示「上一波更高的
    #    核心已断」（高度降级风险），与「今天有没有竞价可读」是两件事。混用会导致
    #    整条线读不到状态（大消费 6 板青岛食品断了，但 4 板东方海洋仍在推进，
    #    旧判据会把这条线整个跳过 → 风向标缺失）。
    _anchors: dict[str, dict[str, Any]] = {}
    if POOL_CALL_ANCHOR_GATE:
        for fam, lc in line_cores.items():
            if not isinstance(lc, dict) or not lc.get("code"):
                continue
            _in_pool = lc.get("in_pool")
            if _in_pool is None:            # 旧存档无该字段 → 退回 broken 口径
                _in_pool = not lc.get("broken")
            if not _in_pool:
                continue
            code = str(lc.get("code"))
            # ⭐ 2026-11-10 口径统一：锚点态度也按 **DEV = 实际开幅 − 它自己的质量预期** 判，
            #    与 `gap_zone` 同一套阈值。⛔ 不传预期就会退回绝对开幅（旧口径），
            #    那正是「卡面说别碰、裁决却买」的根源。
            _exp_anc = (expected_gaps.get(code) if isinstance(expected_gaps, dict) else None)
            if _exp_anc is None and _eg.enabled():
                _exp_anc = _eg.expected_gap_pct((lc or {}).get("quality_row") or lc)
            read = _ac2.anchor_read(qs.get(code), code=code,
                                    expected_range=(lc.get("expectation") or {}).get("expected_range"),
                                    expected_gap=_exp_anc)
            _anchors[fam] = {"code": code, "name": lc.get("name"),
                             "boards": lc.get("boards"), **read,
                             # ⭐ 2026-11-10 晚：动作文案随冻结件一起落盘（单一源 ATT_ACTION），
                             #    卡面/API 都读它，⛔ 不在各自那一层重写一套结论。
                             "action": _ac2.attitude_action(read.get("attitude"))}

    # 🌊 独苗高开预计算（2026-11-04）：按炒作题材统计「昨日同板块涨停票今早红开率」。
    #    供 ⑪d 判「自己高开、同方向其余票集体绿开」。
    #
    #    ⛔ 分母必须是**昨日完整涨停池**（`theme_open_rows`），不是当日候选池：
    #       池内只留 relay+watch，11-04 大消费仅 3 只、红开 2 只=67%，
    #       而全市场同族 12 只里 10 只绿开（跌停开盘）→ 结论完全相反。
    #       这正是 `market_open` 注释里那条：「不能只用已筛选的候选池，
    #       否则红开比例会被选股结果高估」。
    #    ⛔ 题材优先 `core_theme`，退 `sector`；两者都空 → 该票不进分组（不猜）。
    #    ⛔ 缺竞价（quotes 里没有）的票不进分母 —— 没观测到不等于绿开。
    #    ⛔ **必须用独立入参 `theme_open_rows`，不能复用 `zt_rows`**：
    #       `zt_rows` 还承担「市场最高板」兜底计算，一旦传入会改变身位闸的参照系
    #       ——这正是代码里明确警告过的坑（「刻意不从磁盘兜底读 zt_history，
    #       那会让测试偷偷吃到生产文件」，2026-10-21 踩过一次）。
    #       11-04 我又踩了一次：`test_pool_call` 3 例因真实 zt_2026-09-14.json 被读入而红。
    _theme_gaps: dict[str, list[tuple[str, float]]] = {}
    _code_theme: dict[str, str] = {}
    if POOL_CALL_LONE_GAP_GATE:
        for _r in (theme_open_rows or []):
            _cd = str(_r.get("code") or "").strip()
            if not _cd:
                continue
            _th = str(_r.get("core_theme") or "").strip()
            if not _th or _th in ("其他", "—", "-", "未知"):
                _th = str(_r.get("sector") or "").strip()
            if _th and _th not in ("其他", "—", "-", "未知"):
                _code_theme[_cd] = _th
        # 候选池里的票也要有题材（theme_open_rows 可能缺当日新票）
        for _c in pool:
            _cd = str(_c.get("code") or "").strip()
            _th = line_family(_c)
            if _cd and _th:
                _code_theme.setdefault(_cd, _th)
        for _cd, _th in _code_theme.items():
            _g = _gap_of(qs.get(_cd))
            if _g is None:
                continue
            _theme_gaps.setdefault(_th, []).append((_cd, float(_g)))
    _lone_peer_red_ratio: dict[str, tuple[int, int]] = {}
    for _f, _lst in _theme_gaps.items():
        if len(_lst) - 1 < LONE_GAP_MIN_PEERS:      # 其余票不够 → 没有参照，不裁决
            continue
        # 存 (总数, 红开数)；判断时由 ⑪d 排除**自己**再算其余票红开率
        _lone_peer_red_ratio[_f] = (len(_lst), sum(1 for _cd, _g in _lst if _g > 0))

    # 🏷️ 名字炒作簇（2026-11-04 用户「名字炒作维度可以做」）：
    #    实证明天「羊字辈」跨 6 个申万行业，**题材分组完全看不到它们是一伙的**
    #    （东方海洋 sector=农产品加、同组仅自己）。故把「同字簇」也算一种同方向。
    #    ⛔ 只用客观共字，不做谐音（见 `core/name_cluster.py` 边界说明）。
    #    ⛔ 与题材族并列，取**最差**那一族判 —— 名字族先崩本身就是信号。
    _lone_cluster_ratio: dict[str, list[tuple[str, float]]] = {}
    if POOL_CALL_LONE_GAP_GATE and POOL_CALL_NAME_CLUSTER:
        try:
            from core import name_cluster as _nc
            _cl = _nc.build_clusters(theme_open_rows or [])
            for _ch, _codes in _cl.items():
                _lst = []
                for _cd in _codes:
                    _g = _gap_of(qs.get(_cd))
                    if _g is not None:
                        _lst.append((_cd, float(_g)))
                if len(_lst) - 1 >= LONE_GAP_MIN_PEERS:
                    _lone_cluster_ratio[_ch] = _lst
        except Exception:                            # ⛔ 集群识别失败不得打断裁决
            _lone_cluster_ratio = {}

    # ── 逐票裁决 ──────────────────────────────────────────────────────────
    marks: dict[str, dict[str, Any]] = {}
    entries: list[dict[str, Any]] = []
    # 竞价三态硬闸的影子对照：被「只买 A」挡下、但按旧链本来会参与的票。
    _filtered_by_state: list[str] = []
    # 🎯 11-05 用户拍板 A 档：三环联动**只留痕不裁决**。开关关或调用方没注入 → 零字段。
    #    三条硬约束（缺 plates / 缺任一环 / 量能只证伪）已在 sector_gap_context 内实现，
    #    这里只搬运字段，⛔ 不读盘、不重算、不改任何既有裁决变量。
    _sgc_on = bool(_sgc.enabled() and isinstance(sector_gap, dict) and sector_gap)
    _sgc_ctx: dict[str, dict[str, Any]] = sector_gap if _sgc_on else {}

    # 🧮 当日整体开盘偏移（见模块顶部 `POOL_CALL_DEV_ENV_OFFSET` 段）：池内中位开幅
    #    明显为负时，把它当作「今天的基准」从每只票的 DEV 里扣掉。
    _dev_env_offset = 0.0
    _dev_env_info: dict[str, Any] = {"enabled": bool(POOL_CALL_DEV_ENV_OFFSET),
                                     "offset": 0.0}
    if POOL_CALL_DEV_ENV_OFFSET:
        _pool_gaps = sorted(
            g for g in (_gap_of(qs.get(str(c.get("code") or "").strip())) for c in pool)
            if g is not None
        )
        if len(_pool_gaps) >= DEV_ENV_MIN_SAMPLES:
            _n = len(_pool_gaps)
            _med = (_pool_gaps[_n // 2] if _n % 2
                    else (_pool_gaps[_n // 2 - 1] + _pool_gaps[_n // 2]) / 2.0)
            _dev_env_info.update({"pool_n": _n, "median_gap": round(_med, 2),
                                  "trigger": DEV_ENV_TRIGGER_PCT})
            if _med <= DEV_ENV_TRIGGER_PCT:
                _dev_env_offset = max(float(_med), -DEV_ENV_MAX_OFFSET_PCT)
                _dev_env_info["applied"] = True
        _dev_env_info["offset"] = round(_dev_env_offset, 2)

    # 每条线核心与二排的竞价幅度对照（供「核心转弱→允许二排」判定）
    _gap_by_code: dict[str, float] = {}
    # 🧲 2026-11-16 用户「题材强度必须用到昨日涨停的板块个股」：
    #    报价里其实**已经包含**昨日完整涨停池（09:25 抓取时 full_zt_codes 并进去了），
    #    原实现只遍历候选池 → 首板（如凯撒文化 +10% 一字）被漏掉，方向证据因此看不见。
    #    这里改成遍历全部报价，候选池只作为补充来源。
    for _code, _q in (qs or {}).items():
        _g = _gap_of(_q)
        if _code and isinstance(_g, (int, float)):
            _gap_by_code[str(_code)] = float(_g)
    for _c in pool:
        _code = str(_c.get("code") or "").strip()
        if _code and _code not in _gap_by_code:
            _g = _gap_of(qs.get(_code))
            if isinstance(_g, (int, float)):
                _gap_by_code[_code] = float(_g)

    # 🧵 昨日各线涨停家数（判断「成线」用）
    # ⛔ 2026-11-17 事故：live 路径只传 `theme_open_rows`、不传 `zt_rows`（怕改身位参照系），
    #    结果 `_theme_mates` 为空 → 「弱线低开不救」与「题材共振」**全部静默失效**，
    #    当天把「文投控股 −9.89% 开盘」当错杀档买入。
    #    现在单独接受 `theme_rows`（只喂题材/共振，不参与身位闸），口径与 zt_rows 相同。
    _theme_src = zt_rows if zt_rows else theme_rows
    # 昨涨停池行没有题材时，优先用**候选行自己的 core_theme**补（比 code_theme 更全：
    # 11-17 实证 002861 的题材是从题材库兜底补的，快照里没有，code_theme 查不到）。
    _ct_eff: dict[str, str] = dict(code_theme or {})
    for _pc_ in pool:
        _cc = str(_pc_.get("code") or "").zfill(6)
        _tt = str(_pc_.get("core_theme") or _pc_.get("theme_name") or "").strip()
        if _cc and _tt:
            _ct_eff[_cc] = _tt
    _theme_mates: dict[str, int] = {}
    for _r in (_theme_src or []):
        _k = line_family(_r)
        if _k:
            _theme_mates[_k] = _theme_mates.get(_k, 0) + 1
    # 昨日「最强线」集合 = 家数前 N 档（同家数算同档，避免 3/3/3 一刀切切歪）
    _top_line_counts = sorted(set(_theme_mates.values()), reverse=True)[:DEEP_LOW_TOP_LINES] if DEEP_LOW_TOP_LINES > 0 else []
    _top_lines = {k for k, v in _theme_mates.items() if v in _top_line_counts}
    # 族键版本的「最强档」集合（与 _line_key 同口径，供弱线闸使用）
    _mates_key: dict[str, int] = {}
    for _r0 in (_theme_src or []):
        _k0 = _lk_with_theme(_r0, _ct_eff)
        if _k0:
            _mates_key[_k0] = _mates_key.get(_k0, 0) + 1
    _top_counts_key = sorted(set(_mates_key.values()), reverse=True)[:DEEP_LOW_TOP_LINES] if DEEP_LOW_TOP_LINES > 0 else []
    _top_lines_key = {k for k, v in _mates_key.items() if v in _top_counts_key}

    # 🧲 逐线共振分（2026-11-16 全局统一）：口径见 `core/sector_resonance.py`
    #    统计 = 昨日完整涨停池（含首板）× 今日 09:25 报价；⛔ 不用盘中数据。
    try:
        from core import sector_resonance as _sr
        _line_resonance, _strongest_line = _sr.compute(
            (_theme_src or pool), qs, _line_key,
            strong_gap=RESONANCE_STRONG_GAP, min_mates=3,
        )
    except Exception:  # noqa: BLE001 — 共振失败不阻断裁决，按「无共振」处理
        _line_resonance, _strongest_line = {}, None

    for c in pool:
        code = str(c.get("code") or "").strip()
        name = str(c.get("name") or "")
        q = qs.get(code)
        gap = _gap_of(q)
        exp = (c.get("expectation") or {}).get("expected_range")
        # zone 在逐票质量预期算好后统一重算（见下方「竞价是否符合预期」段），此处占位。
        zone = "unknown"
        bias = _bias_of(plan, code)
        need = (c.get("expectation") or {}).get("auction_amount_min_yi")
        amt = _amount_of(q)
        fam = line_family(c)
        lc = line_cores.get(fam) or {}
        is_core = bool(lc) and str(lc.get("code") or "") == code
        boards = c.get("boards")
        try:
            boards_i = int(boards) if boards is not None and str(boards).strip().isdigit() else 0
        except Exception:
            boards_i = 0

        from core.leader_cycle import role_of
        mechanical_leader = _is_mech_leader(c)
        mechanical_leader_note = _mech_leader_note(c) if mechanical_leader else ""
        base = {
            "code": code, "name": name, "gap_pct": gap, "zone": zone, "bias": bias,
            "amount_yi": amt, "need_yi": need if isinstance(need, (int, float)) else None,
            "theme_name": fam, "boards": boards, "line_is_core": is_core,
            # ⛔ 20cm 标志必须随冻结件透传：decide 的物理封死线靠它取 20% 而非 10%。
            "is_20cm": bool(c.get("is_20cm")),
            # ⛔ 2026-10-21 22:45：补 `last_date`（核心最后涨停日）—— 卡面必须能说清
            #    「断了多久」。断 1 天（昨天刚断）和断 5 天（早就凉了）是两件事。
            "line_core": ({"code": lc.get("code"), "name": lc.get("name"),
                           "boards": lc.get("boards"), "broken": bool(lc.get("broken")),
                           "last_date": lc.get("last_date")}
                          if lc and not is_core else None),
            "line_rank": _line_rank.get(code),
            "line_max_boards": _line_max_boards.get(fam),
            # 🎯 身位（2026-11-02）：距全市场最高板还差几板。0=就是最高板。
            #    缺失（market_max_boards 拿不到 / 本票板数未知）→ None，⛔ 不猜、不当最高板。
            "gap_to_top": ((int(_market_max_boards) - boards_i)
                           if _market_max_boards and boards_i else None),
            "market_max_boards": int(_market_max_boards) if _market_max_boards else None,
            # 🧬 近段连板基因（2026-11-10 用户口径）：窗口内最高连板 + 涨停次数 + 最近一次。
            #    只作事实透传与量能豁免依据，⛔ 不参与 zone / view / 名额 / 排序。
            #    缺序列 → None（"不知道"），⛔ 不当成"弱基因"。
            "recent_gene": _gene_recent.get(code),
            # 只读候选事实，在本函数末端的名额排序消费；不改变任何竞价/风险短路规则。
            "mechanical_leader": mechanical_leader,
            "mechanical_leader_note": mechanical_leader_note or None,
            "leader_role": role_of(c),
            "leader_evidence": c.get("leader_evidence") or {},
            # 🐉 龙头身份（2026-10-21 P2）：由 leader_cycle 认定好后透传，卡面只读标签。
            #    缺失（旧候选文件）→ None，卡面不显示标签（⛔ 不现场重算，防两处口径打架）。
            "dragon_confirm": c.get("dragon_confirm") if isinstance(c.get("dragon_confirm"), dict) else None,
            # 🔍 池外龙头校验（2026-10-21）：盘后建池的判定，一路透传到卡面。
            #    ⛔ 缺就 None（旧候选文件）→ 卡面 `_outpool_badge` 不标，不猜。
            "line_tier_outpool": c.get("line_tier_outpool"),
            "line_tier_why": c.get("line_tier_why"),
            "line_top": c.get("line_top"),
            "line_tops": c.get("line_tops"),
            "carry_cnt": c.get("carry_cnt"),
            "relay_stage": c.get("relay_stage"),
            "relay_mood": c.get("relay_mood"),
            "n_plus_n": c.get("n_plus_n"),
            # 板块联动事实只透传；判定放在量能后、锚点前，保持“先确认本票可交易，
            # 再判昨日结构 × 今早同方向承接”的顺序。
            "linkage_facts": c.get("linkage_facts"),
            "linkage_discipline": c.get("linkage_discipline"),
        }
        # 🧩 板块打分（第一版·粗算）—— 2026-11-10 用户
        #    「短线接力的时候，每一个个股其实也是有板块的，这个板块进行一个打分挺好」
        #
        #    四维等权 0~100：规模（本线池内几只）/ 高度（本线最高板）/ 卡位（本票身位）/
        #    承接（今早同板块红开率）。规则与阈值全在 `core/theme_score.py`（单一源）。
        #
        #    ⚠️ 为什么在这里就算：用户在**任一闸之前**都会看到这只票，先给每个票一个分。
        #      走到⑪b之后会用当日真实身位 + 竞价承接**覆盖**成精算版（见 ⑪d 之前那段）。
        #    ⛔ 只写字段：不改 view / position_cap / 名额 / 任何闸。
        if POOL_CALL_THEME_SCORE:
            try:
                _ssp0 = c.get("sector_support")
                _ssp0 = _ssp0 if isinstance(_ssp0, dict) else {}
                base["theme_score"] = _ts.score(
                    theme_zt_count=_line_count.get(fam),
                    theme_max_boards=_line_max_boards.get(fam),
                    theme_depth=_line_depth.get(fam),
                    red_open=_ssp0.get("red_open"),
                    mates_quoted=_ssp0.get("mates_quoted"),
                    theme_name=str(fam or ""),
                )
            except Exception:                    # noqa: BLE001 —— 打分故障不许阻断裁决
                pass

        # 🌊 题材动量（变化，不是水平）—— 同样只留痕
        #    ⛔ 与 `theme_score`（热度）并存但互不依赖：一个描述「多热」，一个描述「在变热还是变冷」。
        #       等分档验证出来再决定留哪个、要不要进权重。
        #    ⛔ 必须由调用方**显式传入**（`load_theme_momentum(买入日)`）：
        #       本函数不知道「今天是哪天」——之前误用了一个不存在的 `day` 变量，
        #       被 try/except 吞成 None，25 只票全无动量却看不出来。
        if POOL_CALL_THEME_MOMENTUM:
            try:
                _mv = (theme_momentum or {}).get(str(fam or "").strip())
                base["theme_momentum"] = _mv if isinstance(_mv, dict) else None
                # 🎯 板块强度分：**只由全市场同题材涨停家数决定**（`theme_score.strength`）。
                #    这是四维热度分验证失败后，唯一通过「去日期效应」分档检验的一维：
                #    n1~2 超额 −0.88% / n3~5 −0.65% / n≥6 **+0.69%**（单调递增）。
                #    ⛔ 同样只写字段，不改裁决。
                base["theme_strength"] = _ts.strength(
                    (_mv or {}).get("n") if isinstance(_mv, dict) else None,
                    theme_name=str(fam or ""))
            except Exception:                    # noqa: BLE001 —— 故障不许阻断裁决
                base["theme_momentum"] = None
                base["theme_strength"] = None

        # 🎯 11-05 A 档影子留痕：三环联动字段在**所有短路之前**写入 base，
        #    被任一闸拦下的票也留痕，便于逐日对照「高开凭什么」。
        #    ⛔ 字段白名单不含任何裁决字段；缺上下文 / 开关关 → 一个字段都不写。
        if _sgc_on:
            _lnk = _sgc_ctx.get(code)
            if isinstance(_lnk, dict):
                for _k in _sgc.FIELDS:
                    if _k in _lnk:
                        base[_k] = _lnk[_k]
        # 🎯 2026-11-05 用户「把 5% 和红开这两条线删掉」→ **竞价是否符合预期的唯一判据**：
        #    DEV = 实际开幅 − 逐票质量预期（昨日封板时间/封单/量能/炸板/板位算出），
        #    由 `ai_caliber.gap_zone` 单点裁决，账本/卡面/池内共用。
        #    ⛔ 不再看「开在百分之几」；强票可高开、弱票可低开，只要不明显低于它自己的预期。
        #    ⛔ 逐票预期独立于三态标注开关：POOL_CALL_AUCTION_STATE=0 时判据仍生效；
        #       `POOL_CALL_EXPECTED_GAP=0` 或 `AI_EXPECTED_GAP_MODE=absolute` 才回退。
        _exp = None
        if _eg.enabled():
            _exp = (expected_gaps.get(code) if isinstance(expected_gaps, dict)
                    else _eg.expected_gap_pct(c))
        zone = (_ac.gap_zone(gap, exp, expected_gap_pct=_exp,
                             is_20cm=bool(c.get("is_20cm")),
                             env_offset=_dev_env_offset)
                if gap is not None else "unknown")
        base["zone"] = zone
        # 🧮 接力终评分（2026-11-18 用户「按已有数据迭代」）：
        #    盘后基础分 × 竞价系数（0.6~1.0，只降不升）→ 0~100 + A/B/C/D 档。
        #    ⛔ 只写字段、供卡面与复盘排序参考；**不改变本函数既有的任何否决/名额逻辑**
        #      （要不要改用它排序，是另一个开关的事，先看回放）。
        try:
            from core import relay_score as _rs
            # ⛔ 复用既有五维评分（`score_relay_candidate`），只在它外面乘一个竞价系数。
            #    不新增指标体系：字段仍是 score/band/parts，另加 auction 明细。
            _sc = _rs.score_relay_after_auction(
                c,
                {"regime": str((c.get("env") or {}).get("regime") or "")} if isinstance(c.get("env"), dict) else {},
                gap=gap, expected_gap=_exp,
                amt_yi=(q.get("auction_amount_yi") if isinstance(q, dict) else None),
                need_yi=need,
            )
            base["relay_score"] = _sc
        except Exception as _e_sc:  # noqa: BLE001
            base["relay_score_error"] = str(_e_sc)
        if _dev_env_offset:
            # 留痕：本票的 DEV 是被扣过环境偏移的（卡面/复盘要看得出这一天整体低开）
            base["dev_env_offset"] = round(_dev_env_offset, 2)
        # ⛔ `AI_EXPECTED_GAP_MODE=absolute` 一键回退：逐字回到 11-05 之前的字段结构，
        #    ⛔ 不写逐票预期字段、不留低开标记（旧测试/旧消费方零感知）。
        if _ac.relative_mode():
            base["expected_gap_pct"] = _exp
            if isinstance(gap, (int, float)) and _exp is not None:
                base["gap_vs_expected"] = round(float(gap) - float(_exp), 2)
            # ⚠️ 低开反证留痕：用户判断「弱票低开一样能拉起来」，历史样本仅 6/130=4.6%。
            #    默认 ⛔ 不否决，只打标供逐日观察；要收紧须显式开 POOL_CALL_LOW_OPEN_GUARD。
            if isinstance(gap, (int, float)) and float(gap) <= 0:
                base["low_open_expected"] = True
            # 🎯 11-05 21:45 用户「高开 8.9% 无限接近涨停，大于 6、7 个点要非常小心，
            #    降低它的买入权重」。**连续降权、不是硬闸**：`gap_zone(DEV)` 仍是唯一准入
            #    判据，这里只决定「真买的话买多重」，⛔ 不改 zone / view / participate / quota。
            #    与影子字段同样放在**所有短路之前**，被别的闸拦下的票也留痕。
            #    ⛔ 唯一实现在 ai_caliber.gap_weight；`AI_GAP_WEIGHT=0` 时连字段都不写。
            if _ac.AI_GAP_WEIGHT:
                _gw = _ac.gap_weight(gap, expected_gap_pct=_exp,
                                     is_20cm=bool(c.get("is_20cm")))
                base["gap_weight"] = _gw["weight"]
                base["gap_weight_active"] = _gw["active"]
                if _gw["active"]:
                    base["gap_weight_note"] = _gw["label"]
        # 🎯 竞价三态（A 系统直买 / B 人工二次 / C 排除 / unknown 不裁决）：只作标注与
        #    名额排序，⛔ 不决定 zone。max_gap_pct 已改传**涨停幅度**（物理买不进线），
        #    ⛔ 不再是 5% 硬线。
        if _ast.enabled():
            base.update(_ast.classify(
                gap_pct=gap, amount_yi=amt,
                prev_amount_yi=_prev_amounts.get(code),
                expected_gap_pct=_exp,
                max_gap_pct=_ac.limit_up_pct(bool(c.get("is_20cm")))))
        # 封板质量读数（只读·审计/卡面用）：**在短路链之前**算出，被前几道闸拦下的票也留痕
        if POOL_CALL_DEFENSE_GATE:
            try:
                base["seal_quality"] = _dg.seal_quality(c)
            except Exception:
                pass

        # 🔮 次日开盘推测接进竞价裁决（2026-11-02 用户「两张卡结合，不要新开链路」）：
        #   盘后选股卡已把 `forecast`（明早大概率开在几个点／几成概率买不到）写进候选文件，
        #   本层**只透传 + 拿实测开盘去查「这档历史胜率」**，⛔ 不现场重算模型
        #   （重算会出现「盘后卡说一套、竞价卡说另一套」的两套口径）。
        # ⛔ 变量名不能叫 `_fc`：本模块第 74 行已把 `front_core` 导入为 `_fc`，
        #    同名会把 `front_core.row_tier` 覆盖掉（2026-11-02 实测 AttributeError）。
        _fcst = c.get("forecast") if isinstance(c.get("forecast"), dict) else None
        if _fcst and not _fcst.get("error"):
            base["forecast"] = _fcst
            base["forecast_verdict"] = _fcst.get("verdict")
            try:
                from core.relay_forecast import band_win_for
                base["forecast_band"] = band_win_for(c, gap)
            except Exception:                                # noqa: BLE001
                base["forecast_band"] = None

        if POOL_CALL_CORE_FIRST and c.get("core_identity_source"):
            from core import gene_height as gh
            rank = _candidate_int(c, "dragon_rank")
            leader_code = str(c.get("dragon_leader_code") or "")
            leader_name = str(c.get("dragon_leader_name") or leader_code)
            swapped = _gene_swap.get(code)
            mine = gh.prev_streak_of(zt_series, code)
            theirs = gh.prev_streak_of(zt_series, leader_code)
            override = bool(POOL_CALL_DRAGON_GENE and rank > 1
                            and _candidate_int(c, "dragon_leader_boards") == boards_i
                            and gh.gene_stronger(mine, theirs))
            if swapped:
                leader_code = swapped["by"]
                leader_name = str(swapped.get("by_name") or leader_code)
                rank = 2
                base["gene_demoted"] = {
                    "by": leader_code, "by_name": leader_name,
                    "mine_prev_streak": swapped["leader_prev_streak"],
                    "challenger_prev_streak": swapped["mine_prev_streak"],
                }
            elif override:
                rank = 1
                base["gene_leader_override"] = {
                    "mine_prev_streak": mine, "leader_prev_streak": theirs,
                    "leader": leader_name, "leader_rank": c.get("dragon_rank"),
                }
            if rank > 0 and c.get("sector_has_mates") is not False:
                base["core_identity"] = {
                    "theme": c["core_identity_theme"],
                    "source": c["core_identity_source"],
                    "code": code if rank == 1 else leader_code,
                    "name": name if rank == 1 else leader_name,
                    "is_core": rank == 1,
                }
                if rank > 1:
                    # 🔓 2026-11-12 用户「核心弱了就降一档看看」：
                    #    同线二排比核心高开 ≥5 个点（核心明显转弱）→ 允许二排进裁决，
                    #    但必须自己量能达标、空间够（后面各闸照旧），仓位打半仓。
                    #    ⛔ 不是「核心买不到就推二排」，是「核心走弱、二排独立走强」。
                    _cg = _gap_by_code.get(str(leader_code or ""))
                    _allow2 = bool(
                        POOL_CALL_CORE_WEAK_SECOND
                        and isinstance(gap, (int, float)) and isinstance(_cg, (int, float))
                        and (float(gap) - float(_cg)) >= CORE_WEAK_SECOND_DIFF
                    )
                    if _allow2:
                        base["core_weak_second"] = True
                        base["core_weak_core_gap"] = round(float(_cg), 2)
                        base["position_cap"] = min(float(base.get("position_cap") or 1.0), 0.5)
                    else:
                        # 🧲 2026-11-16 用户「跟着主线走」：这条线若「整体共振」成立
                        #    （同线有票一字/大高开 + 老龙红开 + 昨日成线），
                        #    非核心票也允许参与（半仓）——买的是「线」，不是「票的地位」。
                        _res = _line_resonance.get(_line_key(c)) or {}
                        _allow_res = bool(
                            POOL_CALL_SECTOR_RESONANCE
                            and _line_key(c) == _strongest_line
                            and int(_res.get("score") or 0) >= RESONANCE_MIN
                        )
                        if _allow_res:
                            # ⛔ 不在这里直接标 participate 并 continue —— 那会绕过后面的
                            #    量能/身位/名额/空间各闸（11-16 实测：横店跳过全部下游闸）。
                            #    这里只解除「非核心」这一条否决，其余闸照旧，仓位打半仓。
                            base["sector_resonance"] = {**_res, "line": _line_key(c)}
                            base["resonance_second"] = True
                            base["position_cap"] = min(float(base.get("position_cap") or 1.0),
                                                       RESONANCE_SECOND_CAP)
                        if not _allow_res:
                            marks[code] = {**base, "view": "watch", "core_first_gate": True,
                                       "dragon_gate": "deputy",
                                           "reason": (f"同真实题材核心是{leader_name}，同板数不等于同地位；"
                                                      "不因核心开盘不可买而改推非核心 → 只观察")}
                            continue

        from core.two_pool import observation_only, recheck_class, all_tradeable_enabled, height_observation
        if height_observation(c):
            marks[code] = {**base, "view": "watch", "height_observe": True,
                           "participation_eligibility": "observe_only", "recheck_class": "hard",
                           "reason": "高度观察，只看不买"}
            continue
        if observation_only(c):
            # 🔁 11-05 用户拍板 A：观察票一律先用 09:25 实测「判一遍」并留痕，
            #    ⛔ 不再开局一句「板块观察参照，不参与交易」直接短路。
            _cls = str(c.get("recheck_class") or recheck_class(c))
            _rb = _ac.volume_band(amt, need)
            # 🔁 11-06 契约缺口修复：量能档原先只写进 `auction_recheck`，没写进决策行，
            #    导致 `pipeline_verify` 的透传白名单（期待 `volume_band`）拿不到值，
            #    卡面/账本只能靠 reason 文案反推。此处与 ⑥ 量能段同源同函数，值一致。
            base["volume_band"] = _rb["band"]
            base["volume_scale"] = _rb["scale"]
            base["volume_ratio"] = _rb.get("ratio")
            _recheck = {
                "class": _cls,
                "gap_pct": gap,
                "amount_yi": amt if isinstance(amt, (int, float)) else None,
                "need_yi": need if isinstance(need, (int, float)) else None,
                "volume_band": _rb.get("band"),
            }
            if not POOL_CALL_RECHECK_OBSERVE:
                marks[code] = {**base, "view": "watch",
                               "participation_eligibility": "observe_only",
                               "reason": c.get("watch_reason") or "板块观察参照，不参与交易"}
                continue
            # 🕯 一字板次日低吸（2026-11-12 用户「毕竟它是光模块的…应该能低吸到」）：
            #    昨天一字（买不到）的核心，今天开在 +2%~+6%（与空间闸 0.4 同口径） → 允许走完整裁决链（同一套闸）。
            _owg = str(c.get("watch_group") or "")
            _ow_low_gap = (_owg in ("一字核心", "一字龙头")
                           and isinstance(gap, (int, float)) and 2.0 <= float(gap) <= 6.0)
            if _ow_low_gap:
                _recheck["verdict"] = "一字次日低吸窗口·进入完整竞价裁决"
                base["recheck_class"] = "state"
                base["auction_recheck"] = _recheck
                base["one_word_low_gap"] = True
            if _cls != "state" and not all_tradeable_enabled() and not _ow_low_gap:
                _gap_txt = "竞价没拿到" if gap is None else f"今早开 {gap:+.2f}%"
                _amt_txt = (f"、竞价 {float(amt):.2f} 亿"
                            if isinstance(amt, (int, float)) else "")
                _band_txt = "、" + {"full": "量足", "soft": "量偏弱",
                                    "dry": "真没量"}.get(_rb.get("band"), "量能无数据")
                # ⛔ 2026-11-12 修：`is_one_word` 是**昨天**的封板形态，
                #    今天买不买得进只看今开 gap（今天开 5% 就不是一字）。
                #    否则「昨日一字、今日低开」的票会被永久判成「封死买不进」。
                _sealed_today = (isinstance(gap, (int, float))
                                 and float(gap) >= (19.5 if c.get("is_20cm") else 9.5))
                if _owg in ("一字核心", "一字龙头") and isinstance(gap, (int, float)):
                    _why = (f"昨日一字，今日开 {float(gap):+.2f}%"
                            "（低吸窗口 +2%~+6% 之外）")
                elif _cls == "restricted":
                    _why = "盘后没过接力准入"
                elif _sealed_today:
                    _why = "一字封死，买不进"
                elif "封单只占流通" in str(c.get("watch_reason") or ""):
                    _why = "最高板封单太薄"
                else:
                    _why = "不在可买范围"
                _recheck["verdict"] = "复核不通过·盘后硬闸保留"
                marks[code] = {**base, "view": "watch",
                               "participation_eligibility": "observe_only",
                               "recheck_class": _cls, "auction_recheck": _recheck,
                               "reason": f"{_gap_txt}{_amt_txt}{_band_txt} → {_why}，今天只看不买"}
                continue
            # 不短路，带复核标记走完整裁决链；买不买由后面每道闸现场判。
            # ⛔ 身份仍为 observe_only，本函数不改候选行、不写 buyable。
            # 🆕 2026-11-10 用户「这些票全部都要有可交易的权限」：`POOL_CALL_ALL_TRADEABLE`
            #    默认为 1 时，restricted / hard 类也一并进入完整裁决链——盘后分组不再
            #    预先剥夺交易资格，只有竞价现场的价格/量能/顶部空间/封板质量/stance 名额能决定买不买。
            _recheck["verdict"] = ("状态类·进入完整竞价裁决" if _cls == "state"
                                   else "全池可交易·进入完整竞价裁决")
            base["recheck_class"] = _cls or "state"
            base["auction_recheck"] = _recheck
            base["rechecked_from_observe"] = True

        if c.get("participation_eligibility") == "conditional":
            _repair = c.get("historical_repair") or {}
            _evidence = _repair.get("evidence") or {}
            if (_repair.get("eligibility") != "conditional" or _repair.get("blocked_by")
                    or _evidence.get("quality") != "ok"
                    or _evidence.get("asof") != candidates.get("date")):
                marks[code] = {**base, "view": "watch", "reason": "历史修复条件资格缺失或过期，不参与"}
                continue

        # ① 无竞价数据
        if gap is None:
            marks[code] = {**base, "view": "watch",
                           "reason": "开盘竞价数据没拿到 → 今天先看，不买"}
            continue
        # ② 竞价封死（物理买不进）/ absolute 回退模式的高开超 5% 线。
        #    ⛔ 用户 11-05 删除「高开超过 5% 不买」这条幅度线：强票按质量预期本就该高开，
        #       现在只拦「已封在涨停、根本买不进」这一物理事实（一字板不参与）。
        if zone == "above":
            _why = ("今天开在涨停附近，竞价就封死了 → 买不进，不追"
                    if _ac.relative_mode() else
                    f"今天开在 {gap:+.2f}%，高开超过 5% 上限 → 没买到，不追"
                    + _dip_hint(q, c, is_core=is_core))
            marks[code] = {**base, "view": "skip", "reason": _why}
            continue
        # ②′ 顶部空间硬闸（2026-11-06 用户「避开开的特别高又没有资格和行情秒板的票」）
        #     `room = 涨停幅度 − 今开幅度`，不足涨停幅度的一半 → 上方没肉、下方敞开，不追。
        #     ⛔ 与 `gap_weight` 的区别：那是「买多重（降到 0.2 仓）」，这是「不买」。
        #     ⚠️ 放在 ③ 预案回避之前：这是**幅度事实**，先于盘后预案例外判断留痕，
        #        与 ② 竞价封死同级（都属于「买不进/不该买」的物理与赔率事实）。
        _lc_now = line_cores.get(line_family(c)) or {}
        _core_alive = bool(_lc_now) and (_lc_now.get("in_pool")
                                         if _lc_now.get("in_pool") is not None
                                         else not _lc_now.get("broken"))
        # 🐉 龙一免「顶部空间」闸（2026-11-11 用户口径）：买最强的，别因为「贵」下移身位
        _leader_buy = bool(POOL_CALL_LEADER_IGNORE_ROOM and (
            is_core or c.get("dragon_rank") == 1 or c.get("is_sector_dragon")))
        if POOL_CALL_HIGH_GAP_REJECT and isinstance(gap, (int, float)) and not _leader_buy:
            _lim = float(_ac.limit_up_pct(bool(c.get("is_20cm"))))
            _room = _lim - float(gap)
            base["gap_room_pct"] = round(_room, 2)
            base["gap_room_ratio"] = round(_room / _lim, 3) if _lim else None
            if _room < _lim * HIGH_GAP_ROOM_MIN:
                marks[code] = {**base, "view": "skip",
                               "high_gap_reject": True,
                               "reason": (f"今开 {gap:+.2f}%，离涨停只剩 {_room:.2f} 个点"
                                          f"（上限 {_lim:.0f}%）→ 上方空间不够，不追"
                                          # 🅱️ 三态（2026-11-11）：核心还在，只是这个价格不合适
                                          #    → 明写「可以不做」，⛔ 不发替代资格
                                          # 只陈述事实；「留意买入机会」由 `_dip_hint` 给（带价格），
                                          # ⛔ 两处都写会变成复读。
                                          + ("；核心仍在，只是这个价不值"
                                             if _core_alive else "")
                                          + _dip_hint(q, c, is_core=is_core))}
                continue
        # ③ 盘后预案＝回避（2026-11-06 用户「盘后预案这个功能关了，全系统关闭」）
        #    ⛔ 默认**不再否决**：只留痕 `bias_avoided`，买不买交给竞价链自己判。
        #    依据：11-06 被 avoid 砍掉的 7 只事后均值 +0.42%（新亚制程 +16.00% 当日第二）。
        #    `POOL_CALL_BIAS_AVOID=1` 逐字回退旧行为。
        if bias == BIAS_AVOID:
            if POOL_CALL_BIAS_AVOID_VETO:
                marks[code] = {**base, "view": "skip",
                               "reason": "昨天做预案时就把它划成回避 → 今天不买"}
                continue
            base["bias_avoided"] = True
        # 🎯 低开例外（2026-11-02·只对全市场最高板）
        #   「最高板 ∧ 低开>3%」是全交叉里唯一显著为正的一组（n=7 胜 71.4%/均 +5.02%，
        #   验证段 n=5 胜 60%）。样本小 → 三道锁（只认最高板 / 半仓 / 不额外放号）。
        #   ⛔ 不覆盖「身位差≥1板」：同型规则在验证段只有 29.2%，属过拟合。
        _lo_top = bool(
            POOL_CALL_LOW_OPEN_TOP_EXCEPTION
            and isinstance(gap, (int, float))
            and float(gap) <= LOW_OPEN_TOP_GAP_MAX
            and base.get("gap_to_top") == 0
        )
        # ④ 明显低于**这只票自己的质量预期**（relative）/ 低于 AI 区间下沿（absolute 回退）
        #    🕳️ 深低开豁免：DEV 曲线是 U 形，「砸穿」和「走弱」必须分开判（见常量区注释）。
        _dev_val = base.get("gap_vs_expected")
        if isinstance(_dev_val, (int, float)) and _dev_env_offset:
            _dev_val = float(_dev_val) - float(_dev_env_offset)
        _lk = _lk_with_theme(c, _ct_eff)
        _mates = _mates_key.get(_lk) if _mates_key else None
        # 弱线（不在昨日最强档）：低开一律按走弱处理，⛔ 不享受「错杀/低开也买」
        _line_weak = bool(_mates_key) and _lk not in _top_lines_key
        # 🌱 首板平开确认（2026-11-17）：昨日首板 + 平开 + 成线 + 封得早/零炸 → 试仓
        _fb_boards = int(c.get("boards") or 0)
        _fb_fbt = str(c.get("fbt") or "999999")
        _fb_zbc = int(c.get("zbc") or 0)
        _fb_flat = bool(
            POOL_CALL_FIRST_BOARD_FLAT
            and _fb_boards == 1
            and isinstance(gap, (int, float))
            and FB_FLAT_LO <= float(gap) <= FB_FLAT_HI
            and (((_mates or 0) >= FB_FLAT_MIN_MATES) or ((_gene_score(str(code).zfill(6)) or -1) >= FB_FLAT_GENE_MIN))
            and (_fb_fbt <= "100000" or _fb_zbc == 0)
        )
        if _fb_flat:
            base["first_board_flat"] = {"gap": round(float(gap), 2), "mates": _mates,
                                        "fbt": _fb_fbt, "zbc": _fb_zbc,
                                        "gene": _gene_score(str(code).zfill(6))}
        _deep_rescued = bool(
            zone == "below"
            and POOL_CALL_DEEP_LOW_RESCUE
            and _ac.relative_mode()
            and isinstance(_dev_val, (int, float))
            and float(_dev_val) <= DEEP_LOW_OPEN_DEV
            and isinstance(gap, (int, float)) and float(gap) > DEEP_LOW_GAP_FLOOR
            # 🧵 只有成线的票才谈「错杀」；孤线/弱线的深低开按走弱处理
            and (not _theme_mates
                 or ((_mates or 0) >= DEEP_LOW_MIN_MATES and fam in _top_lines))
            and not _line_weak
        )
        if _deep_rescued:
            base["deep_low_rescue"] = {
                "dev": round(float(_dev_val), 2),
                "threshold": DEEP_LOW_OPEN_DEV,
                "note": (f"开 {gap:+.2f}% 比它自己的预期低 {abs(float(_dev_val)):.1f} 个点，"
                         f"且这条线昨日 {_mates} 家（最强档）→ 属于被砸下来的错杀档，不按走弱处理"),
            }
        # 🐾 中间档低开放行（2026-11-11 用户「可以在低开的时候买」）。
        #    `zone=below` 且 gap<0、又没到深低开豁免线 → 这才是「低开被一票否决」的全部来源。
        #    ⛔ 不覆盖竞价封死（zone=above 在这一步之前就短路了）、⛔ 不改 gap_zone 判定、
        #       ⛔ 不额外占名额（仍然走后面的量能/身位/名额链），只把仓位压到半仓。
        _low_open_buy = bool(
            zone == "below"
            and POOL_CALL_LOW_OPEN_BUY
            and _ac.relative_mode()
            and isinstance(gap, (int, float))
            and float(gap) < 0
            # 2026-11-17：接近跌停不算「低开」，别拿它当低吸机会
            and float(gap) > DEEP_LOW_GAP_FLOOR
            and not _lo_top
            and not _deep_rescued
            and not _line_weak
        )
        if _low_open_buy:
            base["low_open_buy"] = {
                "gap": round(float(gap), 2),
                "dev": (round(float(_dev_val), 2)
                        if isinstance(_dev_val, (int, float)) else None),
                "position_cap": LOW_OPEN_BUY_POSITION,
                "note": "低开也买（11-11 新口径）→ 半仓参与；⛔ 回退 POOL_CALL_LOW_OPEN_BUY=0",
            }
            base["position_cap"] = min(float(base.get("position_cap") or 1.0),
                                       LOW_OPEN_BUY_POSITION)
        if zone == "below" and _line_weak and not _lo_top and not _fb_flat:
            marks[code] = {**base, "view": "skip", "weak_line_low_open": True,
                           "reason": (f"今天开 {gap:+.2f}%，这条线昨日只有 {_mates} 家"
                                      "（不是昨天最强的那几条线）→ 低开按走弱处理，不买")}
            continue
        if zone == "below" and _fb_flat and not _lo_top:
            # 首板平开档：不按「低于预期」否决，继续走后面的量能/名额链（仓位打 25%）
            base["position_cap"] = min(float(base.get("position_cap") or 1.0), FB_FLAT_POSITION)
        elif zone == "below" and not _lo_top and not _deep_rescued and not _low_open_buy:
            _e_show = base.get("expected_gap_pct")
            if _ac.relative_mode() and isinstance(_e_show, (int, float)):
                _why = (f"今天开 {gap:+.2f}%，按昨日质量该开 {float(_e_show):+.2f}% "
                        "→ 明显低于预期，放弃")
            else:
                _why = f"今天开在 {gap:+.2f}%，低于昨天的预期 → 放弃不接"
            marks[code] = {**base, "view": "skip", "reason": _why}
            continue
        # ⑤ ⛔ 2026-11-05 用户删除「必须红开」闸：弱票低开同样可以符合它自己的预期。
        #    仅 `AI_EXPECTED_GAP_MODE=absolute` 回退时逐字保留旧行为。
        if not _ac.relative_mode() and gap <= 0 and not _lo_top:
            marks[code] = {**base, "view": "watch",
                           "reason": f"开在 {gap:+.2f}%，没红开 → 今天先看，不买"}
            continue
        # ⚠️ 可选低开收紧闸（**默认关**，尊重用户删除指令）。历史 26 日样本里
        #    gap≤0 新放进票仅 6/130＝4.6% 晋级，留此开关供逐日复核后决定是否收紧。
        if (_ac.relative_mode() and POOL_CALL_LOW_OPEN_GUARD
                and gap <= LOW_OPEN_GUARD_MAX_GAP and not _lo_top):
            marks[code] = {**base, "view": "watch", "low_open_guard": True,
                           "reason": f"开在 {gap:+.2f}%，低开观察闸生效 → 今天先看，不买"}
            continue
        if _lo_top:
            base["low_open_top"] = True
            base["position_cap"] = LOW_OPEN_TOP_POSITION
        # ⑥ 量能分档区间（2026-11-05 用户「量能这一块的区间优化一下」）
        #    唯一判据源 = `ai_caliber.volume_band`（与账本 decide / hibernate 阀门同源）：
        #      full ≥0.70 量足 / soft 0.35~0.70 量偏弱但**达标可买** / dry <0.35 真没量才观察。
        #    ⛔ 0.35 地板不拆（31 日真实链路回放：放进真没量的票，日均 2.79%→1.30%）。
        #    ⛔ 只留事实标记，不写「只买半仓」——仓位统一由末尾「平均分配」决定。
        _vb = _ac.volume_band(amt, need)
        base["volume_band"] = _vb["band"]
        base["volume_scale"] = _vb["scale"]
        if _vb["block"] and _fb_flat:
            # 🌱 首板竞价额天然小（11-16 瀛通只 0.02 亿），量能地板对手板平开档错误否决。
            base["volume_band"] = _vb["band"]
            base["volume_scale"] = _vb["scale"]
            base["position_cap"] = min(float(base.get("position_cap") or 1.0), FB_FLAT_POSITION)
            base["first_board_flat_volume_note"] = (
                f"首板平开档：竞价 {float(amt):.2f} 亿（计划 {float(need):.2f} 亿）→ 按试仓 25% 放行")
        elif _vb["block"]:
            _floor = float(need) * VOLUME_HARD_RATIO if isinstance(need, (int, float)) else 0.0
            if (not _ac.AI_VOLUME_BANDS and POOL_CALL_VOLUME_SOFT
                    and isinstance(amt, (int, float)) and float(amt) >= _floor):
                # 回退路径：AI_VOLUME_BANDS=0 且旧软化开关打开时，恢复旧「半仓试错」。
                base["volume_soft"] = True
                base["position_cap"] = min(float(base.get("position_cap") or 1.0),
                                            VOLUME_SOFT_POSITION)
                base["volume_soft_note"] = (
                    f"竞价 {float(amt):.2f} 亿只到计划 {float(need):.2f} 亿的"
                    f"{float(amt) / float(need) * 100:.0f}%，量偏弱 → 只半仓试错")
            else:
                # 回退路径（AI_VOLUME_BANDS=0）逐字保留旧文案「量不够（要 X 亿）」，
                # 新区间路径只把「真没量」的判定事实说清，⛔ 不写机制解释。
                if _ac.AI_VOLUME_BANDS:
                    _txt = (f"开在 {gap:+.2f}% 是红的，但竞价只有 {float(amt):.2f} 亿、"
                            f"只到计划 {float(need):.2f} 亿的 "
                            f"{(_vb['ratio'] or 0) * 100:.0f}% → 真没量，先看")
                else:
                    _txt = (f"开在 {gap:+.2f}% 是红的，但竞价只有 {float(amt):.2f} 亿、"
                            f"量不够（要 {float(need):.2f} 亿）→ 像诱多，先看")
                marks[code] = {**base, "view": "watch", "reason": _txt}
                continue
        # 昨日同路径晋级、溢价与失败核按钮都偏弱时仍加严：须量足（full 档）才放行。
        #    ⛔ 旧口径要求 amt ≥ need（100% 计划量）过苛，改走同一区间判据，但只认 full。
        if c.get("relay_mood") == "pressured" and _vb["band"] != "full":
            marks[code] = {**base, "view": "watch",
                           "reason": "这一路接力昨天反馈偏弱，今天竞价量没到足额 → 先看，不买"}
            continue
        # 🔁 状态类复核票：竞价要推翻盘后状态判断，须拿足额量能来推翻。
        #    soft / dry / 缺目标额都不算新证据（dry 已在⑥拦，这里收口 soft 与 unknown）。
        #    ⛔ 这不是新门槛，是「竞价会变」的举证责任；开关关掉则只按常规量能门走。
        if (base.get("rechecked_from_observe") and POOL_CALL_RECHECK_STATE_FULL
                and _vb["band"] != "full"):
            # 🧬 强基因豁免（2026-11-10 用户）：近月连板基因够硬，就不必再拿满额量当
            #    「推翻盘后状态」的举证 —— 股性本身就是它够格被复核的理由。
            #    ⛔ 只让开 soft（0.35~0.70）；dry（<0.35）照旧拦（31 日回放：拆地板
            #    日均 2.79%→1.30%）。仓位仍走 `volume_scale` 的 soft 系数，不加满。
            _ge_gene = base.get("recent_gene") or {}
            _ge_ok = False
            if (POOL_CALL_GENE_EXEMPT and _vb["band"] == "soft"
                    and isinstance(_ge_gene, dict)):
                try:
                    from core import gene_recent as _gr_mod

                    # ⚠️ 必须显式传门槛：`is_strong` 的默认参数在**模块导入时**就绑定了，
                    #    回放里改 `GENE_MIN_BOARDS` 常量对默认参数无效（A/B 会跑成同一结果）。
                    _ge_ok = _gr_mod.is_strong(
                        _ge_gene,
                        min_boards=int(_gr_mod.GENE_MIN_BOARDS),
                        min_zt=int(_gr_mod.GENE_MIN_ZT))
                except Exception:
                    _ge_ok = False
            if _ge_ok:
                base["gene_exempt"] = True
                base["gene_exempt_reason"] = (
                    f"近月 {_ge_gene.get('max_boards')} 板 · 涨停 "
                    f"{_ge_gene.get('zt_n')} 次，股性够格 → 量 {(_vb['ratio'] or 0) * 100:.0f}% "
                    f"按 {int((_vb.get('scale') or 0.5) * 100)}% 仓放行")
            elif _fb_flat:
                # 🌱 2026-11-17 用户选 B：首板平开档不看量能地板（首板竞价额天然小），
                #    以「股性/成线」替代 → 按 25% 试仓放行（11-17 瀛通 40% 量的实例）。
                base["position_cap"] = min(float(base.get("position_cap") or 1.0), FB_FLAT_POSITION)
                base["first_board_flat_volume_note"] = (
                    f"首板平开档：竞价只到计划 {(_vb['ratio'] or 0) * 100:.0f}%，"
                    f"按股性/成线放行，仓位 {int(FB_FLAT_POSITION * 100)}%")
            else:
                marks[code] = {**base, "view": "watch", "recheck_gate": True,
                               "reason": (f"今早开 {gap:+.2f}%、竞价只到计划 "
                                          f"{(_vb['ratio'] or 0) * 100:.0f}% → 量没给足，今天先看")}
                continue
        # ⑥a 🧲 板块联动纪律（2026-10-22）：低板独苗不能因本票量价达标就直接买。
        #    只在完整涨停池已确认“昨日独苗”时生效；今早同题材成员没报价是 unknown，
        #    不能偷换成“板块0红开”。soft 默认只拦 1~2 板；strict 才扩展至高标。
        try:
            from core.sector_support import compute_support
            from core.theme_linkage import apply_auction_gate
            # qs 是全池竞价报价，成员代码来自昨日完整涨停池；在此现算确保池内裁决
            # 与机械信号使用同一批 09:25 报价，而不是依赖一份可能缺失的外部 map。
            _link_support = compute_support(c, qs)
            _link = apply_auction_gate(c, _link_support)
        except Exception:
            _link_support = None
            _link = {"block": False}
        base["sector_support"] = _link_support
        base["linkage_discipline"] = _link
        if _link.get("block"):
            marks[code] = {**base, "view": "watch", "linkage_gate": True,
                           "reason": f"开在 {gap:+.2f}% 达标，但{_link.get('note')} → 今天先看，不买"}
            continue
        # ⑥b ⚓ 锚点闸（2026-10-21 用户「次日只需做好锚点的对比与观察」）：
        #   这条线的锚点（昨日大将军）今天开成什么样，直接决定这条线还能不能出手。
        #   ⛔ 顺序上紧贴量能之后、跟风闸之前：先确认「这条线今天整体是强是弱」，
        #     再谈「这只票在这条线里排第几」——反过来会拿个股强度盖住线路走坏。
        _anc = _anchors.get(fam) if POOL_CALL_ANCHOR_GATE else None
        if _anc and _anc.get("code") != code:
            base["line_anchor"] = {k: _anc.get(k) for k in ("code", "name", "boards",
                                                            "attitude", "label", "gap", "note")}
            _att = str(_anc.get("attitude") or "")
            # ⭐ 2026-11-10 口径统一后：**只拦 ATT_BREAK**（DEV < 破预期线，真低于它自己的预期）。
            #    `ATT_WEAK` 现在只在「拿不到该票预期」时出现 —— 按「缺数据不裁决」，
            #    ⛔ 不拦，只留痕。旧逻辑把这两档一起拦，等于用绝对开幅替判据说话。
            if _att == _ac2.ATT_BREAK:
                marks[code] = {**base, "view": "watch", "anchor_gate": True,
                               "reason": (f"开在 {gap:+.2f}% 达标，但这条线的锚点"
                                          f"{_anc.get('name')}今天{_anc.get('label')}"
                                          f"（{_anc.get('gap'):+.2f}%，DEV "
                                          f"{_anc.get('dev') if _anc.get('dev') is not None else '—'}"
                                          f"）→ 未达预期，这条线今天放弃")}
                continue
            if _att == _ac2.ATT_WEAK:
                # 无预期可依 + 低开：⛔ 不裁决，留痕即可
                base["anchor_weak_no_expect"] = True
            if _att == _ac2.ATT_FLAT:
                # 平开＝前排还没表态。⛔ 不硬拦（平开太常见，全拦会把系统打成常年空仓），
                #   只留痕：卡面标「前排平开·未表态」，出手与否交给量能/身位两道闸。
                base["anchor_flat"] = True
        # ⑥c 🎯 中位股闸（2026-10-21 学 92科比：「要么做高，要么做低，中位股不要碰」）
        #   中位＝既没有最高板的地位确定性，也没有低位的性价比，两头不靠。
        #   ⛔ 只在市场高度 ≥5 板时才存在中位区（`cycle_phase.mid_band`）；高度不够时不裁决，
        #     否则会把整条线砍光（今天最高 4 板 → 本闸不生效）。
        if POOL_CALL_MIDBOARD_GATE:
            _mh = _market_max_boards
            if _cp.is_mid_board(boards_i, _mh):
                marks[code] = {**base, "view": "watch", "midboard_gate": True,
                               "reason": (f"开在 {gap:+.2f}% 达标，但"
                                          f"{_cp.mid_board_note(boards_i, _mh)}")}
                continue
        # ⑦ 🐣 跟风闸（2026-09-21 用户抓包）：同线核心板数高出 ≥2 且已断板 → 不占名额
        #   ⊕ 归并一致性守卫（2026-10-12 传媒事故）：候选 core_theme 归线 与 sector 归线矛盾
        #     → 归并可疑 → ⛔ 不硬拦（防一次归并错误就整条误杀）。
        _lc = base.get("line_core")
        _lc_ok = True
        if _lc and POOL_CALL_LINE_CONSISTENCY_GATE:
            try:
                from core import sector_support as _ss
                _ct = str(c.get("core_theme") or "")
                _sec = str(c.get("sector") or "")
                _fam_t = _ss._theme_key({"core_theme": _ct}) if (_ct and _ct != "—") else ""
                _fam_s = _ss._theme_key({"sector": _sec}) if _sec else ""
                # 两条路径都归了线、且归到**不同**线 → 归并可疑
                _lc_ok = not (_fam_t and _fam_s and _fam_t != _fam_s)
                if not _lc_ok:
                    base["line_consistency_warn"] = True
            except Exception:  # 守卫异常 → 保守放行，不因守卫本身误拦
                _lc_ok = True
        if (POOL_CALL_CORE_GATE and isinstance(_lc, dict) and not is_core
                and int(_lc.get("boards") or 0) - boards_i >= CORE_GATE_MIN_GAP
                and _lc.get("broken") and _lc_ok):
            marks[code] = {**base, "view": "watch",
                           "follower_gate": True,
                           "reason": (f"开在 {gap:+.2f}% 是达标的，但这条线真正的核心是"
                                      f"{_lc.get('name')}({_lc.get('boards')}板)，昨天已经断板；"
                                      f"它只是跟风补涨 → 今天先看核心，别追小弟")}
            continue
        # ⑧ 🧊 方向资金失血 —— 先看土壤再谈个股（宪法🐲-1「先判土壤再谈板数」）
        drain = ({"drained": False, "known": False, "note": "", "sum_yi": None, "theme": ""}
                 if not _drain_on else _dg.sector_drain_verdict(c, sector_flow))
        if drain.get("drained"):
            marks[code] = {**base, "view": "watch", "defense_gate": "sector_drain",
                           "defense_detail": drain,
                           "reason": (f"开在 {gap:+.2f}% 达标了，但{drain['note']} "
                                      f"→ 今天先看，不买")}
            continue
        # ⑨ 🧭 位置闸（2026-10-08 补·南华生物案例）——连板计数会被断板重置，
        #   「首板」两个字盖住了「这是一只已经涨过一大轮、断板多日后重启的票」。
        #   位置高 ∧ 断板 ≥3 个交易日后重启（上一波最高板 ≥3）→ 第二波反抽，不占名额。
        #   ⛔ 断 1~2 天的快速 N 字回封不拦（那是情绪核心的经典形成路径·加分项）。
        #   ⚠️ 顺序：土壤（板块有没有钱）→ 位置（这票在什么位置）→ 盘口（昨天封得脏不脏）。
        #     位置必须排在派发**前面**——否则先被「换手大/封单薄」命中，卡面只说症状、
        #     说不出「它是第二波高位反抽」这个真原因（10-08 南华两条都中，位置才是主因）。
        pos = ({"level": "none", "note": "", "high_position": False, "wave": {}, "facts": {}}
               if not _pos_on else _dg.position_verdict(c, zt_series, zt_days))
        if pos.get("level") == "warn" and pos.get("note"):
            base["position_warn"] = pos["note"]        # 只留痕（卡面可用·不否决）
        if pos.get("level") == "block":
            marks[code] = {**base, "view": "watch", "defense_gate": "high_position",
                           "defense_detail": pos,
                           "reason": (f"开在 {gap:+.2f}% 达标了，但{pos['note']} "
                                      f"→ 今天先看，不买")}
            continue
        # ⑨b 🐉 老龙复燃闸（2026-10-12 用户抓包·国芳集团）——「炒过的老票沉寂很久后又拉首板」
        #   不是「低位新方向首板」。国芳上一波只到 2 板、位置不高 → 位置闸漏（它要求上一波 ≥3 板），
        #   本闸补上这段。与位置闸同源（zt_history），**先于派发**——否则卡面只会说「换手大/封单薄」
        #   这些盘口症状，说不出「它是一只炒过的老票」这个真原因。
        od = ({"level": "none", "note": "", "facts": {}}
              if not _od_on else _dg.old_dragon_verdict(c, zt_series, zt_days))
        if od.get("level") == "block":
            marks[code] = {**base, "view": "watch", "defense_gate": "old_dragon_rebound",
                           "defense_detail": od,
                           "reason": (f"开在 {gap:+.2f}% 达标了，但{od['note']} "
                                      f"→ 今天先看，不买")}
            continue
        # ⑩ 🛡️ 前置防守闸门·盘口层（2026-10-08 大幅回撤经验固化）——**竞价达标 ≠ 可以买**
        #   10-08 实证：两只票竞价一个 +0.85%、一个 +0.40%，都「没超 5% 上限、也没低于预期」，
        #   机械规则照单全收 → 收盘 −5.85% / −8.18%。缺的是「买之前先看这票脏不脏」。
        dist = ({"level": "none", "hit_count": 0, "note": "", "hits": [], "facts": {}}
                if not (POOL_CALL_DEFENSE_GATE and POOL_CALL_DEFENSE_DISTRIBUTION)
                else _dg.distribution_verdict(c))
        if dist.get("note") and dist.get("level") == "warn":
            base["defense_warn"] = dist["note"]        # 只留痕（卡面可用·不否决）
        if dist.get("level") == "block":
            # ③a 三条全中 → distribution；③b「封单薄+封得晚+位置已很高」→ distribution_thin_late
            #   （两种脏法话术不同，卡面标签分开；见 defense_gate.distribution_verdict 的 kind）
            _dcode = ("distribution_thin_late" if dist.get("kind") == "thin_late_high"
                      else "distribution")
            marks[code] = {**base, "view": "watch", "defense_gate": _dcode,
                           "defense_detail": dist,
                           "reason": (f"开在 {gap:+.2f}% 达标了，但{dist['note']} "
                                      f"→ 今天先看，不买")}
            continue
        # ⑩c 🧊 最高板 + 极薄封单闸（2026-10-27 用户「只观察，行情差没有必要强行接力」）
        #   实证：上海九百(600838) 10-27 是 5 板、全市场唯一高度，但封单只占流通 0.24%。
        #   「最高板」意味着位置最高，「封单最薄」意味着没人愿意垫在后面接 —— 两条同时成立，
        #   缩量 + 情绪 1.8 分的日子再去接力，赔率极差。判据与盘后降级同源同阈值。
        #   ⛔ 缺封单占比（拿不到流通市值）→ 不裁决（不编造、不误拦）。
        if POOL_CALL_THIN_SEAL_TOP and _market_max_boards:
            _ts_boards = boards_i
            _ts_ratio = c.get("seal_ratio_pct")
            if not isinstance(_ts_ratio, (int, float)):
                try:
                    _ts_ratio = _dg.seal_quality(c).get("seal_ratio_pct")
                except Exception:      # 守卫自身异常 → 保守放行，不因守卫误拦
                    _ts_ratio = None
            if (_ts_boards >= int(_market_max_boards)
                    and isinstance(_ts_ratio, (int, float))
                    and float(_ts_ratio) < _dg.DIST_SEAL_RATIO_MAX):
                marks[code] = {**base, "view": "watch", "defense_gate": "thin_seal_top",
                               "defense_detail": {"boards": _ts_boards,
                                                  "market_max_boards": int(_market_max_boards),
                                                  "seal_ratio_pct": round(float(_ts_ratio), 2)},
                               "reason": (f"开在 {gap:+.2f}% 达标了，但它是今天最高 {_ts_boards} 板、"
                                          f"封单只占流通 {float(_ts_ratio):.2f}% —— 位置最高又封得最薄，"
                                          f"行情差没必要强行接力 → 今天只看，不买")}
                continue
        # ⑩d 🐉 板块龙一闸（2026-10-27 用户「推的票必须是板块龙一」）
        #   `dragon_rank` 由盘后 `core.sector_leader` 定（单一源）。这里只认身位：
        #     · 有板块且不是龙一 → 不买（`dragon_rank > 1`）
        #     · 龙一自己买不进（一字/ST）→ 不买，且**不拿同线跟风顶替**
        #   ⛔ 无板块（题材缺失 / 涨停独苗，`sector_has_mates=False` / rank=0）→ 不裁决，
        #     沿用孤军与高标逻辑（缺数据≠不利证据）。
        #   ⛔ 缺 `dragon_rank` 字段（旧候选文件）→ 不裁决（诚实降级，不误拦）。
        if (POOL_CALL_DRAGON_GATE and base.get("core_identity", {}).get("is_core")
                and not base.get("gene_leader_override")
                and (c.get("dragon_leader_block") or c.get("dragon_gate") == "leader_unbuyable")):
            marks[code] = {**base, "view": "watch", "dragon_gate": "leader_unbuyable",
                           "reason": "核心身份保留，但一字/ST风控未通过，开盘不买"}
            continue
        if POOL_CALL_DRAGON_GATE and not base.get("core_identity"):
            _dr = c.get("dragon_rank")
            _has_sector = c.get("sector_has_mates")
            if isinstance(_dr, (int, float)) and int(_dr) > 0 and _has_sector is not False:
                _dr_i = int(_dr)
                _lead_nm = str(c.get("dragon_leader_name") or "") or "别人"
                # 🧬 换位：本票是原龙一，但同板数下有票「上一波更硬」→ 降为跟风身位
                _swapped = _gene_swap.get(code) if _dr_i == 1 else None
                if _swapped:
                    _dr_i = 2
                    _lead_nm = str(_swapped.get("by_name") or _swapped.get("by") or "同线另一只")
                    base["gene_demoted"] = {
                        "by": _swapped.get("by"),
                        "by_name": _swapped.get("by_name"),
                        "mine_prev_streak": _swapped.get("leader_prev_streak"),
                        "challenger_prev_streak": _swapped.get("mine_prev_streak"),
                        "note": (f"同 {_swapped.get('boards')} 板下，"
                                 f"{_lead_nm}的上一波 "
                                 f"{_swapped.get('mine_prev_streak')} 连板比它更硬 → 它降为跟风"),
                    }
                if _dr_i > 1:
                    # 🧬 基因复核（2026-11-06 用户「东方海洋上次四连板，皇台只有三连板」）
                    #   旧候选文件里的 `dragon_rank` 按旧序写死（板数 → 封板时段），
                    #   同板数时「谁封得早」压过「谁上一波走得高」。09:25 是最终裁决，
                    #   这里用**同一套股性判据**复核一次：同板数 ∧ 我上一波严格更硬 → 不按跟风。
                    #   ⛔ 前置不变量：`zt_series` 截止昨日，而池内票昨日全部在板，
                    #      故 `prev_streak_of` 的「当前段」就是它昨日的连板数（口径正确）。
                    if POOL_CALL_DRAGON_GENE:
                        try:
                            from core import gene_height as _gh
                            _my_prev = _gh.prev_streak_of(zt_series, code)
                            _ld_prev = _gh.prev_streak_of(
                                zt_series, str(c.get("dragon_leader_code") or ""))
                            _ld_b = c.get("dragon_leader_boards")
                            _ld_b = int(_ld_b) if isinstance(_ld_b, (int, float)) else None
                            if (_gh.gene_stronger(_my_prev, _ld_prev)
                                    and _ld_b is not None and _ld_b <= boards_i):
                                base["gene_leader_override"] = {
                                    "mine_prev_streak": _my_prev,
                                    "leader_prev_streak": _ld_prev,
                                    "leader": _lead_nm,
                                    "leader_rank": _dr_i,
                                    "note": (f"同板数下它的{_gh.prev_streak_text(_my_prev)}、"
                                             f"强过{_lead_nm}的{_gh.prev_streak_text(_ld_prev)}"
                                             f" → 不按跟风处理"),
                                }
                                _dr_i = 1        # 本地视为龙一：后续「邻龙/顶替」分支不再拦
                        except Exception:                            # noqa: BLE001
                            pass
                if _dr_i > 1:
                    # 🚪 隔壁通道（2026-11-04 用户 BBB「今天龙一买不到，必须考虑龙二能不能买」）
                    #   只在「龙一自己买不进（一字/ST）」且它是第 2 身位时放行：
                    #   ⛔ 不放龙三及以后；仓位减半；龙一开板即走（标记交卡面/账本执行）。
                    #   ⛔ 仍要自己过 5% / 量能 / 预期区间 / 身位 / 四条件 / 名额，一个都不能少。
                    _lb2 = str(c.get("dragon_leader_block") or "")
                    if POOL_CALL_NEXT_TO_LEADER and _dr_i == 2 and _lb2:
                        base["next_to_leader"] = True
                        base["leader_block"] = _lb2
                        base["exit_on_leader_open"] = True
                        _pc0 = base.get("position_cap")
                        _pc0 = float(_pc0) if isinstance(_pc0, (int, float)) and _pc0 > 0 else 1.0
                        base["position_cap"] = round(
                            min(_pc0, _rl.MAX_POSITION_PER_STOCK * NEXT_TO_LEADER_POS_FRAC), 4)
                        base["next_to_leader_note"] = (
                            f"龙一{_lead_nm}今天买不进（{_lb2}）→ 半仓接隔壁，"
                            f"龙一封单松动就撤")
                    else:
                        marks[code] = {**base, "view": "watch", "dragon_gate": "deputy",
                                       "defense_detail": {"dragon_rank": _dr_i,
                                                          "leader": _lead_nm,
                                                          "leader_boards": c.get("dragon_leader_boards")},
                                       "reason": (f"开在 {gap:+.2f}% 达标，但这条线的龙一是"
                                                  f"{_lead_nm}（{c.get('dragon_leader_boards') or '?'} 板），"
                                                  f"它是这条线第{_dr_i}个 —— 只做龙一，后面的不追")}
                        continue
                # ⛔ `next_to_leader` 的票身上也带 `dragon_leader_block`（那描述的是**龙一**
                #    的状态，不是它自己的），必须先排除，否则刚放行的隔壁会被这段再拦一次。
                if (not base.get("next_to_leader")
                        and (str(c.get("dragon_leader_block") or "")
                             or str(c.get("dragon_gate") or "") == "leader_unbuyable")):
                    marks[code] = {**base, "view": "watch", "dragon_gate": "leader_unbuyable",
                                   "reason": (f"它是这条线的龙一，但今天买不进（一字/ST）"
                                              f"→ 不拿跟风顶替，这条线今天不做")}
                    continue

        # ⑩e 🎯 四条件闸（2026-10-27 用户「一定要题材龙+情绪OK+量能+梯队…没有就空！！」）
        #    盘后 `apply_high_win_gate` 已按四条件把不合格票移出接力池并打标；
        #    此处二次拦截，⛔ 防「同日重跑 / 旧存档 / 人工塞票」把被否的票捡回买。
        if POOL_CALL_HIGH_WIN_GATE:
            _hw = c.get("high_win_gate")
            if isinstance(_hw, list) and _hw:
                # 🧊 冰点解冻（2026-10-28 用户「冰点日若开盘后情绪转好 可以出手」）：
                #   只缺**市场级**条件（情绪）且今晨竞价回暖 → 放行；掺个股级硬伤一律不放。
                _market_only = all(str(x) in POOL_CALL_MARKET_LEVEL for x in _hw)
                if bool(POOL_CALL_ICE_UNFREEZE and _market_only and open_read.get("ready")):
                    base["unfrozen"] = "market_open"
                    base["unfrozen_from"] = list(_hw)
                else:
                    marks[code] = {**base, "view": "watch", "high_win_gate": list(_hw),
                                   "reason": f"四条件缺 {'/'.join(str(x) for x in _hw)} —— 今天不推"}
                    continue

        # ⑪ 🚧 后排接力闸（2026-10-21 用户「后排接力严格要谨慎！很有可能没有溢价！」）
        #   放在最后一道：只有土壤(⑧)、位置(⑨b)、派发(⑩)都没问题的票，才轮到谈「它在
        #   这条线里排第几」。⛔ 归因顺序不变，卡面先说真原因、最后才说身位。
        #
        #   触发条件（⛔ 刻意收窄，宁可不拦也不误杀——10-21 实证）：
        #     「这条线的前排**还在**（核心未断板、也不是一字/超5%买不到），而系统却要买后排」
        #   这才是 92科比「往前排核心选」真正针对的错——有前排可买却去买小弟。
        #   若核心已断板 / 今天买不到 → 这条线没有可买的前排，后排就是唯一入口（用户
        #   「买隔壁」场景），⛔ 不拦，只打标签。反例支撑：10-21 传媒线核心新华传媒已断板，
        #   后排南威软件(首板)当日 +9.27%，硬拦会直接错过这只当天最大的肉。
        if POOL_CALL_BACKROW_GATE and not base.get("core_identity", {}).get("is_core"):
            _tier = _fc.row_tier(c, line_is_core=is_core,
                                 line_max_boards=base.get("line_max_boards"),
                                 line_rank=base.get("line_rank"),
                                 boards=boards_i, core_boards=(lc or {}).get("boards"))
            _lc_code = str((lc or {}).get("code") or "")
            _lc_gap = _gap_of(qs.get(_lc_code)) if _lc_code else None
            _lc_name = str((lc or {}).get("name") or "") or None
            # ⛔ 11-05 删 5% 线后：龙头「买不到」只剩物理封死这一事实，
            #    ⛔ 不能再拿「高开超过 5%」当买不到（强票本就该高开）。
            _core_gap_out = (isinstance(_lc_gap, (int, float))
                             and _ac.unbuyable_gap(_lc_gap, (lc or {}).get("is_20cm")))
            _core_broken = bool((lc or {}).get("broken"))
            base["line_tier"] = _tier
            base["line_core_gap"] = _lc_gap
            # 只在「核心仍在板、且今天买得到」时才判后排有罪；缺核心数据 → 不裁决。
            _front_available = bool(lc) and (not _core_broken) and (not _core_gap_out)
            if _tier in (_fc.TIER_SECOND, _fc.TIER_BACK) and _front_available and _lc_ok:
                _gap_bd = None
                try:
                    _gap_bd = int((lc or {}).get("boards") or 0) - boards_i
                except Exception:
                    _gap_bd = None
                # 🐉 文案自洽（2026-10-27）：本票若是**当日板块龙一**，不能说它"跟风补涨"
                #    —— 那是 `line_cores`（历史载体）判的 tier，与当日身位是两回事。
                #    裁决结果不变（仍不买），只把话说对：龙一遇旧载体断板 = 整条线在换挡期。
                _prem = _fc.premium_note(_tier, line_core_name=_lc_name, boards_gap=_gap_bd)
                _is_dragon_today = _candidate_int(c, "dragon_rank") == 1
                if _is_dragon_today:
                    _prem = ("它是这条线今天的龙一，但上一波核心已经断板，"
                             "整条线还在换挡——现在接等于赌换挡成功")
                marks[code] = {**base, "view": "watch", "backrow_gate": True,
                               "reason": (f"开在 {gap:+.2f}% 达标，但前排{_lc_name or '核心'}今天还在板、"
                                          f"想买的话现在就能买；这只"
                                          f"{_prem}"
                                          f" → 有前排就别买小弟，先看")}
                continue
            if _tier in (_fc.TIER_SECOND, _fc.TIER_BACK) and lc:
                # 买隔壁场景：不拦，但把「它只是隔壁」这件事留在盘面上（卡面用）
                base["backrow_note"] = ("core_broken" if _core_broken else "core_unbuyable")
        # ⑪b 📏 身位（2026-11-06 用户 A 重构：分母改同题材 + 硬闸改降权）
        #   ⛔ 旧口径（2026-11-02）用「全市场最高板」做分母，35 日 717 票次实测**零区分度
        #      且方向反**：保留批 −1.05%/胜42.4% vs 剔除批 −0.93%/胜41.8%。
        #      旧闸真正在做的，是把「光伏 1 板」拿去和「传媒 5 板龙头」比身位，
        #      而这两条线根本没传导关系（用户 11-06 原话）。
        #   新口径：分母 = **同题材**最高板（昨日完整涨停池口径）。
        #      同题材差 ≤1 前排 392 只 −0.68%/43.6%；差 ≥2 后排 153 只 −1.62%/37.3%。
        #      旧闸的「错杀批」（市场差≥2 但题材差≤1）n=359 是 −0.64%/43.7%，
        #      比旧闸保留批还好 → 旧闸在系统性扔好票。
        #   ⛔ 默认**只降仓不剔除**（用户「没有强制要求百分之百限制」）；下限 0.20 > 0。
        #   ⛔ `POOL_CALL_POSITION_GAP_HARD=1` → 回到硬闸语义（分母仍是新口径）。
        #   ⛔ 题材缺失/落行业兜底 → basis=unknown、权重 1.0，本块不裁决（不猜）。
        if POOL_CALL_LEADER_ONLY and boards_i:
            _tm_b = _theme_board_max.get(_code_theme_pos.get(code) or "")
            _pos = _ac.position_weight(boards_i, theme_max_boards=_tm_b,
                                      market_max_boards=_market_max_boards)
            if _pos.get("basis") != "unknown":
                base["position_gap"] = {
                    "basis": _pos["basis"], "line_gap": _pos["line_gap"],
                    "market_gap": _pos["market_gap"], "theme_max_boards": _tm_b,
                    "weight": _pos["weight"], "label": _pos.get("label") or "",
                }
                base["position_weight"] = _pos["weight"]
                base["position_weight_label"] = _pos.get("label") or ""
            if POOL_CALL_POSITION_GAP_HARD and _pos.get("basis") != "unknown":
                _gt_i = (_pos["line_gap"] if _pos["basis"] == "line"
                         else _pos["market_gap"])
                if isinstance(_gt_i, int) and _gt_i >= LEADER_ONLY_MAX_GAP:
                    _where = "同题材" if _pos["basis"] == "line" else "全市场"
                    marks[code] = {**base, "view": "watch", "leader_only_gate": True,
                                   "reason": (f"开在 {gap:+.2f}% 达标，但它是 {boards_i} 板、"
                                              f"{_where}最高 {boards_i + _gt_i} 板，"
                                              f"比最高板低 {_gt_i} 板 —— 后排不做"
                                              f"（同题材后排 T+1 均 −1.62%、胜率 37.3%）")}
                    continue
        # 🧩 板块打分（精算版·覆盖前面那一版粗算）
        #    走到这里的票，`line_tier`（当日身位）与 `sector_support`（今早同板块红开）
        #    都已就绪 → 重新算一次，覆盖 base 里那份粗分。
        #
        #    ⛔ 仍然**只写字段**：不改 view、不改 position_cap、不改名额、不进任何闸。
        #       要进权重必须先证明分档单调（`scripts/research_theme_score_ab.py`），
        #       否则它就是个好看的数——比没有更糟，因为会让人以为系统真看了板块。
        if POOL_CALL_THEME_SCORE:
            try:
                _ssp = base.get("sector_support")
                _ssp = _ssp if isinstance(_ssp, dict) else {}
                base["theme_score"] = _ts.score(
                    theme_zt_count=_line_count.get(fam),
                    theme_max_boards=_line_max_boards.get(fam),
                    theme_depth=_line_depth.get(fam),
                    red_open=_ssp.get("red_open"),
                    mates_quoted=_ssp.get("mates_quoted"),
                    theme_name=str(fam or ""),
                )
            except Exception:                    # noqa: BLE001 —— 打分故障不许阻断裁决
                pass

        # ⑪d 🌊 独苗高开闸（2026-11-04 用户「独苗高开可以立马设，适合跑路」）
        #   用户原话：「独苗高开 + 同板块集体绿开」不是强势信号，是派发信号。」
        #
        #   依据（33 归档日 · 1789 个「≥1 板」样本 · 用 T+1 当日竞价截面重建，
        #        即 09:25 那刻**真实可观测**的信息，无未来函数）：
        #     ┌──────────────────────────────┬──────┬────────┬──────────┐
        #     │ 分组                          │ n    │ 晋级率  │ 胜率     │
        #     ├──────────────────────────────┼──────┼────────┼──────────┤
        #     │ 独苗高开(≥3% ∧ 同块红开<30%)   │ 21   │ 57.1%  │ 28.6%    │
        #     │ 其余                          │ 689  │ 20.8%  │ 43.7%    │
        #     └──────────────────────────────┴──────┴────────┴──────────┘
        #   ⇒ **晋级率高 36pp、胜率低 15pp** —— 又一个「晋级率与胜率反向」的形态。
        #     资金能在板块崩塌里把它硬拉上去（所以晋级率高），但拉上去是为了出货
        #     （所以买了不赚钱）。且 21 个里有 19 个次日高开 >5%，**根本买不到**。
        #
        #   ⛔ 边界（诚实）：
        #     · 可成交区间（0<gap≤5%）样本仅 n=2 —— **统计上不足以证明「买不到还硬拦」**，
        #       故本闸的正当性来自「不占名额 + 卡面警示」，不是「证明这一档必亏」。
        #     · 同块用 `theme_name`（炒作题材）；缺题材 / 同块不足 3 只 / 缺竞价 → 不裁决。
        #     · `POOL_CALL_LONE_GAP_GATE=0` 回退。
        if POOL_CALL_LONE_GAP_GATE and gap > LONE_GAP_MIN_PCT:
            # ⛔ 查找键必须与预计算同源（zt_rows 的 core_theme/sector），不能用
            #    `base.theme_name`（那是候选的 line_family，可能对不上 → 白名单失效）
            _fam_key = _code_theme.get(code) or str(base.get("theme_name") or "")
            _cand_ratios: list[tuple[str, int, float]] = []   # (来源, 其余票数, 红开率)
            _peers = _lone_peer_red_ratio.get(_fam_key)
            if _peers is not None:
                _tot, _red_all = _peers
                _n_other = _tot - 1
                if _n_other >= LONE_GAP_MIN_PEERS:
                    _cand_ratios.append(("题材", _n_other,
                                         (_red_all - (1 if gap > 0 else 0)) / _n_other))
            if POOL_CALL_NAME_CLUSTER:
                try:
                    from core import name_cluster as _nc2
                    for _ch in _nc2.cluster_map(theme_open_rows or []).get(code, []):
                        _lst = _lone_cluster_ratio.get(_ch) or []
                        _others = [g for cd, g in _lst if cd != code]
                        if len(_others) >= LONE_GAP_MIN_PEERS:
                            _rr = sum(1 for g in _others if g > 0) / len(_others)
                            _cand_ratios.append((f"名字「{_ch}」", len(_others), _rr))
                except Exception:
                    pass
            if _cand_ratios:
                _src, _pn, _pr = min(_cand_ratios, key=lambda x: x[2])   # 取最差那族
                if _pr < LONE_GAP_PEER_RED_MAX:
                    marks[code] = {
                        **base, "view": "watch", "lone_gap_gate": True,
                        "lone_peer_red_ratio": round(_pr, 4),
                        "lone_peer_n": _pn, "lone_peer_src": _src,
                        "reason": (f"开在 {gap:+.2f}%，但同{_src}其余 {_pn} 只只有 "
                                   f"{_pr * 100:.0f}% 红开 —— 整条线在退，"
                                   f"只有它一个撑着，这是派发不是强势")}
                    continue

        # ⑪c 🔮 预测档闸（2026-11-02 与选股卡共用同一模型·默认关）
        #   只拦一类：**历史崖位**——`boards ≥ 6` 且该档晋级率 < 25%（6进7 实测 12.5%、n=8）。
        #   ⛔ 不拦「放弃」档：那一档＝身位后排，已由 ⑪b 身位闸负责，重复拦＝同一件事判两次。
        #   ⛔ 不拦「只观察」的非崖位情形（多为「样本不足」，那是不知道，不是不行）。
        #   ⛔ 缺 `forecast`（旧候选文件 / 模型样本缺失）→ 一律放行，不裁决。
        if POOL_CALL_FORECAST_GATE and _fcst and not _fcst.get("error"):
            _fb_boards = int(_fcst.get("boards") or 0)
            _fb_promo = _fcst.get("promo_board")
            if (_fb_boards >= FORECAST_CLIFF_BOARDS
                    and isinstance(_fb_promo, (int, float))
                    and _fb_promo < FORECAST_CLIFF_PROMO_MAX):
                marks[code] = {**base, "view": "watch", "forecast_gate": True,
                               "reason": (f"开在 {gap:+.2f}% 达标，但这已经是 {_fb_boards} 板——"
                                          f"历史上到这一档只有 {_fb_promo}% 能再晋级 → 只观察")}
                continue
        # ⑫ 候选买入
        # 🎯 2026-11-05 竞价三态额外硬闸（默认关＝只标注；=1 才只买 A）：
        #    开启后只有 A 态能进系统买入；B/C 转为「人工二次机会」，⛔ 不写系统成交记录。
        #    ⛔ 放在全部既有闸之后：不越权放行任何未过闸的票，也不改 5% 硬线。
        #    ⛔ unknown 由 system_buy_eligible 放行（缺数据不裁决），不拿缺料当利空。
        if _ast.a_state_only() and not _ast.system_buy_eligible(base):
            _filtered_by_state.append(code)
            marks[code] = {**base, "view": "watch",
                           "reason": "竞价未达系统强势档 → 人工二次机会，系统不记录"}
            continue
        # 🚫 题材缺失不裁决（2026-11-12 用户「缺题材直接 pass」）
        if POOL_CALL_REQUIRE_THEME:
            _ct = str(c.get("core_theme") or "").strip().lower()
            if _ct in _THEME_MISSING:
                marks[code] = {**base, "view": "watch", "core_theme": c.get("core_theme"),
                               "reason": "缺题材归属（core_theme 为空/占位·行业兜底不算）→ 不进裁决，不买"}
                continue
        # 🥈 第二波 3 板危险节点（2026-11-12 用户口径：近一个月第一波走完 → 第二波 3 板只观察）
        if POOL_CALL_OLD_STOCK_OBSERVE:
            from core import second_wave as _sw
            _hit = _sw.second_wave(
                c.get("boards"), code,
                window=POOL_CALL_SECOND_WAVE_WINDOW,
                min_boards=POOL_CALL_SECOND_WAVE_MIN_BOARDS,
                prior_zt=POOL_CALL_SECOND_WAVE_PRIOR_ZT)
            if _hit:
                marks[code] = {**base, "view": "watch", "second_wave_node": True,
                               "prior_wave_zt": _hit["prior"], "streak": _hit["streak"],
                               "reason": _hit["note"] + "（2026-11-12 用户口径）"}
                continue
        # 🚫 首板只看不买（2026-11-12 用户「明明写只观察，结果被买了」）
        # 🌱 首板平开档例外（2026-11-17 用户选 B）：满足「平开 + 股性/成线」的首板可 25% 试仓
        if (not POOL_CALL_FIRST_BOARD_BUY and not _fb_flat
                and (str(c.get("watch_group") or "") == "首板观察"
                     or (str(c.get("boards") or "").strip() in ("1", "1.0")
                         and not c.get("new_direction_line")))):
            marks[code] = {**base, "view": "watch",
                           "watch_group": c.get("watch_group"),
                           "reason": "首板只看不买（2026-11-11 起尽量从 2 板开始看）"}
            continue
        entries.append({**base, "view": "participate"})
        marks[code] = {**base, "view": "participate"}

    # ── 排序 + 名额截取 ────────────────────────────────────────────────────
    # ⚖️ 催化排序权重：先建 code→rank（用候选行的真实题材，不是展示名）
    _cat_rank: dict[str, float] = {}
    if catalyst and CATALYST_SORT_WEIGHT:
        _lv = {str(k): str((v or {}).get("level") or "unknown")
               for k, v in (catalyst.get("themes") or {}).items()}
        for _r in (candidates.get("relay") or []) + (candidates.get("watch") or []):
            _code = str((_r or {}).get("code") or "")
            _th = str((_r or {}).get("core_theme") or (_r or {}).get("theme") or "").strip()
            if _code and _th:
                _cat_rank[_code] = CATALYST_RANK.get(_lv.get(_th, "unknown"), 1.0)

    def _key(x: dict[str, Any]) -> tuple:
        b = x.get("boards")
        try:
            bi = int(b) if b is not None and str(b).strip().isdigit() else 0
        except Exception:
            bi = 0
        try:
            lr = int(x.get("line_rank") or 99)
        except Exception:
            lr = 99
        # 融合口径：strong normal 与 focus 同档，不修改其盘后 bias。
        # 仅在机械强龙由可区分事实确认且开关开启时生效；所有风险短路已在入 entries 前执行。
        plan_or_mech_leader = (
            x.get("bias") == BIAS_FOCUS
            or (ENABLE_MECH_LEADER_FUSION and bool(x.get("mechanical_leader")))
        )
        # 🎯 身位档（2026-11-02 起**参与排序**·用户「高胜率优先」）
        #    ⛔ 与 2026-10-21 的旧注释相反：那时因单日一个反例（南威软件 +9.27% 被
        #    海正药业 +1.46% 顶掉）决定「身位只拦错、不改选」。现按 31 个交易日 / 218 个
        #    可执行样本重测，结论翻过来：同批样本只改排序 → 验证段日胜率 46.2%→61.5%、
        #    日均 −0.09%→+2.25%。单日反例抵不过 87 个验证样本的同向证据，故改。
        #    仍然只加一档「是否全市场最高板」，不做更细的身位加权（防过拟合）。
        _gt = x.get("gap_to_top")
        is_top_board = 1 if (POOL_CALL_TOP_BOARD_PRIORITY and _gt == 0) else 0
        from core import leader_cycle
        priority = leader_cycle.status_key(x) if leader_cycle.ENABLE_STATUS_PRIORITY else ()
        return priority + (
            is_top_board,                                # 🎯 全市场最高板优先（高胜率）
            1 if x.get("line_is_core") else 0,          # 同线最高板优先（核心 > 跟风）
            {"supportive": 1, "pressured": -1}.get(x.get("relay_mood"), 0),  # 同身份下看接力心态
            1 if plan_or_mech_leader else 0,              # focus 或机械强龙优先
            bi,                                           # 板数
            -lr,                                          # 同线身位（靠前优先）
            # 🎯 2026-11-05：A 态只作**末尾同分键**——名额受限时优先给竞价强势档，
            #    ⛔ 不推翻上面身位/龙一/核心等已由历史样本验证过的排序证据。
            1 if (_ast.a_state_priority()
                  and x.get("auction_state") == "A") else 0,
            # 🎯 11-05 21:45 高开降权：放在**末尾同分键**，只在身位/龙一/核心/板数
            #    全部打平时才让「开得离涨停更近」的票排后，⛔ 不推翻上面已验证的排序证据。
            #    ⚠️ POOL_CALL_UNLIMITED_SLOTS=1 时名额不截断 → 本项只影响 quota_rank/卡面顺序；
            #       真正生效的降权是末尾的 position_cap（仓位），别把它当成「少推一只」。
            x.get("gap_weight") if isinstance(x.get("gap_weight"), (int, float)) else 1.0,
            # ⚖️ 题材催化（LLM）：同档内 strong 优先，⛔ 只做同分键
            _cat_rank.get(str(x.get("code") or ""), 1.0),
        )

    from core import leader_cycle
    if leader_cycle.ENABLE_STATUS_PRIORITY:
        for rank, row in enumerate(sorted(marks.values(), key=_key, reverse=True), 1):
            row["status_rank"] = rank
        for entry in entries:
            entry["status_rank"] = marks[entry["code"]]["status_rank"]
    entries.sort(key=_key, reverse=True)

    # 🧠 智能评分层：p_win 只作为**最后一位 tie-break**（前面的机械键一个不动），
    #    shadow 模式只记录两套名单，⛔ 不改任何 view / 顺序。
    _ai_info: dict[str, Any] | None = None
    if advice and AI_TILT_MODE in ("shadow", "live"):
        _scores = {str(k): float(v) for k, v in (advice.get("scores") or {}).items()
                   if isinstance(v, (int, float))}
        _base = [e["code"] for e in entries]
        if _scores:
            _ranked = sorted(entries, key=lambda e: (_key(e), _scores.get(e["code"], 0.5)),
                             reverse=True)
        else:
            _ranked = list(entries)
        _ma = [e["code"] for e in _ranked]
        _abstain = [c for c in _ma if _scores.get(c, 1.0) <= AI_TILT_ABSTAIN_P]
        _ai_info = {
            "mode": AI_TILT_MODE,
            "model": advice.get("model"),
            "abstain_enabled": bool(AI_TILT_ABSTAIN),
            "abstain_threshold": AI_TILT_ABSTAIN_P,
            "scores": {c: _scores[c] for c in _ma if c in _scores},
            "arms": {"M": _base, "MA": _ma},
            "abstained": _abstain,
            "changed": _base != _ma,
        }
        if AI_TILT_MODE == "live":
            entries = _ranked                    # 只在同键组内改变先后
            if AI_TILT_ABSTAIN:
                for e in entries:
                    if e["code"] in _abstain:
                        e["ai_abstain"] = True

    for i, e in enumerate(entries):
        code = e["code"]
        if effective_quota <= 0:
            marks[code] = {**e, "view": "watch",
                           "reason": "昨天定的是空仓待命 → 今天不主动买（有破例通道时另说）"}
            continue
        # ⛔ 2026-10-21 用户「商业性项目，不要显示解释性质的语录」：
        #    原句「（机械强龙：09:30秒板，与重点看同档优先）」是系统机制解释——
        #    「机械强龙」「同档优先」都是内部排序术语，对外读者看不懂。
        #    改成只讲**事实**：它几点封的、带动了几家 —— 读者自己就能判断强不强。
        _fusion_note = (
            f"（{e.get('mechanical_leader_note')}）"
            if (ENABLE_MECH_LEADER_FUSION and e.get("mechanical_leader")
                and e.get("bias") != BIAS_FOCUS) else ""
        )
        _mood_note = ({"supportive": "（昨天同路径接力反馈转好）",
                       "pressured": "（昨天同路径反馈偏弱，今天竞价量已达计划）"}
                      .get(e.get("relay_mood"), ""))
        # 🎯 2026-11-02：半仓场景要**说人话**说清「为什么只买一半」，卡面与账本共用同一句。
        _half_note = ""
        if e.get("low_open_top"):
            _half_note = "（它是今天最高板、但低开超 3%，只买半仓试）"
        elif e.get("volume_soft"):
            _half_note = "（今天量能偏弱，只买半仓试）"
        # 🎯 2026-11-05：允许买时**不按名额数字截断**——达标几只买几只。
        #    ⛔ effective_quota == 0 已在上面短路（全池观察），此处不改变空仓语义。
        _unlimited = POOL_CALL_UNLIMITED_SLOTS and effective_quota >= 1
        # 🧪 题材催化强度（LLM）：shadow 只记录；live 且等级不足 → 不做。
        _cat_level = None
        if catalyst and CATALYST_MODE in ("shadow", "live"):
            try:
                from core import ai_advice as _aa
                _th = str(e.get("theme_name") or e.get("core_theme") or "")
                _cat_level = _aa.level_of(catalyst, _th)
            except Exception:  # noqa: BLE001
                _cat_level = None
            if (CATALYST_MODE == "live" and _cat_level is not None
                    and _cat_level not in ("strong", "normal", "unknown")
                    and CATALYST_MIN_LEVEL in ("strong", "normal")):
                marks[code] = {**e, "view": "watch", "catalyst_level": _cat_level,
                               "reason": f"这条线催化强度 {_cat_level} → 今天不做"}
                continue
        if e.get("ai_abstain"):
            marks[code] = {**e, "view": "watch",
                           "reason": (f"今天开在 {e['gap_pct']:+.2f}%，本来达标；"
                                      "但智能层判该档历史胜率极低 → 放弃")
                           if isinstance(e.get("gap_pct"), (int, float))
                           else "智能层判该档历史胜率极低 → 放弃"}
            continue
        if _unlimited or i < effective_quota:
            _open_note = "（昨涨停今早整体转强，空仓日只试这一只）" if open_relief else ""
            # 🔮 2026-11-02：把「开在这个幅度、历史上赚不赚」直接写进买入理由——
            #   这是选股卡那张「明早大概率开在 X%」在竞价卡上的落点，两张卡共用一套数字。
            #   ⛔ 只报**实测档**的历史胜率，不报预测值（预测已经被实测替代）。
            _band = e.get("forecast_band") or {}
            _band_note = ""
            if _band.get("n") and _band.get("win") is not None:
                _band_note = (f"（这个开盘幅度历史上 {_band['n']} 次里 "
                              f"{_band['win']:g}% 收涨、平均 {_band['avg']:+.2f}%"
                              + ("，是这票最赚的一档" if (_band.get("win") or 0) >= 60 else "")
                              + "）")
            # ⛔ 11-05 17:00 用户删掉「高开 5%」这条线后，旧文案「没超 5% 上限」
            #    在 +8.93% 的票上会变成**事实错误**（明明超了）。relative 模式改为
            #    「符合这只票的预期」（与 ai_caliber.decide 同源）；absolute 回退逐字保留旧句。
            _zone_reason = ("符合这只票的预期 → 买入" if _ac.relative_mode()
                            else "没超 5% 上限、也没低于预期 → 买入")
            if isinstance(e.get("deep_low_rescue"), dict):
                # 🕳️ 深低开豁免：⛔ 不能说「符合预期」（它明显低于预期），要说清是**被砸出的错杀**
                _zone_reason = "比它自己的预期低得多，属于被砸下来的错杀档 → 买入"
            elif isinstance(e.get("low_open_buy"), dict):
                # 🐾 中间档低开：⛔ 同样不能说「符合预期」——它确实低于预期，是**政策放行**
                _zone_reason = "低开、比它自己的预期低，但低开也买 → 半仓参与"
            marks[code] = {**e, "view": "participate", "quota_rank": i + 1,
                           **({"catalyst_level": _cat_level} if _cat_level else {}),
                           "reason": (f"今天开在 {e['gap_pct']:+.2f}%，"
                                      f"{_zone_reason}"
                                      + _band_note
                                      + ("（这条线里板数最高的那只）"
                                         if e.get("line_is_core") else "")
                                      + (f"（{e['sector_resonance'].get('line')} 共振 "
                                         f"{e['sector_resonance'].get('score')}/3·非核心半仓跟主线）"
                                         if isinstance(e.get("sector_resonance"), dict) else "")
                                      + _fusion_note + _mood_note + _half_note + _open_note)}
        else:
            marks[code] = {**e, "view": "watch",
                           "reason": (f"今天开在 {e['gap_pct']:+.2f}% 也达标，"
                                      f"但今天只买前 {effective_quota} 只、它排在后面 → 先看"
                                      + _fusion_note)}

    if POOL_CALL_CORE_FIRST:
        for row in marks.values():
            if row.get("core_identity", {}).get("is_core") and row["view"] != "participate":
                row["core_open_view"] = row["view"]
                row["view"] = "watch"
                row["core_manual_wait"] = True
                row["auto_low_buy"] = False
                row["reason"] += "；核心身份保留，人工等待合适价格，直接走强接受错过；不采分时、不自动低吸"

    # ── 🎯 单票场景（2026-11-04 用户「只能推一只票 仓位控死50%」）─────────────────
    # 判定口径＝**当日实际参与票数**（⛔ 不按名额/计划猜：名额 2 但只合格 1 只 →
    #   仍是单票场景）。观察池 observe_only 只会是 view=watch，天然不计入、不占名额。
    # 落 `single_pick` 标记 + 逐票 `position_cap`：本函数保持纯函数（不写盘），
    #   09:25 冻结后由卡面/账本消费（build_verify_card / ai_ledger）。
    # ⛔ 2026-11-11 修：原来按 `marks` 的插入顺序（＝池子顺序）取参与票，
    #    而 `quota_rank` 是按「同线优先 / 板数」重排后的序号 —— 两者可能不一致
    #    （11-11 实测：meta.participate = 600408,600609,603121,001318，
    #      quota_rank = 600408(1),001318(2),600609(3),603121(4)）。
    #    台账/名额/卡面都按「名额序」读，所以这里必须与 quota_rank 同序。
    _participants = sorted(
        (c for c, v in marks.items() if v.get("view") == "participate"),
        key=lambda c: (marks[c].get("quota_rank") or 999),
    )
    _single_pick = len(_participants) == 1
    _single_cap = _rp.single_pick_cap(relay_plan)
    _n_pick = len(_participants)
    _even_cap: float | None = None
    if POOL_CALL_EVEN_ALLOCATION and _n_pick >= 1:
        # 🎯 平均分配（2026-11-05 用户「不限制只数·单票最多50%·平均分配仓位买入」）：
        #   每票上限 = min(单票硬顶 50%, 100%/N)。N=1 → 50%（不打满 100%），
        #   N=2 → 各 50%，N=3 → 各 33%，N=4 → 各 25%。⛔ 只封顶，不强制打满。
        #   个股已有的更严上限（低开半仓 / 隔壁半仓 / 量能档降仓）继续取小。
        _even_cap = round(min(_rl.MAX_POSITION_PER_STOCK, 1.0 / _n_pick), 4)
        if (POOL_CALL_EVEN_RESPECT_DRAWDOWN
                and isinstance(_single_cap, (int, float)) and _single_cap > 0):
            _even_cap = round(min(_even_cap, float(_single_cap)), 4)
        for _code in _participants:
            _v = marks[_code]
            _scale = 1.0
            if POOL_CALL_VOLUME_SCALE_POSITION:
                _s = _v.get("volume_scale")
                if isinstance(_s, (int, float)) and 0 < float(_s) < 1:
                    _scale = float(_s)
            # 🎯 高开接近涨停 → 降仓（11-05 21:45）。与量能档**取小**，⛔ 不相乘：
            #    同一笔仓位被两个理由各打一次折会把试错仓压到没有意义。
            if POOL_CALL_GAP_WEIGHT_POSITION and _ac.AI_GAP_WEIGHT:
                _gw = _v.get("gap_weight")
                if isinstance(_gw, (int, float)) and 0 < float(_gw) < 1:
                    _scale = min(_scale, float(_gw))
            # 📏 身位降权（11-06 用户 A）：同题材身位差越靠后买得越轻。
            #    ⛔ 同样**取小不相乘**：身位 + 量能 + 高开三个理由叠加相乘会把
            #       试错仓压到没有意义（与上面 gap_weight 同一条纪律）。
            if POOL_CALL_POSITION_WEIGHT_POSITION and _ac.AI_POSITION_WEIGHT:
                _pw = _v.get("position_weight")
                if isinstance(_pw, (int, float)) and 0 < float(_pw) < 1:
                    _scale = min(_scale, float(_pw))
            _cap = round(_even_cap * _scale, 4)
            # ⚖️ 题材催化仓位权重（strong 1.0 / normal 0.85 / weak 0.6 / unknown 1.0）
            _cat_mult = 1.0
            if CATALYST_POSITION_WEIGHT and _v.get("catalyst_level"):
                _cat_mult = CATALYST_SIZE.get(str(_v["catalyst_level"]), 1.0)
                _cap = round(_cap * _cat_mult, 4)
            # 🧊 一字板无换手：仓位减半（2026-11-11 用户 D）
            _one_word = bool(_v.get("is_one_word")) and POOL_CALL_ONE_WORD_DISCOUNT
            if _one_word:
                _cap = round(_cap * 0.5, 4)
            _prev_cap = _v.get("position_cap")
            if isinstance(_prev_cap, (int, float)) and _prev_cap > 0:
                _cap = round(min(_cap, float(_prev_cap)), 4)
            _v["position_cap"] = _cap
            _v["even_allocation"] = True
            _v["allocation_count"] = _n_pick
            # ⛔ 账本 ai_ledger 靠逐票 single_pick 识别仓位重裁，双轨必须同值。
            if _single_pick:
                _v["single_pick"] = True
            # 🎯 高开降权只讲**事实 + 真实仓位**，⛔ 不露「权重系数」这类内部口径。
            #    ⛔ 同一事实上限只写一次：降权句已带上限，就不再叠「均分·每只上限」。
            _gw_active = bool(POOL_CALL_GAP_WEIGHT_POSITION and _ac.AI_GAP_WEIGHT
                              and _v.get("gap_weight_active") and _v.get("gap_weight_note"))
            _gw_note = (f"（{_v['gap_weight_note']}，单票上限 {_rl.pct_of(_cap)}）"
                        if _gw_active else "")
            # 📏 身位降权同理：只讲事实（同题材里排第几）+ 真实上限，⛔ 不露权重系数。
            _pw_active = bool(POOL_CALL_POSITION_WEIGHT_POSITION and _ac.AI_POSITION_WEIGHT
                              and 0 < float(_v.get("position_weight") or 1) < 1
                              and _v.get("position_weight_label"))
            _pw_note = (f"（{_v['position_weight_label']}，单票上限 {_rl.pct_of(_cap)}）"
                        if _pw_active else "")
            _alloc_note = "" if (_gw_active or _pw_active) else (
                f"（今日仅1只·单票上限 {_rl.pct_of(_cap)}）" if _single_pick
                else f"（{_n_pick}只均分·每只上限 {_rl.pct_of(_cap)}）")
            _ow_note = ("（一字板无换手，先观察能否开板换手，仓位减半）"
                        if _one_word else "")
            _cat_note = ("" if _cat_mult >= 1 else
                         f"（题材催化 {_v.get('catalyst_level')} → 仓位 ×{_cat_mult}）")
            _v["reason"] = ((_v.get("reason") or "") + _alloc_note + _gw_note + _pw_note + _ow_note + _cat_note)
    elif _single_pick and isinstance(_single_cap, (int, float)) and _single_cap > 0:
        _code = _participants[0]
        _v = marks[_code]
        _prev_cap = _v.get("position_cap")
        _cap = (float(_single_cap) if not isinstance(_prev_cap, (int, float))
                else min(float(_prev_cap), float(_single_cap)))
        # 📏 身位降权同样作用于单票分支（⛔ 两个分支必须同值，否则账本/卡面双轨）
        if POOL_CALL_POSITION_WEIGHT_POSITION and _ac.AI_POSITION_WEIGHT:
            _pw1 = _v.get("position_weight")
            if isinstance(_pw1, (int, float)) and 0 < float(_pw1) < 1:
                _cap = min(_cap, round(float(_single_cap) * float(_pw1), 4))
        if bool(_v.get("is_one_word")) and POOL_CALL_ONE_WORD_DISCOUNT:
            _cap = round(float(_cap) * 0.5, 4)
            _v["reason"] = ((_v.get("reason") or "")
                            + "（一字板无换手，先观察能否开板换手，仓位减半）")
        if CATALYST_POSITION_WEIGHT and _v.get("catalyst_level"):
            _cm = CATALYST_SIZE.get(str(_v["catalyst_level"]), 1.0)
            if _cm < 1:
                _cap = round(float(_cap) * _cm, 4)
                _v["reason"] = ((_v.get("reason") or "")
                                + f"（题材催化 {_v['catalyst_level']} → 仓位 ×{_cm}）")
        _v["position_cap"] = round(_cap, 4)
        _v["single_pick"] = True
        # 交易纪律同步：竞价理由直说「今天只有这一只 + 单票上限」，与卡面/账本同值。
        _pw_only = (f"（{_v['position_weight_label']}，单票上限 {_rl.pct_of(_cap)}）"
                    if (POOL_CALL_POSITION_WEIGHT_POSITION and _ac.AI_POSITION_WEIGHT
                        and 0 < float(_v.get("position_weight") or 1) < 1
                        and _v.get("position_weight_label"))
                    else f"（今日仅1只·单票上限 {_rl.pct_of(_cap)}）")
        _v["reason"] = ((_v.get("reason") or "") + _pw_only)

    # 🎯 2026-11-05 用户「其他机会交给人工二次自己交易、不做记录」：
    #    在**最终 view 确定之后**才落 manual_only，参与票恒为 False，
    #    ⛔ 避免二次否决 11-05 刚打通的观察票竞价复核翻案链。
    if _ast.enabled():
        for _v in marks.values():
            _v["manual_only"] = _ast.manual_only_of(_v)

    # ── 🚦 三态（2026-11-11 用户：「不能都塞进买或放弃」）──────────────
    #  ⛔ **只加字段、只上卡面**：`view` 一个都不改、名额/仓位/账本零影响。
    #    A 可买        = 今天真的买了
    #    B 核心在·价不合适 = 被顶部空间/高开闸拦下（核心没坏，只是这个价不值得）
    #    C 核心失效     = 锚点破预期 / 核心不在（这条线今天不该做）
    _state_of: dict[str, str] = {}
    for _c, _v in marks.items():
        if _v.get("view") == "buy":
            _state_of[_c] = "A"
        elif _v.get("high_gap_reject"):
            _state_of[_c] = "B"
        elif _v.get("anchor_gate") == "break" or _v.get("core_first_gate"):
            _state_of[_c] = "C"
        else:
            _state_of[_c] = ""
        _v["trade_state"] = _state_of[_c]

    # ── 🧾 替代资格（2026-11-11 用户：「其他票必须有独立成立依据，不能因为核心太贵就获得资格」）
    #  ⛔ 仍只标记、不拦买：`view` 不动。卡面把「它自己是龙一/板块龙」写出来，
    #     没有独立依据的替补票必须被读者看见（否则就是这次事故的翻版）。
    _by_code = {str(c.get("code") or ""): c for c in pool}
    for _c, _v in marks.items():
        if _v.get("view") != "buy":
            continue
        _cand = _by_code.get(_c) or {}
        _fam = line_family(_cand) if _cand else ""
        _lc = line_cores.get(_fam) or {}
        _core_code = str(_lc.get("code") or "")
        if not _core_code or _core_code == _c:
            continue
        _core_mark = marks.get(_core_code) or {}
        if _core_mark.get("view") == "buy":
            continue                      # 核心今天也买了，这不是替补
        _indep = (_cand.get("dragon_rank") == 1) or bool(_cand.get("is_sector_dragon"))
        _v["substitute"] = {
            "core_code": _core_code,
            "core_name": str(_lc.get("name") or ""),
            "core_view": _core_mark.get("view") or "",
            "core_state": _core_mark.get("trade_state") or "",
            "independent": bool(_indep),
            "basis": ("该线龙一" if _cand.get("dragon_rank") == 1
                      else "板块龙" if _cand.get("is_sector_dragon") else ""),
        }

    # 🧮 竞价广度：只在 09:25 有竞价快照时算；失败不影响裁决（卡面少一行）
    try:
        _breadth_lines = load_breadth(_date10, qs, line_cores) if POOL_CALL_BREADTH else {}
    except Exception:
        _breadth_lines = {}

    # 🧮→🚦 广度接入三态（2026-11-11 用户拍板）：整条线竞价偏弱的，
    #    没买到的票一律记 C（这条线今天不该做）——⛔ 只影响展示态，`view` 不动。
    for _c, _v in marks.items():
        if _v.get("trade_state") == "A":
            continue
        _cand = _by_code.get(_c) or {}
        _b = _breadth_lines.get(line_family(_cand)) if _cand else None
        if isinstance(_b, dict) and _b.get("applicable") and _b.get("level") == "weak":
            _v["trade_state"] = "C"
            _v["trade_state_note"] = "这条线竞价广度偏弱（后排大面积水下）"

    meta = {
        "caliber": "pool_call_925",
        "stance": _stance,
        "quota": quota,
        "effective_quota": effective_quota,
        "market_open_relief": open_relief,
        "market_open": open_read or None,
        "pool_size": len(pool),
        "participate": _participants,
        # 🏷 顺序口径标记（2026-11-11）：旧冻结件没有这个字段、顺序可能不一致，
        #    终端不变量测试只对带标记的文件强制「participate 与 quota_rank 同序」。
        "participate_order": "quota",
        # 🔁 观察票竞价复核留痕（11-05 A 方案）：卡片/日志可直接答「今天复核了几只、
        #    几只进了完整裁决、最终几只是靠竞价翻案买到的」，⛔ 不参与二次裁决。
        "recheck": {
            "enabled": bool(POOL_CALL_RECHECK_OBSERVE),
            "state_full_required": bool(POOL_CALL_RECHECK_STATE_FULL),
            "observed": sum(1 for v in marks.values() if v.get("auction_recheck")),
            "state_entered": sum(1 for v in marks.values()
                                 if v.get("rechecked_from_observe")),
            "participated": [c for c, v in marks.items()
                             if v.get("view") == "participate"
                             and v.get("rechecked_from_observe")],
            # 🧬 强基因豁免留痕（2026-11-10）：今天有几只靠股性让开了「须满额量」这条闸，
            #    其中几只最终真买到了。⛔ 只读诊断，不参与二次裁决。
            "gene_exempt_enabled": bool(POOL_CALL_GENE_EXEMPT),
            "gene_exempt": [c for c, v in marks.items() if v.get("gene_exempt")],
            "gene_exempt_bought": [c for c, v in marks.items()
                                   if v.get("gene_exempt")
                                   and v.get("view") == "participate"],
        },
        # 🎯 11-05 用户删除 5% 与红开两条绝对线后的主判据留痕（只读诊断）。
        "expected_gap": {
            "mode": "relative" if _ac.relative_mode() else "absolute",
            "dev_min": _ac.EXPECTED_GAP_DEV_MIN,
            "definition": ("DEV=gap-E, E=昨日封板质量算出的逐票预期开幅; "
                           "DEV<dev_min→明显低于预期放弃; 其余(高开/低开均含)→符合; "
                           "竞价封死(>=涨停-0.2pct)→买不进; 缺E→不裁决; "
                           "⛔ 无5%高开线、无必须红开线"),
            "low_open_guard": bool(POOL_CALL_LOW_OPEN_GUARD),
            # 🐾 中间档低开放行（11-11）：开关 + 今天真被放行/真买到的分别是哪几只。
            "low_open_buy": {
                "enabled": bool(POOL_CALL_LOW_OPEN_BUY),
                "position_cap": LOW_OPEN_BUY_POSITION,
                "released": [c for c, v in marks.items() if v.get("low_open_buy")],
                "bought": [c for c, v in marks.items()
                           if v.get("low_open_buy") and v.get("view") == "participate"],
            },
            "low_open_expected": sum(1 for v in marks.values()
                                     if v.get("low_open_expected")),
            "zones": {k: sum(1 for v in marks.values() if v.get("zone") == k)
                      for k in ("below", "ok", "above", "unknown")},
            "covered": sum(1 for v in marks.values()
                           if v.get("expected_gap_pct") is not None),
            "missing": sum(1 for v in marks.values()
                           if v.get("expected_gap_pct") is None),
        },
        # 🎯 9:25 竞价三态留痕（2026-11-05）：只读诊断，⛔ 不参与二次裁决。
        #    默认影子：只标注分层、旧链照买，`filtered` 恒空；
        #    置 `POOL_CALL_A_STATE_ONLY=1` 后，`filtered` 列出「旧链会买、A 闸不买」的票供逐日对照。
        "auction_state": {
            "enabled": bool(_ast.enabled()),
            "a_state_only": bool(_ast.a_state_only()),
            "quality_expected_gap": bool(_eg.enabled()),
            "definition": ("R=竞价额/昨全天成交额×100; "
                           "E=昨日封板质量算出的逐票预期开幅; DEV=gap-E; "
                           "A=DEV>=0.5且R>=3; B=DEV>=-2且R>=3; "
                           "竞价封死→C; ⛔ 无5%与红开线"),
            "expected_gap_covered": sum(1 for v in marks.values()
                                        if v.get("expected_gap_pct") is not None),
            "expected_gap_missing": sum(1 for v in marks.values()
                                        if v.get("expected_gap_pct") is None),
            "counts": {k: sum(1 for v in marks.values()
                              if v.get("auction_state") == k)
                       for k in ("A", "B", "C", "unknown")},
            "system": [c for c, v in marks.items()
                       if v.get("view") == "participate" and v.get("auction_state") == "A"],
            "manual_only": [c for c, v in marks.items() if v.get("manual_only")],
            "filtered": list(_filtered_by_state),
        },
        # 🧮 当日整体开盘偏移（2026-11-06）：池内中位开幅 + 实际生效的偏移量。
        #    ⛔ 只读诊断：被它影响的是 `zone`（已写进逐票 `dev_env_offset`）。
        "dev_env_offset": dict(_dev_env_info),
        # 🕳️ 深低开豁免留痕（2026-11-06）：开关 / 阈值 / 当日被豁免的票。
        #    ⛔ 只读诊断：⛔ 不参与二次裁决，逐票明细在 `views[code].deep_low_rescue`。
        "deep_low_rescue": {
            "enabled": bool(POOL_CALL_DEEP_LOW_RESCUE),
            "threshold": DEEP_LOW_OPEN_DEV,
            "rescued": [c for c, v in marks.items()
                        if isinstance(v.get("deep_low_rescue"), dict)],
        },
        # 🎯 名额策略留痕（2026-11-04）：原 stance 名额 / 最终名额 / 是否放宽到 2 /
        #    真实达标票数 / 是否单票场景。诊断只读，⛔ 不参与二次裁决。
        "slot_policy": {
            "min_two_slots": bool(POOL_CALL_MIN_TWO_SLOTS),
            "min_two_slots_target": int(POOL_CALL_MIN_TWO_SLOTS_TARGET),
            "min_two_slots_applied": _min_two_applied,
            "requested_quota": int(_requested_quota),
            "effective_quota": int(effective_quota),
            "participate_count": len(_participants),
            "single_pick": bool(_single_pick),
            "single_pick_cap": _single_cap,
            # 🎯 2026-11-05：不限制只数 + 平均分配（卡面/账本读这几个字段，⛔ 别再读旧档位）
            "unlimited_slots": bool(POOL_CALL_UNLIMITED_SLOTS),
            "even_allocation": bool(POOL_CALL_EVEN_ALLOCATION),
            "even_cap": _even_cap,
            "allocation_count": _n_pick,
            # 🎯 11-05 21:45 高开降权留痕（只读对账：谁被降了、降到多少仓）
            "gap_weight_enabled": bool(POOL_CALL_GAP_WEIGHT_POSITION and _ac.AI_GAP_WEIGHT),
            "gap_weight_reduced": [c for c in _participants
                                   if marks[c].get("gap_weight_active")],
            "gap_weight_caps": {c: marks[c].get("position_cap")
                                for c in _participants
                                if marks[c].get("gap_weight_active")},
            # 📏 11-06 身位降权留痕（分母＝同题材最高板；只降仓不剔除）
            "position_gap_hard": bool(POOL_CALL_POSITION_GAP_HARD),
            "position_weight_enabled": bool(POOL_CALL_POSITION_WEIGHT_POSITION
                                            and _ac.AI_POSITION_WEIGHT),
            "position_weight_reduced": [c for c in _participants
                                        if 0 < float(marks[c].get("position_weight") or 1) < 1],
            "position_weight_caps": {c: marks[c].get("position_cap")
                                     for c in _participants
                                     if 0 < float(marks[c].get("position_weight") or 1) < 1},
            "position_gap_basis": {c: (marks[c].get("position_gap") or {}).get("basis")
                                   for c in marks
                                   if marks[c].get("position_gap")},
        },
        "single_pick": bool(_single_pick),
        # 🛡️ 防守闸门留痕（2026-10-08 经验）：环境档位 + 两道硬闸各拦了谁
        "defense": {
            "enabled": bool(POOL_CALL_DEFENSE_GATE),
            "env": env_read,
            "distribution_blocked": [c for c, v in marks.items()
                                     if v.get("defense_gate") == "distribution"],
            "distribution_thin_late_blocked": [c for c, v in marks.items()
                                               if v.get("defense_gate")
                                               == "distribution_thin_late"],
            "sector_drain_blocked": [c for c, v in marks.items()
                                     if v.get("defense_gate") == "sector_drain"],
            "position_blocked": [c for c, v in marks.items()
                                 if v.get("defense_gate") == "high_position"],
            "warned": [c for c, v in marks.items()
                       if v.get("defense_warn") or v.get("position_warn")],
            "sector_flow_boards": len(sector_flow),
            "zt_history_days": len(zt_days),
            # 🚧 后排接力闸留痕（2026-10-21）：拦下了谁、谁因为核心买不到获得例外
            "backrow": {
                "enabled": bool(POOL_CALL_BACKROW_GATE),
                "blocked": [c for c, v in marks.items() if v.get("backrow_gate")],
                "tier": {c: v.get("line_tier") for c, v in marks.items() if v.get("line_tier")},
                "relief": {c: v.get("backrow_relief") for c, v in marks.items()
                           if v.get("backrow_relief")},
            },
            # ⚓ 锚点留痕（2026-10-21）：每条线昨天的锚点今天怎么开 + 全局判断
            "anchor": {
                "enabled": bool(POOL_CALL_ANCHOR_GATE),
                "lines": _anchors,
                "blocked": [c for c, v in marks.items() if v.get("anchor_gate")],
                "flat": [c for c, v in marks.items() if v.get("anchor_flat")],
                "market": _ac2.market_verdict([a.get("attitude") for a in _anchors.values()]),
            },
            # 🧠 智能层留痕（2026-11-11）：两套名单 + 分数 + 是否改序
            # 🚦 三态留痕（2026-11-11）：只做展示与复盘，⛔ 不参与任何判据
            "trade_states": {c: v.get("trade_state") for c, v in marks.items()
                             if v.get("trade_state")},
            # 🧾 替补留痕：今天买了、但这条线的核心没买
            "substitutes": {c: v.get("substitute") for c, v in marks.items()
                            if v.get("substitute")},
            # 🧮 竞价广度（2026-11-11）：这条线昨日全体涨停票今天开在哪
            "breadth": {
                "enabled": bool(POOL_CALL_BREADTH),
                "min_size": BREADTH_MIN_SIZE,
                "lines": _breadth_lines,
            },
            # 🎯 中位股留痕（2026-10-21）：拦了谁 + 参照的市场高度
            "midboard": {
                "enabled": bool(POOL_CALL_MIDBOARD_GATE),
                "market_max_boards": _market_max_boards,
                "band": _cp.mid_band(_market_max_boards) if _market_max_boards else None,
                "blocked": [c for c, v in marks.items() if v.get("midboard_gate")],
            },
        },
        # 🎯 早盘接力·动态仓位（2026-10-13 用户需求）留痕：名额被动态方案裁断的结果
        "relay_position": (None if relay_plan is None else {
            "enabled": bool(relay_plan.get("enabled", True)),
            "tier": relay_plan.get("tier"),
            "strength": relay_plan.get("strength"),
            "quota": relay_plan.get("quota"),
            "per_pick_cap": relay_plan.get("per_pick_cap"),
            "total_cap": relay_plan.get("total_cap"),
            "can_buy": relay_plan.get("can_buy"),
            "drawdown_pct": relay_plan.get("drawdown_pct"),
            "drawdown_level": relay_plan.get("drawdown_level"),
            "theme_confidence": relay_plan.get("theme_confidence"),
            # 🎯 单票场景上限（2026-11-04）：与卡面/账本同源，禁在下游另算
            "single_pick_cap": _single_cap,
        }),
        # 🧪 题材催化强度（LLM · 顶层）：mode/levels/by_pick
        # 🧪 题材催化强度（LLM）：shadow 只留痕
        "catalyst": (None if catalyst is None else {
            "mode": CATALYST_MODE,
            "min_level": CATALYST_MIN_LEVEL,
            "source": catalyst.get("source"),
            "model": catalyst.get("model"),
            "levels": {str(k): (v or {}).get("level") for k, v in
                       (catalyst.get("themes") or {}).items()},
            "by_pick": {c: v.get("catalyst_level") for c, v in marks.items()
                        if v.get("catalyst_level")},
        }),
        # 🧠 智能层留痕（顶层）：两套名单 + p_win + 是否改序
        **({"ai_layer": _ai_info} if _ai_info is not None else {}),
        "call_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        # 🌊 周期位置留痕（2026-10-21 P1）：**只记录、不参与裁决**。
        #    写进 meta 是为了让决策日志/复盘能回答「当时是什么周期位置、所以这么判」；
        #    ⛔ 不接进名额或逐票判定（位置判断置信度低于硬数据，让它决策风险大于收益）。
        "cycle_phase": _cycle_info(),
    }
    # 🎯 11-05 A 档：三环联动统计只进 meta，⛔ 开关关时整节不存在（旧消费方零感知）。
    if _sgc_on:
        meta["sector_gap_context"] = _sgc.summary(_sgc_ctx, set(marks))
    return {"enabled": True, "stance": _stance, "quota": quota,
            "effective_quota": effective_quota, "single_pick": bool(_single_pick),
            "views": marks, "meta": meta}


def apply_to_views(pool_result: dict[str, Any], views: dict[str, Any] | None) -> dict[str, Any]:
    """把 9:25 裁决结论合并进 `ai_view.views`（供卡面/结算共用同一份结论）。

    · 池内票：`view` 覆写为 9:25 结论；原盘后预案保留在 `bias` / `plan_view` / `reason` 里
      （卡面要同时展示「昨日预案」与「今日实判」两个时点）。
    · 池外票 / 未裁决票：原样保留（不回退、不删）。
    返回新的 views dict（不就地修改入参）。
    """
    out: dict[str, Any] = {k: dict(v or {}) for k, v in (views or {}).items()
                           if isinstance(v, dict)}
    for code, m in (pool_result.get("views") or {}).items():
        cur = out.setdefault(code, {})
        prev_view = str(cur.get("view") or "").strip()
        if prev_view and not cur.get("plan_view"):
            cur["plan_view"] = prev_view          # 留痕：盘后预案的旧口径终态
        cur["bias"] = m.get("bias")
        cur["view"] = m.get("view")
        cur["reason_925"] = m.get("reason")
        cur["gap_725"] = m.get("gap_pct")
        cur["zone_925"] = m.get("zone")
        cur["line_is_core"] = m.get("line_is_core")
        cur["line_core"] = m.get("line_core")
        cur["follower_gate"] = bool(m.get("follower_gate"))
        cur["linkage_gate"] = bool(m.get("linkage_gate"))
        cur["linkage_discipline"] = m.get("linkage_discipline")
        cur["defense_gate"] = m.get("defense_gate")   # distribution / distribution_thin_late / sector_drain / high_position / old_dragon_rebound
        # 🚦 2026-11-11 三态与替补：只给卡面/复盘读，⛔ 不进任何判据
        cur["trade_state"] = m.get("trade_state")
        cur["substitute"] = m.get("substitute")
        cur["defense_reason"] = (m.get("defense_detail") or {}).get("note") or None
        cur["defense_warn"] = m.get("defense_warn")
        cur["position_warn"] = m.get("position_warn")
        cur["theme_name"] = m.get("theme_name")
        # 🎯 11-05 竞价三态：卡面文案不展示内部档位，但决策行要带 manual_only，
        #    供 ai_caliber.decide / 账本统一拦住「人工二次机会」不写系统成交。
        # ⛔ `is_20cm` 必须透传：decide 的物理封死线要靠它取 20% 而非 10%，
        #    否则创业板票开 +15% 会被误当「已封涨停买不进」。
        #    `low_open_expected` 只作反证留痕（默认不否决）。
        for _k in ("auction_state", "auction_r", "auction_route",
                   "auction_state_reason", "manual_only",
                   "expected_gap_pct", "gap_vs_expected",
                   "is_20cm", "low_open_expected", "low_open_guard",
                   "core_identity", "core_first_gate", "core_manual_wait",
                   "core_open_view", "auto_low_buy", "gene_leader_override", "gene_demoted"):
            if m.get(_k) is not None:
                cur[_k] = m.get(_k)
        if not cur.get("name"):
            cur["name"] = m.get("name")
    return out
