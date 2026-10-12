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
  ④ 低于预期区间下沿    → 放弃（不及预期）
  ⑤ 没红开（gap ≤ 0）   → 观察
  ⑥ 红开但量能不足      → 观察（无量的高开＝诱多）
  ⑦ 同线核心已断板的跟风 → 观察（跟风闸·不占名额）
  ⑧ 所属方向资金失血     → 观察（方向净流出 ≥30 亿·不占名额）        ← 土壤
  ⑨ 位置高 + 二波反抽    → 观察（断板 ≥3 个交易日后重启 ∧ 上一波最高板 ≥3·不占名额）← 位置
  ⑩ 前一日派发特征       → 观察（换手大 ∧ 封单薄 ∧ 封得晚·三条全中·不占名额）← 盘口
  ⑪ 其余                → 候选
  候选先按「地位 → 带动 → 连续性 → 竞争身位」，再沿用同线核心、预案/机械强龙等旧排序键，取前 N 只 → 参与；
  其余候选 → 观察（附「池内今天只买前 N 只」）。
  ⛔ ⑦~⑩ 的判据全部来自**前一交易日收盘**（09:25 时点已可得），不看今日盘中——所以它们能
     在竞价落地的那一刻就生效，而不是收盘后才后悔（2026-10-08 回撤教训）。
  ⚠️ ⑦~⑩ 的先后＝「宏观到微观」：板块有没有钱（⑧）→ 这票在什么位置（⑨）→ 它昨天封得脏不脏（⑩）。
     位置必须排在派发前面，否则先被「换手大/封单薄」命中，卡面只说症状、说不出真原因。

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
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent          # wealth_code/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import ai_caliber as _ac                      # noqa: E402
from core import defense_gate as _dg                    # noqa: E402

# ── 开关（架构改动必留可逆开关）─────────────────────────────────────────────
# True  = 9:25 池内裁决（本模块接管「买哪只」）
# False = 一键回退旧口径（读盘后 ai_view 的 view 终态，行为与 2026-09-21 前逐字一致）
POOL_CALL_ENABLED = True

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

# 回看天数（找「同一条线的核心」——含已断板的核心）
LINE_CORE_LOOKBACK_DAYS = 4

# ── AI × 机械强龙融合（2026-09-24 用户确认）────────────────────────────────────
# 盘后 AI 的 bias 是预案，不可用软理由覆盖候选已验证的强龙事实。融合只影响 09:25
# 名额排序：强龙 normal 与 focus 同档优先；avoid 仍由既有硬伤守卫和短路规则处理。
# 开关关掉可逐字回退 09-24 前的排序（同线核心 → focus → 板数 → 同线身位）。
ENABLE_MECH_LEADER_FUSION = True
MECH_LEADER_SECOND_BOARD_MAX_FBT = 930
MECH_LEADER_MIN_CARRY = 4
MECH_LEADER_MIN_TURNOVER_YI = 5.0

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
CORE_GATE_MIN_GAP = 2
# ── 归并一致性守卫（2026-10-12 传媒事故结构性兜底）────────────────────────────
# 跟风闸拦票前，校验候选 `core_theme` 归线 与 `sector` 归线是否**矛盾**——
# 矛盾（如 core_theme 被误标「AI算力」而 sector=数字媒体→传媒）说明题材归并可疑
# → ⛔ 不硬拦（防「一次归并错误就整条误杀」，10-12 中文在线/掌阅被误杀即此）。
# ⛔ 可逆：False → 回到只看「板数差 ≥2 + 核心已断板」。
POOL_CALL_LINE_CONSISTENCY_GATE = True

