# -*- coding: utf-8 -*-
"""板块过热预警单测（2026-09-08 用户「发酵到高位别追高·话术通俗」）。

锁定 live_signal._climax_hit（三条件：峰值≥6 / 涨停数进入峰值80%高位区 / 本轮不再创新高）
与 build_climax_card（通俗话术·炸板提示·每天每板块1次由 main sig 层控）。
"""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("live_signal", SCRIPTS / "live_signal.py")
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)


def test_climax_hit_high_plateau():
    # 峰值 19·涨停 18-19（≥80%=16 且未创新高）→ 过热
    assert ls._climax_hit(19, 19) is True
    assert ls._climax_hit(18, 19) is True
    assert ls._climax_hit(16, 19) is True
    # 门槛收紧（09-08）：peak=9<10 的大消费级不再预警（原 6 门槛实测 8/9 会触发→ 已上调）
    assert ls._climax_hit(8, 9) is False


def test_climax_hit_below_threshold():
    # 15 < 19*80%(15.2) → 未过热（还差一点）；14 更不是
    assert ls._climax_hit(15, 19) is False
    assert ls._climax_hit(14, 19) is False


def test_climax_hit_new_high_not_warn():
    # 创新高（zc>peak）→ 由 peak 卡报喜，过热不掺和
    assert ls._climax_hit(20, 19) is False
    assert ls._climax_hit(19, 17) is False


def test_climax_hit_small_sector_skip():
    # 09-08 用户「有点子重复」收紧：峰值<10 的小板块/支流不预警（医药 6 家不再推过热）
    assert ls.CLIMAX_MIN_ZT >= 10
    assert ls._climax_hit(8, 8) is False
    assert ls._climax_hit(9, 9) is False
    assert ls._climax_hit(5, 12) is False
    assert ls._climax_hit(10, 10) is True   # ≥10 且到顶 → 过热
    assert ls._climax_hit(11, 12) is True   # 峰值12·11家(≥80%)且未新高 → 过热


def test_climax_card_plain_language():
    c = ls.build_climax_card([{"name": "大农业", "zt_count": 19}], "13:20", broken_cnt=17)
    s = "".join(e["text"]["content"] for e in c["elements"] if e.get("text"))
    assert "大农业" in s and "19 家" in s
    # 通俗话术铁律：人话提示站岗风险
    assert "站岗" in s and "今天买、明天才能卖" in s
    assert "17 家涨停被砸开" in s  # 炸板≥8 附封板松动提示
    assert c["color"] == "orange"


def test_climax_card_no_repeat_lines():
    # 09-08 用户「有点子重复」：删独立💡怎么用行（正文已说清位置+后果，不叠同义句）
    c = ls.build_climax_card([{"name": "泛农业", "zt_count": 19}], "13:20", broken_cnt=19)
    s = "".join(e["text"]["content"] for e in c["elements"] if e.get("text"))
    assert "💡" not in s and "怎么用" not in s
    assert "热度到顶" in s
    # 语义去重：同一意思(别追)只出现一次句式（标题句 + 站岗句为两行不同信息）
    assert s.count("追") <= 2


def test_climax_card_no_broken_hint_when_low():
    c = ls.build_climax_card([{"name": "光通信", "zt_count": 9}], "13:20", broken_cnt=2)
    s = "".join(e["text"]["content"] for e in c["elements"] if e.get("text"))
    assert "涨停被砸开" not in s  # 炸板少时不渲染这句
    assert "光通信" in s
