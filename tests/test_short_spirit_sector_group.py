# -*- coding: utf-8 -*-
"""板块联动归纳（2026-10-12 用户「尽量进行板块联动的观察归纳」）的回归锁。

规则（本测试锁死）：
  ① `_sector_group_summary` = 纯函数：本轮信号按板块（东财行业名 `sector`）聚合；
  ② 返回 (sector, total, zt_count, top_signal)，排序 = 涨停数降 → 家数降 → 组内最强强度降；
  ③ 缺 `sector` 字段 → 归「其他」；空/None 输入 → []；组内 top = strength 最高者；
  ④ 卡面新增「🔥 板块联动」段：仅列 家数≥SECTOR_GROUP_MIN_ITEMS 或 有涨停 的板块；
  ⑤ ⛔ 开关 ENABLE_SPIRIT_SECTOR_GROUP=False → 卡面无该段（回到改动前行为）；
  ⑥ 新段渲染 try/except 降级：异常不得影响主卡（盘中链路安全第一）。

隔离约定：仅调纯函数/纯渲染，不推送、不联网、不碰生产 cache。
"""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location(
    "short_spirit_sectorgroup", SCRIPTS / "short_spirit.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

NOW = ss.datetime(2026, 10, 12, 10, 0)


def _sig(name, code, sector, pct, typ="快速拉升", strength=0.5, theme=None):
    return {"name": name, "code": code, "sector": sector, "theme": theme,
            "market_cap_yi": 50.0,
            "amount_yi": 8.0, "pct": pct, "type": typ, "tags": [],
            "resonance": 50, "flow_yi": 1.0, "strength": strength,
            "reason": f"共振[{sector}]"}


def _content(card):
    return card["elements"][0]["text"]["content"]


# ── ① 纯函数：聚合 ─────────────────────────────────────────────
def test_empty_and_none():
    assert ss._sector_group_summary([]) == []
    assert ss._sector_group_summary(None) == []


def test_single_sector_aggregate_and_top():
    sigs = [
        _sig("天顺风能", "002531", "风电设备", 9.97, "涨停", 0.9),
        _sig("大金重工", "002487", "风电设备", 3.52, "快速拉升", 0.4),
        _sig("金风科技", "002202", "风电设备", 5.0, "快速拉升", 0.6),
    ]
    out = ss._sector_group_summary(sigs)
    assert len(out) == 1
    sec, total, zt, top = out[0]
    assert sec == "风电设备"
    assert total == 3
    assert zt == 1
    assert top["name"] == "天顺风能"          # 组内 strength 最高（0.9）


def test_multi_sector_sort_zt_first():
    sigs = [
        _sig("A", "600001", "医药", 5.0, "快速拉升", 0.9),
        _sig("B", "600002", "医药", 6.0, "快速拉升", 0.8),
        _sig("C", "600003", "影视", 9.9, "涨停", 0.7),
    ]
    out = ss._sector_group_summary(sigs)
    assert out[0][0] == "影视" and out[0][2] == 1     # 有涨停者优先（尽管只有 1 只）
    assert out[1][0] == "医药" and out[1][1] == 2     # 同 0 涨停时家数多者优先


def test_missing_sector_falls_to_other():
    out = ss._sector_group_summary([_sig("X", "600010", "", 3.0)])
    assert out[0][0] == "其他"


def test_theme_preferred_over_sector():
    """口径：theme（题材名）优先于 sector（东财行业名）→ 与「🎯 接力方向」同口径。"""
    sigs = [
        _sig("德才股份", "605287", "影视院线", 10.0, "涨停", 0.9, theme="影视"),
        _sig("慈文传媒", "002343", "影视院线", 10.0, "涨停", 0.8, theme="影视"),
        _sig("X", "600001", "电池", 5.0, "快速拉升", 0.5, theme=None),  # 无题材 → 落行业
    ]
    names = [x[0] for x in ss._sector_group_summary(sigs)]
    assert "影视" in names            # theme 优先
    assert "影视院线" not in names     # 未被行业口径拆开
    assert "电池" in names             # 无 theme → 行业兜底


# ── ② 卡面段渲染 ──────────────────────────────────────────────
def test_card_has_sector_group_section():
    sigs = [
        _sig("天顺风能", "002531", "风电设备", 9.97, "涨停", 0.9),
        _sig("大金重工", "002487", "风电设备", 3.52, "快速拉升", 0.4),
        _sig("华润双鹤", "600062", "医药", 8.5, "接近涨停", 0.7),   # 单只且无涨停 → 不列
    ]
    content = _content(ss.build_card(sigs, "morning", NOW))
    assert "🔥 板块联动 1个" in content      # 仅风电设备满足（家数≥2）
    assert "风电设备" in content
    assert "涨停1" in content


def test_switch_off_hides_section(monkeypatch):
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_SECTOR_GROUP", False)
    sigs = [
        _sig("天顺风能", "002531", "风电设备", 9.97, "涨停", 0.9),
        _sig("大金重工", "002487", "风电设备", 3.52, "快速拉升", 0.4),
    ]
    content = _content(ss.build_card(sigs, "morning", NOW))
    assert "🔥 板块联动" not in content


def test_section_error_degrades_without_breaking_card(monkeypatch):
    def _boom(_signals):
        raise RuntimeError("boom")
    monkeypatch.setattr(ss, "_sector_group_summary", _boom)
    sigs = [_sig("天顺风能", "002531", "风电设备", 9.97, "涨停", 0.9)]
    content = _content(ss.build_card(sigs, "morning", NOW))
    assert "🔥 板块联动" not in content       # 段降级
    assert "天顺风能" in content               # 主卡仍正常生成
