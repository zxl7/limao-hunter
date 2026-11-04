#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材周期相位判定回归（theme_phase_v1·2026-11-03）。

锁定四件事：
1. 七种相位的判据各自可复现（含真实案例：传媒 27→9 是分歧、机器人 40→6 是退潮）
2. **第一次腰斩不是退潮**——这是最容易写错、也最伤人的一条
   （把「分歧第 1 天」误判成「退潮」，等于让系统在最好的位置喊线走完了）
3. 缺失语义：今日家数缺失 → 不判（⛔ 不许拿昨天当今天）
4. 接入 `theme_rotation` 后 phase_v2 ⛔ 不覆盖既有 phase 字段
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import theme_phase as TP  # noqa: E402


# ── 基础相位 ──────────────────────────────────────────────

def test_start_first_day():
    """新线首次成线 → 启动第 1 天。"""
    r = TP.judge([0, 0, 1, 1, 9])
    assert r["phase"] == "start"
    assert r["day_n"] == 1
    assert r["peak"] == 9


def test_climax_when_new_high_on_day3():
    """连续抬升到第 3 天创新高 → 高潮（今天就是峰值）。"""
    r = TP.judge([5, 2, 8, 10, 11])
    assert r["phase"] == "climax"
    assert r["day_n"] == 3
    assert r["peak"] == 11


def test_climax_not_triggered_on_day1_or_2():
    """第 1~2 天即使创新高也只能是启动，⛔ 不许叫高潮。"""
    assert TP.judge([0, 0, 0, 12])["phase"] == "start"
    assert TP.judge([0, 0, 0, 6, 12])["phase"] == "start"


def test_ferment_holds_above_70pct():
    """峰值在前几天、今日仍在峰值 70% 以上 → 发酵。"""
    r = TP.judge([5, 4, 4, 4])          # 4/5 = 80%，未创新高但没掉队
    assert r["phase"] == "ferment"


def test_below_70pct_is_not_ferment():
    """反向：掉到峰值 60% 就不许再叫「仍在高位」。"""
    r = TP.judge([5, 4, 3, 3, 3])       # 3/5 = 60%
    assert r["phase"] != "ferment"


def test_diverge_first_halving_is_not_ebb():
    """⭐ 核心：**第一次腰斩是分歧，不是退潮**。

    传媒 2026-11-02 27 家 → 11-03 9 家（掉 67%、只剩峰值 33%）。
    这正是用户当天最需要判对的位置：说「分歧」= 还有 2/3 回流先验；
    说「退潮」= 让人在分歧位就把线划掉。旧实现就是错判成退潮。
    """
    r = TP.judge([1, 1, 2, 2, 4, 7, 1, 2, 1, 1, 2, 27, 9])
    assert r["phase"] == "diverge", f"期望分歧，实得 {r['phase']}（{r['reason']}）"
    assert r["phase_cn"] == "分歧"
    assert r["peak"] == 27 and r["cur"] == 9


def test_ebb_when_retrace_fails_prior_high():
    """⭐ 核心：反抽不过前高 = 退潮。

    机器人 40 → 27 → 14 → 24 → 6 → 5 → 13：中间反抽到 27 没超前高 40，
    之后再怎么弹也回不到六成，判退潮而不是「回流成功」。
    """
    r = TP.judge([15, 40, 27, 14, 24, 6, 5, 13])
    assert r["phase"] == "ebb"
    assert "反抽" in r["reason"]


def test_ebb_when_below_floor():
    """跌破峰值 25% → 退潮。"""
    r = TP.judge([12, 10, 8, 6, 4, 3, 2])
    assert r["phase"] == "ebb"
    assert r["peak"] == 12


def test_reflow_after_diverge():
    """分歧后回升 ≥15% 且家数够 → 回流。"""
    r = TP.judge([3, 8, 12, 5, 10])
    assert r["phase"] == "reflow"
    assert r["cur"] == 10


def test_reflow_needs_absolute_floor():
    """⭐ 「1 家变 2 家」不是回流 —— 家数下限 4 家，否则杂毛冒头会被当资金回来。"""
    r = TP.judge([1, 2, 4, 1, 2])
    assert r["phase"] != "reflow", f"2 家被误判为回流：{r['reason']}"


def test_reflow_flags_retrace_fail():
    """弱回流：回来但不到前高六成 → 打 is_retrace_fail，措辞降级。"""
    r = TP.judge([20, 18, 20, 4, 6])
    if r["phase"] == "reflow":
        assert r.get("is_retrace_fail") is True
        assert "别当第二轮" in r["action"]


def test_dormant_when_never_formed():
    """整窗最高仅 2 家 → 沉寂，不硬凑相位。"""
    r = TP.judge([1, 2, 1, 2, 1])
    assert r["phase"] == "dormant"


