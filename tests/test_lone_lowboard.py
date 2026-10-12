# -*- coding: utf-8 -*-
"""低板孤军不进接力池（2026-10-12 用户「接力不推没板块的独苗」·C 方案）回归锁。

用户原话：「你推了一个没有板块的独苗！这在接力中非常不可取！！！」
事故（10-09 接力池）：九阳股份（华为链/小家电 都只剩自己）+ 安泰集团（core_theme=「其他」无题材）。

规则（本测试锁死）：
  ① 低板（boards ≤ LONE_LOWBOARD_MAX=2）且孤军 → 移出接力池（自动回落观察池）；
  ② 孤军 = 行业/题材只剩自己（sector_is_lone/theme_is_lone）**或** 无有效题材（core_theme∈{空/其他/—}）；
  ③ ⛔ 高标（boards≥3）孤军 **保留**（2026-08-24 用户「认可独立总龙头」原意）；
  ④ ⛔ 开关默认开、常量锁定（回退 = 关开关）。
"""
import importlib.util
from pathlib import Path

WEALTH = Path(__file__).resolve().parents[1] / "wealth_code"
spec = importlib.util.spec_from_file_location(
    "quant_score_lone", WEALTH / "core" / "quant_score.py")
qs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qs)


def test_lowboard_lone_excluded():
    """① 1-2 板孤军 → True（应移出接力池）。"""
    assert qs._is_lone_lowboard({"boards": 1, "sector_is_lone": True}) is True
    assert qs._is_lone_lowboard({"boards": 1, "theme_is_lone": True}) is True
    assert qs._is_lone_lowboard({"boards": 2, "sector_is_lone": True}) is True


def test_themeless_excluded():
    """② 无有效题材（core_theme 空/其他/—）→ 也算孤军。"""
    for ct in ("", "其他", "—"):
        assert qs._is_lone_lowboard({"boards": 1, "core_theme": ct}) is True, ct


def test_high_board_lone_kept():
    """③ 高标孤军（boards≥3）→ False（保留独立总龙头）。"""
    assert qs._is_lone_lowboard({"boards": 3, "sector_is_lone": True}) is False
    assert qs._is_lone_lowboard({"boards": 5, "theme_is_lone": True}) is False


def test_normal_candidate_not_excluded():
    """有板块支撑的低板票（题材/行业同伴多）→ False（不误伤）。"""
    assert qs._is_lone_lowboard(
        {"boards": 1, "core_theme": "锂电池",
         "sector_is_lone": False, "theme_is_lone": False}) is False


def test_switch_defaults():
    """④ 开关默认开、阈值=2（回退 = 关开关）。"""
    assert qs.ENABLE_LONE_LOWBOARD_EXCLUDE is True
    assert qs.LONE_LOWBOARD_MAX == 2


def test_real_case_jiuyang_and_antai():
    """实证：10-09 接力池的九阳股份(孤军)/安泰集团(无题材) 应被 C 捕获。"""
    jiuyang = {"boards": 1, "core_theme": "华为链", "sector_is_lone": True, "theme_is_lone": True}
    antai = {"boards": 1, "core_theme": "其他", "sector_is_lone": False, "theme_is_lone": False}
    assert qs._is_lone_lowboard(jiuyang) is True
    assert qs._is_lone_lowboard(antai) is True