# ── 🛡️ 买入前防守闸门（2026-10-08 大幅回撤经验固化·见 core/defense_gate.py）──────
# 实证：10-08 只买两只（襄阳轴承 −5.85% / 南华生物 −8.18%，平均 −7.02%，净值 1.058→0.984）。
# 两只票在**前一日收盘**就同时具备「换手 >15% + 封单/流通 <2% + 10:00 后才封板」三条特征，
# 而同日走出来的票三条命中 0~2 条。→ 09:25 竞价达标**不再等于**可以买：
#   · 派发特征（三条全中）→ 降级观察、**不占当日名额**（名额让给封板质量好的票）
#   · 方向资金失血（所属方向昨日净流出 ≥30 亿）→ 同上
#   · 位置高 + 二波反抽（断板 ≥3 个交易日后重启）→ 同上（2026-10-08 补·南华案例）
#   · 环境差（昨收 + 今开双腿判据命中 ≥2/≥3 条）→ 名额压到 1 只 / 当日不主动买
# ⛔ 可逆：`POOL_CALL_DEFENSE_GATE=False` → 逐字回退 10-08 前的裁决行为。
POOL_CALL_DEFENSE_GATE = True
POOL_CALL_DEFENSE_DISTRIBUTION = True
POOL_CALL_DEFENSE_SECTOR_DRAIN = True
POOL_CALL_DEFENSE_POSITION = True
POOL_CALL_DEFENSE_ENV = True

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
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _zt_rows(z: dict[str, Any]) -> list[dict[str, Any]]:
    rows = z.get("zt_list") or []
    return [r for r in rows if isinstance(r, dict)]


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
    picked: list[tuple[str, Path]] = []
    for f in reversed(files):
        day = f.stem[3:]                                   # zt_2026-09-18 → 2026-09-18
        if len(day) != 10 or day > str(date10):
            continue
        picked.append((day, f))
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
    for day, f in picked:
        z = _read_zt(f)
        if not z:
            continue
        for r in _zt_rows(z):
            code = str(r.get("code") or r.get("dm") or "").strip()
            if len(code) != 6:
                continue
            fam = line_family(r)
            b = _boards(r)
            rec = per_code.get(code)
            if rec is None:
                per_code[code] = {
                    "code": code, "name": str(r.get("name") or r.get("mc") or ""),
                    "family": fam, "boards": b, "last_date": day,
                    "last_boards": b, "days": 1,
                }
                continue
            if b > int(rec["boards"]):                     # 窗口内最高板
                rec["boards"] = b
            if day > str(rec["last_date"]):                # 该票自己最近一次涨停
                rec["last_date"] = day
                rec["last_boards"] = b
                rec["family"] = fam
            rec["days"] = int(rec.get("days") or 1) + 1

    out: dict[str, dict[str, Any]] = {}
    for rec in per_code.values():
        fam = str(rec.get("family") or "")
        cur = out.get(fam)
        # 线核心 = 该线窗口内板数最高；同板数取最近仍在板的
        better = (cur is None
                  or int(rec["boards"]) > int(cur["boards"])
                  or (int(rec["boards"]) == int(cur["boards"])
                      and str(rec["last_date"]) > str(cur["last_date"])))
        if better:
            out[fam] = {
                "code": rec["code"], "name": rec["name"], "boards": rec["boards"],
                "last_date": rec["last_date"], "last_boards": rec["last_boards"],
                # 核心里那只票自己最后出现日 != 窗口最新日 → 已断板（真核心可能不在池内）
                "broken": str(rec["last_date"]) != newest_day,
            }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 池内裁决
# ─────────────────────────────────────────────────────────────────────────────
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
    parts: list[str] = []
    if _candidate_int(it, "relay_rank") == 1:
        parts.append("接力池第1")
    fbt = "".join(ch for ch in str(it.get("fbt") or "") if ch.isdigit())
    if len(fbt) >= 4 and int(fbt[:4]) <= MECH_LEADER_SECOND_BOARD_MAX_FBT:
        parts.append(f"{fbt[:2]}:{fbt[2:4]}秒板")
    carry = _candidate_int(it, "carry_cnt")
    if parts and carry >= MECH_LEADER_MIN_CARRY:
        parts.append(f"带动{carry}家")
    return "·".join(parts)


