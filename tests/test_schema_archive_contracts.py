#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据契约扩展回归（P2-10 · 2026-10-14 数据血缘审计）。

被锁对象：`scripts/validate_schema.py` 契约从 7 类在线产物扩到**盘后归档链**
（zt / eruption / sector_flow / theme_rotation / summary_5d / top5 + top5 的 theme_index）。

三条硬要求：
  ① 新契约必须**能报错**——缺顶层字段、时间戳不可解析、行内关键字段缺失/类型错必须 FAIL；
  ② 新契约必须**不误伤**——历史归档里确有 null 的字段（top5.zt_count、summary_5d.zt_count）
     降为「有值时校验」；82 份冻结历史归档全量必须通过（reality lock）；
  ③ 契约匹配泛化（通配符走 fnmatch、无通配符走精确名）不得破坏既有 7 类契约。
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import validate_schema as vs  # noqa: E402


# ── 夹具 ────────────────────────────────────────────────────────────────
@pytest.fixture
def tmpdir_():
    import shutil
    import tempfile
    d = pathlib.Path(tempfile.mkdtemp(dir=str(ROOT / ".pytest_tmp")))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _check(tmpdir_, name, obj):
    p = tmpdir_ / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return vs.validate_one(p)


# ── 1. 契约注册 ────────────────────────────────────────────────────────
NEW_PATTERNS = ("zt_*.json", "eruption_*.json", "sector_flow_*.json",
                "theme_rotation.json", "summary_5d.json", "top5_*.json",
                "theme_index.json")


def test_all_new_patterns_registered():
    missing = [p for p in NEW_PATTERNS if p not in vs.CONTRACTS]
    assert not missing, f"契约未登记：{missing}"


def test_row_contract_keys_are_underscore_prefixed():
    """行级契约键必须 `__` 前缀，才会被字段级循环跳过。"""
    for pat, spec in vs.CONTRACTS.items():
        for k in spec:
            if k.startswith("__"):
                assert k in ("__rows__", "__rowdicts__"), f"{pat} 出现未知行级键 {k}"


# ── 2. 时间戳校验（_f_ts） ──────────────────────────────────────────────
def test_timestamp_checker_accepts_all_three_real_forms():
    assert vs._f_ts("2026-10-14 15:30:32")                     # 空格分隔（zt/feedback）
    assert vs._f_ts("2026-10-13T17:19:11.961178")              # naive ISO（capture_daily）
    assert vs._f_ts("2026-10-13T16:29:26.785409+08:00")        # 带偏移（P1-⑦ 目标形态）


def test_timestamp_checker_rejects_garbage():
    for bad in ("", "昨天", "15:00", None, 12345, "2026/10/13"):
        assert not vs._f_ts(bad), f"{bad!r} 不应被视为合法时间戳"


# ── 3. 正常样本（7 类全过） ─────────────────────────────────────────────
VALID = {
    "zt_2026-10-14.json": {
        "date": "2026-10-14", "zt_count": 36, "max_boards": 4,
        "ladder": {"4": ["百洋股份"]},
        "zt_list": [{"code": "000530", "name": "冰山冷热", "boards": 2}],
    },
    "eruption_2026-10-13.json": {
        "date": "2026-10-13", "generated_ts": "2026-10-13T17:19:11.961178",
        "themes": [{"name": "文化传媒", "stocks": [{"code": "603096.SH", "name": "新经典"}]}],
    },
    "sector_flow_2026-10-13.json": {
        "date": "2026-10-13", "generated_ts": "2026-10-13T17:19:15.458772",
        "boards": {"电子": {"pct": 1.2, "flow_yi": 3.4}},
    },
    "theme_rotation.json": {
        "date": "2026-10-14", "generated_ts": "2026-10-14T15:50:17.725699",
        "candidates": [{"theme": "粮食概念", "phase": "re-entry-candidate"}],
        "sector_zt_trends": [{"theme": "粮食概念"}],
        "market_context": {"sentiment_score": 1}, "data_meta": {"window": "5交易日"},
    },
    "summary_5d.json": {
        "generated_ts": "2026-10-13T17:20:03.558002", "window_days": 5,
        # 真实文件里 zt_count / max_boards 为 null → 契约必须放行
        "days": [{"date": "2026-09-30", "sentiment_score": 4.5,
                  "zt_count": None, "max_boards": None}],
        "trajectory": {"sentiment_seq": [4.5]},
    },
    "top5_2026-10-13.json": {
        "date": "2026-10-13", "source": "market_memory.prev_top5 (选股宝)",
        "archived_at": "2026-10-13T16:29:26.785409+08:00",
        "themes": [{"name": "大消费", "zt_count": 9, "codes": ["000558.SZ"]}],
    },
    "theme_index.json": {
        "updated_at": "2026-10-13T17:40:58.001157+08:00", "days": ["2026-08-24"],
        "theme_count": 152, "themes": {"粮食概念": {"days": 11}},
    },
}


