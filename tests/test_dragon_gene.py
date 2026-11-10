# -*- coding: utf-8 -*-
"""龙头基因沉淀池（`scripts/dragon_gene.py`）回归锁 —— 2026-11-10 用户定义

锁定用户四条规则：
  ① 连续涨停 ≥4 板入池；<4 板不入；
  ② ≥5 板打 `strong` 标；
  ③ 断板后两日累计跌幅 ≤ −10% → `eliminated`（A 杀）；> −10% → `retained`；
  ④ 不足两个交易日 → `pending`（⛔ 不判死、不补 0）。

另锁一条数据诚实性：**盘中归档（无 zt_list）不得当作「当天没涨停」**——
否则会把还在连板的票凭空判成断板（2026-11-10 10:41 实测踩到）。

全隔离：tmp_path 造假归档 + 假 K 线，不读真实 cache。
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "dragon_gene", ROOT / "scripts" / "dragon_gene.py")
    m = importlib.util.module_from_spec(spec)     # type: ignore[arg-type]
    spec.loader.exec_module(m)                    # type: ignore[union-attr]
    return m


@pytest.fixture()
def m(tmp_path, monkeypatch):
    mod = _load()
    zt_dir = tmp_path / "zt_history"
    kl_dir = tmp_path / "kline"
    zt_dir.mkdir()
    kl_dir.mkdir()
    monkeypatch.setattr(mod, "ZT_DIR", zt_dir)
    monkeypatch.setattr(mod, "KLINE_DIR", kl_dir)
    mod._BARS.clear()
    mod._tmp = tmp_path
    return mod


def _zt(m, day: str, rows: list[dict], *, intraday=False):
    body = {"date": day, "zt_count": len(rows), "intraday_only": intraday}
    if not intraday:
        body["zt_list"] = rows
    (m.ZT_DIR / f"zt_{day}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def _kline(m, code: str, closes: dict[str, float]):
    bars = [{"date": d, "open": c, "close": c, "high": c, "low": c} for d, c in sorted(closes.items())]
    (m.KLINE_DIR / f"{code}.json").write_text(json.dumps({"bars": bars}), encoding="utf-8")


DAYS = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]


def test_entry_threshold_and_strong_flag(m):
    """3 板不入池；4 板入；5 板打 strong。"""
    code_a, code_b = "600001", "600002"
    for i, d in enumerate(DAYS[:4]):
        _zt(m, d, [{"code": code_a, "name": "四板票", "boards": i + 1, "sector": "测试"},
                   {"code": code_b, "name": "五板票", "boards": i + 2, "sector": "测试"}])
    _kline(m, code_a, {d: 10 + i for i, d in enumerate(DAYS)})
    _kline(m, code_b, {d: 10 + i for i, d in enumerate(DAYS)})
    m._BARS.clear()

    data = m.build(DAYS[:4])
    a = next(r for r in data["pool"] if r["code"] == code_a)
    b = next(r for r in data["pool"] if r["code"] == code_b)
    assert a["entry_boards"] == 4 and a["status"] == "tracking"
    assert b["entry_boards"] == 4 and b["max_boards"] == 5 and b["strong"] is True


def test_three_boards_never_enters(m):
    code = "600003"
    for i, d in enumerate(DAYS[:3]):
        _zt(m, d, [{"code": code, "name": "三板票", "boards": i + 1}])
    data = m.build(DAYS[:3])
    assert data["pool"] == []


DAYS6 = DAYS + ["2026-11-09"]   # 断板后要满两个交易日才判，故多给一天


def test_retained_when_mild_retreat(m):
    """断板后两日累计 −3% → retained。"""
    code = "600004"
    for i, d in enumerate(DAYS[:4]):
        _zt(m, d, [{"code": code, "name": "温和票", "boards": i + 1}])
    _zt(m, DAYS[4], [])                                   # 断板 D1
    _zt(m, DAYS6[5], [])                                  # D2（空池是有效数据）
    _kline(m, code, {DAYS[0]: 8, DAYS[1]: 9, DAYS[2]: 10, DAYS[3]: 11,
                     DAYS[4]: 10.67, DAYS6[5]: 10.67})
    m._BARS.clear()
    r = m.build(DAYS6)["pool"][0]
    assert r["status"] == "retained" and r["a_kill"] is False
    assert r["post_2d_pct"] == pytest.approx(-3.0, abs=0.2)


def test_eliminated_on_a_kill(m):
    """断板后两日累计 −19% → eliminated（A 杀）。"""
    code = "600005"
    for i, d in enumerate(DAYS[:4]):
        _zt(m, d, [{"code": code, "name": "A杀票", "boards": i + 1}])
    _zt(m, DAYS[4], [])
    _zt(m, DAYS6[5], [])
    _kline(m, code, {DAYS[0]: 8, DAYS[1]: 9, DAYS[2]: 10, DAYS[3]: 11,
                     DAYS[4]: 9.9, DAYS6[5]: 8.9})
    m._BARS.clear()
    r = m.build(DAYS6)["pool"][0]
    assert r["status"] == "eliminated" and r["a_kill"] is True
    assert r["post_2d_pct"] <= -10


def test_pending_with_one_day_only(m):
    """只来得及看到断板当天 → pending，⛔ 不判死。"""
    code = "600006"
    for i, d in enumerate(DAYS[:4]):
        _zt(m, d, [{"code": code, "name": "待判票", "boards": i + 1}])
    _zt(m, DAYS[4], [])
    _kline(m, code, {DAYS[0]: 8, DAYS[1]: 9, DAYS[2]: 10, DAYS[3]: 11, DAYS[4]: 10.2})
    m._BARS.clear()
    r = m.build(DAYS)["pool"][0]
    assert r["status"] == "pending" and r["a_kill"] is None


def test_intraday_archive_is_skipped_not_treated_as_break(m):
    """盘中归档无 zt_list → 不得当作断板（否则连板票被凭空判死）。"""
    code = "600007"
    _zt(m, DAYS[0], [{"code": code, "name": "在途票", "boards": 1}])
    _zt(m, DAYS[1], [{"code": code, "name": "在途票", "boards": 2}])
    _zt(m, DAYS[2], [{"code": code, "name": "在途票", "boards": 3}])
    _zt(m, DAYS[3], [{"code": code, "name": "在途票", "boards": 4}])
    _zt(m, DAYS[4], [], intraday=True)                    # 盘中：没有逐票名单
    _kline(m, code, {DAYS[0]: 8, DAYS[1]: 9, DAYS[2]: 10, DAYS[3]: 11})
    m._BARS.clear()
    data = m.build(DAYS)
    r = data["pool"][0]
    assert r["status"] == "tracking", "盘中归档不可当成断板证据"
    assert data["skipped_days"] == [DAYS[4]]


def test_20cm_threshold_is_normalized_not_absolute(m):
    """20cm 票：−15% 不构成 A 杀（阈值 −20%），但绝对 −10% 对照口径会判 A 杀。"""
    code = "300001"
    for i, d in enumerate(DAYS[:4]):
        _zt(m, d, [{"code": code, "name": "创业板票", "boards": i + 1}])
    _zt(m, DAYS[4], [])
    _zt(m, DAYS6[5], [])
    _kline(m, code, {DAYS[0]: 8, DAYS[1]: 9, DAYS[2]: 10, DAYS[3]: 11,
                     DAYS[4]: 10.0, DAYS6[5]: 9.35})       # 相对 11 → −15%
    m._BARS.clear()
    r = m.build(DAYS6)["pool"][0]
    assert r["limit_pct"] == 20 and r["a_kill_threshold"] == -20
    assert r["post_2d_pct"] == pytest.approx(-15.0, abs=0.3)
    assert r["a_kill"] is False and r["status"] == "retained", "20cm 的 −15% 只相当于 0.75 个跌停"
    assert r["a_kill_abs10"] is True, "绝对 −10% 口径的对照值要如实保留"


def test_st_threshold_is_five_percent(m):
    """ST（5cm）：−6% 已超过一个跌停板 → A 杀。"""
    code = "600008"
    for i, d in enumerate(DAYS[:4]):
        _zt(m, d, [{"code": code, "name": "*ST测试", "boards": i + 1}])
    _zt(m, DAYS[4], [])
    _zt(m, DAYS6[5], [])
    _kline(m, code, {DAYS[0]: 8, DAYS[1]: 9, DAYS[2]: 10, DAYS[3]: 11,
                     DAYS[4]: 10.6, DAYS6[5]: 10.34})      # 相对 11 → −6%
    m._BARS.clear()
    r = m.build(DAYS6)["pool"][0]
    assert r["limit_pct"] == 5 and r["a_kill_threshold"] == -5
    assert r["a_kill"] is True and r["status"] == "eliminated"
