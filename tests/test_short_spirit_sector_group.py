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
def test_linkage_delta_ignores_repeated_and_price_only_changes():
    top = {"code": "600001", "pct": 10}
    row = ("测试题材", 5, 3, top)
    rows, updates = ss._linkage_delta([row], {})
    assert rows == [row]
    assert ss._linkage_delta([row], updates) == ([], {})
    assert ss._linkage_delta([("测试题材", 5, 3, {"code": "600002", "pct": 9.9})], updates) == ([], {})
    assert ss._linkage_delta([("测试题材", 4, 2, top)], updates) == ([], {})
    assert ss._linkage_delta([("测试题材", 5, 4, top)], updates)[0]
    assert ss._linkage_delta([("测试题材", 6, 3, top)], updates)[0]


def test_explicit_empty_group_summary_does_not_fall_back():
    sigs = [_sig("A", "600001", "测试题材", 10, "涨停")]
    card = ss.build_card(sigs, "morning", NOW, group_view=sigs, group_summary=[])
    assert "板块联动" not in _content(card)
    assert "600001" in _content(card)


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
    # 显式关族归并 → 验「原口径」的排序规则本身（2026-11-03 加了归族，默认会把影视并进传媒）
    out = ss._sector_group_summary(sigs, merge_family=False)
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
    names = [x[0] for x in ss._sector_group_summary(sigs, merge_family=False)]
    assert "影视" in names            # theme 优先
    assert "影视院线" not in names     # 未被行业口径拆开
    assert "电池" in names             # 无 theme → 行业兜底


def test_family_merge_rolls_media_industries_together():
    """2026-11-03 用户「早上没发现传媒大爆发」→ 东财口径下传媒被拆成四条小线。

    实录（11-03 涨停池）：大晟=游戏Ⅱ、浙文=纺织制造、华阳=工程咨询、龙版=出版。
    按行业聚合 → 各 1 家，一条主线都排不上；按族归并 → 传媒 8 只顶到第一位。
    ⛔ 只改「板块联动」段的展示聚合，不改 signals 的 theme 字段本身。
    """
    sigs = [
        _sig("大晟文化", "600892", "游戏Ⅱ", 10.0, "涨停", 0.9),
        _sig("华智数媒", "300426", "影视院线", 20.0, "涨停", 0.85),
        _sig("掌阅科技", "603533", "数字媒体", 10.0, "涨停", 0.8),
        _sig("国旅联合", "600358", "广告营销", 10.1, "涨停", 0.75),
        _sig("龙版传媒", "605577", "出版", 10.0, "涨停", 0.7),
        _sig("某电池", "600001", "电池", 10.0, "涨停", 0.95),
    ]
    out = ss._sector_group_summary(sigs, merge_family=True)
    assert out[0][0] == "传媒"
    assert out[0][1] == 5 and out[0][2] == 5      # 5 只全并进来
    # 「电池」是东财行业名，2026-11-03 起归入「锂电池族」（A 股电池行业基本等价锂电）
    assert out[1][0] == "锂电池"

    # 反向：关掉归并 → 恢复成四条各 1 只的小线（证明开关有效、可回退）
    raw = {x[0]: x[1] for x in ss._sector_group_summary(sigs, merge_family=False)}
    assert raw["游戏Ⅱ"] == 1 and raw["数字媒体"] == 1 and raw["广告营销"] == 1


def test_family_merge_unifies_theme_and_sector_naming():
    """2026-11-03 13:07 实录：8 只锂电被拆成「电池 6只」+「锂电池 1只」两组。

    根因：`sec = theme or sector`，而有的票 theme 命中了 top5 题材名「锂电池」，
    有的票 theme 落回东财行业名「电池」→ 同一批票两种叫法 → 家数算不对。
    修法：theme 不中族时，再拿 sector 试一次（`_family_of(theme) or _family_of(sector)`）。
    """
    sigs = [
        _sig("圣阳股份", "002580", "电池", 10.02, "涨停", 0.9, theme="锂电池"),
        _sig("龙蟠科技", "603906", "电池", 9.99, "涨停", 0.85, theme="电池"),
        _sig("盟固利", "301487", "电池", 19.99, "涨停", 0.95, theme="电池"),
    ]
    out = ss._sector_group_summary(sigs, merge_family=True)
    assert len(out) == 1, f"同一条线被拆成 {[x[0] for x in out]}"
    assert out[0][0] == "锂电池" and out[0][1] == 3 and out[0][2] == 3

    # 反向：关掉归并 → 按字面拆成两组（证明这是归并带来的，不是巧合）
    raw = {x[0]: x[1] for x in ss._sector_group_summary(sigs, merge_family=False)}
    assert raw == {"锂电池": 1, "电池": 2}


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
