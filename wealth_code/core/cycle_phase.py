#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""情绪周期四阶段定位 + 中位股排除（2026-10-21 学习 92科比完整框架后落地）。

## 为什么必须和现有 `regime` 分开（⛔ 不要合并）
系统现有 `adapters/input_zt.load_environment()["regime"]` 判的是**今天情绪的绝对水平**
（ice / risk_off / neutral / risk_on）——本质是「今天几度」。

92科比框架问的是**这段行情走到哪了**（低位震荡 / 主升 / 高位震荡 / 主跌）。

两者不可互相替代，因为**同一个温度在不同位置含义相反**：
- 情绪 5 分出现在低位震荡 → 试错良机（资金刚从高位出来找新方向）
- 情绪 5 分出现在高位震荡 → 随时转主跌（不赌穿越）

文档原话：「周期位置不对，题材再好也是毒药」。所以本模块只做**周期位置**，
⛔ 不改 `regime`、不改既有四档接力资格。

## 数据源（全部系统已有，⛔ 不新抓）
`cache/feedback_matrix.json`：
  · `trend.history`（近 5 日 [{date, pr 晋级率, ap 溢价}]）→ 看**方向**
  · 当日：`promote_rate` / `avg_pct` / `up_rate` / `now_zt_count` / `now_max_boards`
    / `broken_rate` / `nuke_count` / `break_count`
`cache/zt_history/zt_<date>.json` 的 `zt_list` → 最高板、家数（兜底）

## 输出
`phase_of()` → 阶段 + 仓位档 + 该做什么手法 + 证据 + 失效条件。
⛔ 只给建议档位，不直接改名额（名额由 `relay_position` / `pool_call` 既有链路决定）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent   # 狸猫猎手/

# ── 四阶段 ─────────────────────────────────────────────────────────────
PHASE_BOTTOM = "bottom"            # 低位震荡：试错
PHASE_ADVANCING = "advancing"      # 主升：重仓
PHASE_HIGH_VOLATILE = "high_vol"   # 高位震荡：轻仓，不赌穿越
PHASE_EBBING = "ebbing"            # 主跌：空仓 / 尾盘试探
PHASE_UNKNOWN = "unknown"

PHASE_LABEL = {
    PHASE_BOTTOM: "低位震荡（试错期）",
    PHASE_ADVANCING: "主升（赚钱效应扩散）",
    PHASE_HIGH_VOLATILE: "高位震荡（不赌穿越）",
    PHASE_EBBING: "主跌（亏钱效应扩散）",
    PHASE_UNKNOWN: "位置不明",
}

# 仓位档（文档 §8.2 周期决定仓位）
PHASE_POSITION = {
    PHASE_BOTTOM: {"level": "small_active", "label": "小仓位但积极试",
                   "quota_hint": "可多试几只、每只都小"},
    PHASE_ADVANCING: {"level": "heavy", "label": "重仓",
                      "quota_hint": "确认的方向可以加重"},
    PHASE_HIGH_VOLATILE: {"level": "light", "label": "轻仓",
                          "quota_hint": "只做最强的、不加码"},
    PHASE_EBBING: {"level": "off", "label": "空仓或极轻仓尾盘试探",
                   "quota_hint": "不出手，除了第二天尾盘"},
    PHASE_UNKNOWN: {"level": "flat", "label": "按正常办", "quota_hint": ""},
}

# 该做什么手法（文档 §5 六字真言：龙头 / 补涨 / 切换）
PHASE_PLAY = {
    PHASE_BOTTOM: "找下一个龙头种子（新方向首板 / 1进2），别追高位残局",
    PHASE_ADVANCING: "做龙头、做补涨（龙头打空间→低位跟涨）",
    PHASE_HIGH_VOLATILE: "只做最强的那一只，不赌穿越、不做跟风补涨",
    PHASE_EBBING: "空仓；第二天尾盘才考虑试探，第三天必走",
    PHASE_UNKNOWN: "按常规纪律执行",
}


def _num(v: Any) -> float | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _load_fb(root: Path | None = None) -> dict[str, Any]:
    p = (root or ROOT) / "cache" / "feedback_matrix.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _trend_dir(vals: list[float | None]) -> str:
    """近几期方向：up / down / flat / unknown（只比首尾，中间噪声不看）。"""
    xs = [v for v in vals if v is not None]
    if len(xs) < 2:
        return "unknown"
    d = xs[-1] - xs[0]
    if d > 3.0:
        return "up"
    if d < -3.0:
        return "down"
    return "flat"