@pytest.mark.parametrize("name", sorted(VALID))
def test_valid_sample_passes(tmpdir_, name):
    ok, errs = _check(tmpdir_, name, VALID[name])
    assert ok, errs


# ── 4. 顶层字段缺失 / 时间戳异常 → FAIL ────────────────────────────────
@pytest.mark.parametrize("name,drop", [
    ("zt_2026-10-14.json", "zt_list"),
    ("eruption_2026-10-13.json", "themes"),
    ("sector_flow_2026-10-13.json", "boards"),
    ("theme_rotation.json", "candidates"),
    ("summary_5d.json", "days"),
    ("top5_2026-10-13.json", "themes"),
    ("theme_index.json", "themes"),
])
def test_missing_top_level_field_fails(tmpdir_, name, drop):
    obj = json.loads(json.dumps(VALID[name]))
    obj.pop(drop)
    ok, errs = _check(tmpdir_, name, obj)
    assert not ok
    assert any(drop in e for e in errs), errs


@pytest.mark.parametrize("name,field", [
    ("eruption_2026-10-13.json", "generated_ts"),
    ("sector_flow_2026-10-13.json", "generated_ts"),
    ("theme_rotation.json", "generated_ts"),
    ("summary_5d.json", "generated_ts"),
    ("top5_2026-10-13.json", "archived_at"),
    ("theme_index.json", "updated_at"),
])
def test_bad_timestamp_fails(tmpdir_, name, field):
    obj = json.loads(json.dumps(VALID[name]))
    obj[field] = "刚刚"
    ok, errs = _check(tmpdir_, name, obj)
    assert not ok
    assert any(field in e for e in errs), errs


def test_symbolic_timestamp_type_fails(tmpdir_):
    obj = json.loads(json.dumps(VALID["eruption_2026-10-13.json"]))
    obj["generated_ts"] = 1750000000     # 数字时间戳不是字符串形态 → FAIL（统一为字符串）
    ok, errs = _check(tmpdir_, "eruption_2026-10-13.json", obj)
    assert not ok and any("generated_ts" in e for e in errs)


# ── 5. 行级契约 ────────────────────────────────────────────────────────
def test_zt_row_missing_required_field_fails(tmpdir_):
    obj = json.loads(json.dumps(VALID["zt_2026-10-14.json"]))
    obj["zt_list"][0].pop("boards")
    ok, errs = _check(tmpdir_, "zt_2026-10-14.json", obj)
    assert not ok
    assert any("zt_list[0].boards" in e for e in errs), errs


def test_zt_row_type_error_fails(tmpdir_):
    obj = json.loads(json.dumps(VALID["zt_2026-10-14.json"]))
    obj["zt_list"][0]["boards"] = "4"
    ok, errs = _check(tmpdir_, "zt_2026-10-14.json", obj)
    assert not ok and any("zt_list[0].boards" in e for e in errs), errs


