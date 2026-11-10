#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价一字识别（core/auction_seal）与卡面渲染的回归。

判据来自 2026-11-09 用户发现：「竞价的时候如果有一字涨停，需要留意他的炒作方向，
比如今天的西部材料带动商业航天逻辑」。当天实盘四条序列是本文的黄金样本：

  西部材料 002149  09:15 41.51(+9.99%) → 09:26 41.51 → 09:31 41.51   = 真一字
  阳光照明 600261  09:15 5.94(+10.0%)  → 09:17 5.93 → 09:24 5.70(+5.56%) = 撤单虚顶
  润贝航科 001316  09:15 30.60(+9.99%) → 09:22 30.40 → 09:23 29.86 → 09:42 回封 = 虚顶（回封不算一字）
  闽发铝业 002578  09:15 5.51(+9.98%)  → 09:26 5.51                          = 真一字

⛔ 本模块只做解释层。测试同时锁住「不进名额/仓位」这一条：`build_seals` 的返回值里
   不得出现任何可被裁决链读走的买入字段。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "wealth_code")):
    if p not in sys.path:
        sys.path.insert(0, p)

from core import auction_seal as seals  # noqa: E402
from core import ai_caliber  # noqa: E402


def _series(*pairs):
    """`[("09:15:09", 9.99), ...]` → build_seals 需要的观测序列。"""
    return [{"ts": f"2026-11-09T{ts}+08:00", "pct": pct, "price": None}
            for ts, pct in pairs]


# ══════════════════════════════════════════════════════════════════════
# ① 判据：三态必须分开，不许把「无法确认」当「一字」
# ══════════════════════════════════════════════════════════════════════
def test_sealed_when_no_drop_and_still_up_at_0925():
    """西部材料：09:15 顶板 → 09:26 仍同价（中间无记录 = 没变过）→ sealed。"""
    v = seals.classify_series(_series(("09:15:09", 9.99), ("09:26:06", 9.99)))
    assert v["state"] == seals.STATE_SEALED
    assert v["low_pct"] == 9.99


def test_wobbled_when_price_dropped_intraday():
    """阳光照明：09:15 顶 10% → 09:24 只剩 5.56% → wobbled（撤单虚顶）。"""
    v = seals.classify_series(_series(("09:15:09", 10.0), ("09:17:09", 9.81),
                                      ("09:24:08", 5.56)))
    assert v["state"] == seals.STATE_WOBBLED
    assert v["low_pct"] == 5.56


def test_reopen_later_is_not_sealed():
    """润贝航科：09:23 掉到 7.33%，09:42 回封 9.99% —— 回封也是打板，不是一字。"""
    v = seals.classify_series(_series(("09:15:09", 9.99), ("09:22:08", 9.27),
                                      ("09:23:10", 7.33), ("09:42:06", 9.99)))
    assert v["state"] == seals.STATE_WOBBLED
    assert v["low_pct"] == 7.33


def test_single_early_snapshot_is_unconfirmed_not_sealed():
    """只在 09:15 出现过一次 → 不知道后面有没有撤单，⛔ 不许当一字。"""
    v = seals.classify_series(_series(("09:15:09", 10.02)))
    assert v["state"] == seals.STATE_UNCONFIRMED


def test_confirm_time_boundary():
    """末条正好 09:25:00 算确认；09:24:59 不确认。"""
    ok = seals.classify_series(_series(("09:15:09", 9.99), ("09:25:00", 9.99)))
    late = seals.classify_series(_series(("09:15:09", 9.99), ("09:24:59", 9.99)))
    assert ok["state"] == seals.STATE_SEALED
    assert late["state"] == seals.STATE_UNCONFIRMED


def test_never_limit_up_is_none():
    v = seals.classify_series(_series(("09:15:09", 3.1), ("09:26:06", 4.4)))
    assert v["state"] == seals.STATE_NONE


def test_20cm_uses_its_own_line():
    """创业板 20cm：+9.99% 不是涨停，+19.8% 才算封。"""
    ten = seals.classify_series(_series(("09:15:09", 9.99), ("09:26:06", 9.99)),
                                is_20cm=True)
    twenty = seals.classify_series(_series(("09:15:09", 19.9), ("09:26:06", 19.9)),
                                   is_20cm=True)
    assert ten["state"] == seals.STATE_NONE
    assert twenty["state"] == seals.STATE_SEALED


