# -*- coding: utf-8 -*-
"""个股「断板回流」扫描单测（方案1 · 2026-09-17 用户拍板）。

锁定口径：最近 N 日曾涨停 ∧ 今日未涨停 ∧ 今日走强 ∧ 断板窗口 [1, MAX_GAP]，
并硬性排除 ST / 科创板 688 / 高位妖股（曾 ≥ EXCLUDE_BOARDS 板）。
样本回归：中京电子（09-14 首板 → 09-15/16 断板 → 09-16 +5.65%），
        在「当日=09-16」口径下 gap_days 必须 = 2 并命中。
全隔离 tmp_path，不触网（quotes 注入）、不碰真实 cache。
"""
import importlib.util
import json
from pathlib import Path

WC = Path(__file__).resolve().parents[1] / "wealth_code"
spec = importlib.util.spec_from_file_location("reflow_scan", WC / "core" / "reflow_scan.py")
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)

TODAY = "2026-09-16"   # 与用户样本同一「复盘日」：09-14 首板 → 09-15/16 断板


def _write_day(cache_dir: Path, day: str, rows: list[dict], intraday_only: bool = False):
    d = cache_dir / "zt_history"
    d.mkdir(parents=True, exist_ok=True)
    payload = {"date": day, "date_raw": day.replace("-", "")}
    if intraday_only:
        payload["intraday_only"] = True
    else:
        payload["zt_list"] = rows
    (d / f"zt_{day}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _row(code, name, boards=1, sector="元件", is_20cm=False, lt_yi=100.0):
    return {"code": code, "name": name, "boards": boards, "sector": sector,
            "is_20cm": is_20cm, "lt_yi": lt_yi, "pct": 10.0}


def _fixture(cache_dir: Path):
    """日历（09-16 为当日）：04/07/08/09/10/11/14/15/16 —— 让 gap 各档可验证。"""
    for d in ("2026-09-04", "2026-09-07", "2026-09-09"):
        _write_day(cache_dir, d, [])
    _write_day(cache_dir, "2026-09-08", [_row("600001", "远窗口样本", boards=1, sector="白酒")])
    _write_day(cache_dir, "2026-09-10", [_row("600002", "超gap样本", boards=1, sector="白酒")])
    _write_day(cache_dir, "2026-09-11", [_row("603266", "天龙股份", boards=1, sector="汽车零部")])
    _write_day(cache_dir, "2026-09-14", [
        _row("002579", "中京电子", boards=1, sector="元件", lt_yi=109.6),   # 用户样本
        _row("605058", "澳弘电子", boards=4, sector="元件"),                # 4 板：允许（<5）
        _row("600100", "高位妖股", boards=5, sector="算力"),                # 5 板：排除
        _row("300999", "创业样本", boards=1, sector="元件", is_20cm=True),
    ])
    _write_day(cache_dir, "2026-09-15", [
        _row("601579", "会稽山", boards=2, sector="非白酒"),
        _row("688111", "科创样本", boards=1, sector="半导体"),              # 688：排除
        _row("002111", "ST样本", boards=1, sector="元件"),                  # ST：排除
    ])
    _write_day(cache_dir, "2026-09-16", [], intraday_only=True)             # 当日盘中无个股名单


def _quotes():
    return {
        "002579": {"name": "中京电子", "pct": 5.65},   # 断板第2日回流 → 命中
        "605058": {"name": "澳弘电子", "pct": 9.4},    # 未封板 +9.4%（4板允许）→ 命中
        "600100": {"name": "高位妖股", "pct": 6.0},    # 5 板 → 排除
        "300999": {"name": "创业样本", "pct": 6.0},    # 创业 20cm 未封 → 命中
        "603266": {"name": "天龙股份", "pct": 6.9},    # 09-11 涨停 → gap3 → 命中
        "601579": {"name": "会稽山", "pct": 5.86},     # 昨涨停今断板(gap1) → 命中
        "688111": {"name": "科创样本", "pct": 7.0},    # 688 → 排除
        "002111": {"name": "ST样本", "pct": 4.0},      # ST → 排除
        "600001": {"name": "远窗口样本", "pct": 3.0},  # 09-08 → 超回看 → 排除
        "600002": {"name": "超gap样本", "pct": 5.0},   # 09-10 → gap4 > max_gap → 排除
    }


def test_zhongjing_caught(tmp_path):
    """用户样本：中京电子（断板第2日 +5.65%）必须命中。"""
    _fixture(tmp_path)
    res = rs.scan_reflow(tmp_path, quotes=_quotes(), today=TODAY, top_n=10)
    codes = {r["code"] for r in res["pool"]}
    assert "002579" in codes, "中京电子样本未命中"
    row = next(r for r in res["pool"] if r["code"] == "002579")
    assert row["gap_days"] == 2
    assert row["today_pct"] == 5.65
    assert row["sector"] == "元件"
    assert row["tag"] == "2天前涨停·今日资金回流"


def test_hard_exclusions(tmp_path):
    """ST / 科创板 688 / 曾≥5板 高位妖股 一律不得进池。"""
    _fixture(tmp_path)
    res = rs.scan_reflow(tmp_path, quotes=_quotes(), today=TODAY, top_n=10)
    codes = {r["code"] for r in res["pool"]}
    assert "002111" not in codes, "ST 未排除"
    assert "688111" not in codes, "科创板 688 未排除"
    assert "600100" not in codes, "曾 5 板高位妖股未排除"
    assert "605058" in codes, "4 板（<5）应保留"


def test_gap_window_and_strength(tmp_path):
    """回看窗口外 / 断板窗口外 / 今日未走强 均不得进池。"""
    _fixture(tmp_path)
    q = _quotes()
    q["002579"] = {"name": "中京电子", "pct": 1.0}   # 今日未走强(<2%)
    res = rs.scan_reflow(tmp_path, quotes=q, today=TODAY, top_n=10)
    codes = {r["code"] for r in res["pool"]}
    assert "600001" not in codes, "回看窗口外的票漏进池"
    assert "600002" not in codes, "gap 超上限的票漏进池"
    assert "002579" not in codes, "今日未走强的票漏进池"


def test_today_limit_up_excluded(tmp_path):
    """今日已涨停（已启动）不得当潜伏候选（方案A ③）。"""
    _fixture(tmp_path)
    q = _quotes()
    q["002579"] = {"name": "中京电子", "pct": 9.98}   # 主板涨停
    q["300999"] = {"name": "创业样本", "pct": 19.9}   # 20cm 涨停
    res = rs.scan_reflow(tmp_path, quotes=q, today=TODAY, top_n=10)
    codes = {r["code"] for r in res["pool"]}
    assert "002579" not in codes
    assert "300999" not in codes


def test_ranking_gap1_first(tmp_path):
    """排序：断板越近越优先（gap=1 的会稽山应排在 gap=3 的天龙股份之前）。"""
    _fixture(tmp_path)
    res = rs.scan_reflow(tmp_path, quotes=_quotes(), today=TODAY, top_n=10)
    order = [r["code"] for r in res["pool"]]
    assert order.index("601579") < order.index("603266")


def test_no_quote_no_crash(tmp_path):
    """行情缺失 → 不炸、不编造（该股不进池）。"""
    _fixture(tmp_path)
    res = rs.scan_reflow(tmp_path, quotes={}, today=TODAY, top_n=10)
    assert res["pool"] == []
    assert res["stats"]["hits"] == 0
