#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘后卡 P1/P2/P3 三块渲染回归锁（2026-10-18 用户「按顺序处理」）。

三块：
· 🧩 新方向首板（P2·不占接力名额）
· 🔎 复盘说的今天兑现了没（P3·复盘→选股通道）
· 📎 成交额不够没进池（P1·不可买）

铁律（本文件锁死）：
1. 数据缺失 → **整块不渲染**（不写"暂无"占位）
2. P1 标题必须带「不可买」——看得到≠买得到
3. P2/P3 必须明确是观察信息，不得出现买入话术
4. 警示票语义相反：今天还在涨停=风险仍在（红）／没涨停=风险缓解（绿）
5. 每块尾部带 hr，块间不粘连
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code"))

from pipelines import build_cards as bc  # noqa: E402


def _texts(blocks):
    out = []
    for b in blocks:
        t = b.get("text")
        out.append(t.get("content") if isinstance(t, dict) else "")
    return out


def _full(payload):
    return "\n".join(_texts(bc._post_pool_blocks(payload)))


def _payload(**over):
    p = {
        "date": "2026-10-16",
        "new_direction_first_board": [
            {"code": "601606", "name": "长城军工", "boards": 1, "pct": 10.01, "core_theme": "军工"},
            {"code": "002085", "name": "万丰奥威", "boards": 1, "pct": 9.98, "core_theme": "低空经济"},
        ],
        "review_directions": {
            "available": True,
            "review_date": "2026-10-16",
            "stats": {
                "hits": [{"code": "000530", "name": "冰山冷热", "kind": "height"},
                         {"code": "601606", "name": "长城军工", "kind": "new_direction"}],
                "missed": [{"code": "002085", "name": "万丰奥威", "kind": "new_direction"},
                           {"code": "600825", "name": "新华传媒", "kind": "caution"}],
            },
        },
        "offpool_high_ident": [
            {"code": "600825", "name": "新华传媒", "boards": 5, "cje_yi": 2.47,
             "core_theme": "影视传媒", "offpool_reason": "成交额2.47亿＜5亿门槛", "buyable": False},
        ],
    }
    p.update(over)
    return p


def test_eod_merge_preserves_qualification_and_input():
    import copy
    payload = _payload(low_pool=[{"code": "601606", "name": "长城军工", "boards": 1,
                                  "buyable": True, "gene": {}, "dragon_note": "原风险说明"}])
    before = copy.deepcopy(payload)
    txt = "\n".join(_texts(bc._post_pool_blocks(payload, eod=True)))
    # 2026-11-11 起新方向「真入池」：线级摘要与低位池逐票各出现一次，
    # 因此同名出现 2 次属预期（不是重复渲染同一块）。
    assert txt.count("长城军工") == 2
    assert "原低位池资格·与接力池共用名额·待竞价裁决" in txt
    # 该行 buyable=True → 走「原低位池资格」分支；「只看不买」只对未获资格的行出现
    assert "原低位池资格" in txt
    assert "原风险说明" in txt and "不可买" in txt
    assert "今天对上了没" not in txt
    assert payload == before


def test_eod_tags_keep_promotion_and_risk():
    it = {"boards": 4, "is_sector_dragon": True, "hs": 20,
          "dim_panel": {"风险": {"label": "高"}}, "trig_gap_pct": 20,
          "zbc": 3, "cje_yi": 8, "lt_mktcap": 10, "seal_amt": 1}
    tags = bc._eod_candidate_tags(it)
    texts = [t for t, _ in tags]
    assert "4进5" in texts and "断板风险高" in texts
    assert "开板3次" in texts and "换手20.0%·换手大" in texts
    assert any("触线" in t for t in texts)
    assert not any(t.startswith(("成交", "流通", "封单")) for t in texts)
    assert ("成交8.0亿", "neutral") in bc._candidate_tags(it)


def test_empty_payload_renders_nothing():
    """⛔ 数据缺失 → 整块不渲染（不写占位）。"""
    assert bc._post_pool_blocks({}) == []


def test_new_direction_block_basics():
    txt = _full(_payload(offpool_high_ident=[], review_directions={}))
    assert "新方向" in txt and "已进池" in txt
    assert "长城军工" in txt and "万丰奥威" in txt
    # 2026-11-11 起新方向「真入池」，文案改为「已进池·明早竞价定」
    assert "明早竞价" in txt


def test_review_block_shows_hit_and_miss():
    txt = _full(_payload(new_direction_first_board=[], offpool_high_ident=[]))
    assert "复盘说的（10-16）今天对上了没" in txt
    assert "冰山冷热 今日涨停" in txt
    assert "万丰奥威 今日没涨停" in txt


def test_caution_semantics_reversed():
    """警示票：今天还在涨停=风险仍在；没涨停=风险缓解。"""
    p = _payload(new_direction_first_board=[], offpool_high_ident=[])
    p["review_directions"]["stats"]["hits"].append({"code": "600825", "name": "新华传媒", "kind": "caution"})
    p["review_directions"]["stats"]["missed"] = []
    txt = _full(p)
    assert "今天还在涨停" in txt and "停牌核查" in txt

    p2 = _payload(new_direction_first_board=[], offpool_high_ident=[])
    txt2 = _full(p2)
    assert "风险缓解" in txt2


def test_offpool_block_marks_not_buyable():
    txt = _full(_payload(new_direction_first_board=[], review_directions={}))
    assert "不可买" in txt, "⛔ P1 必须明说不可买"
    assert "新华传媒(600825)" in txt
    assert "成交额2.47亿" in txt


def test_blocks_end_with_hr():
    blocks = bc._post_pool_blocks(_payload())
    assert blocks[-1].get("tag") == "hr", "最后一块必须以 hr 收尾，避免与后续内容粘连"


def test_blocks_have_no_buy_wording():
    """⛔ 三块只是观察信息，不得出现买入话术。"""
    txt = _full(_payload())
    for banned in ("建议买入", "可以买", "果断买", "必须买"):
        assert banned not in txt


def test_no_placeholder_words():
    for empty in ({}, {"new_direction_first_board": []}, {"offpool_high_ident": []},
                  {"review_directions": {"available": False}}):
        assert bc._post_pool_blocks(empty) == [], f"空数据不得产出占位块: {empty}"


def test_pct_none_does_not_crash():
    p = _payload(new_direction_first_board=[
        {"code": "601606", "name": "长城军工", "boards": 1, "pct": None, "core_theme": "军工"}])
    txt = _full(p)
    assert "长城军工" in txt


def test_missing_theme_still_renders():
    p = _payload(new_direction_first_board=[
        {"code": "601606", "name": "长城军工", "boards": 1, "pct": 10.0}])
    assert "长城军工" in _full(p)
