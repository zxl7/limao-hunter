#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""决策质量审计（反事实账本）回归锁 —— 2026-09-17 立项配套。

锁四类东西，防这条新链路与系统既有铁律脱钩：
 1. **口径同源**：入场硬线必须等于 `core.ai_caliber.USER_ENTRY_MAX_GAP_PCT`（单一源·5%），
    禁止本脚本自带一份阈值（历史上 entry_max_gap 三处独立实现已漂移过一次 → 09-14 越权事故）。
 2. **只读铁律**：脚本**不得**写净值账本 / 不得推送 / 不得改自动化 —— 它只产审计报告，
    否则「反事实」会污染真实账本（可逆性设计的前提）。用源码静态断言（去注释/去 docstring 后扫）。
 3. **判定逻辑**：可执行率与「收盘价是否拿到」解耦；一字板优先于超硬线归类；涨停档不被吞并。
 4. **样本纪律**：分组样本 < 30 必须标 `small_sample`（防拿 3 个样本下结论）。

运行：`pytest tests/test_decision_audit.py -q`
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SRC = BASE / "scripts" / "decision_audit.py"

spec = importlib.util.spec_from_file_location("decision_audit", SRC)
da = importlib.util.module_from_spec(spec)
spec.loader.exec_module(da)


def _code_only(text: str) -> str:
    """剥掉 docstring 与行注释，只留可执行代码（用于只读守卫，防注释里的词误报）。"""
    t = re.sub(r'"""(?:.|\n)*?"""', "", text)
    t = re.sub(r"'''(?:.|\n)*?'''", "", t)
    return "\n".join(re.sub(r"#.*$", "", ln) for ln in t.splitlines())


# ── 1) 口径同源 ────────────────────────────────────────────
def test_entry_gap_is_single_source():
    """入场硬线必须与 core.ai_caliber 同值（禁本脚本自带一份）。"""
    from core import ai_caliber
    assert da.ENTRY_GAP_MAX == ai_caliber.USER_ENTRY_MAX_GAP_PCT == 5.0


def test_fallback_literal_matches_canonical():
    """兜底字面量必须与 ai_caliber 同值 —— 防兜底悄悄漂移（离线路径会跑出另一套口径）。"""
    from core import ai_caliber
    m = re.search(r"ENTRY_GAP_MAX\s*=\s*([0-9.]+)", SRC.read_text(encoding="utf-8"))
    assert m, "缺少兜底字面量"
    assert float(m.group(1)) == ai_caliber.USER_ENTRY_MAX_GAP_PCT


def test_entry_gap_only_as_guarded_fallback():
    """硬线只能从 ai_caliber 取；字面量只允许出现在 except 兜底分支里（离线独立运行用）。"""
    lines = SRC.read_text(encoding="utf-8").splitlines()
    idx = [i for i, ln in enumerate(lines) if re.search(r"ENTRY_GAP_MAX\s*=\s*[0-9]", ln)]
    assert idx, "缺少导入失败的兜底赋值（独立运行需要）"
    for i in idx:
        window = "\n".join(lines[max(0, i - 4):i + 1])
        assert "except" in window, f"第 {i + 1} 行出现了非兜底的硬编码硬线：{lines[i].strip()}"
    assert "from core.ai_caliber import USER_ENTRY_MAX_GAP_PCT" in _code_only(SRC.read_text(encoding="utf-8"))


# ── 2) 只读铁律 ────────────────────────────────────────────
def test_read_only_no_nav_no_push_no_automation():
    """⛔ 反事实账本绝不允许写净值账本 / 推送 / 改自动化。"""
    code = _code_only(SRC.read_text(encoding="utf-8"))
    for forbidden in ("account_nav_history", "ai_nav_history", "push_card",
                      "notify_alert", "mirror_push", "automation_update"):
        assert forbidden not in code, f"决策审计脚本出现了只读铁律禁止的引用：{forbidden}"


def test_only_writes_audit_artifacts():
    """所有写盘目标必须落在审计自身的三个产物 + --out 上。"""
    code = _code_only(SRC.read_text(encoding="utf-8"))
    targets = set(re.findall(r"([A-Za-z_][A-Za-z0-9_\.]*)\.write_text\(", code))
    assert targets <= {"OUT_JSON", "OUT_MD", "CLOSE_CACHE", "Path"}, f"越界写盘目标：{targets}"


# ── 3) 判定逻辑 ────────────────────────────────────────────
def _row(gap, is20=False, close_price=None, ap=100.0):
    zt = {"2026-09-17": {}}
    r = da._mk_row("2026-09-17", "2026-09-16", "000001", "样本", "relay", None,
                   {"is_20cm": is20, "boards": 2},
                   {"auction_price": ap, "prev_close": ap / (1 + gap / 100.0)}, zt, None)
    if close_price is not None:
        r["ret_pct"] = round((close_price - ap) / ap * 100, 2)
    return r


