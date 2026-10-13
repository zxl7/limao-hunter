# -*- coding: utf-8 -*-
"""题材历史回补（东财 getFryTomorrowList + getStockList）测试（2026-10-13 用户「直接回补30个交易日」）。

锁定：
  ① getStockList 真实返回结构 = data.stockList，个股代码字段 = securityCode（**不是** code/list）
     —— 初版错解成 data.list / code → 成分股全 0，此次踩坑，必须钉死。
  ② build_themes 优先取**全成分股**（getStockList 缓存），而非接口自带的 2 只代表股
     —— 否则涨停家数系统性偏低（初版几乎全 0~2）。
  ③ zt_count = 该题材成分 ∩ 当日涨停池的家数（取不到涨停池 → None，不编造）。
  ④ backfill 幂等（已存在跳过）+ ⛔ 幽灵产物闸（非交易日不落盘）。

全隔离：tmp_path / monkeypatch 网络函数，不触真实 cache、不触网。
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("theme_backfill", ROOT / "scripts" / "theme_backfill.py")
tb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tb)

DAY = "2026-10-12"          # 交易日
HOLIDAY = "2026-10-05"      # 国庆（非交易日）


def _items():
    """一条题材催化：自带 2 只代表股（stockList），全成分靠 getStockList 补。"""
    return [{
        "tradeDate": DAY, "themeCode": "01759", "themeName": "PCB",
        "title": "测试题材", "f3": 3.1, "cumulateF3": 5.0, "isHot": 1, "sortNum": 1,
        "stockList": [{"code": "688xxx.SH", "name": "代表1"}, {"code": "300yyy.SZ", "name": "代表2"}],
    }]


def _zt_map():
    # 成分股里 600000 / 600001 当日涨停，600002 未涨停
    return {"600000": 2, "600001": 1}


def test_fetch_members_parses_security_code(monkeypatch):
    """① 关键回归锁：getStockList 返回 data.stockList + securityCode。"""
    fake = json.dumps({"data": {"stockList": [
        {"securityCode": "600000.SH", "name": "工行"},
        {"securityCode": "600001.SH", "name": "邯钢"},
        {"securityCode": "600002.SH", "name": "其他"},
    ]}})
    monkeypatch.setattr(tb, "_curl", lambda *a, **k: fake)
    got = tb.fetch_members("01759")
    codes = [str(s.get("securityCode") or s.get("code") or "").split(".")[0].strip() for s in got]
    assert codes == ["600000", "600001", "600002"]        # 全 3 只，非 0


def test_fetch_members_handles_wrong_shape_gracefully(monkeypatch):
    """接口结构若回退到 data.list / data.items 也能解析（兼容层）。"""
    monkeypatch.setattr(tb, "_curl", lambda *a, **k: json.dumps(
        {"data": {"list": [{"securityCode": "600000.SH"}]}}))
    assert tb.fetch_members("X") == [{"securityCode": "600000.SH"}]


def test_build_themes_uses_full_members_not_repr(monkeypatch):
    """② codes 取全成分（3 只），而非接口自带的 2 只代表股。"""
    members = {"01759": ["600000", "600001", "600002"]}
    themes = tb.build_themes(_items(), _zt_map(), members)
    assert len(themes) == 1
    t = themes[0]
    assert t["name"] == "PCB"
    assert t["codes"] == ["600000", "600001", "600002"]   # 全成分，不是 2 代表股
    assert t["zt_count"] == 2                              # 600000/600001 在涨停池
    assert t["leader_code"] == "600000"                    # 连板最高
    assert t["stocks"]                                      # 代表股仍保留


def test_build_themes_zt_none_when_pool_missing():
    """③ 取不到涨停池 → zt_count=None（不编造）。"""
    themes = tb.build_themes(_items(), {}, {"01759": ["600000", "600001"]})
    assert themes[0]["zt_count"] is None


def test_backfill_idempotent_and_ghost_guarded(tmp_path, monkeypatch):
    """④ 幂等 + 幽灵产物闸（非交易日不落盘）。"""
    monkeypatch.setattr(tb, "fetch_theme_items", lambda *a, **k: _items())
    monkeypatch.setattr(tb, "fetch_members", lambda code: [
        {"securityCode": "600000.SH"}, {"securityCode": "600001.SH"}, {"securityCode": "600002.SH"}])
    monkeypatch.setattr(tb, "fetch_zt_map", lambda day: _zt_map())
    out = tmp_path / "hist"
    r1 = tb.backfill(tmp_path, days=30, last_trade_date=DAY, out_dir=out)
    assert r1["status"] == "ok" and r1["written"] >= 1
    written = r1["written"]
    r2 = tb.backfill(tmp_path, days=30, last_trade_date=DAY, out_dir=out)   # 幂等
    assert r2["written"] == 0 and r2["skipped"] == written
    # 非交易日不应落盘
    fake_holiday = [{"tradeDate": HOLIDAY, "themeCode": "X", "themeName": "假题材",
                     "stockList": [], "cumulateF3": 0}]
    monkeypatch.setattr(tb, "fetch_theme_items", lambda *a, **k: fake_holiday)
    r3 = tb.backfill(tmp_path, days=30, last_trade_date=HOLIDAY, out_dir=out)
    assert (out / f"top5_{HOLIDAY}.json").exists() is False
    assert HOLIDAY in (r3.get("not_trading_day") or [])
