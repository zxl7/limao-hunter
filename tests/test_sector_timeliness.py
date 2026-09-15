"""时效门黄金回归锁（2026-09-07 11:30 用户「推送必须当下·别推早上发生很久的事」）。

事故复盘：10:46 开启板块加强家族开关，泛农业（大农业7+养猪2=9）早在 09:25 就达到
该高度，但家族名不在 sector_pushed → 被判 first 补报"旧闻"，用户抓包。
根因=触发只比对"历史峰值"，没有比对"上一轮快照"→ 存量状态被当增量事件。
修复=_decide_sector_stage 纯函数统一收口：所有触发必须 本轮 zc > 上轮 pzc。
"""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("live_signal", SCRIPTS / "live_signal.py")
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)

G = ls._decide_sector_stage


def test_stale_no_increment_never_pushed():
    """存量高度：本轮 zc == 上轮 pzc（09:25 就 9 只，10:46 仍 9 只）→ 一律不推。"""
    assert G(9, 9, first=True, peak=0) is None          # first 也不补报旧闻
    assert G(9, 9, first=False, peak=7) is None         # peak 不触发
    assert G(9, 9, first=False, peak=7, gradual_ok=True) is None  # gradual 不触发


def test_new_increment_first_is_current():
    """当下增量：本轮 9 > 上轮 8 且当天未报 → first（开盘/新达标瞬间才允许 first）。"""
    assert G(9, 8, first=True, peak=0) == "first"


def test_new_increment_peak():
    """当下增量 + 超已推峰值+2 → peak（强度新高）。"""
    assert G(13, 12, first=False, peak=11) == "peak"
    # 有增量但未达峰值+2 → 不是 peak
    assert G(12, 11, first=False, peak=11) != "peak"


def test_new_increment_gradual():
    """当下增量 + zc>峰值 但不足+2 + 渐进间隔满足 → gradual（+1 爬升场景）。"""
    assert G(12, 11, first=False, peak=11, gradual_ok=True) == "gradual"
    # 渐进间隔未满足（gradual_ok=False）→ 不推
    assert G(12, 11, first=False, peak=11, gradual_ok=False) is None


def test_below_threshold_never_pushed():
    """涨停数不足 3 → 任何情况不推。"""
    assert G(2, 1, first=True, peak=0) is None


def test_family_base_inherited_stale_suppressed():
    """家族语义：成员已报峰值 7，家族当前 9；上轮家族口径 9（无增量）→ 不推。
    修复前此场景会以 first 补报（10:46 事故），现在被时效门拦截。"""
    fam_base = 7                                  # 继承成员峰值
    assert G(9, 9, first=False, peak=fam_base) is None          # 9 < 7+2 → 非 peak
    assert G(9, 9, first=False, peak=fam_base, gradual_ok=True) is None
    # 家族真增量到 10（上轮 9）→ 当下，10 >= 7+2 → peak
    assert G(10, 9, first=False, peak=fam_base) == "peak"
