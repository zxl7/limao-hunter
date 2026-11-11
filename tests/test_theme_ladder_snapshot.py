#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板块强度 + 同题材身位快照（P0）的契约测试。

锁定三件事：
1. **板块强度**：按「家数 → 最高板 → 高度总和」分层排序，⛔ 不做加权；
2. **身位三套排名**同时落盘，且各自的行为可复现（板数键 / 动量键 / 现行键）；
3. **缺料不编造**：K 线缺失 → 涨幅 null + `ret_missing=true`，排序按 0 中性。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealth_code.core.ladder_rank import KlineReturns, build_ladder  # noqa: E402
from wealth_code.core.theme_strength import build_theme_strength  # noqa: E402


def zt(code: str, name: str, boards: int, *, fbt="093000", zbc=0, seal=1.0,
       cje=5.0, sector="机器人") -> dict:
    return {"code": code, "name": name, "boards": boards, "fbt": fbt, "zbc": zbc,
            "seal": seal, "cje_yi": cje, "sector": sector, "pct": 10.0}


def kline_dir(tmp_path: Path, code: str, closes: list[float], day: str) -> Path:
    """造一段以 `day` 结尾的日线（前 n-1 根用序号日期，最后一根就是 day）。"""
    d = tmp_path / "kline_qfq"
    d.mkdir(exist_ok=True)
    bars = [{"date": f"2026-01-{i + 1:02d}", "close": c} for i, c in enumerate(closes[:-1])]
    bars.append({"date": day, "close": closes[-1]})
    (d / f"{code}.json").write_text(json.dumps({"bars": bars}), encoding="utf-8")
    return d


def test_strength_uses_production_caliber(monkeypatch):
    """默认口径 = `market_theme_rank`（高度/梯队为主，家数弱辅助）。"""
    monkeypatch.delenv("POOL_CALL_MARKET_THEME_RANK", raising=False)
    rows = [zt(f"00010{i}", f"甲{i}", 1, sector="广度线") for i in range(5)]
    rows.append(zt("000999", "高度线龙头", 3, sector="高度线"))
    d = build_theme_strength("1999-01-01", rows)
    assert d["strength_basis"] == "market_theme_rank_v1"
    # 5 家 1 板 ≠ 强；1 家 3 板 = 高
    assert d["themes"][0]["theme"] == "高度线"
    assert d["themes"][0]["leader"]["code"] == "000999"


def test_strength_falls_back_to_count_when_switch_off(monkeypatch):
    """开关关闭 → 家数主序，且 basis/caliber 必须写明（⛔ 不许悄悄换口径）。"""
    monkeypatch.setenv("POOL_CALL_MARKET_THEME_RANK", "0")
    rows = [zt(f"00010{i}", f"甲{i}", 1, sector="广度线") for i in range(5)]
    rows.append(zt("000999", "高度线龙头", 3, sector="高度线"))
    d = build_theme_strength("1999-01-01", rows)
    assert d["strength_basis"] == "zt_count_fallback"
    assert d["themes"][0]["theme"] == "广度线"
    assert d["themes"][0]["zt_count"] == 5


def test_three_ranks_coexist_and_disagree(tmp_path: Path):
    day = "2026-03-10"
    rows = [
        zt("000001", "高板低涨幅", 3, fbt="093000", seal=3.0),
        zt("000002", "低板高涨幅", 1, fbt="143000", seal=0.5),
    ]
    # 000002 近期翻倍，000001 只涨一点
    d1 = kline_dir(tmp_path, "000001", [10.0] * 20 + [11.0], day)
    kline_dir(tmp_path, "000002", [10.0] * 20 + [20.0], day)
    got = build_ladder(day, rows, klines=KlineReturns(d1))
    members = {m["code"]: m for t in got["themes"] for m in t["members"]}

    # 板数键：高板第一
    assert members["000001"]["rank_proposed"] == 1
    # 动量键：低板高涨幅第一
    assert members["000002"]["rank_momentum"] == 1
    # 现行键：板数 → 首封，同样是高板第一
    assert members["000001"]["rank_current"] == 1
    assert got["note"].startswith("P0 snapshot only")


def test_missing_kline_is_null_not_zero(tmp_path: Path):
    day = "2026-03-10"
    rows = [zt("000009", "无K线", 2)]
    got = build_ladder(day, rows, klines=KlineReturns(tmp_path))  # 空目录
    m = got["themes"][0]["members"][0]
    assert m["ret_5d_pct"] is None and m["ret_20d_pct"] is None
    assert m["ret_missing"] is True


def test_snapshot_is_idempotent(tmp_path: Path):
    """同一天重算两次 → 结果字节级一致（只有 generated_at 会变，故比对内容体）。"""
    day = "1999-01-01"
    rows = [zt("000001", "甲", 2, sector="X"), zt("000002", "乙", 1, sector="X")]
    a = build_theme_strength(day, rows)
    b = build_theme_strength(day, rows)
    a.pop("generated_at"); b.pop("generated_at")
    assert a == b


def test_apply_to_views_passes_three_states():
    """三态/替补字段必须透传到 views（卡面/复盘都靠它读），⛔ 不能只留在 marks。"""
    from wealth_code.core.pool_call import apply_to_views

    pool_result = {"views": {"000001": {
        "view": "buy", "trade_state": "A",
        "substitute": {"core_code": "000002", "core_name": "核心", "independent": False},
    }}}
    out = apply_to_views(pool_result, {})
    v = out["000001"]
    assert v["trade_state"] == "A"
    assert v["substitute"]["core_code"] == "000002"
    assert v["substitute"]["independent"] is False