def test_sector_flow_dict_bucket_row_check(tmpdir_):
    obj = json.loads(json.dumps(VALID["sector_flow_2026-10-13.json"]))
    obj["boards"]["印制电路板"] = {"pct": 6.08}      # 缺 flow_yi
    ok, errs = _check(tmpdir_, "sector_flow_2026-10-13.json", obj)
    assert not ok
    assert any("boards['印制电路板'].flow_yi" in e for e in errs), errs


def test_top5_null_zt_count_is_tolerated_but_wrong_type_is_not(tmpdir_):
    """历史回填期 zt_count 为 null → 放行；写成字符串 → FAIL。"""
    obj = json.loads(json.dumps(VALID["top5_2026-10-13.json"]))
    obj["themes"][0]["zt_count"] = None
    ok, errs = _check(tmpdir_, "top5_2026-10-13.json", obj)
    assert ok, errs

    obj["themes"][0]["zt_count"] = "9"
    ok, errs = _check(tmpdir_, "top5_2026-10-13.json", obj)
    assert not ok and any("zt_count" in e for e in errs), errs


def test_top5_row_missing_name_fails(tmpdir_):
    obj = json.loads(json.dumps(VALID["top5_2026-10-13.json"]))
    obj["themes"][0].pop("name")
    ok, errs = _check(tmpdir_, "top5_2026-10-13.json", obj)
    assert not ok and any("name" in e for e in errs), errs


def test_summary5d_row_missing_date_fails(tmpdir_):
    obj = json.loads(json.dumps(VALID["summary_5d.json"]))
    obj["days"][0].pop("date")
    ok, errs = _check(tmpdir_, "summary_5d.json", obj)
    assert not ok and any("days[0].date" in e for e in errs), errs


def test_theme_rotation_candidate_missing_phase_fails(tmpdir_):
    obj = json.loads(json.dumps(VALID["theme_rotation.json"]))
    obj["candidates"][0].pop("phase")
    ok, errs = _check(tmpdir_, "theme_rotation.json", obj)
    assert not ok and any("candidates[0].phase" in e for e in errs), errs


def test_row_not_dict_fails(tmpdir_):
    obj = json.loads(json.dumps(VALID["zt_2026-10-14.json"]))
    obj["zt_list"] = ["000530"]     # 行不是 dict
    ok, errs = _check(tmpdir_, "zt_2026-10-14.json", obj)
    assert not ok and any("应为 dict" in e for e in errs), errs


def test_row_error_cap_prevents_flood(tmpdir_):
    obj = json.loads(json.dumps(VALID["zt_2026-10-14.json"]))
    obj["zt_list"] = [{"code": f"00000{i}", "name": "X"} for i in range(20)]   # 全缺 boards
    ok, errs = _check(tmpdir_, "zt_2026-10-14.json", obj)
    assert not ok
    assert len(errs) <= vs._ROW_ERR_CAP + 2, errs
    assert any("行级问题共 20 处" in e for e in errs), errs


# ── zt 盘中快照形态（intraday_only）契约 ────────────────────────────────
ZT_INTRADAY = {
    "date": "2026-10-15", "date_raw": "20261015", "intraday_only": True,
    "intraday_points": [
        {"window": "09:30", "ts": "2026-10-15 09:59",
         "themes": {"医药": 3, "算力硬件/PCB": 3, "其他": 6, "电网": 2},
         "total": 21, "max_boards": 5},
        {"window": "10:00", "ts": "2026-10-15 10:20",
         "themes": {"算力硬件/PCB": 5, "其他": 9, "医药": 4, "电网": 2},
         "total": 26, "max_boards": 5},
    ],
}


def test_zt_intraday_shape_passes(tmpdir_):
    """盘中快照（theme_data 写、theme_series 消费）应走盘中契约，不被日频契约误杀。"""
    ok, errs = _check(tmpdir_, "zt_2026-10-15.json", ZT_INTRADAY)
    assert ok, errs