def phase_of(fb: dict[str, Any] | None = None, *,
             root: Path | None = None) -> dict[str, Any]:
    """定位当前处于情绪周期哪个阶段。

    ⛔ 判据顺序 = 先判最危险的（主跌），再谈机会 —— 与文档「先活下来再谈赚钱」一致。
    ⛔ 每个判据都要「当日读数 + 方向」两条腿；只有单条腿命中不下结论（诚实降级 unknown）。
    """
    # ⛔ 入参语义要明确：None = 没传（自动读盘）；显式传 dict（含空 dict）= 用传入的，
    #    空 dict 判 unknown。旧写法 `and fb` 会让空 dict 悄悄去读盘，测试里看不出、
    #    真实调用时又会把「我以为没数据」变成「用了生产文件」，属隐蔽串味。
    if fb is None:
        fb = _load_fb(root)
    if not isinstance(fb, dict) or not fb:
        return {"phase": PHASE_UNKNOWN, "label": PHASE_LABEL[PHASE_UNKNOWN],
                "confidence": "no_data", "evidence": {}, "play": PHASE_PLAY[PHASE_UNKNOWN],
                "position": PHASE_POSITION[PHASE_UNKNOWN], "why": "读不到情绪矩阵",
                "date": ""}

    pr = _num(fb.get("promote_rate"))
    ap = _num(fb.get("avg_pct"))
    up = _num(fb.get("up_rate"))
    zt = _num(fb.get("now_zt_count"))
    mx = _num(fb.get("now_max_boards"))
    prev_mx = _num(fb.get("prev_max_boards"))
    nuke = _num(fb.get("nuke_count"))
    broken = _num(fb.get("broken_rate"))
    brk = _num(fb.get("break_count"))

    hist = [h for h in ((fb.get("trend") or {}).get("history") or []) if isinstance(h, dict)]
    pr_series = [_num(h.get("pr")) for h in hist] + [pr]
    ap_series = [_num(h.get("ap")) for h in hist] + [ap]
    pr_dir = _trend_dir(pr_series)
    ap_dir = _trend_dir(ap_series)

    ev = {"promote_rate": pr, "avg_pct": ap, "up_rate": up, "zt_count": zt,
          "max_boards": mx, "prev_max_boards": prev_mx, "nuke": nuke,
          "broken_rate": broken, "break_count": brk,
          "pr_dir": pr_dir, "ap_dir": ap_dir,
          "pr_series": [x for x in pr_series if x is not None],
          "ap_series": [x for x in ap_series if x is not None]}
    # 基准日（2026-10-21）：周期位置是「收盘后」才成立的判断，卡面必须标清是哪天的收盘。
    # verify 卡在次日 09:26 跑时读到的仍是**昨日**的 feedback_matrix → 标「（10-20 收盘）」
    # 才不会让读者把上一交易日的定位读成今天。
    _fd = str(fb.get("date") or (fb.get("generated_ts") or "")[:10] or "").strip()

    def out(phase: str, why: str, conf: str = "high", shift: str = "") -> dict[str, Any]:
        """`why` = 完整判据（含当期读数，供日志/复盘）；`shift` = **只讲方向**的一句，
        卡面用 shift —— 因为当期读数（晋级率/情绪分/溢价）已由 📡 大盘环境块讲过，
        再写一遍违反「同一件事实全卡只说一次」铁律（2026-10-21 P1 实测发现）。"""
        return {"phase": phase, "label": PHASE_LABEL[phase], "confidence": conf,
                "evidence": ev, "play": PHASE_PLAY[phase],
                "position": PHASE_POSITION[phase], "why": why,
                "shift": shift or why, "date": _fd}

    # ① 主跌：溢价明显为负 + 涨少跌多 + 晋级率低（三腿齐才算）
    if (ap is not None and ap <= -1.0 and up is not None and up < 40
            and pr is not None and pr < 25):
        return out(PHASE_EBBING,
                   f"昨天涨停的今天平均 {ap:+.2f}%、只有 {up:.0f}% 的票在涨、"
                   f"晋级率 {pr:.0f}% —— 亏钱效应在扩散，别接飞刀",
                   shift="溢价在往下掉、上涨的票越来越少 —— 亏钱效应还在扩散")

    # ② 高位震荡：高度还在但晋级率在掉、炸板多（见顶前兆）
    if (mx is not None and mx >= 5 and pr_dir == "down"
            and broken is not None and broken >= 33):
        return out(PHASE_HIGH_VOLATILE,
                   f"最高 {mx:.0f} 板但晋级率在往下走、炸板 {broken:.0f}% —— 一致性在松，"
                   f"走主跌的概率大于走二波，不赌穿越",
                   shift=f"高度还在（最高 {mx:.0f} 板）但一致性在松、炸板变多 —— "
                         f"走主跌的概率大于走二波")

    # ③ 主升：晋级率够高 + 溢价为正 + **方向不是往下掉**
    #    ⚠️ 方向 unknown（历史不足 2 期）不作为否决：缺方向 ≠ 有坏方向。
    #       否则系统首次运行（trend.history 还没攒起来）永远判不出主升，
    #       会把「明明在涨」的行情一律打成位置不明（2026-10-21 实测发现）。
    if (pr is not None and pr >= 30 and ap is not None and ap > 0
            and pr_dir != "down" and ap_dir != "down"):
        return out(PHASE_ADVANCING,
                   f"晋级率 {pr:.0f}%、溢价 {ap:+.2f}%、赚钱效应在扩散 —— 主升，"
                   f"分歧是加仓窗口不是卖点",
                   shift="赚钱效应在扩散，接力越走越顺 —— 分歧是加仓窗口，不是卖点")

    # ④ 低位震荡：赚不到钱但也没继续恶化（跌不动了）→ 试错期
    if (pr is not None and pr < 30
            and (ap is None or ap > -1.0 or ap_dir == "up")):
        # 证据要能看出「从哪来的」：文档定义低位震荡「往往接着前面一个主跌的亏钱阶段」，
        # 所以必须点出前几期溢价/晋级率的位置，否则读者会误以为在描述「长期趴底」。
        _pr_hist = [x for x in pr_series[:-1] if x is not None]
        _ap_hist = [x for x in ap_series[:-1] if x is not None]
        _from = ""
        if _ap_hist:
            _lo = min(_ap_hist)
            if _lo < 0 <= (ap or 0):
                _from = f"（前几天溢价最低到 {_lo:+.2f}%，现在翻正 → 正在从亏钱里修复）"
            elif _lo < (ap or 0) - 1.0:
                _from = f"（溢价从 {_lo:+.2f}% 修到 {ap:+.2f}% → 在改善）"
        return out(PHASE_BOTTOM,
                   f"晋级率只有 {pr:.0f}%、溢价 {ap:+.2f}% 但没继续恶化{_from} —— "
                   f"高位杀出来的钱要找新方向，低位新题材最容易起来",
                   conf="mid" if pr_dir == "unknown" else "high",
                   # 卡面口径：只讲「从哪来、往哪去」的方向，不复述当期读数（📡 块已讲）
                   shift=((_from.lstrip("（").rstrip("）") + " —— 高位杀出来的钱在找新方向，"
                           "低位新题材最容易起来") if _from else
                          "赚不到钱但也没继续恶化（跌不动了）—— 高位杀出来的钱在找新方向，"
                          "低位新题材最容易起来"))

    # ⑤ 都不明确 → 诚实降级
    return out(PHASE_UNKNOWN, "读数互相矛盾，判不出位置 → 按常规纪律、不加重仓位",
               conf="low")


