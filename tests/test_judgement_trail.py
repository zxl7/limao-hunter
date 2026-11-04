"""单票判断轨迹（core/judgement_trail.py）测试。

守的是四条边界：
1. 事实层全覆盖（涨停池里每只票都有一行，不管进没进池）
2. 门槛层可复现（成交额 < turnover_floor → below_floor，不是"没数据"）
3. 判断层不补造（池外 score/verdict 必须为空，⛔ 不拿重算分顶替当日原话）
4. 重建幂等（同输入连跑两次 → 字节级相同，无重复行）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "wealth_code")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import judgement_trail as jt  # noqa: E402


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def fixture_dirs(tmp_path: Path):
    """两天归档：D1（首板缩量·被门槛挡）+ D2（3 板放量·进池并经裁决）。"""
    log_dir = tmp_path / "decision_log"
    live_dir = tmp_path / "live"
    live_dir.mkdir(parents=True, exist_ok=True)

    _write(log_dir / "2026-09-24" / "zt_pool.json", {
        "date": "2026-09-24",
        "zt_list": [
            {"code": "603278", "name": "大业股份", "boards": 1, "fbt": "093754",
             "zbc": 0, "hs": 4.71, "seal": 0.02, "cje_yi": 1.2, "sector": "通用设备"},
            {"code": "600825", "name": "新华传媒", "boards": 3, "fbt": "092500",
             "zbc": 2, "hs": 3.1, "seal": 1.2, "cje_yi": 9.0, "sector": "传媒"},
        ],
    })
    _write(log_dir / "2026-09-25" / "zt_pool.json", {
        "date": "2026-09-25",
        "zt_list": [
            {"code": "603278", "name": "大业股份", "boards": 3, "fbt": "133000",
             "zbc": 0, "hs": 13.8, "seal": 2.6, "cje_yi": 6.85, "sector": "通用设备"},
        ],
    })
    _write(log_dir / "2026-09-25" / "candidates.json", {
        "relay": [{"code": "603278", "name": "大业股份", "raw_score": 78.93,
                   "bucket_label": "接力池", "tags": ["先发"]}],
        "watch": [],
    })
    _write(log_dir / "2026-09-25" / "pool_call.json", {
        "views": {"603278": {"view": "participate", "reason": "竞价达标"}},
    })
    return log_dir, live_dir


def test_day_rows_covers_whole_zt_pool(fixture_dirs):
    log_dir, live_dir = fixture_dirs
    rows = jt.day_rows("2026-09-24", log_dir=log_dir, live_dir=live_dir)
    assert len(rows) == 2, "涨停池 2 只 → 必须 2 行（池外也要留痕）"
    codes = {r["code"] for r in rows}
    assert codes == {"603278", "600825"}


def test_gate_and_pool_judgement_layers(fixture_dirs):
    log_dir, live_dir = fixture_dirs
    d1 = {r["code"]: r for r in jt.day_rows("2026-09-24", log_dir=log_dir, live_dir=live_dir)}
    # 1.2 亿 < 1~2 板门槛(1.5 亿，2026-11-03 由 3.0 下调) → 被挡；池外不得有分数
    assert d1["603278"]["gate"] == "below_floor"
    assert d1["603278"]["pool"] is None
    assert d1["603278"]["score"] is None
    assert d1["603278"]["verdict"] is None
    # 3 板 9 亿 ≥ 5 亿门槛 → 通过
    assert d1["600825"]["gate"] == "pass"

    d2 = {r["code"]: r for r in jt.day_rows("2026-09-25", log_dir=log_dir, live_dir=live_dir)}
    assert d2["603278"]["pool"] == "relay"
    assert d2["603278"]["score"] == pytest.approx(78.93)
    assert d2["603278"]["verdict"] == "买入"


def test_mech_tags_and_volume_state(fixture_dirs, tmp_path):
    log_dir, live_dir = fixture_dirs
    idx = jt.rebuild(log_dir=log_dir, live_dir=live_dir, out_dir=tmp_path / "out")
    assert idx["rows"] == 3
    rows = jt.query("603278", out_dir=tmp_path / "out")
    assert [r["date"] for r in rows] == ["2026-09-24", "2026-09-25"]
    first, second = rows
    assert "首板" in first["mech"] and "早封" in first["mech"]
    assert "开过板" not in first["mech"]
    # 6.85 / 1.56 = 4.4 倍 → 放量
    assert second["vol_state"] == "expand"
    assert "放量" in second["mech"]
    assert "3连板" in second["mech"]
    assert "午后封" in second["mech"]


def test_rebuild_is_idempotent(fixture_dirs, tmp_path):
    log_dir, live_dir = fixture_dirs
    out = tmp_path / "out"
    jt.rebuild(log_dir=log_dir, live_dir=live_dir, out_dir=out)
    snap1 = {p.name: p.read_text(encoding="utf-8") for p in out.glob("*.jsonl")}
    jt.rebuild(log_dir=log_dir, live_dir=live_dir, out_dir=out)
    snap2 = {p.name: p.read_text(encoding="utf-8") for p in out.glob("*.jsonl")}
    assert snap1 == snap2, "重建必须幂等——同输入两次结果字节相同"
    lines = [ln for ln in snap2["2026-09.jsonl"].splitlines() if ln.strip()]
    assert len(lines) == 3, "⛔ 不得重复累加行"


def test_missing_pool_returns_empty(tmp_path):
    assert jt.day_rows("2026-08-10", log_dir=tmp_path, live_dir=tmp_path) == []
    idx = jt.rebuild(log_dir=tmp_path, live_dir=tmp_path, out_dir=tmp_path / "out")
    assert idx["rows"] == 0
    assert idx["days_without_pool"] == []


# ═══════════════════════════════════════════════════════════════════════════
# 2026-11-03 用户「重新定量」：低位成交额门槛 3.0 → 1.5 亿
#   触发：昨日传媒 9 只首板，5 只 1.5~2.8 亿被 3 亿门槛全剔，今日 5/5 全封。
#   最扎心的一条：**大业股份本人**（用户 5 板案例）首板 1.56 亿，
#   在旧门槛下正是被判 below_floor 的那一只——案例本身就被自己的门槛误杀。
# ═══════════════════════════════════════════════════════════════════════════
def test_floor_lowered_keeps_daye_first_board():
    from pipelines import pipeline_eod as pe
    assert pe.MIN_TURNOVER_YI_LOW_BOARD == 1.5, "低位门槛应为 1.5 亿"
    assert pe.turnover_floor(1) == 1.5
    assert pe.turnover_floor(2) == 1.5
    # ⛔ 高位不放宽（高位缩量是另一回事，⛔ 不与低位同一条线）
    assert pe.turnover_floor(3) == 5.0
    assert pe.turnover_floor(6) == 5.0
    # ⛔ 板数缺失不开口子
    assert pe.turnover_floor(0) == 5.0
    assert pe.turnover_floor(None) == 5.0


def test_floor_can_be_rolled_back(monkeypatch):
    """`ENABLE_TURNOVER_BY_STAGE=0` → 一键回纯 5 亿旧行为（可逆铁律）。"""
    from pipelines import pipeline_eod as pe
    monkeypatch.setenv("ENABLE_TURNOVER_BY_STAGE", "0")
    assert pe.turnover_floor(1) == 5.0
