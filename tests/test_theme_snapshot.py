#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材映射快照回归锁（P0 · 2026-10-18 用户「按顺序处理」）。

背景：`ThemeFetcher.fetch_all()` 走实时接口，映射随抓取时刻漂移 →
同一交易日重跑 eod 会得到两套候选（10-16 实证：接力池
[晨曦航空, 尖峰集团] → [冰山冷热, 新华文轩]）。本模块锁定「同日映射可复现」契约。

铁律：
- 当日快照存在 → **绝不重新请求接口**（重跑不得偷改当日决策依据）
- 快照已存在 → 默认不覆盖（保护首版），只有 FORCE_REFRESH 显式覆盖
- 接口抓空且无快照 → 诚实空标注（source=empty），不编造
"""
import json

import pytest

from common import stock_theme as st


class _FakeFetcher:
    """可控 Fetcher：记录调用次数，返回预设映射。"""

    def __init__(self, mapping=None, *, fail=False):
        self.mapping = mapping if mapping is not None else {}
        self.fail = fail
        self.calls = 0

    def fetch_all(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("接口故障")
        return dict(self.mapping)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """每个用例默认：快照开启、不强制刷新、缓存目录独立。"""
    monkeypatch.delenv("THEME_SNAPSHOT_ENABLED", raising=False)
    monkeypatch.delenv("THEME_SNAPSHOT_FORCE_REFRESH", raising=False)
    yield


def _m1():
    return {"600172": {"name": "黄河旋风", "theme": "金刚石/培育钻石", "logic": "散热", "plates": []}}


def _m2():
    return {"600172": {"name": "黄河旋风", "theme": "机器人", "logic": "误判", "plates": []}}


def test_snapshot_path_naming(tmp_path):
    p = st.theme_snapshot_path("2026-10-16", tmp_path)
    assert p.name == "theme_2026-10-16.json"
    assert p.parent.name == st.THEME_SNAPSHOT_DIRNAME


def test_first_run_fetches_live_and_persists(tmp_path):
    f = _FakeFetcher(_m1())
    mapping, meta = st.get_theme_map("2026-10-16", cache_dir=tmp_path, fetcher=f)
    assert f.calls == 1
    assert meta["source"] == "live"
    assert meta["count"] == 1
    assert meta["fingerprint"]
    assert st.theme_snapshot_path("2026-10-16", tmp_path).exists()
    assert mapping["600172"]["theme"] == "金刚石/培育钻石"


def test_second_run_reads_snapshot_and_never_calls_api(tmp_path):
    """⛔ 核心契约：重跑不得再请求接口（否则结果不可复现）。"""
    st.get_theme_map("2026-10-16", cache_dir=tmp_path, fetcher=_FakeFetcher(_m1()))
    f2 = _FakeFetcher(_m2(), fail=True)  # 就算接口挂了也不影响
    mapping, meta = st.get_theme_map("2026-10-16", cache_dir=tmp_path, fetcher=f2)
    assert f2.calls == 0, "快照存在时不得调用接口"
    assert meta["source"] == "snapshot"
    assert mapping["600172"]["theme"] == "金刚石/培育钻石", "重跑必须复现首跑映射"


def test_rerun_result_is_reproducible(tmp_path):
    m1, _ = st.get_theme_map("2026-10-16", cache_dir=tmp_path, fetcher=_FakeFetcher(_m1()))
    m2, meta2 = st.get_theme_map("2026-10-16", cache_dir=tmp_path, fetcher=_FakeFetcher(_m2()))
    assert m1 == m2
    assert meta2["fingerprint"] == st.theme_fingerprint(m1)


def test_save_does_not_overwrite_existing_snapshot(tmp_path):
    st.get_theme_map("2026-10-16", cache_dir=tmp_path, fetcher=_FakeFetcher(_m1()))
    # 直接再存一次不同的映射（默认 overwrite=False）→ 应保留首版
    st.save_theme_snapshot("2026-10-16", _m2(), cache_dir=tmp_path)
    snap = st.load_theme_snapshot("2026-10-16", cache_dir=tmp_path)
    assert snap is not None
    assert snap["mapping"]["600172"]["theme"] == "金刚石/培育钻石"


def test_force_refresh_overwrites(tmp_path, monkeypatch):
    st.get_theme_map("2026-10-16", cache_dir=tmp_path, fetcher=_FakeFetcher(_m1()))
    monkeypatch.setenv("THEME_SNAPSHOT_FORCE_REFRESH", "1")
    mapping, meta = st.get_theme_map("2026-10-16", cache_dir=tmp_path, fetcher=_FakeFetcher(_m2()))
    assert meta["source"] == "live"
    assert mapping["600172"]["theme"] == "机器人"
    snap = st.load_theme_snapshot("2026-10-16", cache_dir=tmp_path)
    assert snap["mapping"]["600172"]["theme"] == "机器人", "FORCE_REFRESH 应覆盖快照"


def test_force_refresh_empty_falls_back_to_snapshot(tmp_path, monkeypatch):
    """强制刷新但接口抓空 → 退回已有快照，不把当日标注清空。"""
    st.get_theme_map("2026-10-16", cache_dir=tmp_path, fetcher=_FakeFetcher(_m1()))
    monkeypatch.setenv("THEME_SNAPSHOT_FORCE_REFRESH", "1")
    mapping, meta = st.get_theme_map("2026-10-16", cache_dir=tmp_path, fetcher=_FakeFetcher({}))
    assert meta["source"] == "snapshot_fallback"
    assert mapping["600172"]["theme"] == "金刚石/培育钻石"


def test_disabled_switch_restores_legacy_behaviour(tmp_path, monkeypatch):
    st.get_theme_map("2026-10-16", cache_dir=tmp_path, fetcher=_FakeFetcher(_m1()))
    monkeypatch.setenv("THEME_SNAPSHOT_ENABLED", "0")
    f = _FakeFetcher(_m2())
    mapping, meta = st.get_theme_map("2026-10-16", cache_dir=tmp_path, fetcher=f)
    assert meta["source"] == "disabled"
    assert f.calls == 1, "关闭快照后应恢复每次实时抓"
    assert mapping["600172"]["theme"] == "机器人"


def test_empty_live_without_snapshot_is_honest_empty(tmp_path):
    mapping, meta = st.get_theme_map("2026-10-16", cache_dir=tmp_path, fetcher=_FakeFetcher({}))
    assert mapping == {}
    assert meta["source"] == "empty"
    assert not st.theme_snapshot_path("2026-10-16", tmp_path).exists(), "空映射不得落快照"


def test_corrupt_snapshot_falls_back_to_live(tmp_path):
    p = st.theme_snapshot_path("2026-10-16", tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ 这不是 json", encoding="utf-8")
    mapping, meta = st.get_theme_map("2026-10-16", cache_dir=tmp_path, fetcher=_FakeFetcher(_m1()))
    assert meta["source"] == "live"
    assert mapping["600172"]["theme"] == "金刚石/培育钻石"


def test_snapshot_with_empty_mapping_not_trusted(tmp_path):
    """空 mapping 的快照（异常产物）视为无效 → 重新抓取。"""
    p = st.theme_snapshot_path("2026-10-16", tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": st.THEME_SNAPSHOT_SCHEMA, "mapping": {}}), encoding="utf-8")
    mapping, meta = st.get_theme_map("2026-10-16", cache_dir=tmp_path, fetcher=_FakeFetcher(_m1()))
    assert meta["source"] == "live"
    assert mapping


def test_fingerprint_stable_regardless_of_key_order():
    a = {"600001": {"theme": "A"}, "600002": {"theme": "B"}}
    b = {"600002": {"theme": "B"}, "600001": {"theme": "A"}}
    assert st.theme_fingerprint(a) == st.theme_fingerprint(b)
    assert st.theme_fingerprint(a) != st.theme_fingerprint({"600001": {"theme": "A"}})


def test_snapshot_payload_has_audit_fields(tmp_path):
    st.get_theme_map("2026-10-16", cache_dir=tmp_path, fetcher=_FakeFetcher(_m1()))
    raw = json.loads(st.theme_snapshot_path("2026-10-16", tmp_path).read_text(encoding="utf-8"))
    assert raw["schema"] == st.THEME_SNAPSHOT_SCHEMA
    assert raw["date"] == "2026-10-16"
    assert raw["count"] == 1
    assert len(raw["fingerprint"]) == 12
    assert raw["generated_at"][:2] == "20"
