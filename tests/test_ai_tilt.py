#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""智能评分层（2026-11-11 计划）契约测试。

锁定四件事：
1. 特征维度是**契约**：维度/顺序变了 → 旧模型必须拒绝加载（⛔ 不许静默错配）；
2. 模型缺失/损坏/开关关 → 打分返回空，上层等价于纯机械；
3. shadow 模式**不改变任何 view**（这是「只记录不执行」的硬保证）；
4. 弃权只能减少参与，⛔ 不能增加。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealth_code.core import ai_tilt as at  # noqa: E402


def _model(**over) -> dict:
    m = {
        "version": "ai_tilt_test",
        "features": list(at.FEATURES),
        "mean": [0.0] * len(at.FEATURES),
        "std": [1.0] * len(at.FEATURES),
        "weights": [0.0] * len(at.FEATURES),
        "bias": 0.0,
    }
    m.update(over)
    return m


def test_features_shape_and_missing_neutral():
    x = at.features({"code": "000001"})
    assert len(x) == len(at.FEATURES)
    # 缺 gap_to_top → 指示位为 1（⛔ 不把「没数据」当利空）
    assert x[at.FEATURES.index("gap_top_missing")] == 1.0


def test_load_model_rejects_feature_mismatch(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(_model(features=["boards"])), encoding="utf-8")
    assert at.load_model(p) is None


def test_load_model_tolerates_broken_file(tmp_path: Path):
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    assert at.load_model(p) is None
    assert at.score_rows(None, [{"code": "1"}]) == {}


def test_predict_is_probability():
    m = _model(bias=0.5)
    p = at.predict(m, {"boards": 2, "line_rank": 1})
    assert 0.0 < p < 1.0


def test_advice_payload_shape():
    m = _model()
    adv = at.advice_payload(m, [{"code": "000001"}, {"code": "000002"}], mode="shadow")
    assert adv["schema"] and adv["mode"] == "shadow"
    assert set(adv["scores"]) == {"000001", "000002"}


def _replay_day():
    """取最近一个有完整候选+竞价的回放日；缺数据则跳过（CI 无 cache 时不误报）。"""
    import pytest
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(ROOT / "wealth_code"))
    import pool_call_replay as R
    days = R.trading_days()
    for d in reversed(days):
        day = R.replay_day(d, days)
        if day:
            return R, day
    pytest.skip("无回放数据（cache 缺失）")


def test_shadow_does_not_change_views():
    """shadow = 只记录；⛔ 任何 view 都不许变。"""
    R, day = _replay_day()
    from core import pool_call as pc
    base = pc.call_pool(day["cands"], day["quotes"], **day["kw"])
    pool = [*(day["cands"].get("relay") or []), *(day["cands"].get("watch") or [])]
    adv = at.advice_payload(_model(), pool, mode="shadow")
    res = pc.call_pool(day["cands"], day["quotes"], advice=adv, **day["kw"])
    assert {c: v["view"] for c, v in base["views"].items()} == \
           {c: v["view"] for c, v in res["views"].items()}
    ai = (res.get("meta") or {}).get("ai_layer") or {}
    assert ai.get("mode") == "shadow"
    assert "M" in (ai.get("arms") or {}) and "MA" in (ai.get("arms") or {})


def test_abstain_only_removes_participants(monkeypatch):
    """live + 弃权：只能把参与降为观察，⛔ 不能把观察变成参与。"""
    R, day = _replay_day()
    from core import pool_call as pc
    base = pc.call_pool(day["cands"], day["quotes"], **day["kw"])
    base_parts = {c for c, v in base["views"].items() if v["view"] == "participate"}
    monkeypatch.setattr(pc, "AI_TILT_MODE", "live", raising=False)
    monkeypatch.setattr(pc, "AI_TILT_ABSTAIN", True, raising=False)
    monkeypatch.setattr(pc, "AI_TILT_ABSTAIN_P", 0.5, raising=False)
    pool = [*(day["cands"].get("relay") or []), *(day["cands"].get("watch") or [])]
    adv = at.advice_payload(_model(bias=-5.0), pool, mode="live")   # 全部极低分
    res = pc.call_pool(day["cands"], day["quotes"], advice=adv, **day["kw"])
    parts = {c for c, v in res["views"].items() if v["view"] == "participate"}
    assert parts <= base_parts          # 子集：没有新增参与
