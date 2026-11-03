# -*- coding: utf-8 -*-
"""机构方向验证脚本回归（只读计算，不写生产）。

覆盖三类容易悄悄退化的口径：
  1. 中文词面匹配：短题材名不能误命中长词（「铜」≠「覆铜板」）
  2. 泛词拒绝：「宠物经济」不能连到「低空经济」、「AI安全」不能连到「AI应用」
  3. 抓不到 ≠ 没有：无题材样本时结论必须是 no_sample，不能当成「盘面不认」
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import inst_direction as mod  # noqa: E402


# ── 1. 词面匹配 ────────────────────────────────────────────────────────
def test_contains_word_rejects_longer_word():
    assert mod._contains_word("覆铜板涨价", "铜") is False
    assert mod._contains_word("铜价上涨", "铜") is True
    assert mod._contains_word("化肥/钾肥", "化肥") is True


def test_tag_related_rejects_generic_words():
    # 只共享「经济」两字，不足以构成同一方向
    assert mod._tag_related("宠物经济", "低空经济") is False
    # 只共享「AI」两字母，不足以构成同一方向
    assert mod._tag_related("AI安全：攻防双向升级", "AI应用") is False
    # 只共享「存储」两字，方向其实不同（实测曾错连）
    assert mod._tag_related("端侧AI三重催化：存储见顶 + 汇率缓和", "存储芯片") is False
    # 2 字只有「整个题材核心词就是它」才算
    assert mod._tag_related("化肥 / 钾肥：超强厄尔尼诺", "化肥") is True
    assert mod._tag_related("快递旺季第二轮提价落地", "快递概念") is True
    # 3 字以上正常关联
    assert mod._tag_related("人形机器人：数据采集井喷", "人形机器人") is True
    assert mod._tag_related("AI 光通信材料：磷化铟涨价", "光通信") is True
    # 「AI安全」与「网络安全」只共享 2 字且核心词不同 → 宁漏不错，交给 AI 显式声明
    assert mod._tag_related("AI安全：攻防双向升级", "网络安全") is False


def test_tag_related_skips_meta_tags():
    assert mod._tag_related("业绩增长方向", "业绩增长") is False


# ── 2. 趋势判定 ────────────────────────────────────────────────────────
def test_trend_labels():
    assert mod._trend([1, 1, 1, 1, 1, 5, 5, 5, 5, 5])[0] == "rising"
    assert mod._trend([5, 5, 5, 5, 5, 1, 1, 1, 1, 1])[0] == "falling"
    assert mod._trend([3, 3, 3, 3, 3, 3, 3, 3, 3, 3])[0] == "flat"
    # 样本不足必须返回 unknown，不能猜
    assert mod._trend([1, 2, 3])[0] == "unknown"
    assert mod._trend([])[0] == "unknown"


# ── 3. 端到端：临时目录，绝不碰生产缓存 ────────────────────────────────
def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    zt_dir = cache / "zt_history"
    zt_dir.mkdir(parents=True)
    theme_dir = cache / "top5_history"
    theme_dir.mkdir(parents=True)

    monkeypatch.setattr(mod, "CACHE", cache)
    monkeypatch.setattr(mod, "ZT_DIR", zt_dir)
    monkeypatch.setattr(mod, "POOL_GLOB", str(cache / "institution_pool_*.json"))
    monkeypatch.setattr(mod, "THEME_INDEX", theme_dir / "theme_index.json")
    monkeypatch.setattr(mod, "_market_universe", lambda: (5000, "test"))

    # 两个交易日的涨停历史
    days = ["2026-10-27", "2026-10-28"]
    _write_json(zt_dir / "zt_2026-10-27.json", {"date": "2026-10-27", "zt_list": []})
    _write_json(zt_dir / "zt_2026-10-28.json", {
        "date": "2026-10-28",
        "zt_list": [
            {"code": "000001", "name": "涨停甲", "boards": 2, "sector": "汽车零部"},
            {"code": "000002", "name": "涨停乙", "boards": 1, "sector": "汽车零部"},
            {"code": "000003", "name": "涨停丙", "boards": 1, "sector": "汽车零部"},
            {"code": "000004", "name": "涨停丁", "boards": 1, "sector": "汽车零部"},
        ],
    })

    # 题材库：只覆盖「人形机器人」，不覆盖「宠物经济」
    _write_json(theme_dir / "theme_index.json", {
        "updated_at": "2026-10-28",
        "themes": {
            "人形机器人": {"codes": ["000001", "000002", "000003", "000004", "000005"]},
            "低空经济": {"codes": ["000009"]},
        },
    })

    # 机构池：一个能匹配题材的方向 + 一个匹配不到的方向
    _write_json(cache / "institution_pool_2026-10-28.json", {
        "date": "2026-10-28",
        "items": [
            {"code": "000005", "name": "机器人甲", "theme": "人形机器人：触觉传感器爆发"},
            {"code": "000008", "name": "宠物甲", "theme": "宠物经济"},
        ],
    })
    return cache, days


def test_build_separates_no_sample_from_ignore(sandbox):
    payload = mod.build("2026-10-28")
    assert payload["schema"] == mod.SCHEMA
    by_key = {d["direction_key"]: d for d in payload["directions"]}

    robot = by_key["人形机器人"]
    # 4 家涨停 / 全市场 5000 票，题材占比高 → 应被识别为盘面认这个方向
    assert robot["rep_tag"] == "人形机器人"
    assert robot["same_tag_zt"] == 4
    assert robot["today_zt_of_inst"] == []  # 机构点的 000005 没在涨停池

    pet = by_key["宠物经济"]
    # 题材库无对应方向：必须说「无法验证」，不能报「盘面没有反应」
    assert pet["rep_tag"] is None
    assert pet["verdict"] == "no_sample"
    assert pet["quadrant"] == "unpriced"


def test_build_does_not_touch_inputs(sandbox):
    cache, _ = sandbox
    pool = cache / "institution_pool_2026-10-28.json"
    zt = cache / "zt_history" / "zt_2026-10-28.json"
    before = (pool.read_bytes(), zt.read_bytes())
    mod.build("2026-10-28")
    assert (pool.read_bytes(), zt.read_bytes()) == before


def test_build_missing_zt_returns_error(sandbox):
    payload = mod.build("2026-01-01")
    assert payload.get("error", "").startswith("zt_history_missing")


def test_blind_spot_flags_unmentioned_sector(sandbox):
    payload = mod.build("2026-10-28")
    blind = {b["sector"]: b for b in payload["blind_spots"]}
    # 汽车零部今日 4 家涨停，机构池里没有该行业的票
    assert blind["汽车零部"]["today_zt"] == 4
    assert blind["汽车零部"]["inst_named_in_window"] == 0
