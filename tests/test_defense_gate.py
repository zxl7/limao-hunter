# -*- coding: utf-8 -*-
"""买入前防守闸门回归锁 —— 2026-10-08 大幅回撤经验固化（core/defense_gate.py）。

锁定（数据全部取自 2026-09-30 候选 / 板块资金归档 / 涨停历史 + 2026-10-08 竞价，**非编造**）：
  ① 派发特征三条（换手 >15% ∧ 封单/流通 <2% ∧ 10:00 后封板）→ 硬闸，不占名额
  ② 方向资金失血（所属方向昨日净流出 ≥30 亿）→ 硬闸
  ③ 位置高 + 二波反抽（断板 ≥3 个交易日后重启 ∧ 上一波最高板 ≥3）→ 硬闸
  ④ 环境双腿判据 → 名额降档（≥2 条）/ 当日不主动买（≥3 条）
  ⑤ ⛔ 缺数据一律**不裁决**（诚实降级，不编造、不误伤）
  ⑥ ⛔ 开关可逆：POOL_CALL_DEFENSE_GATE=False → 逐字回退 10-08 前行为；四个子开关可逐个关
  ⑦ ⛔ 诚实边界：10-08 当天环境闸门**不触发**（昨收 4.5 分修复、今开 56.9% 红开），
     该用例显式锁死这一事实 —— 防后人把它当成「这几条本来就能拦住那天」的错觉。
  ⑧ ⛔ 「断板回封是加分项」锁：断 1~2 天的快速 N 字**不得**被位置闸拦（只有断好多天 +
     已经涨了一大截才是接盘）。
  ⑨ 🆕 2026-10-12 收紧（用户「如果是指标问题，收一收」·国芳集团漏网）：
     「封单薄 + 封得晚」两条 ＋ **位置已经很高**（与 ④ 位置闸同口径：dev10 ≥45 或 dev30 ≥50）
     → 硬闸（旧的「位置高」只认 dev_10d，国芳 10 日仅 3.9%、30 日已 +97.8% → 判「不高」漏网）；
     ⛔ 但「换手大 + 封单薄」那条**不得**同步收紧（含闽东电力等大赢家，实测新增 41 条拦截）。

全隔离：tmp_path / 显式传参，不读真实 cache、不落盘、不触网。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WC = ROOT / "wealth_code"
for _p in (str(WC), str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import defense_gate as dg  # noqa: E402
from core import pool_call  # noqa: E402


@pytest.fixture(autouse=True)
def _legacy_position_gate(monkeypatch):
    """身位闸 11-06 已改「同题材分母 + 只降仓不剔除」，本文件锁的是 10-08 防守腿场景，
    显式回退旧语义，保证这几条断言里**只有防守腿一个变量**在动。"""
    monkeypatch.setenv("POOL_CALL_POSITION_GAP_SCOPE", "market")
    monkeypatch.setattr(pool_call, "POOL_CALL_POSITION_GAP_HARD", True)


# ⚠️ 既有缺口（2026-11-06 发现，与身位改动**无关**）：本文件多条断言锁的是
#    「10-08/11-04 生产行为」（名额按 stance 截断、量能 0.7 单线否决），
#    但没挂 `legacy_pool_call_rules` → 11-05「不限制只数」上线后这两条一直红。
#    这里补挂，让历史场景断言重新成立（已用「中和身位」复现确认过与本次改动无关）。
pytestmark = pytest.mark.usefixtures("legacy_pool_call_rules")


# ── 夹具：2026-09-30 候选真实字段（code/hs/seal_amt/lt_mktcap/fbt/dev_10d_pct）────────
def _shares(hs, seal_amt, lt_mktcap, fbt, dev10, theme, code, name, boards,
            exp=(0.3, 2.3), need=0.45, core_theme=None):
    """按真实数字构造候选（字段名与 `candidates-*.json` 一致）。"""
    out = {
        "code": code, "name": name, "boards": boards, "theme_name": theme,
        "sector": theme, "hs": hs, "seal_amt": seal_amt, "lt_mktcap": lt_mktcap,
        "fbt": fbt, "dev_10d_pct": dev10,
        "expectation": {"expected_range": list(exp), "auction_amount_min_yi": need},
    }
    if core_theme:
        out["core_theme"] = core_theme
    return out


# 10-08 实际买到的两只（都收大阴）
XIANGYANG = _shares(19.65, 0.671, 59.24, "100300", 51.1, "机器人", "000678", "襄阳轴承", 4,
                    need=0.48, core_theme="机器人")
NANHUA = _shares(22.51, 0.6163, 40.88, "102242", 50.3, "医药", "000504", "南华生物", 1,
                 need=0.39, core_theme="医药")
# 同日真正走出来的票（对照）—— 龙蟠 core_theme=锂电池（当日真主线方向）
LONGPAN = _shares(6.57, 0.4177, 107.19, "101123", 6.3, "新能源", "603906", "龙蟠科技", 1,
                  exp=(0.1, 2.1), need=0.31, core_theme="锂电池")
SHIDAI = _shares(2.61, 0.55, 11.09, "092500", 12.0, "新能源", "600241", "时代万恒", 3,
                 core_theme="锂电池")
ZIZHU = _shares(1.77, 0.30, 12.61, "092500", 8.0, "新能源", "002058", "紫竹高科", 2,
                core_theme="电池技术")
# 泛名题材（如「其他」）—— 用来验证过泛保护
WUFANG = _shares(17.1, 0.5, 33.0, "094921", 23.2, "其他", "002962", "五方光电", 1,
                 core_theme="其他")

# 2026-10-08 真实竞价（`auction_quotes-2026-10-08.json`）
QUOTES_1008 = {
    "000678": {"prev_close": 12.89, "auction_price": 13.0, "auction_amount_yi": 0.78},
    "000504": {"prev_close": 12.42, "auction_price": 12.47, "auction_amount_yi": 0.45},
    "603906": {"prev_close": 19.04, "auction_price": 19.3, "auction_amount_yi": 0.32},
}
# 2026-09-30 盘后环境快照（`candidates-2026-09-30.json`.environment）
ENV_0930 = {"sentiment_score": 4.5, "sentiment_label": "修复", "promote_rate": 21.1,
            "nuke_count": 8, "break_count": 0, "broken_rate": 15.9, "avg_pct": 1.31,
            "regime": "neutral"}
# 2026-10-08 09:25 昨涨停池开盘读数（`pool_call-2026-10-08.json`.meta.market_open）
MO_1008 = {"enabled": True, "pool_size": 52, "quoted": 51, "coverage": 0.9808,
           "red_open_count": 29, "red_open_ratio": 0.5686, "leader_count": 12,
           "leader_red_open_count": 8, "leader_red_open_ratio": 0.6667, "ready": False}
# 2026-09-30 板块资金（`sector_flow_history/sector_flow_2026-09-30.json` 节选）
FLOW_0930 = {
    "机器人概念": {"pct": -0.65, "flow_yi": -44.31},
    "机器人执行器": {"pct": -0.54, "flow_yi": 2.06},
    "机器视觉": {"pct": -1.03, "flow_yi": 2.54},
    "医药生物": {"pct": 2.03, "flow_yi": 61.91},
    "医疗服务": {"pct": 3.09, "flow_yi": 26.03},
    "医药商业": {"pct": 1.15, "flow_yi": -1.07},
    "医药医疗风格": {"pct": 2.92, "flow_yi": 37.9},
    "锂电池": {"pct": 0.34, "flow_yi": 1.8},
    "锂电池概念": {"pct": -0.5, "flow_yi": -14.68},
}

# ── 2026-09-21~09-30 的真实交易日（`cache/zt_history/` 里有涨停票的日）──────────────
# 2026-09-26/27 是周末；**2026-09-25 是中秋节（周五）休市**（2026-10-09 补录 closed_dates）；
# 10-01 起是国庆长假（10-01/02/05 的 zt 文件是空占位）。
ZT_DAYS = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24",
           "2026-09-28", "2026-09-29", "2026-09-30"]
# 逐票涨停序列（`cache/zt_history/zt_<日>.json` 实录·截到 09-30）
ZT_SERIES = {
    # 南华生物：09-21(3板) → 09-22(4板) → **断板 4 个交易日** → 09-30 重新涨停（第二波）
    "000504": [("2026-09-21", 3), ("2026-09-22", 4), ("2026-09-30", 1)],
    # 襄阳轴承：09-24 → 09-28 → 09-30 **连续**涨停（非二波）
    "000678": [("2026-09-24", 1), ("2026-09-28", 2),
               ("2026-09-29", 3), ("2026-09-30", 4)],
    # 龙蟠科技：只在 09-30 出现（序列不足 2 条 → 位置闸不裁决）
    "603906": [("2026-09-30", 1)],
}
# 快速 N 字（09-24 断板 → 09-30 回封，中间只空 2 个交易日）+ 上一波 3 板 → ⛔ 不得拦
QUICK_NZI_SERIES = {"000678": [("2026-09-24", 3), ("2026-09-30", 1)]}
QUICK_NZI_DAYS = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24",
                  "2026-09-28", "2026-09-29", "2026-09-30"]


# ── ① 派发特征 ───────────────────────────────────────────────────────────────
def test_distribution_blocks_the_two_real_losers():
    """10-08 两只亏损票：换手/封单/封板时间三条全中 → 硬闸。"""
    for c in (XIANGYANG, NANHUA):
        v = dg.distribution_verdict(c)
        assert v["level"] == "block", (c["name"], v)
        assert v["hit_count"] == 3
        assert "倒货" in v["note"]
    # 封装读数本身与归因结论一致（1.13% / 1.52%）
    assert dg.seal_quality(XIANGYANG)["seal_ratio_pct"] == 1.13
    assert dg.seal_quality(NANHUA)["seal_ratio_pct"] == 1.51
    assert dg.seal_quality(XIANGYANG)["fbt_hm"] == 1003
    assert dg.seal_quality(NANHUA)["fbt_hm"] == 1022


def test_distribution_does_not_block_the_real_winners():
    """同日走出来的票：0~2 条命中 → 不拦（零误伤是这条闸门能上线的前提）。"""
    assert dg.distribution_verdict(SHIDAI)["level"] == "none"
    assert dg.distribution_verdict(ZIZHU)["level"] == "none"
    assert dg.distribution_verdict(LONGPAN)["level"] == "none"
    # 龙蟠确实命中 2 条（换手不高、封单薄、封得晚），但未达 3 条门槛
    assert dg.distribution_verdict(LONGPAN)["hit_count"] == 2


def test_distribution_warn_needs_two_hits_plus_high_position():
    """两条 + 位置已高 → 只留痕警示；位置不高则连警示都不给。

    ⛔ 这里同时是 **2026-10-12 收紧的边界锁**：该票命中的是「换手大 + 封单薄」
    （fbt 09:31 早封，**不含「封得晚」**）→ 不得被 ③b 组合升级成 block。
    「换手大 + 封单薄」那条 55 个可成交样本里含闽东电力 +7.32% / 传艺科技 +10.92%
    等大赢家，一并收紧会砍掉真龙头（实测新增 41 条拦截，已被数据否证）。
    """
    high = {"hs": 17.0, "seal_amt": 0.5, "lt_mktcap": 80.0, "fbt": "093100",
            "dev_10d_pct": 48.0}
    low = dict(high, dev_10d_pct=5.0)
    v_high = dg.distribution_verdict(high)
    assert v_high["level"] == "warn" and v_high["kind"] == "warn"
    assert dg.distribution_verdict(low)["level"] == "none"


def test_distribution_missing_fields_never_block():
    """字段缺失 → 不裁决（诚实降级，不编造、不误伤）。"""
    assert dg.distribution_verdict({})["level"] == "none"
    assert dg.distribution_verdict(None)["level"] == "none"
    # 只有换手一项 → 一条命中，不够
    assert dg.distribution_verdict({"hs": 30.0})["level"] == "none"


# ── ③b 派发·组合升级（2026-10-12 收紧「封单薄 + 封得晚 + 位置高」）──────────────
# 真实字段：`candidates-2026-10-09.json` 国芳集团（次日 10-12 竞价 +2.41% → 收盘 −12.09%）
GUOFANG = {"code": "601086", "name": "国芳集团", "hs": 10.563,
           "seal_amt": 0.59912388, "lt_mktcap": 99.5004, "fbt": "101607",
           "dev_10d_pct": 3.9, "dev_30d_pct": 97.8}
# 龙蟠科技 09-30 真实字段：同样是「封单薄 + 封得晚」，但位置不高 → 当日涨停 +9.98%
LONGPAN_THIN_LATE = {"code": "603906", "name": "龙蟠科技", "hs": 6.57,
                     "seal_amt": 0.4177, "lt_mktcap": 107.19, "fbt": "101123",
                     "dev_10d_pct": 6.3, "dev_30d_pct": -1.2}


def test_thin_late_high_position_blocks_guofang():
    """国芳集团（10-09 真实字段）：封单 0.60% + 10:16 才封板 + 30 日多涨 97.8% → 硬闸。

    ⛔ 旧口径漏网根因：位置只认 `dev_10d_pct`（3.9 → 判「不高」）→ 连 warn 都没有。
    """
    v = dg.distribution_verdict(GUOFANG)
    assert v["level"] == "block", v
    assert v["kind"] == "thin_late_high"
    assert v["hit_count"] == 2                       # 换手 10.6% 不中，只有两条
    assert dg.seal_quality(GUOFANG)["seal_ratio_pct"] == 0.6
    assert "抬轿子" in v["note"] and "30 天" in v["note"]


def test_thin_late_position_leg_says_which_leg_triggered():
    """文案必须说**真正触发**的那条腿：30 日的账不能说成 10 日（国芳 10 日只涨 3.9%）。"""
    v30 = dg.distribution_verdict(GUOFANG)
    assert "30 天已经涨了 98%" in v30["note"] and "10 天" not in v30["note"]
    v10 = dg.distribution_verdict(dict(LONGPAN_THIN_LATE, dev_10d_pct=48.0))
    assert "10 天已经涨了 48%" in v10["note"]


def test_thin_late_needs_high_position():
    """同是「封单薄 + 封得晚」：位置不高不拦（龙蟠），只把位置抬到高位才拦。"""
    assert dg.distribution_verdict(LONGPAN_THIN_LATE)["level"] == "none"
    v = dg.distribution_verdict(dict(LONGPAN_THIN_LATE, dev_10d_pct=48.0))
    assert v["level"] == "block" and v["kind"] == "thin_late_high"


def test_thin_late_boundaries_and_honest_degradation():
    """边界：封单数据缺失 → 不拦；含「换手大」→ 不属 ③b（走 warn / triple）。"""
    # 只有「封得晚 + 位置高」，封单/换手都没数据 → 不裁决
    assert dg.distribution_verdict({"fbt": "101500", "dev_30d_pct": 120.0})["level"] == "none"
    # 「换手大 + 封单薄」+ 高位（封得早）→ 仍只 warn，⛔ 不升级为 block
    assert dg.distribution_verdict(
        {"hs": 20.0, "seal_amt": 0.5, "lt_mktcap": 100.0, "fbt": "093100",
         "dev_10d_pct": 48.0})["level"] == "warn"
    # 三条全中 → kind=triple（优先级高于 ③b）
    assert dg.distribution_verdict(
        {"hs": 20.0, "seal_amt": 0.5, "lt_mktcap": 100.0, "fbt": "101500",
         "dev_30d_pct": 120.0})["kind"] == "triple"


def test_thin_late_subswitch_is_reversible(monkeypatch):
    """关掉 ③b 子开关 → 逐字回退（国芳回到旧行为的 `none`，不是 block）。"""
    assert dg.distribution_verdict(GUOFANG)["level"] == "block"
    monkeypatch.setattr(dg, "DEFENSE_DIST_THIN_LATE_ENABLED", False)
    v = dg.distribution_verdict(GUOFANG)
    assert v["level"] == "none" and v["kind"] == ""


def test_thin_late_note_passes_plain_copy_gate():
    """note 必须过词表唯一源 `card_copy_rules.scan()`（禁机器黑话上卡）。"""
    import card_copy_rules as ccr
    fails, warns = ccr.scan(dg.distribution_verdict(GUOFANG)["note"])
    assert fails == [] and warns == [], (fails, warns)


def test_thin_late_card_plain_label_registered():
    """「竞价达标但没买」名单必须收录 ③b 的一眼原因，且标签本身过白话词表。"""
    from pipelines import build_cards as bc
    import card_copy_rules as ccr
    lbl = bc._DEFENSE_GATE_PLAIN.get("distribution_thin_late")
    assert lbl, sorted(bc._DEFENSE_GATE_PLAIN)
    assert ccr.scan(lbl) == ([], []), ccr.scan(lbl)


# 国芳集团 10-09 候选（真实字段形态；位置腿只有 30 日那条够高）
def _guofang_candidates():
    return {"date": "2026-10-09", "environment": {},
            "relay": [dict(GUOFANG, boards=1, theme_name="大消费", core_theme="大消费",
                           sector="一般零售",
                           expectation={"expected_range": [0.0, 1.7],
                                        "auction_amount_min_yi": 0.3})],
            "watch": []}


# 10-12 真实竞价：昨收 14.94 → 今开 15.30（+2.41%），量能达标
QUOTES_GUOFANG = {"601086": {"prev_close": 14.94, "auction_price": 15.30,
                             "auction_amount_yi": 1.2}}


def test_pool_call_blocks_thin_late_high_and_reports_it():
    """竞价达标（+2.41%）仍不买 —— 记 `distribution_thin_late`（与三条全中分开），不占名额。"""
    res = pool_call.call_pool(_guofang_candidates(), QUOTES_GUOFANG, plan={},
                              stance="attack", line_cores={}, sector_flow={},
                              zt_series={}, zt_days=[])
    v = res["views"]["601086"]
    assert v["view"] == "watch"
    assert v["defense_gate"] == "distribution_thin_late"
    assert v["defense_detail"]["kind"] == "thin_late_high"
    assert "+2.41%" in v["reason"] and "抬轿子" in v["reason"]
    d = res["meta"]["defense"]
    assert d["distribution_thin_late_blocked"] == ["601086"]
    assert d["distribution_blocked"] == []           # ⛔ 与三条全中分账·不混记
    assert res["meta"]["participate"] == []
    views = pool_call.apply_to_views(res, {})        # 卡面/审计读同一份结论
    assert views["601086"]["defense_gate"] == "distribution_thin_late"


def test_pool_call_thin_late_switch_is_reversible(monkeypatch):
    """关掉 ③b → 国芳恢复为可买候选（其余闸不动），证明拦它的是这一条。"""
    monkeypatch.setattr(dg, "DEFENSE_DIST_THIN_LATE_ENABLED", False)
    res = pool_call.call_pool(_guofang_candidates(), QUOTES_GUOFANG, plan={},
                              stance="attack", line_cores={}, sector_flow={},
                              zt_series={}, zt_days=[])
    assert res["views"]["601086"]["view"] == "participate"
    assert res["views"]["601086"].get("defense_gate") is None


# ── ② 方向资金失血 ───────────────────────────────────────────────────────────
def test_sector_flow_sum_aggregates_the_whole_family():
    """机器人族 = −44.31 + 2.06 = −42.25（「机器视觉」不算，名字里没有「机器人」）；
    医药族合计为正 → 不被「医药商业 −1.07」这类零头误判成失血。"""
    robo = dg.sector_flow_sum("机器人", FLOW_0930)
    assert robo["sum_yi"] == -42.25
    assert [m["board"] for m in robo["matched"]] == ["机器人概念", "机器人执行器"]
    pharma = dg.sector_flow_sum("医药", FLOW_0930)
    assert pharma["sum_yi"] is not None and pharma["sum_yi"] > 0


def test_sector_drain_blocks_xiangyang_only():
    """襄阳在失血方向（机器人）→ 拦；南华在净流入方向（医药）→ 不拦（它靠派发闸拦）。"""
    assert dg.sector_drain_verdict(XIANGYANG, FLOW_0930)["drained"] is True
    assert "失血" in dg.sector_drain_verdict(XIANGYANG, FLOW_0930)["note"]
    assert dg.sector_drain_verdict(NANHUA, FLOW_0930)["drained"] is False


def test_sector_drain_missing_data_is_not_adjudicated():
    """板块资金文件/匹配缺失 → known=False，不拦、不编造。"""
    v = dg.sector_drain_verdict(XIANGYANG, {})
    assert v["drained"] is False and v["known"] is False
    assert dg.load_sector_flow("1999-01-01") == {}
    # 泛名/过短题材不参与匹配
    assert dg.sector_flow_sum("机", FLOW_0930)["sum_yi"] is None


def test_sector_drain_spares_the_real_mainline():
    """⛔ 零误杀锁：龙蟠科技 core_theme=锂电池 → 合计 −12.88 亿，未达 30 亿门槛 → 不拦。

    它当天涨停 +9.98%。若这条闸把它也拦了，10-08 就是「全线空仓」——虽然躲过
    −7% 的回撤，却错过了真主线的钱，那就不是改进而是另一种错误。
    """
    v = dg.sector_drain_verdict(LONGPAN, FLOW_0930)
    assert v["drained"] is False and v["known"] is True
    assert v["sum_yi"] == -12.88


def test_sector_drain_uses_core_theme_not_family_name():
    """⛔ 口径锁：必须用 core_theme（当日真实炒作题材），不用 theme_name（聚合族名）。

    龙蟠的 theme_name=新能源 会命中「新能源车 −36.4 亿」→ 把真主线误杀；
    改成 core_theme=锂电池 才对得上它真正在炒的那条线。
    """
    assert dg.sector_drain_verdict(
        {"core_theme": "锂电池", "theme_name": "新能源"}, FLOW_0930)["drained"] is False
    assert dg.sector_drain_verdict(
        {"theme_name": "新能源"}, {"新能源车": {"flow_yi": -36.4}})["drained"] is True


def test_sector_drain_threshold_is_thirty_yi():
    """门槛 30 亿：−29 亿不拦、−31 亿拦（只抓「显著失血」）。"""
    assert dg.sector_drain_verdict({"core_theme": "某线"}, {"某线": {"flow_yi": -29.0}})["drained"] is False
    assert dg.sector_drain_verdict({"core_theme": "某线"}, {"某线": {"flow_yi": -31.0}})["drained"] is True


def test_sector_drain_ignores_too_broad_theme():
    """过泛保护：泛名（「其他」能命中一堆板块）→ 不裁决，禁把不相关板块资金加总当方向。"""
    broad = {f"其他{n}": {"flow_yi": -5.0} for n in range(9)}
    v = dg.sector_flow_sum("其他", broad)
    assert v["sum_yi"] is None and v["too_broad"] is True
    assert dg.sector_drain_verdict(WUFANG, broad)["drained"] is False
    # 恰好 8 个仍在阈值内（边界）
    edge = {f"其他{n}": {"flow_yi": -5.0} for n in range(8)}
    assert dg.sector_flow_sum("其他", edge)["too_broad"] is False


# ── ③ 位置（高位二波）────────────────────────────────────────────────────────
def test_position_blocks_nanhua_second_wave():
    """南华生物：09-22 那波 4 板 → 断板 4 个交易日（09-25 中秋休市）→ 09-30 重启（首板）。位置高 → 拦。

    ⛔ 这正是系统的原始盲区：断板后连板计数被重置成「首板」，看不出「已经有了一波」。
    """
    v = dg.position_verdict(NANHUA, ZT_SERIES, ZT_DAYS)
    assert v["level"] == "block", v
    assert v["high_position"] is True and v["prior_board_ok"] is True
    assert v["wave"]["is_rebound"] is True and v["wave"]["gap_days"] == 4
    assert v["wave"]["prior_max_board"] == 4
    assert "第二波" in v["note"] and "断板 4 个交易日" in v["note"]


def test_position_spares_consecutive_boards_and_winners():
    """襄阳是**连续** 4 板（非二波）→ 位置闸只留痕；同日走出来的票位置不高 → 不裁决。"""
    x = dg.position_verdict(XIANGYANG, ZT_SERIES, ZT_DAYS)
    assert x["level"] == "warn" and x["wave"]["gap_days"] == 0
    assert "位置偏高" in x["note"]
    for c in (LONGPAN, SHIDAI, ZIZHU):
        assert dg.position_verdict(c, ZT_SERIES, ZT_DAYS)["level"] == "none", c["name"]


def test_position_quick_nzi_is_not_blocked():
    """⛔ 锁死「断板回封是加分项」：断 1~2 天的快速 N 字 + 高位 → 只留痕，不拦。"""
    v = dg.position_verdict(XIANGYANG, QUICK_NZI_SERIES, QUICK_NZI_DAYS)
    assert v["wave"]["gap_days"] == 2 and v["wave"]["prior_max_board"] == 3
    assert v["level"] == "warn"                    # 高位 → 留痕
    assert v["level"] != "block"                   # ⛔ 断得不久 → 不拦


def test_position_needs_both_legs():
    """两条腿缺一不可：不高位的二波不拦；高位但非二波不拦（只 warn）。"""
    low2 = dict(NANHUA, dev_10d_pct=20.0)          # 二波但不高位
    assert dg.position_verdict(low2, ZT_SERIES, ZT_DAYS)["level"] == "none"
    weak_wave = dict(NANHUA, code="600000")        # 高位但序列不足（非二波）
    v = dg.position_verdict(weak_wave, ZT_SERIES, ZT_DAYS)
    assert v["level"] == "warn" and v["wave"]["known"] is False


def test_position_backlog_leg_uses_dev30():
    """高位腿＝dev10 或 dev30 任一达线（dev10 缺时用 dev30 兜底）。"""
    c = {"code": "000504", "dev_30d_pct": 52.0}
    v = dg.position_verdict(c, ZT_SERIES, ZT_DAYS)
    assert v["level"] == "block"
    assert dg.position_verdict({"code": "000504", "dev_30d_pct": 52.0, "dev_10d_pct": 10.0},
                               ZT_SERIES, ZT_DAYS)["level"] == "block"   # dev10 填了也不影响（或关系）


def test_position_missing_data_is_not_adjudicated():
    """涨停序列缺失/不足 2 条 → 不能判「二波」→ 不拦（诚实降级）。"""
    assert dg.position_verdict(NANHUA, {}, [])["level"] == "warn"      # 只剩高位腿 → 留痕
    assert dg.position_verdict(NANHUA, {}, [])["wave"]["known"] is False
    assert dg.position_verdict({"code": "000504", "dev_10d_pct": 50.0},
                               {"000504": [("2026-09-30", 1)]}, ZT_DAYS)["level"] == "warn"


def test_load_zt_series_truncates_and_skips_placeholder(tmp_path):
    """⛔ 序列加载锁：①截到依据日（不含未来）②空文件（节假日占位）不进交易日历。"""
    def _w(day, rows):
        (tmp_path / f"zt_{day}.json").write_text(
            json.dumps({"zt_list": rows}, ensure_ascii=False), encoding="utf-8")
    _w("2026-09-24", [{"code": "000678", "name": "襄阳轴承", "boards": 1}])
    _w("2026-10-01", [])                                     # 节假日占位（空）
    _w("2026-10-08", [{"code": "000678", "name": "襄阳轴承", "boards": 9}])   # 未来日
    s, days = dg.load_zt_series(upto="2026-09-30", root=tmp_path)
    assert days == ["2026-09-24"]                            # 占位与未来日都被剔除
    assert s == {"000678": [("2026-09-24", 1)]}
    # 不传 upto → 未来日纳入（调用方负责给依据日）
    s2, days2 = dg.load_zt_series(root=tmp_path)
    assert days2 == ["2026-09-24", "2026-10-08"] and s2["000678"][-1] == ("2026-10-08", 9)
    # 目录不存在 → 诚实降级
    assert dg.load_zt_series(root=tmp_path / "nope") == ({}, [])


# ── ④ 环境名额降档 ───────────────────────────────────────────────────────────
def test_env_verdict_1008_is_honest_and_does_not_trigger():
    """⛔ 诚实锁：10-08 当天环境闸门**拦不住**（只有晋级率 21.1% 一条命中）→ normal。"""
    v = dg.env_verdict(ENV_0930, MO_1008, prev_date10="2026-09-30")
    assert v["level"] == "normal"
    assert v["hit_count"] == 1


def test_env_verdict_block_on_true_ice_day():
    """真冰点日（情绪 1 + 晋级率 19 + 核按钮 11 + 真破位 3）→ 4 条命中 → 当日不主动买。"""
    ice = {"sentiment_score": 1.0, "promote_rate": 19.2, "nuke_count": 11,
           "break_count": 3, "broken_rate": 44.7}
    v = dg.env_verdict(ice, None)
    assert v["level"] == "block"
    assert v["hit_count"] >= 3


def test_env_verdict_open_leg_only_when_coverage_enough():
    """今开腿：样本覆盖不足时不参与判定（防被少数票带偏）。"""
    weak_open = dict(MO_1008, red_open_ratio=0.10, leader_red_open_ratio=0.10)
    assert dg.env_verdict({}, weak_open)["hit_count"] == 2
    assert dg.env_verdict({}, dict(weak_open, coverage=0.30))["hit_count"] == 0


# ── ⑤ 端到端：10-08 场景（真实候选 + 真实竞价）──────────────────────────────
def _plan():
    return {"stance": "selective",
            "views": {"000678": {"bias": "focus"}, "000504": {"bias": "normal"},
                      "603906": {"bias": "normal"}}}


def _candidates():
    return {"date": "2026-09-30", "environment": ENV_0930,
            "relay": [XIANGYANG], "watch": [NANHUA, LONGPAN]}


def test_pool_call_before_fix_reproduces_the_loss(monkeypatch):
    """回退态（防守闸关闭）复现 10-08 真实结果 → 买襄阳 + 南华（＝当天确实亏的那两只）。

    ⛔ 2026-11-18：本用例复现的是**历史回退态**，必须同时关掉封板期望层，
       否则 EV 会按新口径改入选名单，测的就不是"关掉防守闸会怎样"了。
    """
    monkeypatch.setattr(pool_call, "POOL_CALL_DEFENSE_GATE", False)
    monkeypatch.setattr(pool_call, "POOL_CALL_SEAL_EV", False)
    res = pool_call.call_pool(_candidates(), QUOTES_1008, plan=_plan(), stance="selective",
                              line_cores={}, market_open=MO_1008, sector_flow=FLOW_0930,
                              zt_series=ZT_SERIES, zt_days=ZT_DAYS)
    bought = sorted(c for c, v in res["views"].items() if v["view"] == "participate")
    assert bought == ["000504", "000678"]


def test_pool_call_defense_gate_blocks_both_losers_and_promotes_next():
    """开启后：两只各被**不同的闸**拦下，名额让给封板质量更好的龙蟠科技。

    顺序＝宏观到微观：板块有没有钱（⑧）→ 这票在什么位置（⑨）→ 昨天封得脏不脏（⑩）。
      · 襄阳轴承 → 方向失血（机器人族 −42.25 亿）  ← 土壤先判
      · 南华生物 → 高位二波（断板 4 个交易日后重启 ∧ 上一波 4 板）← 位置才是它的真原因
    ⛔ 位置闸排在派发闸**前面**：两条都中的票（南华）卡面必须说「第二波高位反抽」这个真原因，
      而不是只说「换手大/封单薄」这个症状。
    """
    res = pool_call.call_pool(_candidates(), QUOTES_1008, plan=_plan(), stance="selective",
                              line_cores={}, market_open=MO_1008, sector_flow=FLOW_0930,
                              zt_series=ZT_SERIES, zt_days=ZT_DAYS)
    bought = [c for c, v in res["views"].items() if v["view"] == "participate"]
    assert bought == ["603906"], res["views"]
    meta = res["meta"]["defense"]
    assert meta["sector_drain_blocked"] == ["000678"]
    assert meta["position_blocked"] == ["000504"]
    assert meta["distribution_blocked"] == []
    assert meta["sector_flow_boards"] == len(FLOW_0930)
    assert meta["zt_history_days"] == len(ZT_DAYS)
    # 理由是人话、带数字，且各说各的真原因
    r_xy = res["views"]["000678"]["reason"]
    assert "失血" in r_xy and "42.2 亿" in r_xy and "先看" in r_xy and "→" in r_xy
    assert res["views"]["000678"]["defense_gate"] == "sector_drain"
    r_nh = res["views"]["000504"]["reason"]
    assert "第二波反抽" in r_nh and "断板 4 个交易日" in r_nh and "4 板" in r_nh
    assert res["views"]["000504"]["defense_gate"] == "high_position"
    # 封板质量读数在短路链之前算好 → 被别的闸拦下的票也留痕（审计可查）
    assert res["views"]["000678"]["seal_quality"]["seal_ratio_pct"] == 1.13
    assert res["views"]["000504"]["seal_quality"]["seal_ratio_pct"] == 1.51


def test_pool_call_defense_missing_flow_still_blocks_position():
    """板块资金缺档 → 方向闸不裁决；位置闸/派发闸照常工作（三道闸互不依赖）。"""
    res = pool_call.call_pool(_candidates(), QUOTES_1008, plan=_plan(), stance="selective",
                              line_cores={}, market_open=MO_1008, sector_flow={},
                              zt_series=ZT_SERIES, zt_days=ZT_DAYS)
    d = res["meta"]["defense"]
    assert d["sector_drain_blocked"] == []
    assert d["position_blocked"] == ["000504"]                 # 位置闸不需要板块资金
    assert d["distribution_blocked"] == ["000678"]             # 襄阳漏过位置闸（连续板）→ 派发闸接住
    assert res["views"]["000678"]["defense_gate"] == "distribution"


def test_pool_call_env_block_kills_quota():
    """环境 ≥5 条命中 → 名额 0，全部降观察（不主动买）。

    ⚠️ 2026-11-06 阈值校准：block 门槛 3→5（命中 3/4 条组实测 −0.19%/+0.70%，
    是**最好**的两组，⛔ 不该被 block）。本夹具 5 条全中，新旧口径都拦。
    """
    cands = _candidates()
    cands["environment"] = {"sentiment_score": 1.0, "promote_rate": 19.2, "nuke_count": 11,
                            "break_count": 3, "broken_rate": 44.7}
    res = pool_call.call_pool(cands, QUOTES_1008, plan=_plan(), stance="selective",
                              line_cores={}, market_open=MO_1008, sector_flow=FLOW_0930,
                              zt_series=ZT_SERIES, zt_days=ZT_DAYS)
    assert res["meta"]["effective_quota"] == 0
    assert res["meta"]["defense"]["env"]["level"] == "block"
    assert not [c for c, v in res["views"].items() if v["view"] == "participate"]


def test_pool_call_env_defensive_caps_then_min_two_widens(monkeypatch):
    """≥3 条命中 → 环境腿先把名额压到 1；2026-11-04「尽量推两个」在**允许买**时放宽回 2。

    ⛔ 只放宽名额，不放宽准入：本夹具只有 1 只票过闸 → 实际仍只买 1 只。
    开关 `POOL_CALL_MIN_TWO_SLOTS=False` 逐字回退到「环境压到 1」。

    ⚠️ 2026-11-06 阈值校准：diffensive 门槛 2→3（命中 2 条组实测 +0.20%/胜 58.3%，
    是**次好**的一组，⛔ 不该压名额）→ 2 条走 normal、3 条才 defensive，两条路径都锁。
    """
    from core import defense_gate as dg

    two = _candidates()
    two["environment"] = {"sentiment_score": 2.0, "promote_rate": 21.0, "nuke_count": 3,
                          "break_count": 0, "broken_rate": 10.0}
    r2 = pool_call.call_pool(two, QUOTES_1008, plan=_plan(), stance="selective",
                             line_cores={}, market_open=MO_1008, sector_flow=FLOW_0930,
                             zt_series=ZT_SERIES, zt_days=ZT_DAYS)
    assert r2["meta"]["defense"]["env"]["hit_count"] == 2
    assert r2["meta"]["defense"]["env"]["level"] == "normal", "2 条不该压名额"

    # 回退旧门槛（2）→ 2 条重新算 defensive
    monkeypatch.setattr(dg, "ENV_DEFENSIVE_HITS", 2)
    r2b = pool_call.call_pool(two, QUOTES_1008, plan=_plan(), stance="selective",
                              line_cores={}, market_open=MO_1008, sector_flow=FLOW_0930,
                              zt_series=ZT_SERIES, zt_days=ZT_DAYS)
    assert r2b["meta"]["defense"]["env"]["level"] == "defensive"
    monkeypatch.setattr(dg, "ENV_DEFENSIVE_HITS", 3)

    cands = _candidates()
    cands["environment"] = {"sentiment_score": 2.0, "promote_rate": 21.0, "nuke_count": 3,
                            "break_count": 0, "broken_rate": 45.0}

    def _run():
        return pool_call.call_pool(cands, QUOTES_1008, plan=_plan(), stance="selective",
                                   line_cores={}, market_open=MO_1008, sector_flow=FLOW_0930,
                                   zt_series=ZT_SERIES, zt_days=ZT_DAYS)

    res = _run()
    assert res["meta"]["defense"]["env"]["level"] == "defensive"
    assert res["meta"]["effective_quota"] == 2          # 名额被放宽（原 stance selective=2）
    assert res["meta"]["slot_policy"]["min_two_slots_applied"] is True
    assert len(res["meta"]["participate"]) == 1         # 准入没放宽 → 仍只 1 只合格

    monkeypatch.setattr(pool_call, "POOL_CALL_MIN_TWO_SLOTS", False)
    old = _run()
    assert old["meta"]["effective_quota"] == 1          # 回退旧口径：环境压到 1
    assert len(old["meta"]["participate"]) == 1


def test_pool_call_defense_subswitches_are_reversible(monkeypatch):
    """四个子开关逐个可逆（便于定位是哪条闸在起作用·总开关回退已另锁）。

    ⛔ 2026-11-18：本用例锁的是**防守闸**；封板期望层会改排序与入选，
       会让断言从「谁被哪道闸拦住」变成「谁的期望低」，故显式关闭 EV 层。
    """
    monkeypatch.setattr(pool_call, "POOL_CALL_SEAL_EV", False)
    def _run():
        return pool_call.call_pool(_candidates(), QUOTES_1008, plan=_plan(), stance="selective",
                                   line_cores={}, market_open=MO_1008, sector_flow=FLOW_0930,
                                   zt_series=ZT_SERIES, zt_days=ZT_DAYS)

    def _bought(res):
        return sorted(c for c, v in res["views"].items() if v["view"] == "participate")

    # ① 关位置闸 → 南华回落到派发闸（襄阳仍被方向闸拦）
    monkeypatch.setattr(pool_call, "POOL_CALL_DEFENSE_POSITION", False)
    r1 = _run()
    assert r1["meta"]["defense"]["position_blocked"] == []
    assert r1["meta"]["defense"]["distribution_blocked"] == ["000504"]
    assert r1["meta"]["defense"]["sector_drain_blocked"] == ["000678"]
    assert _bought(r1) == ["603906"]

    # ② 关掉方向闸（位置+派发都开）→ 襄阳漏过位置闸（连续板）被派发闸接住
    monkeypatch.setattr(pool_call, "POOL_CALL_DEFENSE_POSITION", True)
    monkeypatch.setattr(pool_call, "POOL_CALL_DEFENSE_SECTOR_DRAIN", False)
    r2 = _run()
    assert r2["meta"]["defense"]["sector_drain_blocked"] == []
    assert r2["meta"]["defense"]["distribution_blocked"] == ["000678"]
    assert r2["meta"]["defense"]["position_blocked"] == ["000504"]
    assert _bought(r2) == ["603906"]

    # ③ 只关派发闸 → 襄阳走方向闸、南华走位置闸
    monkeypatch.setattr(pool_call, "POOL_CALL_DEFENSE_SECTOR_DRAIN", True)
    monkeypatch.setattr(pool_call, "POOL_CALL_DEFENSE_DISTRIBUTION", False)
    r3 = _run()
    assert r3["meta"]["defense"]["distribution_blocked"] == []
    assert r3["meta"]["defense"]["sector_drain_blocked"] == ["000678"]
    assert r3["meta"]["defense"]["position_blocked"] == ["000504"]
    assert _bought(r3) == ["603906"]

    # ④ 三道全关 → 逐字回到 10-08 生产行为
    monkeypatch.setattr(pool_call, "POOL_CALL_DEFENSE_POSITION", False)
    monkeypatch.setattr(pool_call, "POOL_CALL_DEFENSE_SECTOR_DRAIN", False)
    r4 = _run()
    assert _bought(r4) == ["000504", "000678"]


def test_apply_to_views_carries_defense_record():
    """池内裁决结论合并进 views 时，防守留痕必须一起带出（卡面/审计可查）。"""
    res = pool_call.call_pool(_candidates(), QUOTES_1008, plan=_plan(), stance="selective",
                              line_cores={}, market_open=MO_1008, sector_flow=FLOW_0930,
                              zt_series=ZT_SERIES, zt_days=ZT_DAYS)
    views = pool_call.apply_to_views(res, {})
    assert views["000678"]["defense_gate"] == "sector_drain"
    assert "失血" in (views["000678"]["defense_reason"] or "")
    assert views["000504"]["defense_gate"] == "high_position"
    assert "第二波" in (views["000504"]["defense_reason"] or "")


# ── ⑥ 卡面点名（竞价达标却没买，必须让读者看到原因）────────────────────────────
def test_verify_card_names_defense_blocked_stocks():
    """🛡️ 块只给一眼名单 + 一句白话原因；理由正文仍由个股段渲染（禁两处重复）。"""
    from pipelines import build_cards as bc

    raw = {"decision_date": "2026-10-08", "stance": "selective",
           "stance_label": "只做最强的一两只", "direction": "锂电池", "note": "做多气氛4分",
           "views": {"000678": {"bias": "focus"}, "000504": {"bias": "normal"}}}
    records = [
        {"code": "000678", "name": "襄阳轴承", "boards": 4,
         "performance": {"open_check": {
             "ai_view": "watch", "gap_pct": 0.85, "defense_gate": "sector_drain",
             "pool_reason": "它这条线（机器人）昨天板块资金净流出 42.2 亿"}}},
        {"code": "000504", "name": "南华生物", "boards": 1,
         "performance": {"open_check": {
             "ai_view": "watch", "gap_pct": 0.40, "defense_gate": "high_position",
             "pool_reason": "这是第二波反抽，位置已经很高"}}},
    ]
    # ⚠️ 2026-10-21 融合后（`VERIFY_CARD_UNIFY_MECH` 默认开）：🛡️ 名单不再单列，
    #    压成标题行胶囊 `🛡️ 防守拦下（原因）`——理由仍由个股段渲染，不重复。
    #    本条显式关融合，锁住**回退路径**的旧行为。
    import os
    _old = os.environ.get("VERIFY_CARD_UNIFY_MECH")
    os.environ["VERIFY_CARD_UNIFY_MECH"] = "0"
    try:
        blocks = bc._ai_plan_block(raw, records,
                                   pool_summary={"participate": [], "quota": 2, "pool_size": 3})
    finally:
        if _old is None:
            os.environ.pop("VERIFY_CARD_UNIFY_MECH", None)
        else:
            os.environ["VERIFY_CARD_UNIFY_MECH"] = _old
    txt = "\n".join(b.get("text", {}).get("content", "") for b in blocks if b.get("text"))
    assert "🛡️ 竞价达标但没买" in txt
    assert "襄阳轴承(000678)" in txt and "资金一直在往外走" in txt
    assert "南华生物(000504)" in txt and "第二波反抽" in txt
    # ⛔ 理由正文不在此重复（只由个股段 `_ai_view_line` 渲染）
    assert "板块资金净流出 42.2 亿" not in txt
    # 买入票本来就没有 → 块不出现（不误报）
    blocks2 = bc._ai_plan_block(raw, [dict(records[0], performance={"open_check": {
        "ai_view": "participate", "gap_pct": 0.85, "defense_gate": "sector_drain"}})],
        pool_summary={"participate": ["000678"], "quota": 2, "pool_size": 3})
    txt2 = "\n".join(b.get("text", {}).get("content", "") for b in blocks2 if b.get("text"))
    assert "🛡️ 竞价达标但没买" not in txt2
