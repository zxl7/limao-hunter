# -*- coding: utf-8 -*-
"""ice 解冻（2026-09-09 用户「冰点也要推接力池·买不买由人决定」）单测。

锁定 quant_score._relay_fallback 收严级联（relaxed→broad→emergency→none）
与 ICE_FREEZE_RELAY 默认解冻开关。不触网。
"""
import importlib.util
from pathlib import Path
from unittest import mock

WEALTH = Path(__file__).resolve().parents[1] / "wealth_code"
spec = importlib.util.spec_from_file_location("quant_score", WEALTH / "core" / "quant_score.py")
qs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qs)


def _row(raw: float, name: str = "票"):
    return {"_raw": raw, "name": f"{name}{raw}"}


def test_ice_freeze_switch_default_off():
    """默认解冻（ICE_FREEZE_RELAY=False=新行为）；回退开关存在且可逆。"""
    assert qs.ICE_FREEZE_RELAY is False


def test_fallback_relaxed_first_and_capped3():
    """级联：有 relaxed 合格行 → mode=relaxed，取 raw 最高前 3。"""
    rows = [_row(88), _row(85), _row(80), _row(50)]  # 50 也会过 relaxed mock → 应被 cap 截断
    with mock.patch.object(qs, "_relay_relaxed_ok", return_value=True), \
         mock.patch.object(qs, "_relay_broad_ok", return_value=True), \
         mock.patch.object(qs, "_relay_emergency_ok", return_value=True):
        pool, mode = qs._relay_fallback(rows)
    assert mode == "relaxed"
    assert len(pool) == 3
    assert [r["_raw"] for r in pool] == [88, 85, 80]  # raw 降序


def test_fallback_broad_when_relaxed_empty():
    """relaxed 全不合格 → 下探 broad。"""
    rows = [_row(88)]
    with mock.patch.object(qs, "_relay_relaxed_ok", return_value=False), \
         mock.patch.object(qs, "_relay_broad_ok", return_value=True):
        pool, mode = qs._relay_fallback(rows)
    assert mode == "broad" and len(pool) == 1 and pool[0]["_raw"] == 88


def test_fallback_emergency_and_none():
    """relaxed/broad 都空 → emergency；全空 → ([], 'none') 诚实空不硬塞。"""
    rows = [_row(45)]
    with mock.patch.object(qs, "_relay_relaxed_ok", return_value=False), \
         mock.patch.object(qs, "_relay_broad_ok", return_value=False), \
         mock.patch.object(qs, "_relay_emergency_ok", return_value=True):
        pool, mode = qs._relay_fallback(rows)
    assert mode == "emergency" and len(pool) == 1
    with mock.patch.object(qs, "_relay_relaxed_ok", return_value=False), \
         mock.patch.object(qs, "_relay_broad_ok", return_value=False), \
         mock.patch.object(qs, "_relay_emergency_ok", return_value=False):
        pool, mode = qs._relay_fallback([_row(45)])
    assert mode == "none" and pool == []


def test_fallback_sorted_desc():
    """多合格行按 raw 降序（身位最高者优先）。"""
    rows = [_row(60, "B"), _row(90, "A"), _row(70, "C")]
    with mock.patch.object(qs, "_relay_relaxed_ok", return_value=True):
        pool, mode = qs._relay_fallback(rows)
    assert [r["name"] for r in pool] == ["A90", "C70", "B60"]


# ── 历史涨停基因常驻渲染行（build_cards._hist_gene_line）──

def _build_cards_module():
    import importlib.util as _iu
    from pathlib import Path as _P
    _spec = _iu.spec_from_file_location("build_cards", _P(__file__).resolve().parents[1] / "wealth_code" / "pipelines" / "build_cards.py")
    _m = _iu.module_from_spec(_spec)
    _spec.loader.exec_module(_m)
    return _m


def test_hist_gene_line_with_file(monkeypatch, tmp_path):
    """有 hist 档案 → 渲染基因行；缺失 → 空串（诚实省略）。"""
    bc = _build_cards_module()
    import json as _json
    (tmp_path / "cache" / "zt_genes").mkdir(parents=True)
    (tmp_path / "cache" / "zt_genes" / "hist_000759.json").write_text(_json.dumps(
        {"code": "000759", "zt_250": 13, "max_boards_hist": 6,
         "promo_rate_hist": 35.0, "first_prem_open_hist": 0.08}), encoding="utf-8")
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")   # ROOT.parent → tmp_path
    line = bc._hist_gene_line({"code": "000759"})
    assert "13次" in line and "6板" in line and "35%" in line and "+0.08%" in line
    assert bc._hist_gene_line({"code": "888888"}) == ""   # 无档案
    assert bc._hist_gene_line({"code": ""}) == ""