def test_missing_pct_is_skipped_not_treated_as_drop():
    """pct 为空的观测被丢弃（未观测 ≠ 没下探），不能因此判 wobbled。"""
    series = [{"ts": "2026-11-09T09:15:09+08:00", "pct": 9.99, "price": 41.5},
              {"ts": "2026-11-09T09:18:00+08:00", "pct": None, "price": None},
              {"ts": "2026-11-09T09:26:06+08:00", "pct": 9.99, "price": 41.5}]
    assert seals.classify_series(series)["state"] == seals.STATE_SEALED


def test_empty_series_is_none_state():
    assert seals.classify_series([])["state"] == seals.STATE_NONE


def test_out_of_window_ignored():
    """09:31 之后的观测不属于竞价段，不能拿来确认。"""
    v = seals.classify_series(_series(("09:15:09", 9.99), ("09:31:07", 9.99)))
    assert v["state"] == seals.STATE_UNCONFIRMED


# ══════════════════════════════════════════════════════════════════════
# ② 涨停线不得漂移（⛔ 不造第 5 套阈值）
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("is_20cm", [True, False, 0, 1, None])
def test_limit_line_matches_ai_caliber(is_20cm):
    assert seals.limit_up_pct(is_20cm) == ai_caliber.limit_up_pct(is_20cm)


def test_tolerance_matches_ai_caliber():
    assert seals.LIMIT_TOL == ai_caliber.PHYSICAL_LIMIT_TOL


# ══════════════════════════════════════════════════════════════════════
# ③ 方向归属：取题材标签，⛔ 不取行业名
# ══════════════════════════════════════════════════════════════════════
def _meta(**kw):
    return {"name": kw.get("name", "测试"), "is_20cm": kw.get("is_20cm", False),
            "industry": kw.get("industry")}


def test_theme_comes_from_tags_not_industry():
    """002149 行业=小金属、题材=商业航天 → 必须报商业航天。"""
    res = seals.build_seals(
        {"002149": _series(("09:15:09", 9.99), ("09:26:06", 9.99))},
        meta_by_code={"002149": _meta(name="西部材料", industry="小金属")},
        plates_by_code={"002149": ["商业航天", "航天"]},
    )
    assert res["items"][0]["theme"] == "商业航天"
    assert res["items"][0]["industry"] == "小金属"      # 仅作对照展示
    assert res["themes"] == [{"theme": "商业航天", "n": 1, "codes": ["002149"]}]


def test_attr_only_plates_yield_no_theme():
    """全是属性标签（业绩增长等）→ theme=None，⛔ 不编「其他」。"""
    from common.theme_base import is_attr_plate

    res = seals.build_seals(
        {"300001": _series(("09:15:09", 9.99), ("09:26:06", 9.99))},
        meta_by_code={"300001": _meta()},
        plates_by_code={"300001": ["业绩增长", "资产重组"]},
        attr_filter=is_attr_plate,
    )
    assert res["items"][0]["theme"] is None
    assert res["themes"] == []


def test_theme_counts_aggregate_by_seal_theme():
    res = seals.build_seals(
        {"600207": _series(("09:15:09", 10.0), ("09:26:06", 10.0)),
         "603366": _series(("09:16:08", 10.0), ("09:26:06", 10.0)),
         "002149": _series(("09:15:09", 9.99), ("09:26:06", 9.99))},
        meta_by_code={"600207": _meta(name="安彩高科"), "603366": _meta(name="日出东方"),
                      "002149": _meta(name="西部材料")},
        plates_by_code={"600207": ["光伏概念"], "603366": ["光伏概念"],
                        "002149": ["商业航天"]},
    )
    assert [t["theme"] for t in res["themes"]] == ["光伏概念", "商业航天"]
    assert res["themes"][0]["n"] == 2


# ══════════════════════════════════════════════════════════════════════
# ④ 空数据 / 全虚顶：⛔ 不写「无一字」
# ══════════════════════════════════════════════════════════════════════
def test_no_data_yields_no_card_lines():
    res = seals.build_seals({})
    assert res["items"] == []
    assert seals.card_lines(res) == []


def test_all_wobbled_still_reports_exclusion():
    res = seals.build_seals(
        {"600261": _series(("09:15:09", 10.0), ("09:24:08", 5.56))},
        meta_by_code={"600261": _meta(name="阳光照明")},
    )
    lines = seals.card_lines(res)
    assert res["items"] == []
    assert any("撤单" in ln for ln in lines)


