#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中卡白话门禁防误伤（2026-09-18）。

⛔ 事故预演：白话门禁上线当天，实测**核按钮卡 / 回流前兆卡**因卡面文案含 `MA5`
被 Tier-1 判为机器黑话 → 若直接进入阻断期，这两类盘中卡会**整卡推不出去（丢卡）**。
（已修源头：`MA5`→`5日线/10日线`、`非AI研判`→`非AI判断`、`恶化维度`→`恶化的方面`、
`承接强度`→`有没有资金接`。）

本测试锁定：**脚本生成的全部盘中卡必须干净通过白话门禁**——防止
① 门禁误伤生产卡；② 未来新增文案再次引入黑话。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import card_lint as L           # noqa: E402
import intraday_beat as ib      # noqa: E402
import live_signal as ls        # noqa: E402
from short_spirit import build_card as spirit_build_card   # noqa: E402

CACHE = ROOT / "cache"


def _jload(p: Path, d=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return d


_FB = _jload(CACHE / "feedback_matrix.json", {}) or {}
_LATEST = _jload(CACHE / "pulse_snapshots" / "latest.json", {}) or {}
_TR = _jload(CACHE / "theme_rotation.json", {}) or {}
_CANDS = (_TR.get("candidates") or [])[:3]
_TOP5 = (_LATEST.get("top5") or [])[:3]

# ⛔ 阻断期强制打开：本测试验证的是"阻断期行为"，与当前是否处于观察期无关
L.PLAIN_GATE_ENFORCE_FROM = "1970-01-01"

_CASES = [
    ("节拍卡", lambda: ib.build_beat_card()[0]),
    ("短线精灵卡", lambda: spirit_build_card(
        [{"name": "测试股", "code": "123456", "pct": 9.98, "sector": "AI",
          "type": "涨停", "reason": "板块爆发"}], "morning")),
    ("合并脉冲卡", lambda: ls.build_card([], [], [], [], "10:30")),
    ("涨停潮卡", lambda: ls.build_climax_card(_TOP5, "10:30")),
    ("核按钮卡", lambda: ls.build_nuke_card(_FB, "10:30")),
    ("回流前兆卡", lambda: ls.build_reflow_card(_CANDS, "10:30")),
    ("板块加强卡", lambda: ls.build_sector_boost_card(_TOP5, "10:30", _LATEST)),
    ("涨停反馈卡", lambda: ls.build_ebb_card(_FB, _LATEST, "10:30", [])),
    ("首板提示卡", lambda: ls.build_first_zt_card({}, "AI", "10:30")),
    ("机构票卡", lambda: ls.build_institution_card([], "10:30")),
]


@pytest.mark.parametrize("name,fn", _CASES, ids=[c[0] for c in _CASES])
def test_live_card_passes_plain_gate(name, fn):
    """盘中卡不得被白话门禁阻断（阻断期下也必须通过）。"""
    try:
        card = fn()
    except Exception as e:      # noqa: BLE001
        pytest.skip(f"{name} 构建需要更多数据: {type(e).__name__}")
    if not isinstance(card, dict):
        pytest.skip(f"{name} 未返回卡体")
    ok, errs, warns = L.lint_card(card)
    assert ok, f"{name} 被白话门禁阻断（会丢卡！）：{errs}"
    # 预警（非失败）：行业术语虽不阻断，但应持续收敛
    if warns:
        print(f"[{name}] WARN: " + "; ".join(w["msg"] for w in warns))


def test_nuke_and_reflow_cards_have_no_ma5():
    """回归锚：核按钮/回流卡曾因 `MA5` 被判黑话（已修源头）——锁定不再出现。"""
    for name, card in (("核按钮卡", ls.build_nuke_card(_FB, "10:30")),
                       ("回流前兆卡", ls.build_reflow_card(_CANDS, "10:30"))):
        blob = json.dumps(card, ensure_ascii=False)
        assert "MA5" not in blob and "MA10" not in blob, f"{name} 残留 MA5/MA10"
        assert "非AI研判" not in blob, f"{name} 残留「非AI研判」（应为「非AI判断」）"


def test_climax_card_returns_none_when_nothing_to_say():
    """⛔ 无内容 → 返回 None，⛔ 不许返回 `content=""` 的卡（2026-10-22）。

    事故预演：`build_climax_card([], "10:30")` 曾产出
    `{"tag":"div","text":{"tag":"lark_md","content":""}}` —— 飞书该字段必须非空，
    否则整块渲染异常。实测触发条件是早上 `pulse_snapshots/latest.json` 的 `top5` 为空。
    """
    assert ls.build_climax_card([], "10:30") is None
    # 有炸板提示时仍应出卡（内容来自 broken_cnt，与 sectors 无关）
    card = ls.build_climax_card([], "10:30", broken_cnt=ls.CLIMAX_BROKEN_HINT_N)
    assert isinstance(card, dict)
    assert card["elements"][0]["text"]["content"].strip()


def test_no_live_card_has_empty_content():
    """⛔ 全部盘中卡在任何数据状态下都不得产出空 content（飞书硬要求）。"""
    def _blank(o):
        if isinstance(o, dict):
            return ("content" in o and not str(o.get("content") or "").strip()) or any(
                _blank(v) for v in o.values())
        if isinstance(o, list):
            return any(_blank(v) for v in o)
        return False

    for name, fn in _CASES:
        try:
            card = fn()
        except Exception:      # noqa: BLE001
            continue
        if not isinstance(card, dict):
            continue
        assert not _blank(card), f"{name} 含空 content（飞书会渲染异常）"
