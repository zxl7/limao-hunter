#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""龙头资格认定（P2）+ 阶段连续天数（P3）契约锁 —— 2026-10-21。

锁五件事：
  1. **两把尺是叙述层，不是资格层**：五维 confirmed 但尺没过 → 仍是龙头（不降级）；
     五维没 confirmed 但尺都过 → 仍不升龙头。⛔ 用户 2026-09-24 拍板的资格判据不许被绕。
  2. **序列必须连续**：中间断板即止，不许把上一波数据混进来（会让两把尺全失真）。
  3. **数据不足就 unknown**：不猜、不给假通过。
  4. **主跌计数幂等**：同一基准日多次渲染只算一天。
  5. **天数只在有先后纪律的阶段出现**：主跌/主升给，低位/高位震荡不给。
"""
import json
import pathlib

import pytest

from core import cycle_phase as cp
from core import dragon_confirm as dc
from core import leader_cycle as lc


# ── 夹具 ────────────────────────────────────────────────────────────────
def _hist(days):
    """days = [{"date":..,"rows":[{code,fbt,cje_yi,boards}]}]"""
    return days


def _row(**kw):
    base = {"code": "600001", "name": "测试龙", "boards": 3, "carry_cnt": 5,
            "leader_evidence": {"confirmation": "confirmed", "continuous_days": 4}}
    base.update(kw)
    return base


def _seq(fbts, cjes=None):
    cjes = cjes or [5.0] * len(fbts)
    return [{"date": f"2026-10-{10 + i:02d}", "fbt": f, "cje_yi": c,
             "boards": i + 1} for i, (f, c) in enumerate(zip(fbts, cjes))]


# ── 1) 资格层 vs 叙述层 ─────────────────────────────────────────────────
def test_confirmed_with_both_scales_is_true_dragon():
    seq = _seq(["144500", "103000", "093500"], [3.0, 7.5, 4.0])   # 加速 + 爆量后缩量
    r = dc.confirm(_row(), seq)
    assert r["level"] == dc.LEVEL_CONFIRMED
    assert r["label"] == "龙头"
    assert r["scale_ok"] is True


def test_confirmed_without_scales_stays_dragon_not_downgraded():
    """⛔ 核心：五维 confirmed、两把尺都没过 → **仍是龙头**，只标注未验证。

    这是 P2 最重要的边界：不许用两把尺推翻用户拍板的资格判据。
    """
    seq = _seq(["093500", "103000", "142000"], [3.0, 3.2, 3.1])   # 越封越晚 + 没爆量
    r = dc.confirm(_row(), seq)
    assert r["level"] == dc.LEVEL_CONFIRMED_THIN
    assert r["label"] == "龙头（未经验证）"
    assert r["identity_role"] == "龙头"            # 身份不变
    assert r["scale_ok"] is False
    assert "一波流" in r["narrative"]


def test_pending_with_both_scales_stays_zhun_longtou():
    """⛔ 反向边界：五维没 confirmed，两把尺都过也只是「准龙头（形态像）」，不升龙头。"""
    row = _row(leader_evidence={"confirmation": "pending", "continuous_days": 4})
    seq = _seq(["144500", "103000", "093500"], [3.0, 7.5, 4.0])
    r = dc.confirm(row, seq)
    assert r["level"] == dc.LEVEL_PARTIAL
    assert r["identity_role"] == "准龙头"
    assert "只按准龙头看" in r["narrative"]


def test_pending_without_scales_is_plain_zhun_longtou():
    row = _row(leader_evidence={"confirmation": "pending", "continuous_days": 2})
    r = dc.confirm(row, _seq(["093500", "103000"], [3.0, 3.1]))
    assert r["level"] == dc.LEVEL_PENDING


def test_no_five_dim_falls_back_to_three_elements():
    """无逐日证据 → 回落到三要素 identity（不越权认定）。"""
    row = _row(leader_evidence={}, expectation={"dragon_diagnosis": {"identity": "真龙头(主升)"}})
    r = dc.confirm(row, [])
    assert r["level"] == dc.LEVEL_CONFIRMED_THIN


# ── 2) 序列必须连续 ─────────────────────────────────────────────────────
def test_series_stops_at_break():
    """中间断板 → 序列在那里截断（⛔ 不把上一波混进来）。"""
    hist = _hist([
        {"date": "2026-10-15", "rows": [{"code": "600001", "fbt": "093000", "cje_yi": 3.0}]},
        # 10-16 没有它 → 断板
        {"date": "2026-10-19", "rows": [{"code": "600001", "fbt": "140000", "cje_yi": 4.0}]},
        {"date": "2026-10-20", "rows": [{"code": "600001", "fbt": "093500", "cje_yi": 5.0}]},
    ])
    seq = dc.series_of(hist, "600001", "2026-10-20")
    assert [s["date"] for s in seq] == ["2026-10-19", "2026-10-20"]


def test_series_empty_when_not_in_pool():
    hist = _hist([{"date": "2026-10-20", "rows": [{"code": "000002"}]}])
    assert dc.series_of(hist, "600001", "2026-10-20") == []


def test_series_respects_asof():
    """asof 之后的数据不能算进来（防未来函数）。"""
    hist = _hist([
        {"date": "2026-10-20", "rows": [{"code": "600001", "fbt": "093000"}]},
        {"date": "2026-10-21", "rows": [{"code": "600001", "fbt": "093000"}]},
    ])
    assert [s["date"] for s in dc.series_of(hist, "600001", "2026-10-20")] == ["2026-10-20"]


# ── 3) 数据不足 → unknown ───────────────────────────────────────────────
def test_checks_mark_unknown_without_data():
    r = dc.confirm(_row(leader_evidence={"confirmation": "confirmed"}), [])
    acc = next(c for c in r["checks"] if c["name"] == "涨停时间越走越早")
    vol = next(c for c in r["checks"] if c["name"] == "爆量检验")
    assert acc["passed"] is None      # 序列不足 → 不猜
    assert vol["passed"] is None


def test_switch_off_returns_empty():
    r = dc.confirm(_row(), _seq(["093000"]), enabled=False)
    assert r["enabled"] is False and r["level"] == ""


# ── 4) 卡面标签只给前两档 ───────────────────────────────────────────────
def test_tag_only_for_dragon_levels():
    assert "龙头" in dc.tag_text({"enabled": True, "level": dc.LEVEL_CONFIRMED})
    assert "未验证" in dc.tag_text({"enabled": True, "level": dc.LEVEL_CONFIRMED_THIN})
    assert dc.tag_text({"enabled": True, "level": dc.LEVEL_PENDING}) is None
    assert dc.tag_text({"enabled": True, "level": dc.LEVEL_NOT}) is None
    assert dc.tag_text(None) is None


def test_leader_cycle_writes_dragon_confirm():
    """annotate 必须产出该字段（否则卡面永远读不到）。"""
    hist = _hist([
        {"date": "2026-10-19", "rows": [{"code": "600001", "fbt": "093000", "cje_yi": 3.0}]},
        {"date": "2026-10-20", "rows": [{"code": "600001", "fbt": "093000", "cje_yi": 3.0}]},
    ])
    # load_history 需要完整快照；这里直接测 series_of + confirm 的组合路径
    row = _row()
    row["dragon_confirm"] = dc.confirm(row, dc.series_of(hist, "600001", "2026-10-20"))
    assert row["dragon_confirm"]["enabled"] is True
    assert row["dragon_confirm"]["series_len"] == 2


# ── 5) 阶段连续天数（P3）────────────────────────────────────────────────
def _fb(day, pr=18, ap=-2.5, up=30):
    return {"date": day, "promote_rate": pr, "avg_pct": ap, "up_rate": up,
            "nuke_count": 8, "broken_rate": 30, "break_count": 2, "trend": {"history": []}}


def test_streak_counts_across_days(tmp_path):
    n = []
    for d in ("2026-10-19", "2026-10-20", "2026-10-21"):
        r = cp.track_state(cp.phase_of(_fb(d)), root=tmp_path)
        n.append(r["streak"])
    assert n == [1, 2, 3]
    assert r["since"] == "2026-10-19"


def test_streak_idempotent_same_day(tmp_path):
    """⛔ 同一基准日多次渲染只算一天（卡面/盘中重算都吃这条）。"""
    cp.track_state(cp.phase_of(_fb("2026-10-21")), root=tmp_path)
    r = cp.track_state(cp.phase_of(_fb("2026-10-21")), root=tmp_path)
    assert r["streak"] == 1


def test_streak_resets_on_phase_change(tmp_path):
    cp.track_state(cp.phase_of(_fb("2026-10-20")), root=tmp_path)          # ebbing 第 1 天
    r = cp.track_state(cp.phase_of(_fb("2026-10-21", pr=35, ap=1.5, up=60)), root=tmp_path)
    assert r["phase"] != cp.PHASE_EBBING and r["streak"] == 1


def test_streak_note_only_for_ebbing_and_advancing():
    """⛔ 低位/高位震荡不给天数——那两段没有「第几天该做什么」的节奏。"""
    assert cp.streak_note({"phase": cp.PHASE_EBBING, "streak": 2})
    assert cp.streak_note({"phase": cp.PHASE_ADVANCING, "streak": 1})
    assert cp.streak_note({"phase": cp.PHASE_BOTTOM, "streak": 5}) == ""
    assert cp.streak_note({"phase": cp.PHASE_HIGH_VOLATILE, "streak": 3}) == ""


def test_streak_ebbing_day2_tells_partial_try():
    note = cp.streak_note({"phase": cp.PHASE_EBBING, "streak": 2})
    assert "尾盘" in note and "必须走" in note


def test_streak_survives_missing_state(tmp_path):
    """状态文件不存在/损坏 → 从 1 开始，⛔ 不炸。"""
    assert cp.load_state(tmp_path) == {}
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cache" / cp.STATE_FILE).write_text("not json", encoding="utf-8")
    assert cp.load_state(tmp_path) == {}
    r = cp.track_state(cp.phase_of(_fb("2026-10-21")), root=tmp_path)
    assert r["streak"] == 1


def test_track_state_persist_false_writes_nothing(tmp_path):
    cp.track_state(cp.phase_of(_fb("2026-10-21")), root=tmp_path, persist=False)
    assert not (tmp_path / "cache" / cp.STATE_FILE).exists()


# ── 6) 「只差催化」替代路径（⛔ 默认关闭）────────────────────────────────
_DIMS_OK = {"competition": {"status": "theme_winner"},
            "sector_follow": {"status": "sustained"},
            "divergence": {"status": "recovery_with_carry"},
            "launch": {"status": "new_direction_candidate"},
            "catalyst": {"status": "unknown"}}


def _relief_row(dims=None, **kw):
    row = {"code": "600001", "name": "测试龙", "boards": 5, "carry_cnt": 6,
           "leader_evidence": {"confirmation": "pending", "continuous_days": 4,
                               "dimensions": dims or _DIMS_OK}}
    row.update(kw)
    return row


_SEQ_OK = _seq(["144500", "103000", "093500"], [3.0, 7.5, 4.0])   # 加速 + 爆量后缩量


def test_relief_on_by_default(monkeypatch):
    """用户 2026-10-21 拍板 A：替代路径**默认开启**（自动化不设环境变量）。"""
    monkeypatch.delenv(dc.ALLOW_NO_CATALYST_ENV, raising=False)
    assert dc.ALLOW_NO_CATALYST is True
    r = dc.confirm(_relief_row(), _SEQ_OK)
    assert r["level"] in (dc.LEVEL_CONFIRMED, dc.LEVEL_CONFIRMED_THIN)


def test_relief_can_be_switched_off(monkeypatch):
    """⛔ 环境变量可一键关掉（回到「只差催化仍是准龙头」）。"""
    monkeypatch.setenv(dc.ALLOW_NO_CATALYST_ENV, "0")
    r = dc.confirm(_relief_row(), _SEQ_OK)
    assert r["level"] == dc.LEVEL_PARTIAL


def test_relief_on_promotes_only_catalyst_missing(monkeypatch):
    monkeypatch.setenv(dc.ALLOW_NO_CATALYST_ENV, "1")
    r = dc.confirm(_relief_row(), _SEQ_OK)
    assert r["level"] == dc.LEVEL_CONFIRMED
    assert "催化这一项目前拿不到证据" in r["narrative"]   # 措辞必须诚实


def test_relief_does_not_apply_when_catalyst_explicitly_weak(monkeypatch):
    """催化**明确**只有单日（不是 unknown）→ 不套替代路径，说明确实还差催化。"""
    monkeypatch.setenv(dc.ALLOW_NO_CATALYST_ENV, "1")
    dims = {**_DIMS_OK, "catalyst": {"status": "single_verified"}}
    r = dc.confirm(_relief_row(dims), _SEQ_OK)
    assert r["level"] != dc.LEVEL_CONFIRMED


def test_relief_ignores_scales_for_qualification(monkeypatch):
    """⛔ 两把尺**不进资格判据**（叙述层）：尺都没过时仍可确认，但等级降为「未经验证」。

    这是 2026-10-21 修正的关键点：首版把两把尺塞进替代条件，导致命中恒为 0；
    而原文 confirmed 根本不看它们。正确做法＝尺只决定 confirmed vs confirmed_thin。
    """
    monkeypatch.setenv(dc.ALLOW_NO_CATALYST_ENV, "1")
    bad = [{"date": f"2026-10-1{i}", "fbt": "140000", "cje_yi": 3.0, "boards": i + 1}
           for i in range(3)]
    r = dc.confirm(_relief_row(), bad)
    assert r["level"] == dc.LEVEL_CONFIRMED_THIN
    assert r["identity_role"] == "龙头"


def test_relief_requires_original_confirmed_dims(monkeypatch):
    """其余四维（跟随/分歧/启动）缺一条 → 不放行。"""
    monkeypatch.setenv(dc.ALLOW_NO_CATALYST_ENV, "1")
    for bad_dim in ("sector_follow", "divergence", "launch"):
        dims = {**_DIMS_OK, bad_dim: {"status": "unknown"}}
        r = dc.confirm(_relief_row(dims), _SEQ_OK)
        assert r["level"] != dc.LEVEL_CONFIRMED, bad_dim


def test_relief_does_not_depend_on_competition(monkeypatch):
    """⛔ competition 不是条件：原文 confirmed 不看它（实测全是 not_in_cohort/promoted）。

    首版误把它当条件 → 命中恒 0。此测试锁住「不再犯」。
    """
    monkeypatch.setenv(dc.ALLOW_NO_CATALYST_ENV, "1")
    dims = {**_DIMS_OK, "competition": {"status": "not_in_cohort"}}
    r = dc.confirm(_relief_row(dims), _SEQ_OK)
    assert r["level"] in (dc.LEVEL_CONFIRMED, dc.LEVEL_CONFIRMED_THIN)


def test_relief_promotes_role_only_upward():
    """⛔ 只升不降：认定龙头 → role 升级；认定准龙头 → role 不动（五维确认过受保护）。"""
    row = _relief_row()
    assert dc.confirm(row, _SEQ_OK)["identity_role"] == "龙头"
    weak = _relief_row(leader_evidence={"confirmation": "pending", "continuous_days": 1,
                                        "dimensions": {**_DIMS_OK, "sector_follow": {"status": "alone"}}})
    assert dc.confirm(weak, [])["identity_role"] == "准龙头"


def test_relief_requires_carry_and_continuity(monkeypatch):
    monkeypatch.setenv(dc.ALLOW_NO_CATALYST_ENV, "1")
    assert dc.confirm(_relief_row(carry_cnt=0), _SEQ_OK)["level"] != dc.LEVEL_CONFIRMED
    row = _relief_row()
    row["leader_evidence"]["continuous_days"] = 2
    assert dc.confirm(row, _SEQ_OK)["level"] != dc.LEVEL_CONFIRMED


# ── 7) 盘后卡也要标龙头（2026-10-21 补齐遗漏）────────────────────────────
def _tag_cloud(item):
    from pipelines import build_cards as bc
    return bc._candidate_tags(item)


def test_eod_card_tags_dragon(_=None):
    """盘后卡（_candidate_tags）：龙头必须出现在标签云**第一位**。"""
    item = {"code": "002724", "name": "海洋王", "boards": 3,
            "dragon_confirm": {"enabled": True, "level": dc.LEVEL_CONFIRMED,
                               "label": "龙头", "checks": []}}
    tags = _tag_cloud(item)
    assert tags and tags[0] == ("🐉 龙头", "red")


def test_eod_card_tags_dragon_thin_yellow():
    item = {"code": "002724", "name": "海洋王", "boards": 3,
            "dragon_confirm": {"enabled": True, "level": dc.LEVEL_CONFIRMED_THIN,
                               "label": "龙头（未经验证）", "checks": []}}
    tags = _tag_cloud(item)
    # ⚠️ 2026-10-21 降权：未验证龙头从 orange（太抢）改为 yellow（中性）
    assert tags[0] == ("🐉 龙头·未验证", "yellow")


def test_eod_card_tags_no_dragon_for_pending():
    """⛔ 准龙头/跟风不标：每只都挂身份＝等于没身份。"""
    for lv in (dc.LEVEL_PARTIAL, dc.LEVEL_PENDING, dc.LEVEL_NOT):
        item = {"code": "600001", "name": "测试", "boards": 2,
                "dragon_confirm": {"enabled": True, "level": lv, "checks": []}}
        assert not any("龙头" in t for t, _c in _tag_cloud(item)), lv


def test_eod_card_tags_tolerates_missing_field():
    """旧候选无 dragon_confirm → 不炸、不标（诚实降级）。"""
    tags = _tag_cloud({"code": "600001", "name": "测试", "boards": 2})
    assert not any("龙头" in t for t, _c in tags)
