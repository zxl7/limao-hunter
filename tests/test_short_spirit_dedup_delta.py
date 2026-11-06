# -*- coding: utf-8 -*-
"""短线精灵「重复推送」有界修复回归锁（2026-11-05）。

实证背景（只读生产事件流，未补跑）：
  · 11-05 09:28–10:21 共推 22 张卡，其中 20 张由 --force 触发，10 张只含 1 只票；
  · 09:57 一张卡把 002428 推了两行完全一样的「接近涨停」；
  · 11-04 接力段「机器人」一天展示 7 次，多数只是涨幅/家数在抖。

本文件锁死三件事，⛔ 不联网、不推送、不写生产缓存：
  ① 同轮同 code 只保留一条（卡内不重复、联动家数不虚高）；
  ② 后排候选当日已展示不重展，且只有成功送达才登记；
  ③ 接力方向区分「身份变化」与「状态抖动」，抖动不许绕过冷却。
"""
import importlib.util
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location(
    "short_spirit_dedup_delta", SCRIPTS / "short_spirit.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

NOW = ss.datetime(2026, 11, 5, 10, 30)
TODAY = "2026-11-05"


def _sig(code="600001", name="测试票", strength=0.8, sector="锂电池"):
    return {"name": name, "code": code, "sector": sector, "theme": sector,
            "market_cap_yi": 300.0, "amount_yi": 12.0, "pct": 9.9,
            "type": "涨停", "tags": [], "resonance": 80, "flow_yi": 1.0,
            "strength": strength, "reason": f"共振[{sector}]"}


def _relay(sector="机器人", front="600001", ladder=("600001", "600002"),
           tradable=("300201",), zt=6, pct=6.0):
    return {"sector": sector, "zt_count": zt,
            "front": {"code": front, "name": "前排"},
            "ladder": [{"code": c} for c in ladder],
            "tradable": [{"code": c, "pct": pct} for c in tradable]}


def _card_body(card):
    return card["elements"][0]["text"]["content"]


@pytest.fixture(autouse=True)
def _isolate_official(monkeypatch):
    """官方板块码映射是生产只读文件；测试一律不挂码，保证纯隔离。"""
    monkeypatch.setattr(ss, "_official_tag", None)


# ── ① 同轮同 code 去重 ────────────────────────────────────────────
def test_dedup_signals_keeps_first_and_preserves_order():
    a, b, c = _sig("600001"), _sig("600001"), _sig("600002")
    out = ss._dedup_signals([a, b, c])
    assert len(out) == 2 and out[0] is a and out[1] is c
    assert ss._dedup_signals([]) == []
    assert ss._dedup_signals(None) == []
    # 无 code 的行丢弃，不让它混进推送名额
    assert ss._dedup_signals([{"name": "无名"}, a]) == [a]


def test_leaders_fallback_pool_dedups_same_code():
    """latest.leaders 同一票多条（不同榜单）→ 兜底池只留一条。"""
    row = {"code": "002428", "boards": "3", "pct": 9.9, "price": 10.98,
           "is_20cm": False, "sector": "小金属"}
    out = ss._leaders_as_pool({"leaders": [row, dict(row), {"code": "600001", "boards": "2"}]})
    # 重复的 002428 只留一条；另一只合法的 2 板票必须保留
    assert [x["code"] for x in out] == ["002428", "600001"]


def test_group_view_dedup_keeps_sector_count_honest():
    """全池视图去重后，板块联动家数不再被同一票撑大。"""
    rows = [_sig("600001"), _sig("600001"), _sig("600002")]
    summary = ss._sector_group_summary(ss._dedup_signals(rows), merge_family=False)
    assert summary[0][0] == "锂电池" and summary[0][1] == 2 and summary[0][2] == 2
    raw = ss._sector_group_summary(rows, merge_family=False)
    assert raw[0][1] == 3, "未去重时家数虚高（本断言锁死问题确实存在）"


# ── ② 后排候选跨卡增量 ────────────────────────────────────────────
def _backseat():
    return {
        "candidates": [{"code": "600011", "name": "跟风甲", "pct": 6.0,
                        "sector": "锂电池", "vol_ratio": 1.5}],
        "potential": [{"code": "600012", "name": "后排乙", "pct": 7.0,
                       "sector": "锂电池", "vol_ratio": 1.6, "why": "题材"}],
    }


def test_filter_backseat_shown_is_pure_and_filters_both_lists():
    src = _backseat()
    out = ss._filter_backseat_shown(src, {"600011", "600012"})
    assert out["candidates"] == [] and out["potential"] == []
    # 入参不得被改（同一份候选还要给别的消费方）
    assert len(src["candidates"]) == 1 and len(src["potential"]) == 1
    assert ss._filter_backseat_shown(src, []) is src
    partial = ss._filter_backseat_shown(src, ["600011"])
    assert [x["code"] for x in partial["potential"]] == ["600012"]


def test_build_card_sink_collects_only_displayed_potential():
    """sink 只收实际渲染的后排：被接力段占用的不重复登记。"""
    bs = _backseat()
    bs["potential"].append({"code": "600013", "name": "后排丙", "pct": 5.0,
                            "sector": "锂电池", "vol_ratio": 1.4, "why": "题材"})
    relay = {"sector": "锂电池", "front": {}, "ladder": [], "zt_count": 1,
             "tradable": [{"code": "600013", "name": "后排丙", "pct": 5.0}]}
    sink = set()
    card = ss.build_card([_sig()], "morning", NOW, relay=relay, backseat=bs,
                         group_summary=[], shown_sink=sink)
    body = _card_body(card)
    assert "后排乙" in body
    assert body.count("后排丙(600013)") == 1, "只在接力段出现，不在后排段重复列"
    assert sink == {"600012"}, "sink 只登记后排段真正展示过的 code"


# ── ③ 接力方向：身份 vs 状态 ──────────────────────────────────────
def _relay_env(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "ROOT", tmp_path)
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    return tmp_path / "cache" / "short_spirit_relay_state.json"


def test_relay_identity_ignores_pct_and_zt_count():
    a = _relay(pct=6.0, zt=6)
    b = _relay(pct=8.5, zt=9)
    assert ss._relay_identity(a) == ss._relay_identity(b)
    assert ss._relay_fingerprint(a) != ss._relay_fingerprint(b), "旧指纹确实会被抖动带偏"
    assert ss._relay_identity(a) != ss._relay_identity(_relay(tradable=("300202",)))
    assert ss._relay_identity(a) != ss._relay_identity(_relay(front="600009"))


def test_relay_state_jitter_does_not_bypass_cooldown(tmp_path, monkeypatch):
    path = _relay_env(tmp_path, monkeypatch)
    r = _relay()
    emit, fp = ss._relay_should_emit(r, NOW)
    assert emit is True, "首次应展示"
    ss._relay_mark(r, fp, NOW)
    assert path.exists() and json.loads(path.read_text(encoding="utf-8"))["date"] == TODAY
    jitter = _relay(pct=9.4, zt=8)
    assert ss._relay_should_emit(jitter, NOW + timedelta(minutes=3))[0] is False, \
        "只有涨幅/家数在抖 → 不许绕过冷却"
    # 真换人（可参与票换了）→ 立即展示，信息不丢
    assert ss._relay_should_emit(
        _relay(tradable=("300299",)), NOW + timedelta(minutes=1))[0] is True
    # 冷却过后仍会带最新状态再展示一次
    assert ss._relay_should_emit(jitter, NOW + timedelta(minutes=25))[0] is True


def test_relay_state_resets_across_days(tmp_path, monkeypatch):
    path = _relay_env(tmp_path, monkeypatch)
    r = _relay()
    old = {"机器人": {"sig": ss._relay_fingerprint(r), "ident": ss._relay_identity(r),
                      "ts": "2026-11-04 10:00:00"}}
    path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    assert ss._relay_should_emit(r, NOW)[0] is True, "跨日旧档作废，新日首次必展示"
    ss._relay_mark(r, ss._relay_fingerprint(r), NOW)
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["date"] == TODAY and list(state["sectors"]) == ["机器人"]


def test_relay_identity_delta_switch_is_reversible(tmp_path, monkeypatch):
    """开关关 → 回到旧指纹判定（含涨幅抖动即立即发），证明可回滚。"""
    _relay_env(tmp_path, monkeypatch)
    r = _relay()
    _, fp = ss._relay_should_emit(r, NOW)
    ss._relay_mark(r, fp, NOW)
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_RELAY_IDENTITY_DELTA", False)
    assert ss._relay_should_emit(_relay(pct=9.4), NOW + timedelta(minutes=1))[0] is True


# ── ④ 端到端（tmp 隔离，不推送）────────────────────────────────────
def _env(tmp_path, monkeypatch, signals, *, backseat=None, system_rc=0):
    monkeypatch.setattr(ss, "ROOT", tmp_path)
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    snap = tmp_path / "latest.json"
    snap.write_text(json.dumps({"top5": [], "leaders": [], "limit_up_stocks": []},
                               ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ss, "SNAP", snap)
    pp = tmp_path / "pushed.json"
    monkeypatch.setattr(ss, "PUSHED_PATH", pp)
    monkeypatch.setattr(ss, "OUT_PATH", tmp_path / "out.json")
    monkeypatch.setattr(ss, "in_trading", lambda: True)
    monkeypatch.setattr(ss, "load_cfg", lambda: {
        "min_pct": 3.0, "min_pct_down": 7.5, "min_market_cap_yi": 30,
        "amount_morning_yi": 3.0, "amount_afternoon_yi": 2.0,
        "batch_threshold": 999, "batch_timeout_min": 30,
        "max_signals": 5, "top_per_sector": 2, "weight": {}})
    monkeypatch.setattr(ss, "build_signals",
                        lambda d, n, c: (signals, "morning", len(signals), len(signals)))
    monkeypatch.setattr(ss, "build_signals_down", lambda d, n, c: ([], "morning", 0, 0))
    monkeypatch.setattr(ss, "_relay_hint", lambda *a, **k: None)
    monkeypatch.setattr(ss, "backseat_pick", lambda *a, **k: backseat or {})
    monkeypatch.setattr(ss, "GROUP_VIEW_CACHE", {"view": None})
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_SECTOR_GROUP", False)
    monkeypatch.setattr(os, "system", lambda *a, **k: system_rc)
    monkeypatch.setattr(sys, "argv", ["short_spirit.py", "--push", "--force"])
    pp.write_text(json.dumps({"date": NOW.strftime("%Y-%m-%d"), "codes": [],
                              "last_push": "", "pending": [], "pending_ts": ""},
                             ensure_ascii=False), encoding="utf-8")
    return pp


def _run_card(tmp_path):
    ss.main()
    return json.loads((tmp_path / "cache" / "short_spirit_card.json").read_text(encoding="utf-8"))


def test_e2e_same_code_not_repeated_in_one_card(tmp_path, monkeypatch):
    pp = _env(tmp_path, monkeypatch, [_sig(), _sig()])
    card = _run_card(tmp_path)
    body = _card_body(card)
    assert body.count("测试票(600001)") == 2, "概览一行 + 分组一行（改动前是三行）"
    pushed = json.loads(pp.read_text(encoding="utf-8"))
    assert pushed["codes"] == ["600001"], "同 code 不得占两个推送名额"


def test_e2e_backseat_shown_only_committed_on_success(tmp_path, monkeypatch):
    pp = _env(tmp_path, monkeypatch, [_sig()], backseat=_backseat(), system_rc=1)
    card = _run_card(tmp_path)
    assert "后排乙" in _card_body(card), "失败轮仍应在卡里给出候选"
    assert "backseat_shown" not in json.loads(pp.read_text(encoding="utf-8")), \
        "发送失败不得登记为已展示"

    pp2 = _env(tmp_path, monkeypatch, [_sig()], backseat=_backseat(), system_rc=0)
    _run_card(tmp_path)
    assert json.loads(pp2.read_text(encoding="utf-8"))["backseat_shown"] == ["600012"]


def test_e2e_backseat_not_repeated_on_next_card(tmp_path, monkeypatch):
    pp = _env(tmp_path, monkeypatch, [_sig("600001")], backseat=_backseat())
    body1 = _card_body(_run_card(tmp_path))
    assert "后排乙" in body1, "首张应展示后排候选"
    assert json.loads(pp.read_text(encoding="utf-8"))["backseat_shown"] == ["600012"]
    # 第二张：换一只新票触发推送，候选仍是同一批 → 不应再展示
    monkeypatch.setattr(ss, "build_signals",
                        lambda d, n, c: ([_sig("600002", "新票")], "morning", 1, 1))
    card2 = _run_card(tmp_path)
    assert "后排乙" not in _card_body(card2), "当日已展示的后排不重展"
    assert "新票(600002)" in _card_body(card2), "主信号推送不受影响"