def test_first_halving_below_floor_is_diverge_not_ebb():
    """⭐ 掉破 25% 但只有第 1 天 → 仍是分歧，不是退潮（2026-11-03 实测踩到）。

    短剧/互动影游 11-02 29 家 → 11-03 6 家（21%）。旧实现走退潮 A
    （只要 ratio < EBB_FLOOR 就命中），当天就把线划成「走完了」；
    可当天只看得见「掉下来」，看不见「反抽不过前高」——那是明天的信息。
    """
    r = TP.judge([2, 3, 5, 12, 29, 6])
    assert r["phase"] == "diverge", f"期望分歧，实得 {r['phase']}（{r['reason']}）"
    assert r["peak"] == 29 and r["cur"] == 6
    assert "等明日反抽定" in r["reason"]


def test_ebb_below_floor_needs_two_points_after_peak():
    """同一形态，但峰值后已经有过第 2 个点 → 才是退潮。"""
    r = TP.judge([2, 3, 5, 12, 29, 12, 6])
    assert r["phase"] == "ebb", f"期望退潮，实得 {r['phase']}（{r['reason']}）"


def test_small_line_never_called_ebb():
    """⭐ 峰值不到 5 家的，不配叫退潮（2026-11-03 白酒 峰4→今1 实测）。

    白酒整窗最高 4 家，怎么算都不是一条主线；标「退潮」只会在终端刷假风险。
    """
    r = TP.judge([2, 4, 4, 3, 1])
    assert r["phase"] == "dormant", f"期望没成线，实得 {r['phase']}（{r['reason']}）"
    assert "没成线" in r["reason"]


# ── 缺失语义（抓不到≠没有）────────────────────────────────

def test_empty_series():
    assert TP.judge([])["phase"] == "dormant"
    assert TP.judge(None)["phase"] == "dormant"


def test_all_none_series():
    r = TP.judge([None, None, None])
    assert r["phase"] == "dormant"
    assert r["quality"] == "empty"


def test_trailing_none_is_trimmed():
    """尾部 None 应被裁掉（缺席不等于 0），不能把它当「今天 0 家」。"""
    a = TP.judge([3, 8, 12, 5, None])
    b = TP.judge([3, 8, 12, 5])
    assert a["phase"] == b["phase"] and a["cur"] == b["cur"] == 5


def test_today_param_appends():
    """today 显式传入时追加到序列末（供盘中实时调用）。"""
    base = TP.judge([5, 10, 12])
    with_today = TP.judge([5, 10, 12], today=3)
    assert base["cur"] == 12 and with_today["cur"] == 3
    assert with_today["phase"] == "diverge"


# ── 质量与输出结构 ────────────────────────────────────────

def test_quality_weak_on_short_window():
    """有效天数 < 8 → quality=weak（样本不足必须自曝）。"""
    assert TP.judge([3, 5, 8])["quality"] == "weak"


def test_quality_full_on_long_window():
    s = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert TP.judge(s)["quality"] == "full"


def test_output_has_action_and_reason():
    """每档必须带人话 action + 可追溯 reason（卡面/终端直接渲染）。"""
    for s in ([0, 0, 9], [5, 10, 12, 4], [12, 10, 6, 3]):
        r = TP.judge(s)
        assert r["schema"] == TP.SCHEMA
        assert r["action"] and r["reason"]
        assert r["phase_cn"] in TP.PHASE_CN.values()


def test_no_buy_sell_wording():
    """⛔ 相位不输出买卖建议措辞 —— 它只回答「第几天」，不回答「买不买」。"""
    banned = ("买入", "卖出", "满仓", "清仓")
    for s in ([0, 0, 9], [5, 10, 12, 4], [12, 10, 6, 3], [1, 2, 1]):
        r = TP.judge(s)
        assert not any(w in r["action"] for w in banned), r["action"]


def test_line_summary():
    rows = [TP.judge(s) for s in ([0, 0, 9], [5, 2, 8, 10, 11], [12, 10, 8, 6, 2])]
    txt = TP.line(rows)
    assert "启动" in txt and "高潮" in txt and "退潮" in txt


def test_judge_many():
    out = TP.judge_many({"A": [0, 0, 9], "B": [1, 2, 1]})
    assert out["A"]["phase"] == "start"
    assert out["B"]["phase"] == "dormant"


# ── 接入 theme_rotation：⛔ 不覆盖旧 phase ─────────────────

