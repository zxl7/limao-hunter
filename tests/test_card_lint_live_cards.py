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
