#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中猎手候选预筛（毫秒级，零推理）。

由 pulse_fetcher.sh 每轮刷新数据时调用：读 latest.json，规则化排出
TOP3-5 候选（异动强度 × 20cm加权 × 板数 × 可买 × 健康换手），写
cache/pulse_snapshots/candidates.json。AI 猎手任务直接读此文件做研判，
不再重跑 intraday_pulse、不再穷举全市场三维打分，从而把卡片时延从
~5min 压到 ~1-2min。

输出候选字段：
  name/code/pct/sector/is_20cm/boards/sealed/buyable/price/turnover/reason
"""
import json
import os
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SNAP = os.path.join(ROOT, "cache", "pulse_snapshots")
LATEST = os.path.join(SNAP, "latest.json")
OUT = os.path.join(SNAP, "candidates.json")

MIN_PCT = 5.0          # 弱异动不入候选
TOP_N = 5


def load(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


import re


def parse_boards(v):
    """boards 字段可能是 int 也可能是 '5天5板' 这类字符串，统一解析成板数(int)。"""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    nums = re.findall(r"\d+", str(v))
    return int(nums[-1]) if nums else 0


def is_zt(l):
    pct = float(l.get("pct") or 0)
    is20 = bool(l.get("is_20cm"))
    boards = parse_boards(l.get("boards"))
    return boards > 0 or pct >= (19.9 if is20 else 9.9)


def match_news(l, news):
    name = l.get("name", "")
    code = l.get("code", "")
    sec = l.get("sector", "")
    for n in news:
        title = n.get("title") or ""
        plates = n.get("plates") or []
        stocks = n.get("stocks") or []
        if name and name in title:
            return title
        if code and code in title:
            return title
        if sec and (sec in title or sec in plates):
            return title
        if name and name in stocks:
            return title
    return ""


def main():
    d = load(LATEST)
    top5 = d.get("top5") or []
    news = d.get("newsflash") or []

    # 换手率 cross-ref：top5[].leader.turn 按 code 映射到候选
    turn_map = {}
    for t in top5:
        ld = t.get("leader") or {}
        c = ld.get("code")
        if c and ld.get("turn") is not None:
            turn_map[c] = ld.get("turn")

    # 合并多源个股（主板倾斜·用户2026-08-04）：
    # leaders(涨幅异动,创业板居多) + hot_spots[].stocks(题材领涨,含主板) + early[].buy/members(爆发前夜)
    # → 把主板连板/强势名也纳入候选池，避免主板机会被创业板涨幅榜完全淹没
    seen = {}
    def _add(l, sector=""):
        code = l.get("code")
        if not code or code in seen:
            return
        if "ST" in (l.get("name") or "").upper():
            return
        if not l.get("sector"):
            l = dict(l); l["sector"] = sector
        seen[code] = l
    for l in (d.get("leaders") or []):
        _add(l)
    for hs in (d.get("hot_spots") or []):
        sec = hs.get("sector", "")
        for s in (hs.get("stocks") or []):
            _add(s, sec)
    for ea in (d.get("early") or []):
        b = ea.get("buy")
        if isinstance(b, dict):
            _add(b, ea.get("sector", ""))
        for m in (ea.get("members") or []):
            if isinstance(m, dict):
                _add(m, ea.get("sector", ""))

    scored = []
    for l in seen.values():
        code = l.get("code")
        if not code:
            continue
        name = l.get("name") or ""
        if "ST" in name.upper():
            continue
        pct = float(l.get("pct") or 0)
        if pct < MIN_PCT:
            continue
        is20 = bool(l.get("is_20cm"))
        boards = parse_boards(l.get("boards"))
        sealed = is_zt(l)
        buyable = bool(l.get("buyable"))
        turn = turn_map.get(code)
        try:
            turn_f = float(turn) if turn is not None else None
        except Exception:
            turn_f = None
        reason = match_news(l, news) or (l.get("sector", "") + " 板块异动")

        # ── 板块属性偏置（2026-09-23 核对：与用户 2026-08-13 已确认需求冲突，暂不行为变更）──
        # 历史：2026-08-04 用户因 20cm 单日回撤风险暂定「主板倾斜」；2026-08-13 用户已撤销
        #   （原话「创业板权限打开，主板和创业板都有周期」）→ 主板/创业板按同一套封板质量 →
        #   竞价溢价周期**对称参与，无板块属性加分/减分**，20cm 只保留「⚠️20cm波动大·紧止损」标注。
        # 现状：下面两行仍是旧「主板倾斜」实现（pct 封顶 10 + 非主板 ×0.85），**与上述需求不一致**。
        # ⛔ 改它=改变盘中候选排序（AI 盘中看到的票会变），属策略评分口径，须用户单独拍板。
        #   一行开关切换：MAIN_BOARD_TILT=False + CAP_EXTRA_PCT_TO_10=False 即为对称参与。
        MAIN_BOARD_TILT = True          # False=主板/创业板对称（按 2026-08-13 需求）
        MAIN_BOARD_TILT_FACTOR = 0.85   # 仅 MAIN_BOARD_TILT=True 时对非主板生效
        CAP_EXTRA_PCT_TO_10 = True      # True=20cm 涨幅按 10 封顶（旧倾斜配套）
        eff_pct = min(pct, 10.0) if CAP_EXTRA_PCT_TO_10 else pct
        is_main = (code[:1] in ("6", "0")) and not code.startswith(("688", "300", "301"))
        score = eff_pct * (1.0 if (is_main or not MAIN_BOARD_TILT) else MAIN_BOARD_TILT_FACTOR)
        score += boards * 6
        score += 3 if buyable else 0
        if turn_f is not None and 3 <= turn_f <= 25:
            score += 2   # 健康换手加分，过高(>25)警惕不加分

        scored.append((score, pct, {
            "name": name,
            "code": code,
            "pct": round(pct, 2),
            "sector": l.get("sector", ""),
            "is_20cm": is20,
            "boards": boards,
            "sealed": sealed,
            "buyable": buyable,
            "price": l.get("price"),
            "turnover": turn,
            "reason": reason,
        }))

    # 按分数降序，同分按涨幅降序
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    top = [c for _, _, c in scored[:TOP_N]]

    out = {
        "generated_at": datetime.datetime.now().strftime("%H:%M:%S"),
        "phase": "intraday",
        "count": len(top),
        "candidates": top,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[gen_candidates] 候选 {len(top)} 只 -> {OUT}")


if __name__ == "__main__":
    main()