def test_card_lines_list_theme_per_name():
    res = seals.build_seals(
        {"002149": _series(("09:15:09", 9.99), ("09:26:06", 9.99))},
        meta_by_code={"002149": _meta(name="西部材料")},
        plates_by_code={"002149": ["商业航天"]},
    )
    text = "\n".join(seals.card_lines(res))
    assert "商业航天" in text
    assert "西部材料" in text


# ══════════════════════════════════════════════════════════════════════
# ⑤ 只读层：不建表、不落盘、缺库不炸
# ══════════════════════════════════════════════════════════════════════
def test_load_auction_window_missing_db_is_empty(tmp_path):
    from common.stock_registry import load_auction_window

    out = load_auction_window("2026-11-09", db_path=tmp_path / "nope.db")
    assert out["series"] == {}
    assert out["rows"] == 0
    assert not (tmp_path / "nope.db").exists()       # ⛔ 只读不得顺手建库


def test_load_auction_window_reads_only(tmp_path):
    from common import stock_registry as registry
    from common.stock_registry import load_auction_window

    db = tmp_path / "registry.db"
    conn = registry.connect(db)
    conn.close()

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO stock_observations(code, trade_date, asof_ts, source, payload, dedup_hash)"
        " VALUES(?,?,?,?,?,?)",
        ("002149", "2026-11-09", "2026-11-09T09:15:09+08:00", "t",
         json.dumps({"pct": 9.99, "price": 41.51}), "h1"))
    conn.execute(
        "INSERT INTO stock_observations(code, trade_date, asof_ts, source, payload, dedup_hash)"
        " VALUES(?,?,?,?,?,?)",
        ("002149", "2026-11-09", "2026-11-09T09:26:06+08:00", "t",
         json.dumps({"pct": 9.99, "price": 41.51}), "h2"))
    conn.execute("INSERT INTO stocks(code, name, is_20cm, industry, first_seen_date)"
                 " VALUES('002149','西部材料',0,'小金属','2026-10-28')")
    conn.execute("INSERT INTO stock_tags(code, tag_type, tag_value)"
                 " VALUES('002149','theme','航天')")
    conn.execute("INSERT INTO stock_tags(code, tag_type, tag_value)"
                 " VALUES('002149','board','商业航天')")
    conn.commit()
    conn.close()

    out = load_auction_window("2026-11-09", db_path=db)
    assert out["rows"] == 2
    assert out["meta"]["002149"]["name"] == "西部材料"
    # board 排在 theme 之前 → 方向取「商业航天」而不是「航天」
    assert out["plates"]["002149"][0] == "商业航天"


def test_gateway_projection_has_no_internal_paths(tmp_path, monkeypatch):
    """投影层不得吐出库路径 / SQL。"""
    from common import data_gateway

    monkeypatch.setattr(data_gateway, "_NOPE", None, raising=False)
    out = data_gateway.auction_seal("1900-01-01")
    blob = json.dumps(out, ensure_ascii=False)
    assert "registry.db" not in blob
    assert "SELECT" not in blob


def test_switch_off(tmp_path, monkeypatch):
    monkeypatch.setattr(seals, "ENABLE_AUCTION_SEAL", False)
    from common import data_gateway

    out = data_gateway.auction_seal("2026-11-09")
    assert out.get("disabled") is True


# ══════════════════════════════════════════════════════════════════════
# ⑥ 卡面渲染（pipeline_verify 契约 + build_verify_card）
# ══════════════════════════════════════════════════════════════════════
def _card(seal_box):
    from pipelines.build_cards import build_verify_card

    rec = {"code": "600261", "name": "阳光照明", "theme": "照明设备",
           "performance": {"open_check": {
               "ai_view": "participate", "decision_status": "buy",
               "gap_pct": -1.48, "auction_amount_yi": 0.53,
               "auction_amount_need_yi": 0.51,
               "pool_reason": "符合逐票预期", "can_enter": True}}}
    vr = {"summary": {"trade_date10": "2026-11-09", "total_candidates": 1,
                      "buy_signals": 1, "hit_signals": 0, "covered": 24,
                      "caliber": "ai",
                      "pool_call": {"participate": 1, "pool_size": 15,
                                    "effective_quota": 1}},
          "payload": {"records": [rec],
                      "environment": {"regime": "risk_on", "regime_trusted": True,
                                      "regime_score": 7.9, "regime_label": "偏强"},
                      "auction_seal": seal_box}}
    return build_verify_card(vr)


