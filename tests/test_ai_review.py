#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🧠 09:26 池内复核（`core/ai_review`）契约测试

锁住三条**不可回退**的行为（2026-11-13 接入）：
1. **只降不升**：机械结论否决过的票，模型说 buy 也必须压回 watch。
2. **只评池内**：模型吐出的池外票一律丢弃，池内漏答的补 watch（⛔ 不补 buy）。
3. **黑话不上卡**：模型自由文本必须能过 `card_copy_rules`，否则整句丢弃。

第 3 条是 11-13 全天无卡事故的直接产物：`口径：` 命中 PLAIN_FAIL → `push_card` 硬阻断。
模型是自由文本源，比模板更容易吐黑话 ⇒ 必须有测试兜住。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "wealth_code", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core import ai_review  # noqa: E402


def _views(**kw):
    """构造 views：{code: {"view": ..., "name": ...}}"""
    return {code: {"view": v[0], "name": v[1]} for code, v in kw.items()}


def test_mechanical_veto_cannot_be_upgraded():
    views = _views(X=("watch", "甲"))
    raw = {"market": "冷", "picks": [{"code": "X", "action": "buy", "why": "很好"}]}
    r = ai_review._normalize(raw, views)
    p = r["picks"][0]
    assert p["action"] == "watch", "机械否决过的票，模型无权放行"
    assert p["capped"] is True
    assert r["buy_count"] == 0
    assert r["verdict"] == "空仓"


def test_participate_can_be_downgraded_to_skip():
    """本模块唯一能增加的决策价值：机械说可买，模型可以否掉。"""
    views = _views(X=("participate", "甲"))
    raw = {"picks": [{"code": "X", "action": "skip", "why": "换手太高"}]}
    r = ai_review._normalize(raw, views)
    assert r["picks"][0]["action"] == "skip"
    assert r["buy_count"] == 0


def test_participate_buy_kept():
    views = _views(X=("participate", "甲"))
    raw = {"picks": [{"code": "X", "action": "buy", "why": "量能够"}]}
    r = ai_review._normalize(raw, views)
    assert r["picks"][0]["action"] == "buy"
    assert r["buy_count"] == 1
    assert r["verdict"] == "可买 1 只"


def test_out_of_pool_picks_dropped():
    views = _views(X=("watch", "甲"))
    raw = {"picks": [{"code": "Y", "action": "buy"}, {"code": "X", "action": "watch"}]}
    r = ai_review._normalize(raw, views)
    codes = [p["code"] for p in r["picks"]]
    assert "Y" not in codes, "池外票必须丢弃（11-13 教训：送了 7 只 6 只在池外）"
    assert codes == ["X"]


def test_missing_pick_filled_as_watch():
    views = _views(X=("participate", "甲"), Y=("watch", "乙"))
    r = ai_review._normalize({"picks": [{"code": "X", "action": "buy"}]}, views)
    fill = {p["code"]: p for p in r["picks"]}
    assert fill["Y"]["action"] == "watch", "漏答补 watch，⛔ 不补 buy"
    assert fill["Y"]["why"] == "模型未作答"


def test_illegal_action_normalized():
    views = _views(X=("participate", "甲"))
    r = ai_review._normalize({"picks": [{"code": "X", "action": "strong_buy"}]}, views)
    assert r["picks"][0]["action"] == "watch"


def test_blackword_text_must_not_reach_card():
    """模型吐黑话 → 要么洗掉，要么整句丢弃；⛔ 不许带病上卡。"""
    import card_copy_rules as CCR
    views = _views(X=("watch", "甲"), Y=("watch", "乙"))
    raw = {"market": "按 2026-11-11 口径：从 2 板看",
           "picks": [{"code": "X", "action": "watch", "why": "口径：首板不看"},
                     {"code": "Y", "action": "watch", "why": "量能不足，0.43 亿低于所需 0.58 亿"}]}
    r = ai_review._normalize(raw, views)
    assert "口径：" not in r["market"]
    for p in r["picks"]:
        assert "口径：" not in p["why"]
    # 干净的句子必须保留（不能因为清洗把有效信息全扔了）
    assert any(p["why"] for p in r["picks"])
    fails, _ = CCR.scan(r["market"] + " ".join(p["why"] for p in r["picks"]))
    assert not fails, f"清洗后仍命中阻断词：{fails}"


def test_empty_views_returns_none_without_calling_model():
    """池内 0 只 ⇒ 机械已否决一切，⛔ 不该白烧 token。"""
    assert ai_review.review("2026-11-13", {}, None) is None


def test_disabled_switch():
    import os
    os.environ["AI_REVIEW"] = "0"
    try:
        assert ai_review.enabled() is False
        assert ai_review.review("2026-11-13", _views(X=("participate", "甲"))) is None
    finally:
        os.environ.pop("AI_REVIEW", None)
    assert ai_review.enabled() is True


def test_prompt_only_contains_pool_codes():
    views = _views(X=("watch", "甲"))
    p = ai_review.build_prompt(views, {"promote_rate": 18.9})
    assert "X" in p and "甲" in p
    assert "promote_rate" in p
