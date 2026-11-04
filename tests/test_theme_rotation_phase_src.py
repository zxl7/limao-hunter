#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相位家数**口径**回归锁（2026-11-03 用户拍板「先按选股宝走」）。

背景（为什么必须有这个文件）：
  上一版 `_phase_of` 的家数 = 「该题材**历史成员**今天有几只涨停」（30 天 code 并集），
  实测会凭空造线 —— 11-02「大消费 19 家」名单里混着阳光照明（照明）、
  松炀资源（造纸）、大千生态（园林）；「人形机器人」在当日快照里根本不存在这个
  板块名，子串匹配硬并到「机器人」，算出一条**当天没有的线**。

  正确口径 = **当日归属到该题材的票 ∩ 当日涨停池**，归属取选股宝 `plates`
  第一个非属性标签项。实测 8/9 个题材与官方 `zt_count` 完全一致（仅短剧差 2）。

本文件锁七条：
  ① 家数只数**当日归属**，不数历史成员（同一天两套数据的差别必须被显式测出来）；
  ② 快照某天缺失 → 该点 `None`，⛔ 绝不当 0（当 0 = 把「没记账」判成「线没了」）；
  ③ 涨停池某天缺失 → 同上，`None`；
  ④ 属性标签（业绩增长/ST股…）不是线 → 直接 `None`；
  ⑤ 名字对不上 → 降级到代码并集，且 `src` 必须标出来（可追溯）；
  ⑥ exact 命中也要继续并子串（否则漏掉当天真有家数的那条）；
  ⑦ 不吃未来数据。

⛔ 全程不触网、不读真实 cache —— 一律 monkeypatch。
运行：python3 -m pytest tests/test_theme_rotation_phase_src.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import theme_rotation as tr  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════════
# 造数据
# ══════════════════════════════════════════════════════════════════════════════
def snap(days: dict) -> dict:
    """{date: {plate: [codes]}} → 归一成 set。"""
    return {
        d: {p: set(cs) for p, cs in plates.items()}
        for d, plates in days.items()
    }


def pool(dates: dict) -> tuple:
    """{date: [codes]} → (days, by_date)，by_date 的 value 是涨停池 {code: {...}}。"""
    days = sorted(dates)
    return days, {d: {c: {"name": f"票{c}"} for c in cs} for d, cs in dates.items()}


@pytest.fixture(autouse=True)
def _clear_caches():
    """两个模块级缓存都会被 monkeypatch 绕过，但保险起见每例清空。"""
    tr._SNAP_CACHE["sig"] = None
    tr._SNAP_CACHE["res"] = None
    tr._TOP5_CODES_CACHE["sig"] = None
    tr._TOP5_CODES_CACHE["res"] = None
    yield


def _patch(monkeypatch, *, snaps, days_by_date, top5=None):
    monkeypatch.setattr(tr, "_snapshot_plates_by_date", lambda: snaps)
    monkeypatch.setattr(tr, "_load_zt_pool_series", lambda n=None: pool(days_by_date))
    monkeypatch.setattr(tr, "_top5_codes_by_theme", lambda: (top5 or {}))


def test_today_codes_really_supplies_unarchived_target_day(monkeypatch):
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    _patch(monkeypatch,
           snaps=snap({"2020-01-01": {"游戏": "ABCDEF"}, today: {"游戏": "ABCDEF"}}),
           days_by_date={"2020-01-01": "ABCDEF"})
    assert tr._plates_seq("游戏", today, today_codes=set("AB"))["seq"] == [6, 2]
    assert tr._plates_seq("游戏", today, today_codes=set("ABC"))["seq"] == [6, 3]
    assert tr._plates_seq("游戏", today, today_codes=None) is None


