# -*- coding: utf-8 -*-
"""低强度合并（2026-09-17 用户「前后端都改」）的回归锁。

规则（用户原话）：
  ① 低强度推送合并 —— 1 分钟内只推了一个、强度不高的 → 不单独出卡，等相邻低强度一起推；
  ② 高强度联动爆发 —— 多板块联动 → 即时聚合（本测试不覆盖，属 live_signal 侧）；
  ③ 普通推送 —— 无板块联动的独立事件（如单个涨停）→ 普通平卡。

「低强度」= 既有类型语义「快速拉升」（涨幅未达冲板门槛），**不新造数值阈值**。

隔离约定：文件状态全部落 tmp_path；mock `os.system`，不推送、不联网、不碰生产 cache。
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("short_spirit_ls", SCRIPTS / "short_spirit.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

REAL_DT = ss.datetime
TODAY = "2026-09-17"          # 周四
AFTER_WINDOW = REAL_DT(2026, 9, 17, 10, 30)   # 10:30 → 早盘高频窗口外
IN_WINDOW = REAL_DT(2026, 9, 17, 9, 35)       # 09:35 → 早盘高频窗口内


def _sig(code, typ="快速拉升"):
    return {"name": f"股{code}", "code": code, "sector": "S", "market_cap_yi": 50.0,
            "amount_yi": 3.0, "pct": 5.0, "type": typ, "tags": [],
            "resonance": 0, "flow_yi": 0.0, "strength": 1.0, "reason": "r"}


def _freeze(monkeypatch, dt):
    class FakeDT(REAL_DT):
        @classmethod
        def now(cls, tz=None):
            return dt
    monkeypatch.setattr(ss, "datetime", FakeDT)


def _env(tmp_path, monkeypatch, signals, now, last_push):
    # ⚠️ 生产默认是 False（保证盘中高频不变）；本文件的逻辑测试显式打开开关来验证能力本身。
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_LOWSTRENGTH_MERGE", True)
    monkeypatch.setattr(ss, "ROOT", tmp_path)
    (tmp_path / "cache").mkdir()
    snap = tmp_path / "latest.json"
    snap.write_text(json.dumps({"top5": [], "boards": {}}), encoding="utf-8")
    monkeypatch.setattr(ss, "SNAP", snap)
    pp = tmp_path / "pushed.json"
    monkeypatch.setattr(ss, "PUSHED_PATH", pp)
    monkeypatch.setattr(ss, "OUT_PATH", tmp_path / "out.json")
    monkeypatch.setattr(ss, "in_trading", lambda: True)
    monkeypatch.setattr(ss, "load_cfg", lambda: {
        "min_pct": 5.0, "min_market_cap_yi": 30, "amount_morning_yi": 3.0,
        "amount_afternoon_yi": 2.0, "batch_threshold": 999, "batch_timeout_min": 30,
        "max_signals": 5, "weight": {}})
    monkeypatch.setattr(ss, "build_signals",
                        lambda d, n, c: (signals, "morning", len(signals), len(signals)))
    _freeze(monkeypatch, now)
    pp.write_text(json.dumps({"date": TODAY, "codes": [], "last_push": last_push,
                              "pending": [], "pending_ts": ""}), encoding="utf-8")
    return pp


def _run(monkeypatch, force=True, send_rc=0):
    calls = {"n": 0}

    def fake_system(*a, **k):
        calls["n"] += 1
        return send_rc
    monkeypatch.setattr(os, "system", fake_system)
    argv = ["short_spirit.py", "--push"] + (["--force"] if force else [])
    monkeypatch.setattr(sys, "argv", argv)
    ss.main()
    return calls["n"]


# ── ① 低强度单条 → 合并等待（不单独出卡）────────────────────────────
def test_lone_weak_signal_is_held_not_pushed(tmp_path, monkeypatch):
    """1 条「快速拉升」+ --force + 窗口外 → 不出卡，pending 保留待相邻合并。"""
    pp = _env(tmp_path, monkeypatch, [_sig("600101")], AFTER_WINDOW, "10:25:00")
    n = _run(monkeypatch, force=True)
    assert n == 0, "低强度单条不应发起推送"
    pushed = json.loads(pp.read_text(encoding="utf-8"))
    assert [s["code"] for s in pushed["pending"]] == ["600101"], "应保留待推"
    assert pushed["codes"] == [], "不应登记已推"
    assert pushed["last_push"] == "10:25:00", "不应刷新 last_push"


# ── ③ 普通推送：单个涨停 → 普通平卡（照旧推）──────────────────────
def test_lone_limit_up_still_pushed(tmp_path, monkeypatch):
    """1 条「涨停」→ 属规则③普通推送，仍出平卡。"""
    pp = _env(tmp_path, monkeypatch, [_sig("600102", typ="涨停")], AFTER_WINDOW, "10:25:00")
    n = _run(monkeypatch, force=True)
    assert n == 1, "单个涨停应照常推送"
    pushed = json.loads(pp.read_text(encoding="utf-8"))
    assert pushed["codes"] == ["600102"]


def test_lone_charge_warning_still_pushed(tmp_path, monkeypatch):
    """「接近涨停」（冲板预警）属高强度 → 仍即时推，不被合并吞掉。"""
    _env(tmp_path, monkeypatch, [_sig("600103", typ="接近涨停")], AFTER_WINDOW, "10:25:00")
    assert _run(monkeypatch, force=True) == 1, "冲板预警不应被合并"


def test_lone_broken_board_still_pushed(tmp_path, monkeypatch):
    """「炸板·回落」是风险信号 → 仍即时推。"""
    _env(tmp_path, monkeypatch, [_sig("600104", typ="炸板·回落")], AFTER_WINDOW, "10:25:00")
    assert _run(monkeypatch, force=True) == 1, "炸板风险不应被合并"


# ── 相邻两条低强度 → 一起推（「只合并相邻」）─────────────────────
def test_two_adjacent_weak_signals_pushed_together(tmp_path, monkeypatch):
    """相邻 2 条低强度 → 不再视为「只推了一个」，合并出卡。"""
    pp = _env(tmp_path, monkeypatch, [_sig("600105"), _sig("600106")], AFTER_WINDOW, "10:25:00")
    n = _run(monkeypatch, force=True)
    assert n == 1, "相邻两条低强度应合并出卡"
    pushed = json.loads(pp.read_text(encoding="utf-8"))
    assert sorted(pushed["codes"]) == ["600105", "600106"]


# ── ⚠️ 早盘高频窗口豁免 ─────────────────────────────────────────
def test_early_window_exempt_from_merge(tmp_path, monkeypatch):
    """09:25–09:55 窗口内（用户最高优先级：有信号就立刻推）→ 不合并。"""
    _env(tmp_path, monkeypatch, [_sig("600107")], IN_WINDOW, "09:30:00")
    assert _run(monkeypatch, force=True) == 1, "早盘高频窗口应豁免低强度合并"


# ── 信息不丢：超时兜底仍推 ─────────────────────────────────────
def test_timeout_fallback_still_pushes_lone_weak(tmp_path, monkeypatch):
    """距上推 ≥ batch_timeout_min → 兜底仍推，避免低强度信号全天滞留。"""
    _env(tmp_path, monkeypatch, [_sig("600108")], AFTER_WINDOW, "09:00:00")  # 90 分钟前
    assert _run(monkeypatch, force=True) == 1, "超时兜底不应被合并阻断"


# ── ⛔ 生产默认必须关闭（盘中高频不变的硬保证）────────────────────
def test_production_default_is_off_to_keep_intraday_cadence():
    """⛔ 用户 2026-09-17「保证盘中异动那边高频不要变化」——开关默认必须是 False，
    否则本模块会改变盘中推送节奏。此断言是「盘中高频不变」的回归锁。"""
    assert ss.ENABLE_SPIRIT_LOWSTRENGTH_MERGE is False, "生产默认必须关闭低强度合并"


# ── 开关可回退 ───────────────────────────────────────────────
def test_switch_off_restores_previous_behavior(tmp_path, monkeypatch):
    """关掉开关 → 回到改动前行为（低强度单条也照推）。"""
    _env(tmp_path, monkeypatch, [_sig("600109")], AFTER_WINDOW, "10:25:00")
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_LOWSTRENGTH_MERGE", False)
    assert _run(monkeypatch, force=True) == 1, "关闭开关应恢复原行为"


# ── 时段守卫helper 自身 ───────────────────────────────────────
def test_early_window_helper_boundaries():
    assert ss.in_early_realtime_window(REAL_DT(2026, 9, 17, 9, 25)) is True
    assert ss.in_early_realtime_window(REAL_DT(2026, 9, 17, 9, 55)) is True
    assert ss.in_early_realtime_window(REAL_DT(2026, 9, 17, 9, 56)) is False
    assert ss.in_early_realtime_window(REAL_DT(2026, 9, 17, 10, 30)) is False
    assert ss.in_early_realtime_window(REAL_DT(2026, 9, 19, 9, 35)) is False  # 周六