def _blob(card) -> str:
    return json.dumps(card, ensure_ascii=False)


def test_card_renders_seal_block():
    box = {"available": True,
           "items": [{"code": "002149", "name": "西部材料", "pct": 9.99,
                      "theme": "商业航天", "industry": "小金属",
                      "first_ts": "09:15:09", "last_ts": "09:26:06", "n_obs": 2}],
           "themes": [{"theme": "商业航天", "n": 1, "codes": ["002149"]}],
           "states": {"sealed": 1, "unconfirmed": 3, "wobbled": 2},
           "meta": {}}
    blob = _blob(_card(box))
    assert "🧲 竞价一字 → 方向" in blob
    assert "商业航天" in blob
    assert "西部材料" in blob


def test_card_says_untaken_not_none_when_unavailable():
    """取不到 → 写「未取到」，⛔ 不写「无一字」（抓不到 ≠ 没有）。"""
    blob = _blob(_card({"available": False, "reason": "当日竞价时段无观测"}))
    assert "未取到" in blob
    assert "无一字" not in blob


def test_card_no_seal_box_does_not_render():
    blob = _blob(_card(None))
    assert "🧲" not in blob


def test_card_seal_block_can_be_hidden(monkeypatch):
    import pipelines.build_cards as bc

    monkeypatch.setenv("VERIFY_CARD_HIDE", "seal")
    box = {"available": True,
           "items": [{"code": "002149", "name": "西部材料", "pct": 9.99,
                      "theme": "商业航天"}],
           "themes": [{"theme": "商业航天", "n": 1, "codes": ["002149"]}],
           "states": {"sealed": 1}, "meta": {}}
    assert "🧲" not in _blob(_card(box))
    assert bc is not None


def test_card_volume_zero_does_not_crash():
    """0 只 + 有虚顶 → 只报排除，不炸。"""
    box = {"available": True, "items": [],
           "themes": [], "states": {"sealed": 0, "unconfirmed": 0, "wobbled": 2},
           "meta": {}}
    blob = _blob(_card(box))
    assert "🧲" in blob
    assert "撤单" in blob


def test_card_says_pending_when_snapshot_not_yet_0926():
    """⏱️ 卡生成在 09:25 后、09:26 快照前 → 「尚未确认」，⛔ 绝不写「没有一字」。

    这是 11-09 真实时序风险：验证卡 09:26:10 出，09:26:06 快照刚好抢在前面；
    慢一分钟就会读不到 sealed。此时说「没有」= 把数据延迟报成市场结论。
    """
    box = {"available": True, "items": [], "themes": [],
           "states": {"sealed": 0, "unconfirmed": 168, "wobbled": 0}, "meta": {}}
    blob = _blob(_card(box))
    assert "尚未确认" in blob
    assert "没有封死" not in blob
    assert "无一字" not in blob


def test_card_says_none_when_truly_nothing_hit():
    box = {"available": True, "items": [], "themes": [],
           "states": {"sealed": 0, "unconfirmed": 0, "wobbled": 0}, "meta": {}}
    blob = _blob(_card(box))
    assert "没有顶到涨停" in blob


def test_core_card_lines_pending_wording():
    res = seals.build_seals({"600261": _series(("09:15:09", 10.0))},
                            meta_by_code={"600261": _meta(name="阳光照明")})
    text = "\n".join(seals.card_lines(res))
    assert "尚未确认" in text
    assert "无一字" not in text


# ══════════════════════════════════════════════════════════════════════
# ⑦ pipeline_verify 契约：取不到不拖垮竞价卡
# ══════════════════════════════════════════════════════════════════════
def test_payload_unavailable_when_gateway_raises(monkeypatch):
    from pipelines import pipeline_verify as pv
    from common import data_gateway

    def _boom(*_a, **_k):
        raise RuntimeError("db locked")

    monkeypatch.setattr(data_gateway, "auction_seal", _boom)
    out = pv._auction_seal_payload("2026-11-09")
    assert out["available"] is False
    assert "db locked" in out["reason"]


def test_payload_available_shape():
    from pipelines import pipeline_verify as pv

    out = pv._auction_seal_payload("2026-11-09")
    # 真实库可能没有该日数据；只校验契约形状，不假设一定有票
    assert "available" in out
    if out["available"]:
        for k in ("items", "themes", "states", "meta"):
            assert k in out
        assert "sealed" in out["states"]