# ══════════════════════════════════════════════════════════════════════════════
# ① 只数当日归属，不数历史成员
# ══════════════════════════════════════════════════════════════════════════════
def test_counts_today_attribution_not_historical_members(monkeypatch):
    """11-02 快照里短剧只有 A~F 六只；G~J 是「别的题材今天涨停」。

    ⛔ 旧口径会把 G~J 也算进来（它们是历史成员）→ 家数虚高 → 相位漂移。
    正确 = 6。
    """
    _patch(
        monkeypatch,
        snaps=snap({
            "2026-10-30": {"短剧/互动影游": "ABCDEF"},
            "2026-11-02": {"短剧/互动影游": "ABCDEF"},
        }),
        # 涨停池：A~F 是短剧，G~J 属于别的题材（快照里它们不在短剧名下）
        days_by_date={
            "2026-10-30": "ABCDEF",
            "2026-11-02": "ABCDEFGHIJ",
        },
    )
    r = tr._phase_of("短剧/互动影游", set(), "2026-11-02")
    assert r is not None
    assert r["cur"] == 6, "家数必须是「当日归属 ∩ 涨停池」，不是涨停池里所有票"
    assert r["series"] == [6, 6]
    assert r["src"] == "snapshot_plates"


def test_historical_member_would_have_inflated(monkeypatch):
    """把 G~J 塞进**历史**快照的短剧名下，今日家数不应变大。

    这条是上一版 bug 的最小复现：历史并集 → 今日把 G~J 也数进去。
    """
    _patch(
        monkeypatch,
        snaps=snap({
            "2026-10-30": {"短剧/互动影游": "ABCDEFGHIJ"},   # 历史成员含 G~J
            "2026-11-02": {"短剧/互动影游": "ABCDEF"},        # 今日归属只剩 A~F
        }),
        days_by_date={
            "2026-10-30": "ABCDEF",
            "2026-11-02": "ABCDEFGHIJ",
        },
    )
    r = tr._phase_of("短剧/互动影游", set(), "2026-11-02")
    assert r["cur"] == 6
    assert r["series"] == [6, 6], "历史那天的 G~J 不能渗进今天"


# ══════════════════════════════════════════════════════════════════════════════
# ②③ 缺失语义：没记账 ≠ 0
# ══════════════════════════════════════════════════════════════════════════════
def test_missing_snapshot_day_is_none_not_zero(monkeypatch):
    """10-30 没有题材快照 → 该点必须是 None，⛔ 不是 0。

    若写 0，`judge` 会读成「那天一条线都没有」→ 把启动判成分歧/退潮。
    """
    _patch(
        monkeypatch,
        snaps=snap({"2026-11-02": {"机器人": "ABCDEF"}}),   # 只有今天有快照
        days_by_date={"2026-10-30": "ABCDEFGH", "2026-11-02": "ABCDEFGH"},
    )
    r = tr._phase_of("机器人", set(), "2026-11-02")
    assert r is not None
    assert r["series"][0] is None
    assert r["series"][1] == 6
    assert 0 not in r["series"][:1], "快照缺失不能写成 0"


def test_missing_pool_day_drops_from_series_not_zero(monkeypatch):
    """涨停池某天没归档 → 那天**不进序列**（窗口变短），⛔ 绝不补一个 0。

    ⚠️ 与快照缺失不同：快照缺失会占位写 `None`（保留「第几天」的时间轴），
    涨停池缺失则整日消失 —— 因为 `_load_zt_pool_series` 的 `days` 就是池的日期集合。
    两条都要保证同一件事：**不许把「没记账」记成「0 家」**。
    """
    _patch(
        monkeypatch,
        snaps=snap({
            "2026-10-30": {"机器人": "ABCDEF"},
            "2026-11-02": {"机器人": "ABCDEF"},
        }),
        days_by_date={"2026-11-02": "ABCDEF"},              # 缺 10-30 的涨停池
    )
    r = tr._phase_of("机器人", set(), "2026-11-02")
    assert r["series"] == [6], "缺池那天不能补 0，也不能凭空占位"
    assert 0 not in r["series"]