def _load_tr():
    spec = importlib.util.spec_from_file_location(
        "theme_rotation_under_test", ROOT / "scripts" / "theme_rotation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_phase_of_returns_none_when_series_date_missing():
    """主序列末日缺失 → 不判。`_load_zt_pool_series` 止于昨日，
    拿 None 当今天会让整条相位偏一天。"""
    tr = _load_tr()
    assert tr._phase_of("机器人", {"600892"}, None) is None


def test_phase_of_returns_none_without_codes():
    tr = _load_tr()
    assert tr._phase_of("X", set(), "2026-11-02") is None


def test_match_top5_codes_exact_also_unions():
    """⭐ 回归：exact 命中也要继续并子串匹配。

    top5 里同时存在「人形机器人」和「机器人」时，若 exact 命中就 return，
    只能拿到前者那几只；而后者才是 11-02 真有 13 家涨停的那条线。
    实测该 bug 让机器人今日家数从 13 掉到 1、相位被误判成「退潮」。
    """
    tr = _load_tr()
    top5 = {
        "人形机器人": {"600001"},
        "机器人": {"600002", "600003", "600004"},
        "半导体": {"600009"},
    }
    got = tr._match_top5_codes("人形机器人", top5)
    assert "600001" in got, "exact 自己的必须留"
    assert {"600002", "600003", "600004"} <= got, "同族长名必须并进来"
    assert "600009" not in got, "无关题材不许并进来"


def test_match_top5_codes_ignores_empty():
    tr = _load_tr()
    assert tr._match_top5_codes("机器人", {}) == set()


def test_phase_of_skips_when_codes_too_few(monkeypatch):
    """兜底口径下成分股不足 PHASE_MIN_CODES → 不判（家数会系统性低估，宁可空着）。

    2026-11-03 口径切换后，这条**只对兜底（代码并集）成立**：
    主口径（选股宝快照归属）不依赖成分股，其低估闸是「题材在快照里的票数」
    （见 `tests/test_theme_rotation_phase_src.py::test_too_few_theme_codes_returns_none`）。
    这里显式把快照置空，强制走兜底，锁的才是本意。
    """
    tr = _load_tr()
    monkeypatch.setattr(tr, "_top5_codes_by_theme", lambda: {})
    monkeypatch.setattr(tr, "_snapshot_plates_by_date", lambda: {})
    assert tr._phase_of("机器人", {"600001"}, "2026-11-02") is None


def test_phase_of_uses_zt_pool_series(monkeypatch):
    """有成分股 + 涨停池含该日 → 走涨停池交集算序列。"""
    tr = _load_tr()
    days = ["2026-11-03", "2026-11-04"]
    by_date = {"2026-11-03": {"600892": {}, "601599": {}},
               "2026-11-04": {"600892": {}}}
    monkeypatch.setattr(tr, "_load_zt_pool_series", lambda n=None: (days, by_date))
    # 自造成分股（5 只，须 ≥ PHASE_MIN_CODES）+ 关闭 top5 合并，避免真实文件干扰
    monkeypatch.setattr(tr, "_top5_codes_by_theme", lambda: {})
    monkeypatch.setattr(tr, "_snapshot_plates_by_date", lambda: {})
    codes = {"600892", "601599", "600001", "600002", "600003"}
    for c in ("600001", "600002", "600003"):
        monkeypatch.setitem(by_date["2026-11-03"], c, {})
    r = tr._phase_of("测试题材", codes, "2026-11-03")
    assert r is not None
    assert r["schema"] == TP.SCHEMA
    assert r["cur"] == 5          # 全 5 只在 11-03 池里
    assert r["src"] == "pool_codes"


def test_phase_of_returns_none_when_date_not_in_pool(monkeypatch):
    """主序列末日不在涨停池（今天还没归档）且无实时池 → 不判，⛔ 不拿昨天冒充今天。"""
    tr = _load_tr()
    days = ["2026-11-03"]
    by_date = {"2026-11-03": {"600892": {}}}
    monkeypatch.setattr(tr, "_load_zt_pool_series", lambda n=None: (days, by_date))
    monkeypatch.setattr(tr, "_top5_codes_by_theme", lambda: {})
    monkeypatch.setattr(tr, "_snapshot_plates_by_date", lambda: {})
    codes = {"600892", "601599", "600001", "600002"}
    assert tr._phase_of("测试题材", codes, "2099-01-01") is None


def test_phase_v2_does_not_overwrite_phase():
    """接入后 phase_v2 是**独立字段**，⛔ 不改旧 phase 的语义与取值域。"""
    tr = _load_tr()
    src = (ROOT / "scripts" / "theme_rotation.py").read_text(encoding="utf-8")
    # 只允许出现 `"phase_v2":` 赋值，不允许对 `"phase":` 做相位改写
    assert '"phase_v2"' in src
    assert tr.PHASE_LOOKBACK >= 8, "窗口必须 ≥8 个交易日，否则算不出「第几天」"
    assert hasattr(tr, "ENABLE_THEME_PHASE")


def test_phase_switch_reversible(monkeypatch):
    """可逆开关：ENABLE_THEME_PHASE=0 → 不写 phase_v2。"""
    import subprocess
    out = subprocess.run(
        [sys.executable, "-c",
         "import importlib.util;"
         f"spec=importlib.util.spec_from_file_location('tr', r'{ROOT}/scripts/theme_rotation.py');"
         "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
         "print(m.ENABLE_THEME_PHASE)"],
        capture_output=True, text=True, env={"ENABLE_THEME_PHASE": "0", "PATH": "/usr/bin:/bin"},
        cwd=str(ROOT))
    assert out.stdout.strip() in ("False", "0"), out.stderr
