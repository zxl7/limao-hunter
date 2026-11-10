"""人工题材归因（`common/manual_theme.py`）的回归锁。

## 这个模块解决什么

系统给不出题材时会用**申万二级行业名**填空（东方创业 600278 → 「贸易Ⅱ」），
然后把它当成一条真题材线，在卡面上写「这条线最高板」—— 一条只有它自己的线。
用户实际归类是「东方系」（名字带东方），而这类逻辑选股宝 plates 给不出来
（plates 只有 `['其他']`，`theme` 是空串）。

## 因此本文件锁三件事

1. **读不到就是读不到**：文件缺失 / 坏 JSON / 空 theme → 一律返回空，⛔ 不抛、⛔ 不编造。
2. **命中才覆盖**：没标注的票返回 None，调用方必须回落到系统原题材。
3. ⛔ **只存档，不上卡，不进判据**（2026-11-10 用户「就 pass 掉这个玄学方案吧，只是记录一下」）：
   - 不上卡：`build_cards` 不读它（卡面题材名保持系统原值，哪怕那是行业兜底「贸易Ⅱ」）；
   - 不进判据：板块龙 / 身位 / 同线家数 / 仓位 一律不读它 ——
     命名族炒作没有基本面锚（羊系 11-03 峰值 11 只 → 11-04 掉到 2 只），
     拿它做准入等于把系统押在谐音梗上。
   本文件用源码扫描把这两条钉死。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from common import manual_theme as mt

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def f(tmp_path):
    """给模块一个临时文件，避开真实 cache（⛔ 不读写生产标注）。"""
    p = tmp_path / "manual_theme.json"
    return p


def _write(p: Path, payload) -> None:
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
                 encoding="utf-8")


def test_missing_file_returns_empty(f):
    assert mt.load(f, refresh=True) == {}
    assert mt.lookup("600278", f) is None


def test_broken_json_does_not_raise(f):
    _write(f, "{ this is not json")
    assert mt.load(f, refresh=True) == {}


def test_valid_entry_is_readable(f):
    _write(f, {"themes": {"600278": {"theme": "东方系", "note": "带东方", "added": "2026-11-10"}}})
    hit = mt.lookup("600278", f)
    assert hit and hit["theme"] == "东方系"
    assert hit["note"] == "带东方"


@pytest.mark.parametrize("bad", [
    {"themes": {"600278": {"theme": "", "note": "空题材"}}},          # 空 theme
    {"themes": {"600278": {"note": "没有 theme 键"}}},
    {"themes": {"600278": "字符串不是对象"}},
    {"themes": {}},
    {},                                                              # 缺 themes
    {"themes": ["列表不是字典"]},
])
def test_invalid_payloads_yield_nothing(f, bad):
    _write(f, bad)
    assert mt.load(f, refresh=True) == {}
    assert mt.lookup("600278", f) is None


def test_unlabelled_code_returns_none_so_caller_falls_back(f):
    _write(f, {"themes": {"600278": {"theme": "东方系"}}})
    assert mt.lookup("000001", f) is None
    # 回落必须给回原题材，⛔ 不能变成空串把卡面弄花
    assert mt.label("000001", fallback="贸易Ⅱ", path=f) == "贸易Ⅱ"
    assert mt.label("600278", fallback="贸易Ⅱ", path=f) == "东方系"


def test_real_file_is_valid_json():
    """生产标注文件必须是合法 JSON、且每条都有 theme（防止手改写坏）。"""
    real = ROOT / "cache" / "manual_theme.json"
    if not real.exists():
        pytest.skip("尚无标注文件")
    obj = json.loads(real.read_text(encoding="utf-8"))
    themes = obj.get("themes") or {}
    assert isinstance(themes, dict)
    for code, item in themes.items():
        assert isinstance(item, dict), f"{code} 不是对象"
        assert str(item.get("theme") or "").strip(), f"{code} 缺 theme"


def test_never_used_by_decision_paths():
    """⛔ 铁律：人工归因只存档，连卡面都不上，准入判据更不准读。

    用户 2026-11-10 定：「就 pass 掉这个玄学方案吧，只是记录一下」。
    若哪天有人图方便在 `pool_call` / `sector_leader` / `relay_position` 里读它，
    等于让「谐音梗」参与买不买的裁决 —— 这条会直接红。
    """
    forbidden = [
        ROOT / "wealth_code" / "core" / "pool_call.py",
        ROOT / "wealth_code" / "core" / "sector_leader.py",
        ROOT / "wealth_code" / "core" / "relay_position.py",
        ROOT / "wealth_code" / "core" / "ai_caliber.py",
        ROOT / "wealth_code" / "core" / "daily_screen.py",
    ]
    offenders = []
    for p in forbidden:
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8")
        if "manual_theme" in txt:
            offenders.append(p.name)
    assert not offenders, (
        "准入判据里出现了 manual_theme —— 人工玄学归因只能用于展示，"
        f"不能影响买卖裁决：{offenders}"
    )