# ══════════════════════════════════════════════════════════════════════════════
# ④ 属性标签不是线
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("nm", ["业绩增长", "ST股", "资产重组", "其他"])
def test_attr_plate_is_never_a_phase(monkeypatch, nm):
    """「业绩增长」横跨全市场，把它的家数当板块会得到一个假主线。"""
    _patch(
        monkeypatch,
        snaps=snap({
            "2026-10-30": {nm: "ABCDEFG"},
            "2026-11-02": {nm: "ABCDEFG"},
        }),
        days_by_date={"2026-10-30": "ABCDEFG", "2026-11-02": "ABCDEFG"},
    )
    assert tr._phase_of(nm, set(), "2026-11-02") is None


# ══════════════════════════════════════════════════════════════════════════════
# ⑤ 名字对不上 → 降级且必须能看出来
# ══════════════════════════════════════════════════════════════════════════════
def test_fallback_marks_src_as_pool_codes(monkeypatch):
    """快照里没有这条线（如 eruption 叫「AI应用」而快照只有「AI大模型/智能体」）
    → 降级到代码并集，`src` 必须写 `pool_codes`，否则两套数打架时无从查起。
    """
    _patch(
        monkeypatch,
        snaps=snap({"2026-11-02": {"AI大模型/智能体": "ABCDEF"}}),
        days_by_date={"2026-10-30": "XYZWQ", "2026-11-02": "XYZWQP"},
        top5={"AI应用": {"X", "Y", "Z", "W", "Q", "P"}},
    )
    r = tr._phase_of("AI应用", set(), "2026-11-02")
    assert r is not None
    assert r["src"] == "pool_codes"
    assert r["cur"] == 6


def test_plates_primary_never_mixed_with_codes(monkeypatch):
    """主口径判得出时，⛔ 不得掺入 `theme_codes`（按天切换口径会造出假拐点）。"""
    _patch(
        monkeypatch,
        snaps=snap({
            "2026-10-30": {"机器人": "ABCDEF"},
            "2026-11-02": {"机器人": "ABCDEF"},
        }),
        days_by_date={"2026-10-30": "ABCDEFXXXX", "2026-11-02": "ABCDEFXXXX"},
    )
    # 传入 4 只额外成分股，若被掺入家数会变 10
    r = tr._phase_of("机器人", {"X", "X2", "X3", "X4"}, "2026-11-02")
    assert r["src"] == "snapshot_plates"
    assert r["cur"] == 6


# ══════════════════════════════════════════════════════════════════════════════
# ⑥ 名字匹配：exact 也要继续并
# ══════════════════════════════════════════════════════════════════════════════
def test_plates_match_exact_also_unions_substring():
    """快照里同时有「机器人」和「人形机器人」时，两个都要拿。

    只取 exact 会漏掉当天真有家数那条（11-02 实测：机器人 13 家 vs 人形机器人 0 家）。
    """
    hits = tr._plates_match("人形机器人", ["机器人", "人形机器人"])
    assert "人形机器人" in hits
    assert "机器人" in hits, "子串匹配必须同时命中，否则会漏掉真正有家数的板块"


def test_plates_match_does_not_cross_siblings():
    """「人形机器人」≠「工业机器人」：两条是兄弟不是父子，⛔ 不许互串。

    真的并错会把两条线合成一条，家数翻倍 → 相位直接判错。
    """
    assert tr._plates_match("人形机器人", ["工业机器人"]) == set()
    assert tr._plates_match("工业机器人", ["人形机器人"]) == set()


def test_plates_match_short_name_guard():
    """短名 <2 字不参与子串匹配，避免泛词四处误撞。"""
    assert tr._plates_match("车", ["汽车", "机器人"]) == set()


def test_plates_match_records_plates_for_audit(monkeypatch):
    """落盘的 `plates` 字段 = 实际匹配到的选股宝板块名，便于人工复核。"""
    _patch(
        monkeypatch,
        snaps=snap({
            "2026-10-30": {"PCB板": "ABCDEF"},
            "2026-11-02": {"PCB板": "ABCDEF"},
        }),
        days_by_date={"2026-10-30": "ABCDEF", "2026-11-02": "ABCDEF"},
    )
    r = tr._phase_of("PCB", set(), "2026-11-02")
    assert r["plates"] == ["PCB板"]