def call_pool(candidates: dict[str, Any] | None,
              quotes: dict[str, dict[str, Any]] | None,
              *,
              plan: dict | None = None,
              stance: str | None = None,
              zt_rows: list[dict[str, Any]] | None = None,
              line_cores: dict[str, dict[str, Any]] | None = None,
              market_open: dict[str, Any] | None = None,
              sector_flow: dict[str, dict[str, Any]] | None = None,
              zt_series: dict[str, list[tuple[str, int]]] | None = None,
              zt_days: list[str] | None = None,
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
      enabled    = 覆盖开关（测试用）；None → 读模块常量 `POOL_CALL_ENABLED`

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

    plan = plan if isinstance(plan, dict) else {}
    _stance = str(stance or plan.get("stance") or "").strip() or None
    quota = quota_for(_stance)
    # 昨日空仓不是绝对封死：只有整批昨涨停股的 09:25 红开、连板红开、竞价量能
    # 同时转强，才放宽为 1 只。缺全市场冻结快照或任何一项不达标，保持原名额。
    open_read = market_open if isinstance(market_open, dict) else {}
    open_relief = bool(_stance == "hibernate" and open_read.get("ready"))
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
            effective_quota = 0
        elif env_read.get("level") == "defensive":
            effective_quota = min(effective_quota, _dg.DEFENSIVE_QUOTA_CAP)

    cands: list[dict[str, Any]] = []
    for k in ("relay", "watch"):
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

    # 涨停序列（位置闸用·截到依据日 T-1；缺失/序列不足 → 位置闸不裁决，诚实降级）
    if zt_series is None and zt_days is None:
        if POOL_CALL_DEFENSE_GATE and POOL_CALL_DEFENSE_POSITION:
            try:
                zt_series, zt_days = _dg.load_zt_series(upto=_env_date)
            except Exception:
                zt_series, zt_days = {}, []
    zt_series = zt_series if isinstance(zt_series, dict) else {}
    zt_days = list(zt_days) if isinstance(zt_days, (list, tuple)) else []
    _pos_on = bool(POOL_CALL_DEFENSE_GATE and POOL_CALL_DEFENSE_POSITION)

    # 同线板数排名（池内）：板数高的排前面（核心优先于跟风）
    _by_line: dict[str, list[dict[str, Any]]] = {}
    for c in pool:
        _by_line.setdefault(line_family(c), []).append(c)
    _line_rank: dict[str, int] = {}
    for fam, arr in _by_line.items():
        for i, c in enumerate(sorted(arr, key=lambda x: -(int(x.get("boards") or 0)
                                                          if str(x.get("boards") or "0").isdigit()
                                                          else 0))):
            _line_rank[str(c.get("code"))] = i + 1

    # ── 逐票裁决 ──────────────────────────────────────────────────────────
    marks: dict[str, dict[str, Any]] = {}
    entries: list[dict[str, Any]] = []
    for c in pool:
        code = str(c.get("code") or "").strip()
        name = str(c.get("name") or "")
        q = qs.get(code)
        gap = _gap_of(q)
        exp = (c.get("expectation") or {}).get("expected_range")
        zone = _ac.gap_zone(gap, exp) if gap is not None else "unknown"
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
            "line_core": ({"code": lc.get("code"), "name": lc.get("name"),
                           "boards": lc.get("boards"), "broken": bool(lc.get("broken"))}
                          if lc and not is_core else None),
            "line_rank": _line_rank.get(code),
            # 只读候选事实，在本函数末端的名额排序消费；不改变任何竞价/风险短路规则。
            "mechanical_leader": mechanical_leader,
            "mechanical_leader_note": mechanical_leader_note or None,
            "leader_role": role_of(c),
            "leader_evidence": c.get("leader_evidence") or {},
            "carry_cnt": c.get("carry_cnt"),
            "relay_stage": c.get("relay_stage"),
            "relay_mood": c.get("relay_mood"),
            "n_plus_n": c.get("n_plus_n"),
        }
        # 封板质量读数（只读·审计/卡面用）：**在短路链之前**算出，被前几道闸拦下的票也留痕
        if POOL_CALL_DEFENSE_GATE:
            try:
                base["seal_quality"] = _dg.seal_quality(c)
            except Exception:
                pass

        # ① 无竞价数据
        if gap is None:
            marks[code] = {**base, "view": "watch",
                           "reason": "开盘竞价数据没拿到 → 今天先看，不买"}
            continue
        # ② 超 5% 硬线
        if zone == "above":
            marks[code] = {**base, "view": "skip",
                           "reason": f"今天开在 {gap:+.2f}%，高开超过 5% 上限 → 没买到，不追"}
            continue
        # ③ 盘后预案＝回避
        if bias == BIAS_AVOID:
            marks[code] = {**base, "view": "skip",
                           "reason": "昨天做预案时就把它划成回避 → 今天不买"}
            continue
        # ④ 低于预期
        if zone == "below":
            marks[code] = {**base, "view": "skip",
                           "reason": f"今天开在 {gap:+.2f}%，低于昨天的预期 → 放弃不接"}
            continue
        # ⑤ 没红开
        if gap <= 0:
            marks[code] = {**base, "view": "watch",
                           "reason": f"开在 {gap:+.2f}%，没红开 → 今天先看，不买"}
            continue
        # ⑥ 红开但没量（诱多典型）——与 `ai_caliber.decide` 同一量能门
        if (isinstance(amt, (int, float)) and isinstance(need, (int, float)) and need > 0
                and amt < float(need) * _ac.VOLUME_WEAK_RATIO):
            marks[code] = {**base, "view": "watch",
                           "reason": (f"开在 {gap:+.2f}% 是红的，但竞价只有 {float(amt):.2f} 亿、"
                                      f"量不够（要 {float(need):.2f} 亿）→ 像诱多，先看")}
            continue
        # 昨日同路径晋级、溢价与失败核按钮都偏弱时，须用足计划量能确认；缺量不放行。
        if c.get("relay_mood") == "pressured" and (
                not isinstance(amt, (int, float)) or not isinstance(need, (int, float))
                or need <= 0 or amt < need):
            marks[code] = {**base, "view": "watch",
                           "reason": "这一路接力昨天反馈偏弱，今天竞价量没达到计划要求 → 先看，不买"}
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
        # ⑩ 🛡️ 前置防守闸门·盘口层（2026-10-08 大幅回撤经验固化）——**竞价达标 ≠ 可以买**
        #   10-08 实证：两只票竞价一个 +0.85%、一个 +0.40%，都「没超 5% 上限、也没低于预期」，
        #   机械规则照单全收 → 收盘 −5.85% / −8.18%。缺的是「买之前先看这票脏不脏」。
        dist = ({"level": "none", "hit_count": 0, "note": "", "hits": [], "facts": {}}
                if not (POOL_CALL_DEFENSE_GATE and POOL_CALL_DEFENSE_DISTRIBUTION)
                else _dg.distribution_verdict(c))
        if dist.get("note") and dist.get("level") == "warn":
            base["defense_warn"] = dist["note"]        # 只留痕（卡面可用·不否决）
        if dist.get("level") == "block":
            marks[code] = {**base, "view": "watch", "defense_gate": "distribution",
                           "defense_detail": dist,
                           "reason": (f"开在 {gap:+.2f}% 达标了，但{dist['note']} "
                                      f"→ 今天先看，不买")}
            continue
        # ⑪ 候选买入
        entries.append({**base, "view": "participate"})
        marks[code] = {**base, "view": "participate"}

    # ── 排序 + 名额截取 ────────────────────────────────────────────────────
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
        from core import leader_cycle
        priority = leader_cycle.status_key(x) if leader_cycle.ENABLE_STATUS_PRIORITY else ()
        return priority + (
            1 if x.get("line_is_core") else 0,          # 同线最高板优先（核心 > 跟风）
            {"supportive": 1, "pressured": -1}.get(x.get("relay_mood"), 0),  # 同身份下看接力心态
            1 if plan_or_mech_leader else 0,              # focus 或机械强龙优先
            bi,                                           # 板数
            -lr,                                          # 同线身位（靠前优先）
        )

    from core import leader_cycle
    if leader_cycle.ENABLE_STATUS_PRIORITY:
        for rank, row in enumerate(sorted(marks.values(), key=_key, reverse=True), 1):
            row["status_rank"] = rank
        for entry in entries:
            entry["status_rank"] = marks[entry["code"]]["status_rank"]
    entries.sort(key=_key, reverse=True)
    for i, e in enumerate(entries):
        code = e["code"]
        if effective_quota <= 0:
            marks[code] = {**e, "view": "watch",
                           "reason": "昨天定的是空仓待命 → 今天不主动买（有破例通道时另说）"}
            continue
        _fusion_note = (
            f"（机械强龙：{e.get('mechanical_leader_note')}，与重点看同档优先）"
            if (ENABLE_MECH_LEADER_FUSION and e.get("mechanical_leader")
                and e.get("bias") != BIAS_FOCUS) else ""
        )
        _mood_note = ({"supportive": "（昨天同路径接力反馈转好）",
                       "pressured": "（昨天同路径反馈偏弱，今天竞价量已达计划）"}
                      .get(e.get("relay_mood"), ""))
        if i < effective_quota:
            _open_note = "（昨涨停今早整体转强，空仓日只试这一只）" if open_relief else ""
            marks[code] = {**e, "view": "participate", "quota_rank": i + 1,
                           "reason": (f"今天开在 {e['gap_pct']:+.2f}%，"
                                      f"没超 5% 上限、也没低于预期 → 买入"
                                      + ("（这条线里板数最高的那只）"
                                         if e.get("line_is_core") else "")
                                      + _fusion_note + _mood_note + _open_note)}
        else:
            marks[code] = {**e, "view": "watch",
                           "reason": (f"今天开在 {e['gap_pct']:+.2f}% 也达标，"
                                      f"但今天只买前 {effective_quota} 只、它排在后面 → 先看"
                                      + _fusion_note)}

    meta = {
        "caliber": "pool_call_925",
        "stance": _stance,
        "quota": quota,
        "effective_quota": effective_quota,
        "market_open_relief": open_relief,
        "market_open": open_read or None,
        "pool_size": len(pool),
        "participate": [c for c, v in marks.items() if v.get("view") == "participate"],
        # 🛡️ 防守闸门留痕（2026-10-08 经验）：环境档位 + 两道硬闸各拦了谁
        "defense": {
            "enabled": bool(POOL_CALL_DEFENSE_GATE),
            "env": env_read,
            "distribution_blocked": [c for c, v in marks.items()
                                     if v.get("defense_gate") == "distribution"],
            "sector_drain_blocked": [c for c, v in marks.items()
                                     if v.get("defense_gate") == "sector_drain"],
            "position_blocked": [c for c, v in marks.items()
                                 if v.get("defense_gate") == "high_position"],
            "warned": [c for c, v in marks.items()
                       if v.get("defense_warn") or v.get("position_warn")],
            "sector_flow_boards": len(sector_flow),
            "zt_history_days": len(zt_days),
        },
        "call_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return {"enabled": True, "stance": _stance, "quota": quota, "views": marks, "meta": meta}


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
        cur["defense_gate"] = m.get("defense_gate")   # distribution / sector_drain / high_position
        cur["defense_reason"] = (m.get("defense_detail") or {}).get("note") or None
        cur["defense_warn"] = m.get("defense_warn")
        cur["position_warn"] = m.get("position_warn")
        cur["theme_name"] = m.get("theme_name")
        if not cur.get("name"):
            cur["name"] = m.get("name")
    return out
