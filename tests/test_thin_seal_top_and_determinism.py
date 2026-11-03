# -*- coding: utf-8 -*-
"""2026-10-27 两条用户定调的回归锁。

① **同日重跑分数必须恒定（可复现）**
   用户原话：「处理问题一」——同一交易日、同一份涨停池，早跑和晚跑算出两套分。
   根因：`_score_all` 的题材只认 `code2themes`，而该旁路数据到得晚 →
   回退行业（通用设备/软件开发）→ 题材桶变 → 梯队分组变（C→A、health 4.6→10）
   → 题材统计变（exp 2.75→38.67）→ 最终分 56.1→92.03。
   锁：`ztgc` 行已带 `core_theme`（当日题材快照回填）时，`_score_all` **必须优先用它**，
       不再看 `code2themes` 是否已就绪。

② **最高板 + 极薄封单 → 只观察（能看不能买）**
   用户原话：「只观察，行情差没有必要强行接力」。
   实证：上海九百(600838) 5 板=全市场唯一高度，封单只占流通 0.24%。
   锁：`pipeline_eod.demote_thin_seal_top` 把它从接力池降进观察池（`watch_only=True`），
       且 `pool_call` 在 09:25 也认这条（`defense_gate=thin_seal_top`），
       防止从别的路径被捡回买单队列。缺封单占比 → 不裁决。
"""
import sys
from pathlib import Path

WC = Path(__file__).resolve().parents[1] / "wealth_code"
if str(WC) not in sys.path:
    sys.path.insert(0, str(WC))

from pipelines import pipeline_eod as pe  # noqa: E402


# ── ① 确定性：题材优先取当日快照回填的 core_theme ─────────────────────
def _zt_row(code, name, lbc=1, hy="通用设备", core_theme="机器人"):
    return {
        "dm": code, "mc": name, "lbc": lbc, "hy": hy, "core_theme": core_theme,
        "hs": 8.0, "zbc": 0, "fbt": "092500", "lbt": "092500", "zf": 10.0,
        "zj": 2.0, "lt": 50.0, "cje": 4.0, "zsz": 80.0, "price": 10.0,
        "is_20cm": False, "is_st": False,
    }


def test_score_all_prefers_core_theme_over_code2themes():
    """`code2themes` 为空（旁路数据没到）时，题材仍取 `core_theme`，不回退行业。"""
    rows = [
        _zt_row("603028", "赛福天", lbc=4, hy="通用设备", core_theme="机器人"),
        _zt_row("603528", "多伦科技", lbc=4, hy="软件开发", core_theme="机器人"),
    ]
    out = pe._score_all(rows, {}, [])
    themes = {r["code"]: r["theme"] for r in out}
    assert themes["603028"] == "机器人", f"赛福天题材应取 core_theme，实得 {themes['603028']}"
    assert themes["603528"] == "机器人", f"多伦题材应取 core_theme，实得 {themes['603528']}"


def test_score_all_repeat_is_deterministic():
    """同一份输入连算两次，每票分数必须一模一样（可复现的最低要求）。"""
    rows = [
        _zt_row("603028", "赛福天", lbc=4, core_theme="机器人"),
        _zt_row("603528", "多伦科技", lbc=4, core_theme="机器人"),
        _zt_row("600838", "上海九百", lbc=5, hy="一般零售", core_theme="大消费"),
    ]
    a = {r["code"]: r["score"] for r in pe._score_all(rows, {}, [])}
    b = {r["code"]: r["score"] for r in pe._score_all(rows, {}, [])}
    assert a == b, f"两次评分不一致（不可复现）：{a} vs {b}"


def test_score_all_falls_back_when_core_theme_missing():
    """快照缺失（core_theme 为空）→ 仍走旧路径回退行业，⛔ 不许编造题材。"""
    rows = [_zt_row("600001", "独苗", lbc=1, hy="钢铁行业", core_theme="")]
    out = pe._score_all(rows, {}, [])
    assert out[0]["theme"] == "钢铁行业", f"缺题材时应回退行业，实得 {out[0]['theme']}"


# ── ② 最高板 + 极薄封单 → 降为观察 ────────────────────────────────────
def _cand(code, name, boards, seal_ratio, rank=1, price=10.0):
    return {"code": code, "name": name, "boards": boards, "seal_ratio_pct": seal_ratio,
            "relay_rank": rank, "price": price, "theme": "机器人", "fbt": "093000",
            "zbc": 0, "cje_yi": 4.0, "hs": 8.0, "stage": "high_relay"}


def _ztgc(boards_list):
    return [{"dm": f"00{i:04d}", "mc": f"票{i}", "lbc": b} for i, b in enumerate(boards_list)]


def test_thin_seal_top_demoted_to_watch():
    """最高板 + 封单占流通 <2% → 移出接力池、进观察池，并带上白话理由。"""
    relay = [_cand("600838", "上海九百", 5, 0.24, rank=1),
             _cand("603028", "赛福天", 4, 0.85, rank=2)]
    watch = []
    kept, watch_out, moved, market_max = pe.demote_thin_seal_top(relay, watch, _ztgc([5, 4, 1]))
    assert market_max == 5
    assert [c["code"] for c in kept] == ["603028"], "非最高板不该被动"
    assert [c["code"] for c in moved] == ["600838"]
    assert watch_out[0]["watch_only"] is True
    assert "只看不买" in watch_out[0]["watch_reason"]
    assert kept[0]["relay_rank"] == 1, "降级后接力池序号必须重排，避免断档编号"


def test_thin_seal_top_not_triggered_when_seal_is_thick():
    """最高板但封单够厚（≥2%）→ 不降级（不能因为「最高」就一律否决）。"""
    relay = [_cand("600838", "上海九百", 5, 3.20)]
    kept, watch, moved, _ = pe.demote_thin_seal_top(relay, [], _ztgc([5, 1]))
    assert len(kept) == 1 and not watch and not moved


def test_thin_seal_top_missing_ratio_is_not_judged():
    """缺封单占比（拿不到流通市值）→ 不裁决、不动（⛔ 不编造不利证据）。"""
    relay = [_cand("600838", "上海九百", 5, None)]
    kept, watch, moved, _ = pe.demote_thin_seal_top(relay, [], _ztgc([5, 1]))
    assert len(kept) == 1 and not moved


def test_thin_seal_top_below_3_boards_disabled():
    """全市场最高板不到 3 板 → 谈「高度」无意义，整条规则不启用。"""
    relay = [_cand("600001", "两板票", 2, 0.10)]
    kept, watch, moved, market_max = pe.demote_thin_seal_top(relay, [], _ztgc([2, 1]))
    assert market_max == 2
    assert len(kept) == 1 and not moved


def test_thin_seal_top_switch_is_reversible(monkeypatch):
    """`ENABLE_THIN_SEAL_TOP_WATCH=0` → 逐字回退，票留在接力池。"""
    monkeypatch.setenv("ENABLE_THIN_SEAL_TOP_WATCH", "0")
    relay = [_cand("600838", "上海九百", 5, 0.24)]
    kept, watch, moved, _ = pe.demote_thin_seal_top(relay, [], _ztgc([5, 1]))
    assert len(kept) == 1 and not moved and not watch