# ══════════════════════════════════════════════════════════════════════════════
# ⑦ 不吃未来数据
# ══════════════════════════════════════════════════════════════════════════════
def test_no_future_data(monkeypatch):
    """涨停池里有 11-03，但主序列末日是 11-02 → 序列只能到 11-02。"""
    _patch(
        monkeypatch,
        snaps=snap({
            "2026-10-30": {"机器人": "ABCDEF"},
            "2026-11-02": {"机器人": "ABCDEF"},
            "2026-11-03": {"机器人": "ABCDEFGHIJ"},
        }),
        days_by_date={
            "2026-10-30": "ABCDEF",
            "2026-11-02": "ABCDEF",
            "2026-11-03": "ABCDEFGHIJ",
        },
    )
    r = tr._phase_of("机器人", set(), "2026-11-02")
    assert r["series"] == [6, 6], "11-03 是未来，不许进序列"


# ══════════════════════════════════════════════════════════════════════════════
# 覆盖度闸
# ══════════════════════════════════════════════════════════════════════════════
def test_too_few_theme_codes_returns_none(monkeypatch):
    """题材在快照里只有 3 只票 → 家数必然低估，宁可空着不给假退潮。"""
    _patch(
        monkeypatch,
        snaps=snap({
            "2026-10-30": {"小题材": "ABC"},
            "2026-11-02": {"小题材": "ABC"},
        }),
        days_by_date={"2026-10-30": "ABC", "2026-11-02": "ABC"},
    )
    assert tr._phase_of("小题材", set(), "2026-11-02") is None


def test_no_snapshot_dir_falls_back(monkeypatch):
    """整个快照目录都没有 → 不崩，走兜底。"""
    _patch(
        monkeypatch,
        snaps={},
        days_by_date={"2026-11-02": "XYZWQP"},
        top5={"机器人": {"X", "Y", "Z", "W", "Q", "P"}},
    )
    r = tr._phase_of("机器人", set(), "2026-11-02")
    assert r is not None
    assert r["src"] == "pool_codes"


# ══════════════════════════════════════════════════════════════════════════════
# ⭐ 当日无快照 → 不许降级（11-03 实跑踩到的真坑）
# ══════════════════════════════════════════════════════════════════════════════
def test_no_snapshot_today_must_return_none_not_fallback(monkeypatch):
    """快照机制在工作、但**今天**没落盘 → 必须返回 None，⛔ 不许拿兜底冒充今天。

    坑的现场：11-03 当天 `theme_snapshot` 还没生成（eod 才落），代码降级到兜底，
    「锂电池」算出 7 家（主口径只有 3 家）。等 eod 落盘后数变回 3，相位从
    「启动」跳走 —— 用户会以为这条线变天了。序列内部必须同源。
    """
    _patch(
        monkeypatch,
        snaps=snap({"2026-10-30": {"机器人": "ABCDEF"}}),      # 只有昨天有快照
        days_by_date={"2026-10-30": "ABCDEF", "2026-11-02": "ABCDEFGHIJ"},
        top5={"机器人": {"A", "B", "C", "D", "E", "F", "G"}},   # 兜底明明判得出
    )
    assert tr._phase_of("机器人", set(), "2026-11-02") is None


def test_today_snapshot_present_uses_it(monkeypatch):
    """对照：今天有快照就走主口径（证明上一条是「缺快照」拦的，不是永远拦）。"""
    _patch(
        monkeypatch,
        snaps=snap({
            "2026-10-30": {"机器人": "ABCDEF"},
            "2026-11-02": {"机器人": "ABCDEF"},
        }),
        days_by_date={"2026-10-30": "ABCDEF", "2026-11-02": "ABCDEFGHIJ"},
        top5={"机器人": {"A", "B", "C", "D", "E", "F", "G"}},
    )
    r = tr._phase_of("机器人", set(), "2026-11-02")
    assert r is not None
    assert r["src"] == "snapshot_plates"
    assert r["cur"] == 6
