"""封板强度闸（core/seal_gate.py）测试。

守的边界：
1. 分档单调（时点/封单比/炸板/缩量），权重来自 33 日回测不是拍脑袋
2. **缺数据不放宽**——⛔ 不允许「因为不知道反而开后门」
3. 成交额**流动性**下限已拆（2026-11-03「A 拆掉下限」）→ 由封单/流通比承接；
   只保留数据防呆线（挡字段异常，⛔ 不是流动性判据）
4. 热题材豁免必须用「同题材家数」，⛔ 不许拿东财行业家数冒充
5. 可逆开关回旧行为
6. 真实案例回归（2026-11-02 传媒 9 只首板）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "wealth_code") not in sys.path:
    sys.path.insert(0, str(ROOT / "wealth_code"))

from core import seal_gate as SG  # noqa: E402


def _row(**kw):
    base = {"code": "600892", "name": "票", "boards": 1, "fbt": "092500",
            "zbc": 0, "hs": 1.0, "seal_amt": 0.8, "lt_mktcap": 24.0, "cje_yi": 2.0}
    base.update(kw)
    return base


# ── ① 分档单调 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("fbt,expect", [
    ("092500", 3), ("092600", 3), ("092601", 3), ("092700", 2),
    ("093100", 2), ("093101", 2), ("093200", 1), ("100000", 1),
    ("100100", 0), ("145959", 0),
    (92500, 3), ("09:25:00", 3),
])
def test_timing_bands(fbt, expect):
    """分档到「分钟」为止（秒被忽略）：092601 仍是 9:26 → 3 分。"""
    assert SG.seal_profile(_row(fbt=fbt))["parts"]["timing"] == expect


@pytest.mark.parametrize("seal,lt,expect", [
    (2.5, 24.0, 3),    # 10.4% ≥10%
    (1.2, 24.0, 2),    # 5.0%
    (0.6, 24.0, 1),    # 2.5%
    (0.4, 24.0, 0),    # 1.7% <2%
])
def test_seal_ratio_bands(seal, lt, expect):
    assert SG.seal_profile(_row(seal_amt=seal, lt_mktcap=lt))["parts"]["seal"] == expect


@pytest.mark.parametrize("zbc,expect", [(0, 2), (1, 1), (2, 0), (7, 0)])
def test_zbc_bands(zbc, expect):
    assert SG.seal_profile(_row(zbc=zbc))["parts"]["zbc"] == expect


@pytest.mark.parametrize("cje,expect", [(1.0, 2), (1.5, 2), (1.51, 1), (2.9, 1), (3.0, 1), (3.01, 0)])
def test_shrink_bands(cje, expect):
    # ⚠️ 2026-11-04 起缩量只对**一字板**加分（换手板缩量是做局，不是锁筹）
    assert SG.seal_profile(_row(cje_yi=cje, is_one_word=True))["parts"]["shrink"] == expect


def test_shrink_only_applies_to_low_boards():
    """高位缩量是另一回事（派发），⛔ 不在低位这条线上加分。"""
    low = SG.seal_profile(_row(boards=2, cje_yi=1.0, is_one_word=True))["parts"]["shrink"]
    high = SG.seal_profile(_row(boards=5, cje_yi=1.0, is_one_word=True))["parts"]["shrink"]
    assert low == 2 and high == 0


@pytest.mark.parametrize("one_word,expect", [
    (True, 2),      # 一字板：封死 → 缩量=锁筹，加分
    (False, 0),     # 换手板：缩量=少数资金对倒，⛔ 不给分
    (None, 0),      # 字段缺失：⛔ 不猜（既不没收也不奖励）
])
def test_shrink_depends_on_board_style(one_word, expect):
    """缩量加分的**唯一前提是一字板**（2026-11-04 用户「一字板成交额低没问题」）。"""
    row = _row(boards=2, cje_yi=1.0)
    if one_word is None:
        row.pop("is_one_word", None)
    else:
        row["is_one_word"] = one_word
    assert SG.seal_profile(row)["parts"]["shrink"] == expect


# ── ② 缺数据不放宽 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("field", ["fbt", "seal_amt", "lt_mktcap", "zbc"])
def test_missing_field_blocks_relaxation(field):
    row = _row(cje_yi=0.9)          # 成交额远低于任何门槛
    row[field] = None
    prof = SG.seal_profile(row)
    assert prof["grade"] == "unknown"
    assert prof["score"] is None
    assert field in prof["missing"]
    # 关键：unknown 不许当成 strong 放行
    v = SG.admit(row, 1.5)
    assert v["keep"] is False


def test_zero_lt_is_treated_as_missing():
    prof = SG.seal_profile(_row(lt_mktcap=0, cje_yi=0.9))
    assert prof["grade"] == "unknown"


def test_nan_is_not_zero():
    """NaN 不能当 0 用（否则 '0 亿成交额' 会误判成合格）；也不能当缺数据后放行。"""
    prof = SG.seal_profile(_row(cje_yi=float("nan")))
    assert prof["grade"] == "unknown" or prof["score"] is not None
    v = SG.admit(_row(cje_yi=float("nan")), 1.5)
    assert v["keep"] is False


# ── ③ 准入判定 ──────────────────────────────────────────────────────────────
def test_pass_when_turnover_enough():
    v = SG.admit(_row(cje_yi=9.0), 1.5)
    assert v["keep"] and v["exempt"] is False and v["reason"] == "成交额达标"


def test_absolute_floor_torn_down_strong_passes():
    """2026-11-03 用户「A 拆掉下限」：0.4 亿一字+厚封单 → 强封档放行。

    旧行为是硬拦（"真买不进"），实盘证伪：11-03 大晟文化 0.26 亿、华阳国际 0.47 亿、
    龙版传媒 0.48 亿全是 9:25 竞价一字，成交额小=筹码锁死，不是没人要。
    """
    row = _row(cje_yi=0.4, fbt="092500", seal_amt=2.5, zbc=0)
    assert SG.seal_profile(row)["grade"] == "strong"
    v = SG.admit(row, 1.5)
    assert v["keep"] is True and v["exempt"] is True and v["source"] == "strong"
    assert "绝对下限" not in v["reason"]


def test_data_sanity_floor_still_blocks():
    """⛔ 拆的是**流动性**下限，不是数据校验：0.01 亿（100 万）仍拦（疑似字段异常）。"""
    row = _row(cje_yi=0.01, fbt="092500", seal_amt=2.5, zbc=0)
    assert SG.seal_profile(row)["grade"] == "strong"
    v = SG.admit(row, 1.5)
    assert v["keep"] is False and v["source"] == "below_sanity"


def test_thick_seal_exempt_catches_weak_shape():
    """厚封单承接下限职能：形态一般（弱档）但封单/流通 ≥5% → 放行。

    构造：10:30 才封、炸板 2 次、不缩量（cje 大） → 形态分低；
    但封单 3 亿 / 流通 24 亿 = 12.5% → 厚封单豁免。
    """
    row = _row(cje_yi=1.0, fbt="103000", zbc=2, seal_amt=3.0, lt_mktcap=24.0)
    prof = SG.seal_profile(row)
    assert prof["grade"] != "strong", prof
    assert prof["seal_ratio_pct"] >= SG.THICK_SEAL_EXEMPT_PCT
    v = SG.admit(row, 1.5)
    assert v["keep"] is True and v["source"] == "thick_seal"


def test_thin_seal_and_weak_shape_still_rejected():
    """⛔ 反向：成交额小 + 形态弱 + 封单薄 → 仍拦（拆下限不等于全放）。"""
    row = _row(cje_yi=0.3, fbt="143000", zbc=3, seal_amt=0.1, lt_mktcap=24.0)
    prof = SG.seal_profile(row)
    assert prof["grade"] == "weak" and prof["seal_ratio_pct"] < SG.THICK_SEAL_EXEMPT_PCT
    v = SG.admit(row, 1.5)
    assert v["keep"] is False and v["source"] == "none"


def test_strong_relaxed_to_strong_floor():
    row = _row(cje_yi=1.0, fbt="092500", seal_amt=2.5, zbc=0)   # 3+3+2+2=10
    v = SG.admit(row, 1.5)
    assert v["keep"] is True and v["grade"] == "strong" and v["exempt"] is True
    assert v["floor_used"] == SG.STRONG_FLOOR_YI


def test_theme_hot_relaxed():
    """同题材 ≥8 家 → 1.0 亿放行（形态一般也行：27 家同门的分歧有人接力）。"""
    row = _row(cje_yi=1.2, fbt="094133", zbc=1, seal_amt=0.5, lt_mktcap=24.0)  # 弱形态
    assert SG.seal_profile(row)["grade"] != "strong"
    assert SG.admit(row, 1.5, theme_mates=2)["keep"] is False
    v = SG.admit(row, 1.5, theme_mates=9)
    assert v["keep"] is True and "热题材" in v["reason"]


def test_theme_hot_requires_sanity_floor_not_zero():
    """热题材也守数据防呆线：0.01 亿（字段异常量级）不放，哪怕同题材 20 家。"""
    assert SG.admit(_row(cje_yi=0.01), 1.5, theme_mates=20)["keep"] is False


def test_theme_hot_now_covers_low_turnover():
    """拆下限后热题材覆盖面下探：1.0 亿以下、形态普通、封单不厚的热题材票也能进。

    原 THEME_HOT_FLOOR_YI=1.0 在 0.5 亿硬下限之上，拆下限后若不同步下调，
    1.0 亿会变成一道**新的变相下限** → 同为防呆线。
    """
    row = _row(cje_yi=0.6, fbt="140000", zbc=3, seal_amt=0.3, lt_mktcap=24.0)
    prof = SG.seal_profile(row)
    assert prof["grade"] == "weak", prof
    assert prof["seal_ratio_pct"] < SG.THICK_SEAL_EXEMPT_PCT
    assert SG.admit(row, 1.5, theme_mates=2)["keep"] is False
    v = SG.admit(row, 1.5, theme_mates=20)
    assert v["keep"] is True and v["source"] == "theme_hot"


def test_leader_floor_still_works():
    v = SG.admit(_row(cje_yi=1.6, fbt="140000"), 5.0, 1.5)
    assert v["keep"] is True and v["reason"] == "高度层豁免"


def test_reject_when_weak_and_below_floor():
    row = _row(cje_yi=1.2, fbt="140000", zbc=3, seal_amt=0.1)
    v = SG.admit(row, 1.5)
    assert v["keep"] is False and "封板强度不足" in v["reason"]


def test_switch_off_restores_pure_floor(monkeypatch):
    monkeypatch.setenv("ENABLE_SEAL_GATE", "0")
    strong = _row(cje_yi=1.0, fbt="092500", seal_amt=2.5, zbc=0)
    v = SG.admit(strong, 1.5)
    assert v["keep"] is False and v["grade"] == "off"


# ── ④ annotate 只写字段 ─────────────────────────────────────────────────────
def test_annotate_does_not_reorder_or_score():
    rows = [_row(fbt="140000", cje_yi=1.0), _row(fbt="092500", seal_amt=2.5, cje_yi=2.0, zbc=0)]
    before_ids = [id(r) for r in rows]
    before_names = [r["name"] for r in rows]
    SG.annotate(rows)
    assert [id(r) for r in rows] == before_ids
    assert [r["name"] for r in rows] == before_names
    assert rows[1]["seal_gate"]["grade"] == "strong"


def test_annotate_survives_non_dict():
    assert SG.annotate([None, {}, _row()])["total"] == 3


def test_summary_line_empty_when_none():
    assert SG.summary_line([]) == ""
    assert SG.summary_line([{"seal_gate": {"grade": "normal"}}]) == ""


def test_summary_line_lists_strong():
    rows = [{"name": "甲", "seal_gate": {"grade": "strong"}},
            {"name": "乙", "seal_gate": {"grade": "strong"}}]
    line = SG.summary_line(rows)
    assert "强封放行 2 只" in line and "甲" in line and "\n" not in line


# ── ⑤ 真实案例：2026-11-02 传媒 9 只首板 ────────────────────────────────────
# 昨日成交额/封板时间/炸板次数为真实值；今日结果见注释。
REAL = [
    # name, boards, fbt, zbc, cje, seal, lt, 今日, 旧门槛(3亿)
    ("浙文影业", 1, "110639", 1, 1.49, 0.45, 40.27, "9:25一字", False),
    ("祥源文旅", 1, "112416", 0, 1.60, 0.54, 37.11, "9:30封", False),
    ("电魂网络", 1, "095031", 1, 2.28, 0.49, 36.61, "9:25一字", False),
    ("大晟文化", 1, "094133", 1, 2.44, 0.81, 24.44, "9:25一字", False),
    ("龙版传媒", 1, "094430", 0, 2.82, 0.67, 57.78, "9:25一字", False),
    ("华阳国际", 1, "112748", 1, 3.61, 0.64, 21.39, "9:25一字", True),
    ("海看股份", 1, "110139", 0, 5.34, 0.79, 28.27, "+15%未封", True),
    ("奥飞娱乐", 1, "103630", 1, 6.98, 0.57, 88.66, "9:30封", True),
    ("文投控股", 1, "093028", 0, 8.74, 0.62, 64.12, "没封", True),
]


def test_real_media_case_regression():
    """新口径：昨日 9 只全进池（旧门槛只进 4 只，而漏掉的正是 4 只一字）。
    ⚠️ 注意：这不是「新口径能预测一字」——它们昨日形态很普通，
    今天能一字是当日新催化；这里守的是**不因缩量而误杀**。"""
    rows = [_row(name=n, boards=b, fbt=f, zbc=z, cje_yi=c, seal_amt=s, lt_mktcap=l)
            for n, b, f, z, c, s, l, _today, _old in REAL]
    verdicts = {r["name"]: SG.admit(r, 1.5, theme_mates=9) for r in rows}
    assert all(v["keep"] for v in verdicts.values()), \
        {k: v["reason"] for k, v in verdicts.items() if not v["keep"]}
    # 旧门槛下被误杀 5 只，其中 4 只今天竞价一字
    old_pass = {n for n, *_rest in REAL if _rest[-1]}
    missed = {n for n in verdicts if n not in old_pass}
    assert len(missed) == 5
    assert {"浙文影业", "电魂网络", "大晟文化", "龙版传媒"} <= missed


def test_real_media_case_needs_hot_theme():
    """若题材家数不足（比如孤票），同样的低成交额仍应被拦 —— 防「一刀切全放」。"""
    row = _row(name="浙文影业", fbt="110639", zbc=1, cje_yi=1.49,
               seal_amt=0.45, lt_mktcap=40.27)
    assert SG.admit(row, 1.5, theme_mates=1)["keep"] is False


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 一字板 vs 换手板（2026-11-04 用户定）
# ══════════════════════════════════════════════════════════════════════════
# 用户原话：「紫竹可以挡掉，但不是因为身位，而是因为它不是一字板，它是换手板。
#   换手板的成交不到 5 亿，这个其实可以不要的……一字板成交额低没问题，
#   但是换手板成交额低其实是很有问题的，容易被资金做局。」

@pytest.mark.parametrize("v,expect", [
    (True, "yizi"), (False, "turnover"),
    (None, "unknown"), ("true", "unknown"), (1, "unknown"),
])
def test_board_style_no_lenient_coercion(v, expect):
    """⛔ 不宽松转换：`"true"` / `1` 一律 unknown（历史文件里这字段出现过 null）。"""
    row = _row()
    row["is_one_word"] = v
    assert SG.board_style(row) == expect


def test_board_style_missing_field_is_unknown():
    row = _row()
    row.pop("is_one_word", None)
    assert SG.board_style(row) == "unknown"


@pytest.mark.parametrize("boards,cje,one_word,base_floor,expect_floor", [
    (2, 2.54, False, 1.5, 1.5),     # 🔴 2026-11-11：换手板**不再**提到 5 亿
    (2, 2.54, True, 1.5, 1.5),      # 一字板 2 板 → 原门槛
    (2, 2.54, None, 1.5, 1.5),      # 缺字段 → 不收紧
    (3, 2.54, False, 5.0, 5.0),     # 调用方传多少就是多少，本闸不重复改
    (1, 0.9, False, 1.5, 1.5),      # 首板换手同样不收紧
])
def test_style_floor(boards, cje, one_word, base_floor, expect_floor):
    """分档闸已关（2026-11-11 用户「5 亿限制全系统清除」）→ 一律返回调用方门槛。"""
    row = _row(boards=boards, cje_yi=cje, is_one_word=one_word)
    assert SG.style_floor(row, base_floor) == expect_floor


def test_style_floor_switch_off_restores_old(monkeypatch):
    """`ENABLE_TURNOVER_BOARD_FLOOR=0` → 回到「一字换手不分」。"""
    monkeypatch.setenv("ENABLE_TURNOVER_BOARD_FLOOR", "0")
    row = _row(boards=2, cje_yi=2.54, is_one_word=False)
    assert SG.style_floor(row, 1.5) == 1.5
    assert SG.admit(row, 1.5)["keep"] is True


def test_zizhu_real_case_now_passes_on_turnover():
    """真实案例 002058 紫竹高科（2026-11-03）：2 板·换手·2.54 亿 → **不再因成交额被挡**。

    🔴 2026-11-11 用户「这个 5 亿的限制 pass 掉，全系统清除」。
    缩量表（shrink=0 / grade=normal）这些**封板质量**事实照旧留痕，
    但成交额不再产生否决 —— 否则青山纸业（3.29 亿·换手 4.6%）这类
    缩量锁筹票会被同一把尺子误杀。
    ⛔ 回归锁：哪天把 5 亿线加回来，本条立刻变红。
    """
    row = _row(code="002058", name="紫竹高科", boards=2, fbt="094848", zbc=0,
               cje_yi=2.54, seal_amt=0.73327439, lt_mktcap=30.2932, is_one_word=False)
    prof = SG.seal_profile(row)
    assert prof["parts"]["shrink"] == 0          # 换手板缩量不给分（质量事实保留）
    assert prof["grade"] == "normal"
    res = SG.admit(row, 1.5, theme_mates=7)
    assert res["keep"] is True
    assert res["style"] == "turnover"
    assert res["floor_used"] == 1.5          # 调用方门槛，闸不再往上抬
    assert "换手板" not in res["reason"]


def test_dongfang_real_case_still_passes():
    """真实案例 002086 东方海洋（2026-11-03）：4 板·7.88 亿 → 成交额达标，不受影响。"""
    row = _row(code="002086", name="东方海洋", boards=4, fbt="092500", zbc=3,
               cje_yi=7.88, seal_amt=2.15780207, lt_mktcap=54.0692, is_one_word=False)
    res = SG.admit(row, 5.0)
    assert res["keep"] is True
    assert res["source"] == "enough"


def test_yizi_small_turnover_still_passes():
    """一字板成交额 0.9 亿 → 不因量小被拦（形态强封放行或原门槛）。"""
    row = _row(boards=2, fbt="092500", zbc=0, cje_yi=0.9,
               seal_amt=1.2, lt_mktcap=20.0, is_one_word=True)
    res = SG.admit(row, 1.5)
    assert res["keep"] is True


def test_turnover_board_high_turnover_passes_at_five_yi():
    """换手板刚好 5 亿 → 达标（边界取 >=）。"""
    row = _row(boards=2, fbt="100000", zbc=1, cje_yi=5.0,
               seal_amt=0.5, lt_mktcap=40.0, is_one_word=False)
    assert SG.admit(row, 1.5)["keep"] is True
