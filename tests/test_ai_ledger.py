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
