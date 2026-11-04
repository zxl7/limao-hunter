"""5 日均量比（`core.vol_ratio.vol_ma5`）测试。

守的边界（2026-11-04 用户「量能判断要基于 5 日平均」）：

1. **口径**：当日量 ÷ 前 5 日（不含当日）均量；⛔ 不是「÷昨日」
2. **不猜缺失**：不足 5 根 / 当日不在 K 线里 / 代码不存在 → None，⛔ 不拿邻近日期冒充
3. **不参与门槛**：本函数只做诊断，实测量比区分度不如绝对成交额（见 docstring 对照表）
4. 真实案例：紫竹 1.15x 放量、东方海洋 0.66x 缩量（后者反而更强，说明量能不能单用）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "wealth_code") not in sys.path:
    sys.path.insert(0, str(ROOT / "wealth_code"))

from core import vol_ratio as VR  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    VR.reset_cache()
    yield
    VR.reset_cache()


def _write_kline(root: Path, code: str, bars: list[tuple[str, float]]) -> None:
    d = root / "cache" / "kline_qfq"
    d.mkdir(parents=True, exist_ok=True)
    payload = {"schema": "kline_qfq_v1", "code": code,
               "bars": [{"date": dt, "open": 1.0, "close": 1.0, "high": 1.0, "low": 1.0, "vol": v}
                        for dt, v in bars]}
    (d / f"{code}.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """把 `cache/kline_qfq` 根指到临时目录，⛔ 不碰真实缓存。"""
    monkeypatch.setattr(VR, "_local_bars", _make_local(tmp_path))
    return tmp_path


def _make_local(root: Path):
    def _load(code: str):
        p = root / "cache" / "kline_qfq" / f"{code}.json"
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        out = {}
        for b in data.get("bars") or []:
            d = str(b.get("date") or "")[:10]
            v = b.get("vol", b.get("volume"))
            if d and isinstance(v, (int, float)) and float(v) > 0:
                out[d] = {"vol": float(v)}
        return out
    return _load


def test_ratio_is_against_five_day_average(sandbox):
    """当日 1500 / 前 5 日均 1000 → 1.5 倍。"""
    _write_kline(sandbox, "600001", [
        ("2026-10-27", 1000), ("2026-10-28", 1000), ("2026-10-29", 1000),
        ("2026-10-30", 1000), ("2026-11-02", 1000), ("2026-11-03", 1500),
    ])
    r = VR.vol_ma5("600001", "2026-11-03")
    assert r["ratio"] == 1.5
    assert r["ma5"] == 1000.0
    assert r["today"] == 1500.0
    assert r["window"] == 5


def test_excludes_the_day_itself(sandbox):
    """⛔ 当日不能计入均量（否则放量票自己会把自己拉平）。"""
    _write_kline(sandbox, "600002", [
        ("2026-10-27", 100), ("2026-10-28", 100), ("2026-10-29", 100),
        ("2026-10-30", 100), ("2026-11-02", 100), ("2026-11-03", 10000),
    ])
    r = VR.vol_ma5("600002", "2026-11-03")
    assert r["ma5"] == 100.0        # 不含当日 10000
    assert r["ratio"] == 100.0


def test_insufficient_history_returns_none(sandbox):
    """只有 3 根前序 → 不算（⛔ 不拿 3 天当 5 天）。"""
    _write_kline(sandbox, "600003", [
        ("2026-10-30", 100), ("2026-11-02", 100), ("2026-11-03", 500),
    ])
    assert VR.vol_ma5("600003", "2026-11-03") is None


def test_missing_asof_date_returns_none(sandbox):
    """`asof` 不在 K 线里 → None，⛔ 不拿最近一根冒充。"""
    _write_kline(sandbox, "600004", [
        ("2026-10-27", 100), ("2026-10-28", 100), ("2026-10-29", 100),
        ("2026-10-30", 100), ("2026-11-02", 100),
    ])
    assert VR.vol_ma5("600004", "2026-11-03") is None


def test_unknown_code_returns_none(sandbox):
    assert VR.vol_ma5("999999", "2026-11-03") is None


def test_bad_input_returns_none():
    assert VR.vol_ma5("", "2026-11-03") is None
    assert VR.vol_ma5("600001", "") is None
    assert VR.vol_ma5("600001", "2026-11") is None


def test_text_verb_up_and_down(sandbox):
    _write_kline(sandbox, "600005", [
        ("2026-10-27", 1000), ("2026-10-28", 1000), ("2026-10-29", 1000),
        ("2026-10-30", 1000), ("2026-11-02", 1000), ("2026-11-03", 1200),
    ])
    _write_kline(sandbox, "600006", [
        ("2026-10-27", 1000), ("2026-10-28", 1000), ("2026-10-29", 1000),
        ("2026-10-30", 1000), ("2026-11-02", 1000), ("2026-11-03", 700),
    ])
    assert "放量 1.20 倍" in VR.vol_ma5_text("600005", "2026-11-03")
    assert "缩量 0.70 倍" in VR.vol_ma5_text("600006", "2026-11-03")
    assert VR.vol_ma5_text("999999", "2026-11-03") == ""


# ── 真实数据（本地 cache 存在才跑，⛔ 不让 CI 因缺缓存失败）───────────────
_REAL_ZIZHU = ROOT / "cache" / "kline_qfq" / "002058.json"


@pytest.mark.skipif(not _REAL_ZIZHU.exists(), reason="本地无 002058 前复权日K")
def test_real_zizhu_is_volume_up():
    """紫竹 002058（2026-11-03）：2 板换手，量比 1.15x **放量**。

    ⚠️ 这个案例说明「成交额低」≠「缩量」：它成交额只有 2.54 亿（看着不多），
       但相对自己的 5 日均量是放量的。绝对额与相对量是**两个维度**。
    """
    r = VR.vol_ma5("002058", "2026-11-03")
    assert r is not None
    assert r["ratio"] > 1.0, r
    assert round(r["ratio"], 2) == 1.15


@pytest.mark.skipif(not (ROOT / "cache" / "kline_qfq" / "002086.json").exists(),
                    reason="本地无 002086 前复权日K")
def test_real_dongfang_is_volume_down():
    """东方海洋 002086（2026-11-03）：4 板，量比 0.66x **缩量**，但它是当日更强的票。

    ⛔ 这正是「量能不能单独当判据」的反证：缩量的二板/高板里也有强者，
       所以 vol_ma5 只做诊断，不做门槛。
    """
    r = VR.vol_ma5("002086", "2026-11-03")
    assert r is not None
    assert r["ratio"] < 1.0, r
    assert round(r["ratio"], 2) == 0.66