def test_buyable_and_limit_open_classification():
    """≤硬线 = 可买；开盘涨停 = 结构性买不到。"""
    assert _row(0.0)["buyable"] is True
    assert _row(5.0)["buyable"] is True                 # 边界含 5%
    assert _row(5.01)["buyable"] is False
    assert _row(9.99)["gone_limit_open"] is True
    assert _row(19.98, is20=True)["gone_limit_open"] is True
    # 20cm 票高开 9.99% 属正常区间，不是一字板
    assert _row(9.99, is20=True)["gone_limit_open"] is False
    assert _row(9.99, is20=True)["buyable"] is False    # 但已超 5% 硬线 → 不可买
    # 主板票高开 9.99% 距涨停线须落在判定窗口内（阈值写松会漏判一字板）
    assert _row(9.99)["gone_limit_open"] is True
    assert _row(9.0)["gone_limit_open"] is False        # 未及涨停线 → 不是一字板


def test_limit_open_implies_unbuyable():
    """核心蕴含关系：一字板必 > 5% 硬线 → **开盘涨停的票天然不可买**（故 buyable 无需重复判涨停）。"""
    for is20 in (False, True):
        for gap in (0.0, 4.9, 5.0, 9.9, 9.99, 19.98, 20.02):
            r = _row(gap, is20=is20)
            if r["gone_limit_open"]:
                assert r["buyable"] is False, f"一字板却判可买：gap={gap} is20={is20}"


def test_exec_rate_independent_of_close_availability():
    """可执行率只由竞价决定，不因收盘价缺失而变成 0（09-17 首版踩过的坑）。"""
    rs = [_row(1.0), _row(2.0), _row(8.0)]              # 均无收盘价
    s = da._stats(rs)
    assert s["n"] == 3 and s["exec_n"] == 2
    assert s["exec_rate"] == 66.7
    assert s["ret_exec"] is None                        # 无收益样本 → 诚实为 None，不是 0


def test_ret_exec_vs_ret_all_caliber():
    """ret_exec 只含**可执行**样本；ret_all 覆盖全样本（含被硬线挡掉的）——两者禁混用。"""
    ok = _row(1.0, close_price=101.0)          # 可买 · 收盘 +1.0%
    blocked = _row(9.0, close_price=90.0)      # 超硬线被挡 · 若买则 -10.0%
    s = da._stats([ok, blocked])
    assert s["exec_n"] == 1
    assert s["ret_exec"] == 1.0                # 只按能买到的算
    assert s["ret_all"] == -4.5                # (1.0 + -10.0) / 2（含被挡票的「若买」结果）
    assert s["win_rate"] == 100.0              # 胜率只按可执行样本


def test_gate_priority_limit_open_over_gap():
    """门槛分档：一字板优先于「超硬线」，不重复计入。"""
    rs = [_row(1.0), _row(6.0), _row(9.99), _row(9.99, is20=True)]
    g = da._gate_cost(rs)
    assert g["可买（≤硬线）"]["n"] == 1
    assert g["超硬线（>硬线）"]["n"] == 2               # 6.0 + 20cm 的 9.99
    assert g["一字板（开盘涨停）"]["n"] == 1            # 主板 9.99
    assert sum(v["n"] for v in g.values()) == 4         # 不丢不重


def test_missed_dedupes_same_day_code():
    """被挡机会表：同一 执行日+标的 只留一条（池档与 AI 档会各出一条）。"""
    a = _row(9.99); a.update({"zt_on_t": True, "ret_pct": 0.0})
    b = dict(a); b["ai_view"] = "participate"           # 同票不同档
    out = da._missed([a, b])
    assert len(out) == 1
    # ⛔ 可买但涨停的票**不得**进「被挡」表（它是「买到且吃到」，不是「被挡」）
    hit_buyable = _row(1.0); hit_buyable.update({"zt_on_t": True})
    assert hit_buyable["buyable"] is True
    assert da._missed([hit_buyable]) == []


# ── 4) 样本纪律与漂移 ──────────────────────────────────────
def test_small_sample_flag():
    assert da._stats([_row(1.0)] * 29)["small_sample"] is True
    assert da._stats([_row(1.0)] * 30)["small_sample"] is False


def test_drift_detects_tier_change():
    """同票档位变化被识别（且只统计有 ai_view 的行）。"""
    r1 = _row(1.0); r1.update({"trade_date": "2026-09-14", "ai_view": "participate", "code": "000993", "name": "闽东电力"})
    r2 = _row(1.0); r2.update({"trade_date": "2026-09-15", "ai_view": "watch", "code": "000993", "name": "闽东电力"})
    r3 = _row(1.0); r3.update({"trade_date": "2026-09-16", "ai_view": "watch", "code": "000993", "name": "闽东电力"})
    pool_only = _row(1.0); pool_only.update({"trade_date": "2026-09-16", "ai_view": None, "code": "600000"})
    d = da._drift([r1, r2, r3, pool_only])
    assert len(d) == 1 and d[0]["switches"] == 1 and d[0]["n_days"] == 3


def test_monotonic_judgement():
    """档位单调性判定：真单调 → True；倒挂 → False。"""
    def mk(tier, zt):
        r = _row(1.0); r.update({"ai_view": tier, "zt_on_t": zt}); return r
    good = [mk("participate", True)] + [mk("watch", False)] * 3 + [mk("skip", False)] * 3
    bad = [mk("participate", False)] + [mk("watch", True)] * 3 + [mk("skip", False)] * 3
    assert da._ai_matrix(good)["monotonic"] is True
    assert da._ai_matrix(bad)["monotonic"] is False
