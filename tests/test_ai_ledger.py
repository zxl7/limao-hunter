# -*- coding: utf-8 -*-
"""AI 判断平行台账单测（2026-09-10 用户「把我的思考记到真实台账」）。

锁定 record/settle 口径（与 Python 轨同源：T+1 竞价买→T+1 收盘卖·等权·nav 递推）
与幂等/跳过/诚实降级。全隔离 tmp_path，不触真实台账、不触网。
"""
import importlib.util
import json
from pathlib import Path

WC = Path(__file__).resolve().parents[1] / "wealth_code"
spec = importlib.util.spec_from_file_location("ai_ledger", WC / "scripts" / "ai_ledger.py")
al = importlib.util.module_from_spec(spec)
spec.loader.exec_module(al)


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(al, "CACHE", tmp_path)
    monkeypatch.setattr(al, "DECISIONS", tmp_path / "ai_decisions.jsonl")
    monkeypatch.setattr(al, "AI_LEDGER", tmp_path / "ai_nav_history.jsonl")
    # PY_LEDGER 也要隔离：否则 compare 单测会去读真实生产账本（2026-09-14 修）
    monkeypatch.setattr(al, "PY_LEDGER", tmp_path / "account_nav_history.jsonl")
    # ⛔ 2026-09-11：ai_view 落盘目录独立于 CACHE（狸猫猎手/cache），必须一并隔离，
    #    否则单测会写进真实 cache 污染生产（并掩盖路径接缝）。
    monkeypatch.setattr(al, "AI_VIEW_DIR", tmp_path)
    # ⛔ 2026-11-03：结算新增「报价 vs 当日开盘价」交叉校验，会读日K缓存。
    #    不隔离就会拿真实K线覆盖测试桩里的假价格（5 条 settle 用例实测因此变红）。
    monkeypatch.setattr(al, "KLINE_DIR", tmp_path / "kline_qfq")


def test_ai_view_dir_seam(monkeypatch, tmp_path):
    """⛔ 接缝锁（2026-09-11 修 bug 后立）：写入方 ai_ledger.AI_VIEW_DIR 必须与
    消费方 build_cards._ai_view_dir() 完全一致——09-10 两侧各写各的
    （al 写 wealth_code/cache、卡片读 狸猫猎手/cache）导致「写了也读不到」静默失效，
    而各自单测都 mock 自己那半 → 接缝零覆盖。此测试专门锁跨模块路径一致性。"""
    import importlib.util as _u
    spec2 = _u.spec_from_file_location("build_cards", WC / "pipelines" / "build_cards.py")
    bc = _u.module_from_spec(spec2)
    spec2.loader.exec_module(bc)
    assert al.AI_VIEW_DIR == bc._ai_view_dir(), (
        f"AI 判断视图读写路径不一致：写 {al.AI_VIEW_DIR} vs 读 {bc._ai_view_dir()}")
    assert bc._ai_view_dir().name == "cache" and bc._ai_view_dir().parent.name == "狸猫猎手"


def test_record_writes_ai_view_with_plan_meta(monkeypatch, tmp_path):
    """AI 决策落盘 → 同时产出竞价卡用的 ai_view（含攻守定调 meta·2026-09-11 新需求）。"""
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "picks.json"
    f.write_text(json.dumps({
        "stance": "selective", "stance_label": "只做最强的一只",
        "direction": "电力/绿电", "note": "市场不冷不热，只做最强的一只",
        "picks": [{"code": "000993", "name": "闽东电力", "view": "participate",
                   "reason": "3板·带动3家", "entry_max_gap": 3.0}],
    }, ensure_ascii=False), encoding="utf-8")
    rc = al.cmd_record(A(file=str(f), decision_date="2026-09-11", trade_date="2026-09-14"))
    assert rc == 0
    vp = tmp_path / "ai_view_2026-09-14.json"
    assert vp.exists()
    d = json.loads(vp.read_text(encoding="utf-8"))
    assert d["date"] == "2026-09-14" and d["decision_date"] == "2026-09-11"
    assert d["stance_label"] == "只做最强的一只" and d["direction"] == "电力/绿电"
    assert d["views"]["000993"]["view"] == "participate"


