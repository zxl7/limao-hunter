"""板块打分（`core/theme_score.py`）的回归锁。

## 这个模块回答什么

用户 2026-11-10：「短线接力的时候，每一个个股其实也是有板块的，这个板块进行一个打分挺好」

→ 把散在 `row_tier`（身位）/ `sector_support`（红开）/ `theme_linkage`（联动）/
`theme_phase`（阶段）的板块信息，合成一个 0~100 的数。

## 本文件锁五条设计决定（每条都是踩过坑才定的）

1. **打分对象是板块，不是个股**：⛔ 不含「卡位/身位」——
   第一版把卡位放进四维，分数立刻退化成「板数排名 + 同线家数」的复述，零新信息。
2. **缺维度按已给维度折算**：⛔ 缺失既不按 0 算（系统性偏低）也不按满分算（虚高）。
3. **只有 1 维可评 → 不给总分**：⛔ 单维折算必然出 100 或 0，那是假高分。
4. **`total` 可能是 `None`**：调用方必须当「没有这个数」，⛔ 不许当 0 分。
5. **不参与任何裁决**：只写 `views[code].theme_score`，⛔ 不改 view / 仓位 / 名额。

## 以及一条「已验证」的事实（写进测试防遗忘）

`scripts/research_theme_score_ab.py` 35 日回放：**分档不单调，方向甚至相反**
（全池 ≥70 强 −0.57% / 30~49 弱 +0.24%；控板数分层后各档方向也不一致）。
⇒ 这个分数**目前没有预测力**，⛔ 不许进权重。
它现在的定位是**描述性标签**（「这条线多热」），不是信号。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ts():
    """加载 `core/theme_score.py`（纯函数模块，不依赖 pool_call）。"""
    spec = importlib.util.spec_from_file_location(
        "theme_score", ROOT / "wealth_code" / "core" / "theme_score.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── ① 四维齐全：等权合成 ────────────────────────────────────────────────────
def test_all_four_dims_scored(ts):
    sc = ts.score(theme_zt_count=3, theme_max_boards=4, theme_depth=3,
                  red_open=2, mates_quoted=3, theme_name="绿电")
    assert sc["n_dims"] == 4
    assert sc["theme_name"] == "绿电"
    # scale(3)=20 height(4)=21 depth(3)=25 support(2/3≈0.667)=25
    assert sc["dims"]["scale"]["score"] == 20.0
    assert sc["dims"]["height"]["score"] == 21.0
    assert sc["dims"]["depth"]["score"] == 25.0
    assert sc["dims"]["support"]["score"] == 25.0
    assert sc["total"] == pytest.approx((20 + 21 + 25 + 25) / 100 * 100, abs=0.05)
    assert sc["grade"] == "强"


# ── ② 缺维度按已给维度折算（不是按 100 折）────────────────────────────────
def test_missing_dim_normalizes_by_given_dims(ts):
    """盘后没有竞价数据 → 3 维。分母是 75 不是 100。"""
    sc = ts.score(theme_zt_count=3, theme_max_boards=4, theme_depth=3)
    assert sc["n_dims"] == 3
    assert sc["dims"]["support"]["score"] is None
    # (20+21+25)/75*100 = 88.0
    assert sc["total"] == pytest.approx(88.0, abs=0.1)
    assert "缺 support" in sc["why"]


def test_missing_dim_is_not_zero_and_not_full(ts):
    """⛔ 缺失维度既不能当 0 分（会把盘后分压低）也不能当满分（虚高）。"""
    full = ts.score(theme_zt_count=3, theme_max_boards=4, theme_depth=3,
                    red_open=0, mates_quoted=3)      # support 给最低 2 分
    partial = ts.score(theme_zt_count=3, theme_max_boards=4, theme_depth=3)
    # 缺 support 时的折算分应高于「support=0 红开」的实算分
    assert partial["total"] > full["total"]


# ── ③ 单维可评 → 不出总分（假高分防线）──────────────────────────────────
def test_single_dim_yields_no_total(ts):
    """⛔ 只有一维可评时不能出总分：25/25 折算成 100 分是假的。

    2026-11-10 实测踩过：只剩「卡位」一维时，全池 25 只票清一色「强 100 分」。
    """
    sc = ts.score(theme_zt_count=1)
    assert sc["total"] is None
    assert sc["grade"] == "不可评"
    assert sc["n_dims"] == 1
    assert "一维" in sc["why"]


def test_two_dims_can_produce_total(ts):
    sc = ts.score(theme_zt_count=2, theme_max_boards=2)
    assert sc["n_dims"] == 2
    assert sc["total"] is not None


# ── ④ 全缺 → 不可评，且不能当 0 分 ─────────────────────────────────────────
def test_all_missing_is_not_a_zero_score(ts):
    sc = ts.score()
    assert sc["total"] is None
    assert sc["grade"] == "不可评"
    assert sc["n_dims"] == 0


def test_brief_says_unscorable_not_zero(ts):
    """卡面文案：缺分写「不可评」，⛔ 不许写「0 分」（读者会当成极差）。"""
    assert ts.brief(None) == "板块不可评"
    assert ts.brief({"total": None}) == "板块不可评"
    assert "0/100" not in ts.brief({"total": None})
    assert ts.brief({"total": 88.0, "grade": "强"}) == "板块强 88/100"


# ── ⑤ 阶梯边界（改阈值必须同步这条）───────────────────────────────────────
@pytest.mark.parametrize("n,expect", [(0, 0.0), (1, 6.0), (2, 13.0), (3, 20.0), (4, 25.0), (9, 25.0)])
def test_scale_steps(ts, n, expect):
    assert ts.score(theme_zt_count=n, theme_max_boards=1)["dims"]["scale"]["score"] == expect


@pytest.mark.parametrize("b,expect", [(1, 4.0), (2, 10.0), (3, 16.0), (4, 21.0), (5, 25.0), (9, 25.0)])
def test_height_steps(ts, b, expect):
    assert ts.score(theme_zt_count=1, theme_max_boards=b)["dims"]["height"]["score"] == expect


@pytest.mark.parametrize("d,expect", [(0, 4.0), (1, 11.0), (2, 18.0), (3, 25.0), (6, 25.0)])
def test_depth_steps(ts, d, expect):
    assert ts.score(theme_zt_count=1, theme_max_boards=1, theme_depth=d)["dims"]["depth"]["score"] == expect


@pytest.mark.parametrize("red,total,expect", [
    (0, 3, 2.0), (1, 3, 13.0), (1, 2, 20.0), (2, 3, 25.0), (3, 3, 25.0),
])
def test_support_steps(ts, red, total, expect):
    sc = ts.score(theme_zt_count=1, theme_max_boards=1, theme_depth=0,
                  red_open=red, mates_quoted=total)
    assert sc["dims"]["support"]["score"] == expect


def test_support_zero_quoted_is_missing_not_zero(ts):
    """⛔ 没有一家有报价 ≠ 零家红开。分母为 0 时整维记缺失，不猜成 0%。"""
    sc = ts.score(theme_zt_count=1, theme_max_boards=1, theme_depth=0,
                  red_open=0, mates_quoted=0)
    assert sc["dims"]["support"]["score"] is None


# ── ⑥ 设计决定：不含个股属性 ───────────────────────────────────────────────
def test_score_has_no_slot_dimension(ts):
    """⛔ 板块分只描述线，不掺「这只票排第几」。

    第一版把卡位放进四维，分数立刻退化成板数排名的复写 —— 那是零新信息。
    身位已经有 `line_tier` / ⑪b 在管，不在这里重复。
    """
    sc = ts.score(theme_zt_count=3, theme_max_boards=4, theme_depth=3)
    assert set(sc["dims"]) == {"scale", "height", "depth", "support"}
    assert "slot" not in sc["dims"]


def test_same_line_same_score(ts):
    """同一线的票得分相同 —— 这是正确行为（问的是「这条线值不值得买票」）。"""
    kw = dict(theme_zt_count=3, theme_max_boards=3, theme_depth=2)
    a = ts.score(**kw, theme_name="绿电")
    b = ts.score(**kw, theme_name="绿电")
    assert a["total"] == b["total"]


# ── ⑦ 边界与类型 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", ["3", None, 3.5, [], {}])
def test_non_int_count_is_treated_as_missing(ts, bad):
    """入参类型不对 → 该维记缺失，⛔ 不抛（打分故障不许阻断裁决）。"""
    sc = ts.score(theme_zt_count=bad, theme_max_boards=2, theme_depth=1)
    dim = sc["dims"]["scale"]
    assert dim["score"] is None or isinstance(dim["score"], float)


def test_total_is_bounded(ts):
    lo = ts.score(theme_zt_count=1, theme_max_boards=1, theme_depth=0,
                  red_open=0, mates_quoted=3)
    hi = ts.score(theme_zt_count=6, theme_max_boards=6, theme_depth=5,
                  red_open=3, mates_quoted=3)
    assert 0.0 <= lo["total"] <= 100.0
    assert hi["total"] == 100.0
    assert lo["grade"] in ("无", "弱")
    assert hi["grade"] == "强"


# ── ⑧ 板块强度分（strength）：唯一通过验证的一维 ────────────────────────────
def test_strength_steps(ts):
    """阶梯：n≥10→100 / 6~9→85 / 3~5→50 / 1~2→20。

    🔬 依据（`research_theme_momentum_ab.py` 38 日 · 去日期效应）：
    n1~2 超额 −0.88% / n3~5 −0.65% / n≥6 **+0.69%** —— 唯一单调递增的一维。
    """
    assert ts.strength(1)["total"] == 20.0
    assert ts.strength(2)["total"] == 20.0
    assert ts.strength(3)["total"] == 50.0
    assert ts.strength(5)["total"] == 50.0
    assert ts.strength(6)["total"] == 85.0
    assert ts.strength(9)["total"] == 85.0
    assert ts.strength(10)["total"] == 100.0
    assert ts.strength(40)["total"] == 100.0


def test_strength_missing_is_not_zero(ts):
    """⛔ 缺家数 ≠ 0 家。0 家会被算成「弱 20 分」，那是编造。"""
    for bad in (None, {}, [], "abc"):
        r = ts.strength(bad)
        assert r["total"] is None
        assert r["grade"] == "不可评"
        assert r["n"] is None


def test_strength_monotonic_by_construction(ts):
    """分数必须随家数单调不减 —— 这是它唯一的价值（上一版四维热度就坏在这）。"""
    scores = [ts.strength(n)["total"] for n in range(1, 31)]
    assert all(a <= b for a, b in zip(scores, scores[1:]))


def test_strength_grade_aligns_with_total(ts):
    assert ts.strength(20)["grade"] == "强"
    assert ts.strength(4)["grade"] == "中"
    assert ts.strength(1)["grade"] == "弱"


def test_strength_why_mentions_n(ts):
    """why 里要写出家数与超额参考，否则卡面/日志看不出依据。"""
    w = ts.strength(8)["why"]
    assert "8" in w and "超额" in w