# ── 中位股排除（文档 §5.1「要么做高，要么做低，中间不要碰」）──────────────

def mid_band(max_boards: int) -> tuple[int, int] | None:
    """当前市场高度下的「中位区间」(lo, hi)，含端点；无中位区 → None。

    ⛔ 高度 <5 板时没有中位区：总共就 1~4 板，再切一段出来会把整条线砍光。
    规则（H ≥ 5 起）：
      · 低位 = 1~2 板（试错/补涨的性价比区）
      · 高位 = H-1 ~ H 板（龙头的地位确定性区）
      · 中位 = 3 ~ H-2 板 → **排除**
    例：H=8 → 低位 1-2 / 中位 3-6 / 高位 7-8；H=5 → 低位 1-2 / 中位 3 / 高位 4-5。
    """
    try:
        h = int(max_boards)
    except (TypeError, ValueError):
        return None
    if h < 5:
        return None
    lo, hi = 3, h - 2
    if lo > hi:
        return None
    return lo, hi


def is_mid_board(boards: Any, max_boards: Any) -> bool:
    """是否落在中位区间（该排除）。无高度数据 → False（⛔ 不猜、不误杀）。"""
    band = mid_band(max_boards) if max_boards is not None else None
    if not band:
        return False
    try:
        b = int(boards)
    except (TypeError, ValueError):
        return False
    return band[0] <= b <= band[1]