def test_record_no_view_derivation_from_bias(monkeypatch, tmp_path):
    """⛔ 2026-09-22「彻底不派」：盘后只写 bias，⛔ 不派生 view。

    给定 bias=focus/normal/avoid（无 view）→ 落盘 view 恒为 None，预案档照写进 views；
    `_effective_view` 回退口径按 bias 反推终态（focus→participate / avoid→skip /
    normal→watch）——与旧派生口径逐字一致，且 ⛔ 不得提前拍板「买哪只」。
    """
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "picks.json"
    f.write_text(json.dumps({
        "stance": "selective", "stance_label": "只做最强的一只",
        "direction": "电力/绿电", "note": "t",
        "picks": [
            {"code": "000993", "name": "闽东电力", "bias": "focus", "reason": "3板·带动3家", "entry_max_gap": 3.0},
            {"code": "000823", "name": "超声电子", "bias": "normal", "reason": "r"},
            {"code": "603421", "name": "鼎信通讯", "bias": "avoid", "reason": "封板勉强"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    rc = al.cmd_record(A(file=str(f), decision_date="2026-09-11", trade_date="2026-09-14"))
    assert rc == 0
    vp = tmp_path / "ai_view_2026-09-14.json"
    assert vp.exists()
    v = json.loads(vp.read_text(encoding="utf-8"))["views"]
    # ⛔ 彻底不派：view 恒为 None（不派生），bias 照写
    assert v["000993"]["view"] is None and v["000993"]["bias"] == "focus"
    assert v["000823"]["view"] is None and v["000823"]["bias"] == "normal"
    assert v["603421"]["view"] is None and v["603421"]["bias"] == "avoid"
    # _effective_view 回退：按 bias 反推终态，与旧派生口径逐字一致
    assert al._effective_view({"code": "000993", "bias": "focus"}, {}) == "participate"
    assert al._effective_view({"code": "000823", "bias": "normal"}, {}) == "watch"
    assert al._effective_view({"code": "603421", "bias": "avoid"}, {}) == "skip"
    # 9:25 结论优先（池内裁决覆盖预案）
    assert al._effective_view({"code": "000993", "bias": "focus"},
                              {"000993": {"view": "skip"}}) == "skip"


def _write_quotes(tmp_path, trade_date, rows):
    (tmp_path / f"auction_quotes-{trade_date}.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8")


class A:  # 简易 args 容器
    def __init__(self, **kw): self.__dict__.update(kw)


def test_record_requires_reason(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "picks.json"
    f.write_text(json.dumps([{"code": "000930", "name": "中粮科技", "reason": ""}]), encoding="utf-8")
    assert al.cmd_record(A(decision_date="2026-09-10", file=str(f))) == 0
    assert al._read_jsonl(al.DECISIONS) == []          # 无理由不落盘（数据即事实）


def test_record_freezes_candidate_ranking_score(monkeypatch, tmp_path):
    """台账评分必须取盘后冻结候选，不得在后续结算时重算。"""
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "candidates-2026-09-10.json").write_text(json.dumps({
        "relay": [{"code": "000930", "name": "中粮科技", "score": 88.6}],
        "watch": [],
    }), encoding="utf-8")
    f = tmp_path / "picks.json"
    f.write_text(json.dumps([{
        "code": "000930", "name": "中粮科技", "reason": "排序靠前",
    }], ensure_ascii=False), encoding="utf-8")
    assert al.cmd_record(A(decision_date="2026-09-10", file=str(f))) == 0
    assert al._read_jsonl(al.DECISIONS)[0]["ranking_score"] == 88.6


def test_settle_return_and_nav(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "picks.json"
    f.write_text(json.dumps([{"code": "000930", "name": "中粮科技", "reason": "题材最强+龙头分首选",
                              "entry_max_gap": 3.0}]), encoding="utf-8")
    al.cmd_record(A(decision_date="2026-09-10", file=str(f)))
    # T+1 竞价 10.0 / prev 9.80 → gap 2.04% ≤3 通过；收盘 10.5 → +5.0%
    _write_quotes(tmp_path, "2026-09-11", {"000930": {"prev_close": 9.8, "auction_price": 10.0}})
    monkeypatch.setattr(al, "_close_of", lambda code, d: 10.5)
    al.cmd_settle(A(trade_date="2026-09-11"))
    rows = al._read_jsonl(al.AI_LEDGER)
    assert len(rows) == 1
    r = rows[0]
    assert r["strategy"] == "ai_judgement_next_day_close"
    assert r["stock_count"] == 1 and r["stocks"][0]["return_pct"] == 5.0
    assert abs(r["nav"] - 1.05) < 1e-9                 # nav 递推 1.0×(1+5%)
    assert al._read_jsonl(al.DECISIONS)[0]["settled"] is True


def test_settle_skip_when_gap_over_limit(monkeypatch, tmp_path, capsys):
    """竞价高开 >5%（用户硬线）→ 不追；0 成交日不写账本行（2026-09-14 与账户账本同语义）。"""
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "picks.json"
    f.write_text(json.dumps([{"code": "600865", "name": "百大集团", "reason": "高度锚",
                              "entry_max_gap": 3.0}]), encoding="utf-8")
    al.cmd_record(A(decision_date="2026-09-10", file=str(f)))
    # 竞价高开 6% → 超过用户 5% 硬线，不追（不计收益·nav 不变）
    _write_quotes(tmp_path, "2026-09-11", {"600865": {"prev_close": 10.0, "auction_price": 10.6}})
    monkeypatch.setattr(al, "_close_of", lambda code, d: 11.0)
    al.cmd_settle(A(trade_date="2026-09-11"))
    out = capsys.readouterr().out
    assert "0 成交" in out and "超过 5%" in out          # 诚实留痕在运行日志
    assert al._read_jsonl(al.AI_LEDGER) == []             # 账本只记有实际买入的交易日
    assert all(x.get("settled") for x in al._read_jsonl(al.DECISIONS))   # 决策仍标记已结算


def test_settle_only_counts_participate(monkeypatch, tmp_path):
    """⛔ 2026-09-11：只有 AI 选中要买的（view=participate）才计入 AI 收益。

    watch/skip 是「AI 看过但没买」的 → 标记已结算、但不进收益（原实现把全部待结算
    决策都当买入 → AI 成绩虚高失真）。decided/not_picked 留作审计。
    """
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "picks.json"
    f.write_text(json.dumps([
        {"code": "000993", "name": "闽东电力", "view": "participate",
         "reason": "带节奏", "entry_max_gap": 3.0},
        {"code": "000823", "name": "超声电子", "view": "watch", "reason": "先看不动"},
        {"code": "603421", "name": "鼎信通讯", "view": "skip", "reason": "封板勉强"},
    ], ensure_ascii=False), encoding="utf-8")
    al.cmd_record(A(decision_date="2026-09-11", file=str(f), trade_date="2026-09-14"))
    _write_quotes(tmp_path, "2026-09-14", {
        "000993": {"prev_close": 10.0, "auction_price": 10.2},
        "000823": {"prev_close": 10.0, "auction_price": 10.1},
        "603421": {"prev_close": 10.0, "auction_price": 10.3},
    })
    monkeypatch.setattr(al, "_close_of", lambda code, d: 10.71)     # 10.2 → +5.0%
    al.cmd_settle(A(trade_date="2026-09-14"))
    r = al._read_jsonl(al.AI_LEDGER)[0]
    assert r["stock_count"] == 1 and r["decided"] == 1 and r["not_picked"] == 2
    assert [t["code"] for t in r["stocks"]] == ["000993"]
    assert abs(r["avg_return_pct"] - 5.0) < 0.01 and r["nav"] > 1.0
    # 未选中的票也须标记已结算（否则每次都当待结算反复出现）
    assert all(x.get("settled") for x in al._read_jsonl(al.DECISIONS))


def test_settle_missing_quote_honest_skip(monkeypatch, tmp_path, capsys):
    """无竞价数据 → 诚实跳过（不假设成交）；0 成交日不写账本行。"""
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "picks.json"
    f.write_text(json.dumps([{"code": "603308", "name": "应流股份", "reason": "首板魔咒观察"}]), encoding="utf-8")
    al.cmd_record(A(decision_date="2026-09-10", file=str(f)))
    _write_quotes(tmp_path, "2026-09-11", {})          # 无该票竞价
    al.cmd_settle(A(trade_date="2026-09-11"))
    assert "无竞价数据" in capsys.readouterr().out
    assert al._read_jsonl(al.AI_LEDGER) == []
    assert all(x.get("settled") for x in al._read_jsonl(al.DECISIONS))


def test_settle_idempotent(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "picks.json"
    f.write_text(json.dumps([{"code": "000759", "name": "中百集团", "reason": "零售第2高度"}]), encoding="utf-8")
    al.cmd_record(A(decision_date="2026-09-10", file=str(f)))
    _write_quotes(tmp_path, "2026-09-11", {"000759": {"prev_close": 10.0, "auction_price": 10.1}})
    monkeypatch.setattr(al, "_close_of", lambda code, d: 10.3)
    al.cmd_settle(A(trade_date="2026-09-11"))
    al.cmd_settle(A(trade_date="2026-09-11"))          # 二次调用不应重复记账
    assert len(al._read_jsonl(al.AI_LEDGER)) == 1


def test_nav_recursion_two_days(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    for d, code in (("2026-09-10", "000930"), ("2026-09-11", "002498")):
        f = tmp_path / f"p_{d}.json"
        f.write_text(json.dumps([{"code": code, "name": code, "reason": "测试理由"}]), encoding="utf-8")
        al.cmd_record(A(decision_date=d, file=str(f)))
    _write_quotes(tmp_path, "2026-09-11", {"000930": {"prev_close": 10.0, "auction_price": 10.0}})
    _write_quotes(tmp_path, "2026-09-14", {"002498": {"prev_close": 10.0, "auction_price": 10.0}})
    monkeypatch.setattr(al, "_close_of", lambda code, d: 10.0 if code == "000930" else 9.5)
    al.cmd_settle(A(trade_date="2026-09-11"))          # +0% → nav 1.0
    al.cmd_settle(A(trade_date="2026-09-14"))          # -5% → nav 0.95
    rows = al._read_jsonl(al.AI_LEDGER)
    assert [r["nav"] for r in rows] == [1.0, 0.95]


# ── 2026-09-14 修复的两处回归锁 ──────────────────────────────────────
def test_ai_view_carries_per_stock_entry_limit(monkeypatch, tmp_path):
    """⛔ 逐票 entry_max_gap 必须写进 ai_view.views（三处同值）。

    起因（2026-09-14 实证）：views 只存 view/reason/factors → `build_cards._ai_plan_block`
    取不到逐票上限 → 回落到顶层 meta 默认 3.0，卡面渲染「没买到（高开超上限+3%）」，
    而同一张卡上 AI 自己的理由写「开超2%就不买」，且与结算口径不一致
    （`cmd_settle` 读决策行的 2.0）。实证标的：闽东电力 000993（竞价+4.54%）。
    三者必须同值 = 决策台账 = 卡片视图 = 结算口径。
    """
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "picks.json"
    f.write_text(json.dumps({
        "stance_label": "只观察·最多试一只",
        "direction": "电力/绿电",
        "picks": [{"code": "000993", "name": "闽东电力", "view": "participate",
                   "reason": "3板带节奏", "entry_max_gap": 2.0}],
    }, ensure_ascii=False), encoding="utf-8")
    al.cmd_record(A(file=str(f), decision_date="2026-09-11", trade_date="2026-09-14"))
    views = json.loads(
        (tmp_path / "ai_view_2026-09-14.json").read_text(encoding="utf-8"))["views"]
    assert views["000993"]["entry_max_gap"] == 2.0, "卡片视图缺逐票上限 → 会回落 3.0"
    assert al._read_jsonl(al.DECISIONS)[0]["entry_max_gap"] == 2.0, "决策台账与视图必须同值"


def test_compare_survives_empty_ai_ledger(monkeypatch, tmp_path, capsys):
    """⛔ AI 轨尚无结算行时 compare 不得崩（原实现 NoneType 格式化 → TypeError）。

    首日/接力卡任务漏跑 settle 时都会命中该分支；compare 是「AI 能否跑赢固定脚本」的
    唯一判据，不能因为账本为空就整条命令报错。诚实显示「—」并提示原因。
    """
    _isolate(monkeypatch, tmp_path)
    assert al.cmd_compare(A(days=30)) == 0
    out = capsys.readouterr().out
    assert "AI 判断（我）" in out and "Python 脚本" in out
    assert "还没有结算记录" in out, "空账本必须给出人话提示，而不是崩掉"


# ── 🛡️ 交易闸门三条 · 赔率/周期留痕（2026-09-23）回归锁 ────────────────
# 依据 = `config/shared_rules.md`【🛡️ 交易闸门三条】：🛡️-1 赔率闸门（目标/止损 ≥ 2）·
# 🛡️-2 周期闸门（兑现周期标签 intraday / swing / mainline）。
# ⛔ 本组只锁**留痕**：三字段全可选、缺失即 None、不抛错、不阻断落盘、⛔ 不改 bias/view
#    —— 闸门靠纪律与宪法约束，代码不做硬拦（硬拦会误杀真实机会）。
def _views_of(tmp_path, trade_date):
    return json.loads(
        (tmp_path / f"ai_view_{trade_date}.json").read_text(encoding="utf-8"))["views"]


def test_record_gate_fields_passthrough(monkeypatch, tmp_path):
    """三字段原样透传： ① ai_view.views[code]（与 bias/reason 同级） ② 台账行。"""
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "picks.json"
    f.write_text(json.dumps({
        "stance_label": "只做最强的一只", "direction": "PCB板",
        "picks": [{"code": "000910", "name": "大亚圣象", "bias": "focus",
                   "reason": "4天4板纯连板", "entry_max_gap": 3.0,
                   "target_pct": 25.0, "stop_pct": 8.0, "hold_horizon": "mainline"}],
    }, ensure_ascii=False), encoding="utf-8")
    assert al.cmd_record(A(file=str(f), decision_date="2026-09-23", trade_date="2026-09-24")) == 0
    row = al._read_jsonl(al.DECISIONS)[0]
    assert row["target_pct"] == 25.0 and row["stop_pct"] == 8.0
    assert row["hold_horizon"] == "mainline"
    assert row["payoff_warning"] is None          # 25/8 = 3.13 ≥ 2 → 达标
    assert row["hold_horizon_warning"] is None
    v = _views_of(tmp_path, "2026-09-24")["000910"]
    assert v["target_pct"] == 25.0 and v["stop_pct"] == 8.0
    assert v["hold_horizon"] == "mainline"
    assert v["bias"] == "focus"                   # 留痕不得动判断
    assert v["payoff_warning"] is None and v["hold_horizon_warning"] is None


def test_record_gate_fields_missing_is_none(monkeypatch, tmp_path):
    """⛔ 缺失即 None：不做必填校验、不抛错、不阻断落盘（闸门不靠代码硬拦）。"""
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "picks.json"
    f.write_text(json.dumps([{"code": "002285", "name": "世联行", "bias": "normal",
                              "reason": "午后封板"}]), encoding="utf-8")
    assert al.cmd_record(A(file=str(f), decision_date="2026-09-23", trade_date="2026-09-24")) == 0
    row = al._read_jsonl(al.DECISIONS)[0]         # 未给闸门字段仍照常落盘
    for k in ("target_pct", "stop_pct", "payoff_warning",
              "hold_horizon", "hold_horizon_warning"):
        assert row[k] is None, f"{k} 缺失应为 None"
    v = _views_of(tmp_path, "2026-09-24")["002285"]
    assert v["target_pct"] is None and v["stop_pct"] is None and v["hold_horizon"] is None
    assert v["bias"] == "normal"


def test_record_payoff_warning_below_two_keeps_bias(monkeypatch, tmp_path):
    """🛡️-1 赔率 <2 → 出 payoff_warning；⛔ 只告警，⛔ 不改 bias / view 与 view 的终态。"""
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "picks.json"
    f.write_text(json.dumps([{"code": "603396", "name": "金辰股份", "bias": "focus",
                              "reason": "首板+10.02%", "target_pct": 6.5, "stop_pct": 5.0}]),
                 encoding="utf-8")
    assert al.cmd_record(A(file=str(f), decision_date="2026-09-23", trade_date="2026-09-24")) == 0
    row = al._read_jsonl(al.DECISIONS)[0]
    assert "赔率 1.3 不足 2" in row["payoff_warning"]
    assert "不做" in row["payoff_warning"]        # 大白话给结论
    assert row["bias"] == "focus" and row["view"] is None    # 判断原样
    v = _views_of(tmp_path, "2026-09-24")["603396"]
    assert v["bias"] == "focus" and v["view"] is None
    assert "赔率 1.3 不足 2" in v["payoff_warning"]


def test_record_payoff_warning_boundaries(monkeypatch, tmp_path):
    """边界：赔率恰好 =2 达标；止损 0 / 缺一值 → 不判且**不崩**；止损可负（按绝对值比）。"""
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "picks.json"
    f.write_text(json.dumps([
        {"code": "000001", "name": "恰好2", "bias": "normal", "reason": "r",
         "target_pct": 10.0, "stop_pct": 5.0},
        {"code": "000002", "name": "止损0", "bias": "normal", "reason": "r",
         "target_pct": 10.0, "stop_pct": 0},
        {"code": "000003", "name": "缺止损", "bias": "normal", "reason": "r",
         "target_pct": 10.0},
        {"code": "000004", "name": "负止损", "bias": "normal", "reason": "r",
         "target_pct": -5.0, "stop_pct": -3.0},
        {"code": "000005", "name": "非数", "bias": "normal", "reason": "r",
         "target_pct": "高", "stop_pct": 5.0},
    ], ensure_ascii=False), encoding="utf-8")
    assert al.cmd_record(A(file=str(f), decision_date="2026-09-23", trade_date="2026-09-24")) == 0
    rows = {r["code"]: r for r in al._read_jsonl(al.DECISIONS)}
    assert rows["000001"]["payoff_warning"] is None          # 恰好 2 → 达标
    assert rows["000002"]["payoff_warning"] is None          # 止损 0 → 不判（⛔ 不除零崩）
    assert rows["000003"]["payoff_warning"] is None          # 缺一值 → 不判
    assert "赔率 1.67 不足 2" in rows["000004"]["payoff_warning"]   # |−5 / −3|
    assert rows["000005"]["payoff_warning"] is None          # 非数 → None 不崩


def test_record_invalid_hold_horizon_warns_without_raise(monkeypatch, tmp_path):
    """🛡️-2 周期标签非法 → 原样留痕 + 告警字段；⛔ 不抛错、不阻断落盘、不改判断。"""
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "picks.json"
    f.write_text(json.dumps([{"code": "000910", "name": "大亚圣象", "bias": "focus",
                              "reason": "4天4板", "hold_horizon": "weekly"}]),
                 encoding="utf-8")
    assert al.cmd_record(A(file=str(f), decision_date="2026-09-23", trade_date="2026-09-24")) == 0
    row = al._read_jsonl(al.DECISIONS)[0]
    assert row["hold_horizon"] == "weekly"                   # 原样留痕（⛔ 不清空、不归一）
    assert "不是标准档" in row["hold_horizon_warning"]
    assert "weekly" in row["hold_horizon_warning"]
    assert row["bias"] == "focus"                            # 判断不受影响
    # 三个合法档逐一显式通过（无告警）——宪法 🛡️-2 的档位枚举即契约
    assert al._horizon_of({"hold_horizon": "intraday"}) == ("intraday", None)
    assert al._horizon_of({"hold_horizon": "swing"}) == ("swing", None)
    assert al._horizon_of({"hold_horizon": "mainline"}) == ("mainline", None)
    assert al._horizon_of({}) == (None, None)                # 缺失即 None·不告警
    assert al._horizon_of({"hold_horizon": "MAINLINE"})[1] is not None   # 大小写不归一→告警


def test_gate_warning_text_is_plain_language():
    """⛔ 宪法【🗣️ 卡片话术铁律】：新增告警文案必须大白话（零 Tier-1 阻断词）。

    直接拿**真实产出**的告警串过词表唯一源 `scripts/card_copy_rules.scan()`——
    防「代码改了、文案带黑话」在后续上卡时被 card_lint 拦死整张卡。
    """
    import importlib.util as _u
    p = Path(__file__).resolve().parents[1] / "scripts" / "card_copy_rules.py"
    spec2 = _u.spec_from_file_location("card_copy_rules", p)
    ccr = _u.module_from_spec(spec2)
    spec2.loader.exec_module(ccr)
    texts = [al._payoff_of({"target_pct": 6.5, "stop_pct": 5.0})[2],
             al._horizon_of({"hold_horizon": "weekly"})[1]]
    for t in texts:
        assert t, "该场景必须产出告警文案"
        fails, _ = ccr.scan(t)
        assert fails == [], f"告警文案含机器黑话 {fails}：{t}"


def test_settle_post_pool_call_plan_needs_frozen_verdict(monkeypatch, tmp_path, capsys):
    """9:25 模式缺冻结件时，盘后重点预案不得被结算成实际成交。"""
    _isolate(monkeypatch, tmp_path)
    al._append_jsonl(al.DECISIONS, {
        "decision_date": "2026-09-23", "code": "000001", "name": "样本股",
        "reason": "盘后重点", "bias": "focus", "view": None,
        "execution_mode": "pool_call_925", "settled": False,
    })
    _write_quotes(tmp_path, "2026-09-24", {"000001": {"prev_close": 10.0, "auction_price": 10.2}})
    monkeypatch.setattr(al, "_close_of", lambda code, d: 10.8)
    assert al.cmd_settle(A(trade_date="2026-09-24")) == 0
    assert al._read_jsonl(al.AI_LEDGER) == []
    row = al._read_jsonl(al.DECISIONS)[0]
    assert row["settled"] is True and "冻结件缺失" in row["settlement_note"]
    assert "冻结件缺失" in capsys.readouterr().out


def test_settle_post_pool_call_plan_needs_frozen_verdict(monkeypatch, tmp_path, capsys):
    """9:25 模式缺冻结件时，盘后重点预案不得被结算成实际成交。"""
    _isolate(monkeypatch, tmp_path)
    al._append_jsonl(al.DECISIONS, {
        "decision_date": "2026-09-23", "code": "000001", "name": "样本股",
        "reason": "盘后重点", "bias": "focus", "view": None,
        "execution_mode": "pool_call_925", "settled": False,
    })
    _write_quotes(tmp_path, "2026-09-24", {"000001": {"prev_close": 10.0, "auction_price": 10.2}})
    monkeypatch.setattr(al, "_close_of", lambda code, d: 10.8)
    assert al.cmd_settle(A(trade_date="2026-09-24")) == 0
    assert al._read_jsonl(al.AI_LEDGER) == []
    row = al._read_jsonl(al.DECISIONS)[0]
    assert row["settled"] is True and "冻结件缺失" in row["settlement_note"]
    assert "冻结件缺失" in capsys.readouterr().out


def _write_pool_call(tmp_path, trade_date, code, view="participate"):
    """9:25 冻结件：2026-09-21 起结算必须有它，否则不记成交（见 _requires_pool_call）。"""
    (tmp_path / f"pool_call-{trade_date}.json").write_text(
        json.dumps({"views": {code: {"view": view, "reason": "测试桩"}}}, ensure_ascii=False),
        encoding="utf-8")


def _write_kline(tmp_path, code, bars):
    """造一份 `cache/kline_qfq/<code>.json`（与真实结构同形：{schema,code,bars:[{date,open,close}]}）"""
    d = tmp_path / "kline_qfq"
    d.mkdir(exist_ok=True)
    (d / f"{code}.json").write_text(json.dumps({"schema": "kline_qfq_v1", "code": code, "bars": bars},
                                               ensure_ascii=False), encoding="utf-8")


def test_settle_stale_quote_uses_kline_open(monkeypatch, tmp_path, capsys):
    """⛔ 2026-11-03 事故回归：跨日竞价报价必须换掉，否则一天虚增 9.8pp。

    真实案例（2026-09-30 · 掌阅科技 603533）：
      auction_quotes-2026-09-30.json = {prev_close: 21.5, auction_price: 21.76, time: "2026-09-29 15:00:01"}
      ← prev_close 是 09-28 收盘、21.76 是 09-29 开盘，整条是 **09-29 的竞价**被落进了 09-30 的文件。
      旧算法：(23.88 − 21.76)/21.76 = **+9.74%**（假）
      真口径：(23.88 − 23.90)/23.90 = **−0.08%**
    """
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "picks.json"
    f.write_text(json.dumps([{"code": "603533", "name": "掌阅科技", "reason": "AI应用首板",
                              "entry_max_gap": 5.0}]), encoding="utf-8")
    al.cmd_record(A(decision_date="2026-09-29", file=str(f)))

    _write_kline(tmp_path, "603533", [
        {"date": "2026-09-28", "open": 22.16, "close": 21.50},
        {"date": "2026-09-29", "open": 21.76, "close": 23.65},
        {"date": "2026-09-30", "open": 23.90, "close": 23.88},
    ])
    # 跨日报价：time 指向 09-29，价格也是 09-29 的竞价
    _write_quotes(tmp_path, "2026-09-30", {"603533": {
        "prev_close": 21.5, "auction_price": 21.76, "time": "2026-09-29 15:00:01"}})
    _write_pool_call(tmp_path, "2026-09-30", "603533")
    monkeypatch.setattr(al, "_close_of", lambda code, d: 23.88)

    al.cmd_settle(A(trade_date="2026-09-30"))
    rows = al._read_jsonl(al.AI_LEDGER)
    assert len(rows) == 1, "换价后仍应记为已成交（交易确实发生过）"
    s = rows[0]["stocks"][0]
    assert s["auction_price"] == 23.90, "必须用当日开盘价，而不是 09-29 的 21.76"
    assert s["return_pct"] == -0.08, "真实收益是 −0.08%，不是 +9.74%"
    assert s["price_src"] == "kline_open" and s["quote_time"] == "2026-09-29 15:00:01"
    assert abs(rows[0]["nav"] - 0.9992) < 1e-4
    assert "竞价报价跨日" in capsys.readouterr().err


def test_settle_stale_quote_fixes_prev_close_for_gap(monkeypatch, tmp_path):
    """换价必须**连带换前收**：否则 (23.90 − 21.5)/21.5 = +11.2% 会被 5% 硬线误判为「高开不追」。

    正确前收是 09-29 收盘 23.65 → gap +1.06% → 该买。
    """
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "picks.json"
    f.write_text(json.dumps([{"code": "603533", "name": "掌阅科技", "reason": "AI应用首板",
                              "entry_max_gap": 5.0}]), encoding="utf-8")
    al.cmd_record(A(decision_date="2026-09-29", file=str(f)))
    _write_kline(tmp_path, "603533", [
        {"date": "2026-09-29", "open": 21.76, "close": 23.65},
        {"date": "2026-09-30", "open": 23.90, "close": 23.88},
    ])
    _write_quotes(tmp_path, "2026-09-30", {"603533": {
        "prev_close": 21.5, "auction_price": 21.76, "time": "2026-09-29 15:00:01"}})
    _write_pool_call(tmp_path, "2026-09-30", "603533")
    monkeypatch.setattr(al, "_close_of", lambda code, d: 23.88)
    al.cmd_settle(A(trade_date="2026-09-30"))
    assert len(al._read_jsonl(al.AI_LEDGER)) == 1, "前收被换成 09-29 收盘后 gap 只有 +1.06%，不该跳过"


def test_resolve_auction_keeps_clean_quote(monkeypatch, tmp_path):
    """当日报价且与开盘价一致 → 原样保留，不写 price_src（⛔ 不无谓加字段）。"""
    _isolate(monkeypatch, tmp_path)
    _write_kline(tmp_path, "600000", [{"date": "2026-09-30", "open": 10.0, "close": 10.5}])
    res = al._resolve_auction("600000", "2026-09-30",
                              {"prev_close": 9.9, "auction_price": 10.0, "time": "2026-09-30 09:25:00"})
    assert res["ap"] == 10.0 and res["src"] == "auction_quote" and res["note"] is None


def test_resolve_auction_flags_big_deviation_without_time(monkeypatch, tmp_path):
    """没有 time 的老报价：靠「与开盘价偏差 > 5%」兜底（异常幅度远超除权可解释范围）。"""
    _isolate(monkeypatch, tmp_path)
    _write_kline(tmp_path, "600000", [{"date": "2026-09-30", "open": 10.0, "close": 10.1}])
    res = al._resolve_auction("600000", "2026-09-30", {"prev_close": 9.9, "auction_price": 8.5})
    assert res["src"] == "kline_open" and res["ap"] == 10.0