def test_zt_intraday_missing_points_fails(tmpdir_):
    obj = json.loads(json.dumps(ZT_INTRADAY))
    obj.pop("intraday_points")
    ok, errs = _check(tmpdir_, "zt_2026-10-15.json", obj)
    assert not ok and any("intraday_points" in e for e in errs), errs


def test_zt_intraday_point_bad_ts_fails(tmpdir_):
    obj = json.loads(json.dumps(ZT_INTRADAY))
    obj["intraday_points"][0]["ts"] = "刚刚"
    ok, errs = _check(tmpdir_, "zt_2026-10-15.json", obj)
    assert not ok and any("intraday_points[0].ts" in e for e in errs), errs


def test_zt_intraday_point_nonint_total_fails(tmpdir_):
    obj = json.loads(json.dumps(ZT_INTRADAY))
    obj["intraday_points"][0]["total"] = "21"
    ok, errs = _check(tmpdir_, "zt_2026-10-15.json", obj)
    assert not ok and any("intraday_points[0].total" in e for e in errs), errs


# ── 6. 匹配泛化不得误伤 ────────────────────────────────────────────────
def test_unrelated_file_in_archive_dir_still_conservative_pass(tmpdir_):
    """top5_history/ 里的 theme_members.json 不在契约内 → 保守通过。"""
    p = tmpdir_ / "theme_members.json"
    p.write_text(json.dumps({"00012": ["600790"]}), encoding="utf-8")
    ok, errs = vs.validate_one(p)
    assert ok and not errs


def test_exact_name_patterns_still_match(tmpdir_):
    """泛化匹配后，既有精确名契约（latest.json 等）仍必须命中。"""
    p = tmpdir_ / "latest.json"
    p.write_text(json.dumps({"ts": "2026-10-14T15:00:12+08:00", "total_zt": 36,
                             "sentiment": "强", "index": [], "top5": []}), encoding="utf-8")
    ok, errs = vs.validate_one(p)
    assert ok, errs
    # 反向：缺字段必须被抓住（证明命中了契约而非保守通过）
    p.write_text(json.dumps({"ts": "x"}), encoding="utf-8")
    ok, errs = vs.validate_one(p)
    assert not ok and any("total_zt" in e for e in errs), errs


def test_archive_regex_rejects_non_date_files():
    assert vs._ARCHIVE_RE.match("zt_2026-10-14.json")
    assert vs._ARCHIVE_RE.match("sector_flow_2026-09-23.json")
    for bad in ("theme_index.json", "zt_backup.json", "top5_x.json", "zt_2026-1-4.json"):
        assert not vs._ARCHIVE_RE.match(bad), bad


def test_discover_includes_archive_latest():
    names = {p.name for p in vs.discover()}
    for stem in ("zt_", "eruption_", "sector_flow_", "top5_"):
        hits = [n for n in names if n.startswith(stem)]
        assert hits, f"discover() 未纳入 {stem}* 最新归档"
    assert "theme_rotation.json" in names
    assert "summary_5d.json" in names


# ── 7. Reality lock：冻结历史归档全量必须通过 ──────────────────────────
def test_frozen_archive_history_all_passes():
    """82 份历史归档是**冻结**的 → 契约若与现实脱节，此测试必红（不依赖当日行情）。"""
    dirs = [ROOT / "cache" / k for k in
            ("zt_history", "eruption_history", "sector_flow_history", "top5_history")]
    targets = [f for d in dirs if d.exists()
               for f in sorted(d.glob("*.json")) if vs._ARCHIVE_RE.match(f.name)]
    assert targets, "未找到归档样本（环境异常）"
    failures = {}
    for f in targets:
        ok, errs = vs.validate_one(f)
        if not ok:
            failures[f.name] = errs[:3]
    assert not failures, f"{len(failures)}/{len(targets)} 份归档未通过契约：{failures}"