def mid_board_note(boards: Any, max_boards: Any) -> str:
    """中位股一句话（卡面用·大白话）。"""
    band = mid_band(max_boards) if max_boards is not None else None
    if not band:
        return ""
    return (f"{boards}板正卡在中间（{band[0]}~{band[1]}板这一档）——"
            f"既没有最高板的地位，也没有低位的性价比，两头不靠，不做")


# ── 阶段连续天数（2026-10-21 P3）────────────────────────────────────────
# 92科比：「主跌第二天尾盘试、第三天必出」——这条规则的前提是「第几天」可得。
# 跨日计数必须落盘，否则每次渲染都从 0 开始。
STATE_FILE = "cycle_phase_state.json"


def _state_path(root: Path | None = None) -> Path:
    return (root or ROOT) / "cache" / STATE_FILE


def load_state(root: Path | None = None) -> dict[str, Any]:
    """读阶段状态（无文件/损坏 → {}，⛔ 不猜）。"""
    try:
        d = json.loads(_state_path(root).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def track_state(result: dict[str, Any], *, root: Path | None = None,
                persist: bool = True) -> dict[str, Any]:
    """给阶段结果补上「连续第几天」，并按需落盘。

    ⛔ 幂等：同一基准日（`result["date"]`）多次渲染不重复计数——
      同一张卡渲两次、或盘中反复重算，都只算一天。这是本函数唯一的写入语义。

    ⛔ 只在**落盘成功后**才把 streak 写进返回值吗？不是——返回值始终反映
      「算出来的天数」；落盘失败只影响下一次能不能续上，不影响本次展示。
      这样盘写坏了也不会让卡面突然丢掉「第几天」这个信息。
    """
    ph = str(result.get("phase") or "")
    day = str(result.get("date") or "").strip()[:10]
    prev = load_state(root)
    prev_phase = str(prev.get("phase") or "")
    prev_day = str(prev.get("date") or "")[:10]
    prev_streak = int(prev.get("streak") or 0) if str(prev.get("streak") or "").isdigit() else 0

    if not ph or ph == PHASE_UNKNOWN:
        return {**result, "streak": 0, "since": ""}
    if ph == prev_phase:
        if day and day == prev_day:
            streak, since = max(1, prev_streak), str(prev.get("since") or day)
        elif day and day > prev_day:
            streak, since = max(1, prev_streak) + 1, str(prev.get("since") or prev_day or day)
        else:
            # 基准日比已记录的还早（回放历史/补算）→ 不递增、不覆盖状态
            return {**result, "streak": max(1, prev_streak), "since": str(prev.get("since") or day)}
    else:
        streak, since = 1, day

    if persist and day:
        try:
            p = _state_path(root)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"phase": ph, "date": day, "streak": streak,
                                     "since": since,
                                     "updated": __import__("datetime").datetime.now().isoformat(timespec="seconds")},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass              # 落盘失败不影响本次展示（见 docstring）
    return {**result, "streak": streak, "since": since}


def streak_note(result: dict[str, Any]) -> str:
    """「第几天」的白话提示（只在主跌/主升这类**有先后纪律**的阶段才说）。

    ⛔ 低位震荡/高位震荡不给天数：那两个阶段没有「第几天该做什么」的纪律，
      硬给一个数字只会让人以为有个不存在的节奏。
    """
    ph = str(result.get("phase") or "")
    n = result.get("streak") or 0
    if not n:
        return ""
    if ph == PHASE_EBBING:
        if n == 1:
            return "主跌第 1 天，别急着抄"
        if n == 2:
            return "主跌第 2 天，只允许尾盘拿一点点试，明天必须走"
        return f"主跌第 {n} 天，早过了试探窗口，等新周期起来再说"
    if ph == PHASE_ADVANCING:
        return f"主升第 {n} 天" + ("，刚起，可以上" if n <= 2 else "，位置已经不低了")
    return ""
