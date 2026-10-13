# -*- coding: utf-8 -*-
"""选股宝题材 top5 长期归档 测试（2026-10-13 用户「题材可以积累和沉淀」）。

锁定：
  ① 逐日归档幂等（已存在不覆盖·--force 才重写）
  ② ⛔ 幽灵产物闸：非交易日**不落盘**（宪法【交易日判定唯一真源】铁律）
  ③ 题材库索引正确聚合（出现天数/最高与累计涨停家数/成分股并集/龙头）
  ④ 源缺失一律诚实降级（no_source，不编造、不崩）

全隔离：tmp_path / 显式传 out_dir，不读真实 cache、不落盘、不触网。
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("top5_history", ROOT / "scripts" / "top5_history.py")
th = importlib.util.module_from_spec(spec)
spec.loader.exec_module(th)

DAY = "2026-10-13"          # 交易日（周二）
DAY2 = "2026-10-12"         # 交易日
HOLIDAY = "2026-10-05"      # 国庆假期（非交易日·实测已被 backfill 拦下）


def _theme(name, zt, codes):
    return {"name": name, "zt_count": zt,
            "leader": {"name": f"{name}龙头", "code": codes[0]},
            "zt_stocks": [{"code": c, "name": f"票{c}"} for c in codes],
            "followers": []}


def _seed_mem(cache, day, themes):
    (cache / f"market_memory_{day}.json").write_text(
        json.dumps({"date": day, "prev_top5": themes}, ensure_ascii=False), encoding="utf-8")


def test_archive_writes_and_is_idempotent(tmp_path):
    _seed_mem(tmp_path, DAY, [_theme("大消费", 9, ["600693", "601086"])])
    out = tmp_path / "hist"
    r1 = th.archive(tmp_path, DAY, out_dir=out)
    assert r1["status"] == "written" and r1["themes"] == 1
    target = out / f"top5_{DAY}.json"
    assert target.exists()
    first = target.read_text(encoding="utf-8")
    r2 = th.archive(tmp_path, DAY, out_dir=out)          # 幂等：不覆盖
    assert r2["status"] == "exists" and r2["themes"] == 1
    assert target.read_text(encoding="utf-8") == first
    assert th.archive(tmp_path, DAY, force=True, out_dir=out)["status"] == "written"


def test_holiday_is_blocked_no_ghost_product(tmp_path):
    """⛔ 非交易日不落盘（防再造 market_memory_2026-10-05 那类幽灵产物）。"""
    _seed_mem(tmp_path, HOLIDAY, [_theme("医药", 3, ["600488"])])
    out = tmp_path / "hist"
    r = th.archive(tmp_path, HOLIDAY, out_dir=out)
    assert r["status"] == "not_trading_day"
    assert not (out / f"top5_{HOLIDAY}.json").exists()


def test_ghost_guard_is_deterministic(tmp_path, monkeypatch):
    _seed_mem(tmp_path, DAY, [_theme("大消费", 9, ["600693"])])
    monkeypatch.setattr(th, "_is_trading_day", lambda d: False)
    out = tmp_path / "hist"
    assert th.archive(tmp_path, DAY, out_dir=out)["status"] == "not_trading_day"
    assert not out.exists() or not (out / f"top5_{DAY}.json").exists()


def test_index_aggregates_across_days(tmp_path):
    _seed_mem(tmp_path, DAY2, [_theme("医药", 6, ["600488", "300642"]),
                               _theme("锂电池", 9, ["600241"])])
    _seed_mem(tmp_path, DAY, [_theme("医药", 3, ["600488", "002172"])])
    out = tmp_path / "hist"
    th.archive(tmp_path, DAY2, out_dir=out)
    th.archive(tmp_path, DAY, out_dir=out)
    ix = th.rebuild_index(tmp_path, out_dir=out)
    assert ix["day_count"] == 2 and ix["theme_count"] == 2
    y = ix["themes"]["医药"]
    assert y["days"] == 2 and y["max_zt"] == 6 and y["total_zt"] == 9
    assert y["first_seen"] == DAY2 and y["last_seen"] == DAY
    assert y["codes"] == ["002172", "300642", "600488"]        # 并集·去重·升序
    assert ix["themes"]["锂电池"]["days"] == 1
    assert (out / th.INDEX_NAME).exists()


def test_missing_source_is_graceful(tmp_path):
    out = tmp_path / "hist"
    assert th.archive(tmp_path, DAY, out_dir=out)["status"] == "no_source"
    assert th.archived_days(tmp_path, out) == []
    ix = th.rebuild_index(tmp_path, out_dir=out)
    assert ix["day_count"] == 0 and ix["theme_count"] == 0


def test_read_top5_skips_nameless_and_missing_codes(tmp_path):
    _seed_mem(tmp_path, DAY, [{"name": "", "zt_count": 5},
                              {"name": "医药", "zt_count": 3,
                               "zt_stocks": [{"name": "无码"}, {"code": "600488", "name": "津药药业"}]}])
    rows = th.read_top5(tmp_path, DAY)
    assert len(rows) == 1 and rows[0]["name"] == "医药"
    assert rows[0]["codes"] == ["600488"]
