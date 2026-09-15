# -*- coding: utf-8 -*-
"""短线精灵·涨停当下性硬门（2026-09-07 用户「时间顺序第一」）回归锁。

⛔ 涨停信号必须是「本轮新观测到」：上轮已被观测为涨停 = 存量涨停（可能上午就封住），
   不是当下新封板 → 剔除不推。拦截三类旧闻补推：激活晚补存量 / 东财恢复补上午 /
   降级源把整天封板的存量当「涨停事件」反复产出。
锁死：
  1. 空轮快照 → 本轮涨停信号保留（当天首次观测 = 首推时机）
  2. 上轮已观测该 code 涨停 → 本轮涨停信号剔除（存量不补推）
  3. 上轮有但本轮 type=非涨停(快速拉升) → 不受限（非涨停天然当下）
  4. 调用后 round 快照更新为「本轮观测」（含信号+池内涨停）
  5. 开关 ENABLE_SPIRIT_TIMELINESS=False → 直通不过滤（可逆）
"""
import json
import importlib.util
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("short_spirit_tl", SCRIPTS / "short_spirit.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


def _sig(code, typ="涨停"):
    return {"name": "X", "code": code, "sector": "S", "pct": 10.0, "type": typ,
            "tags": [], "resonance": 90, "flow_yi": 0.0, "strength": 8.0, "reason": "共振[X]"}


def _round_file(date="2026-09-07", zt=None):
    ss.ROUND_PATH.write_text(json.dumps(
        {"date": date, "ts": "10:00:00", "zt": zt or []}, ensure_ascii=False), encoding="utf-8")


def test_fresh_zt_pushed_when_no_prev_round(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "ROUND_PATH", tmp_path / "r.json")   # 空快照
    out = ss._timeliness_gate([_sig("600103")], [], datetime(2026, 9, 7, 14, 1))
    assert len(out) == 1, "首见涨停应保留（首推时机）"


def test_stale_zt_dropped_when_seen_prev_round(tmp_path, monkeypatch):
    rp = tmp_path / "r.json"
    monkeypatch.setattr(ss, "ROUND_PATH", rp)
    _round_file(zt=["600103"])                                   # 上轮已观测 600103 涨停
    out = ss._timeliness_gate([_sig("600103")], [], datetime(2026, 9, 7, 14, 1))
    assert out == [], "上轮已观测涨停=存量(上午封到现在)→本轮不推"


def test_non_zt_signal_not_gated(tmp_path, monkeypatch):
    rp = tmp_path / "r.json"
    monkeypatch.setattr(ss, "ROUND_PATH", rp)
    _round_file(zt=["600103"])
    out = ss._timeliness_gate([_sig("600103", typ="快速拉升")], [], datetime(2026, 9, 7, 14, 1))
    assert len(out) == 1, "非涨停信号(快速拉升)不受当下性门限制"


def test_round_updated_with_observed_zt(tmp_path, monkeypatch):
    rp = tmp_path / "r.json"
    monkeypatch.setattr(ss, "ROUND_PATH", rp)
    pool = [{"code": "600127", "name": "Y", "pct": 10.0}]
    ss._timeliness_gate([_sig("600103")], pool, datetime(2026, 9, 7, 14, 1))
    r = json.loads(rp.read_text(encoding="utf-8"))
    assert r["date"] == "2026-09-07"
    assert "600103" in r["zt"] and "600127" in r["zt"], "观测=信号涨停+池内涨停"


def test_switch_off_passthrough(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "ROUND_PATH", tmp_path / "r.json")
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_TIMELINESS", False)   # 可逆开关
    out = ss._timeliness_gate([_sig("600103")], [], datetime(2026, 9, 7, 14, 1))
    assert len(out) == 1
