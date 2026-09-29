# -*- coding: utf-8 -*-
"""短线精灵·有界可靠性修复回归锁（2026-09-17）。

⛔ 仅锁「无需业务阈值」的可靠性修复，不触碰 batch/force/选股/过滤/调度业务：
  1. _send_card：dry-run 不得写下内容去重；实际发送失败不_commit dedup；
     仅「实际发送成功」才写 dedup（45s 防抖不再误拦失败卡的重发）。
  2. main push 模式：入池后、任何发送尝试之前可靠 save_pushed(update_last=False)；
     发送失败 → pending 已持久化、codes/last_push 不登记 → 下一轮可重试。
  3. build_signals 观测状态（当下性硬门/轮快照）只过滤内存 signals，不触碰
     PUSHED_PATH["pending"]；故即使下一轮 signals 为空，已持久化 pending 仍可重试。

隔离约定：
  - 所有文件状态落在 pytest tmp_path（不写真实 cache/生产文件）。
  - mock 网络/系统调用（os.system 直接替换返回值），不调用真实 push_card、
    不访问实时接口、不推送任何群、不改配置/服务/automations。
  - 跨系统 push_card.py 的发送前去重属只读报告项，本测试不修改它。
"""
import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("short_spirit_rel", SCRIPTS / "short_spirit.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


def _sig(code, typ="涨停", name="测试股"):
    return {"name": name, "code": code, "sector": "S", "market_cap_yi": 50.0,
            "amount_yi": 3.0, "pct": 10.0, "type": typ, "tags": ["大单流入"],
            "resonance": 90, "flow_yi": 1.0, "strength": 8.0, "reason": "共振[X]"}


def _card_content_hash(card: dict) -> str:
    return hashlib.sha1(json.dumps(card, ensure_ascii=False).encode()).hexdigest()


def _fake_cfg():
    return {"min_pct": 5.0, "min_market_cap_yi": 30, "amount_morning_yi": 3.0,
            "amount_afternoon_yi": 2.0, "batch_threshold": 999,
            "batch_timeout_min": 30, "max_signals": 5, "weight": {}}


# ─────────────────────────── 1. _send_card 去重时序 ───────────────────────────
def test_send_card_dry_does_not_write_dedup(tmp_path, monkeypatch):
    """dry-run 仅预览，不得写下内容去重。"""
    monkeypatch.setattr(ss, "ROOT", tmp_path)
    (tmp_path / "cache").mkdir()
    monkeypatch.setattr(ss, "in_trading", lambda: True)
    monkeypatch.setattr(os, "system", lambda *a, **k: 0)  # 不应被调用
    card = {"title": "短线精灵·测试", "color": "red", "elements": [{"tag": "div",
              "text": {"tag": "lark_md", "content": "dry 内容"}}]}
    cf = tmp_path / "card.json"
    cf.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    rc = ss._send_card(str(cf), dry=True)
    assert rc == 0
    dp = ss._send_dedup_path()
    assert not dp.exists(), "dry-run 不应创建/写入去重文件"


def test_send_card_failure_does_not_commit_dedup(tmp_path, monkeypatch):
    """实际发送失败（rc!=0）→ 不_commit dedup，便于下一轮重试。"""
    monkeypatch.setattr(ss, "ROOT", tmp_path)
    (tmp_path / "cache").mkdir()
    monkeypatch.setattr(ss, "in_trading", lambda: True)
    monkeypatch.setattr(os, "system", lambda *a, **k: 1)  # 模拟推送失败
    card = {"title": "短线精灵·测试失败", "color": "red", "elements": [{"tag": "div",
              "text": {"tag": "lark_md", "content": "失败内容"}}]}
    cf = tmp_path / "card.json"
    cf.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    rc = ss._send_card(str(cf), dry=False)
    assert rc == 1, "失败应返回 1"
    dp = ss._send_dedup_path()
    # 失败路径：当前实现仅在发送成功时提交去重（short_spirit.py:524-528），
    # 失败时不写/不更新去重文件。核心保证是「该内容 hash 不被登记为已推」——
    # 文件是否创建无关紧要，故此处改为：若文件存在则 hash 必不在其中。
    if dp.exists():
        ded = json.loads(dp.read_text(encoding="utf-8"))
        assert _card_content_hash(card) not in ded, "失败不应把卡记成已推"


def test_send_card_success_commits_dedup(tmp_path, monkeypatch):
    """实际发送成功（rc==0）→ 提交内容去重。"""
    monkeypatch.setattr(ss, "ROOT", tmp_path)
    (tmp_path / "cache").mkdir()
    monkeypatch.setattr(ss, "in_trading", lambda: True)
    monkeypatch.setattr(os, "system", lambda *a, **k: 0)  # 模拟推送成功
    card = {"title": "短线精灵·测试成功", "color": "red", "elements": [{"tag": "div",
              "text": {"tag": "lark_md", "content": "成功内容"}}]}
    cf = tmp_path / "card.json"
    cf.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    rc = ss._send_card(str(cf), dry=False)
    assert rc == 0, "成功应返回 0"
    dp = ss._send_dedup_path()
    ded = json.loads(dp.read_text(encoding="utf-8"))
    assert _card_content_hash(card) in ded, "成功应把卡记入去重"


def test_send_card_dry_then_real_send_still_allowed(tmp_path, monkeypatch):
    """dry 不写 dedup → 之后真实发送不被 45s 防抖误拦（重发应当成功）。"""
    monkeypatch.setattr(ss, "ROOT", tmp_path)
    (tmp_path / "cache").mkdir()
    monkeypatch.setattr(ss, "in_trading", lambda: True)
    calls = {"n": 0}
    def fake_system(*a, **k):
        calls["n"] += 1
        return 0  # 真实发送成功
    monkeypatch.setattr(os, "system", fake_system)
    card = {"title": "短线精灵·dry后发", "color": "red", "elements": [{"tag": "div",
              "text": {"tag": "lark_md", "content": "同一内容"}}]}
    cf = tmp_path / "card.json"
    cf.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    assert ss._send_card(str(cf), dry=True) == 0   # dry 不写 dedup
    assert ss._send_card(str(cf), dry=False) == 0  # 真实发送应成功（未被防抖拦）
    assert calls["n"] == 1, "真实发送应实际调用一次推送"


# ─────────────────────────── 2. main push 失败保留 pending ───────────────────────────
def _setup_main_env(tmp_path, monkeypatch, signals):
    monkeypatch.setattr(ss, "ROOT", tmp_path)
    (tmp_path / "cache").mkdir()
    snap = tmp_path / "latest.json"
    snap.write_text(json.dumps({"top5": [], "boards": {}}), encoding="utf-8")
    monkeypatch.setattr(ss, "SNAP", snap)
    monkeypatch.setattr(ss, "PUSHED_PATH", tmp_path / "pushed.json")
    monkeypatch.setattr(ss, "OUT_PATH", tmp_path / "out.json")
    monkeypatch.setattr(ss, "in_trading", lambda: True)
    monkeypatch.setattr(ss, "load_cfg", lambda: _fake_cfg())
    monkeypatch.setattr(ss, "build_signals",
                       lambda d, now, cfg: (signals, "morning", len(signals), len(signals)))
    # ⛔ 下跌支路（2026-09-21 新增）也必须 mock —— 否则 main() 会去抓**真实跌幅榜**（触网）。
    #    本文件只用 main() 的推送簿记，与向下的选股无关。
    monkeypatch.setattr(ss, "build_signals_down",
                        lambda d, now, cfg: ([], "morning", 0, 0))
    return tmp_path / "pushed.json"


def _run_main_push(monkeypatch, send_rc=0):
    calls = {"n": 0}
    def fake_system(*a, **k):
        calls["n"] += 1
        return send_rc
    monkeypatch.setattr(os, "system", fake_system)
    monkeypatch.setattr(sys, "argv", ["short_spirit.py", "--push"])
    ss.main()
    return calls["n"]


def test_main_push_failure_keeps_pending(tmp_path, monkeypatch):
    """发送失败：pending 已落盘、codes/last_push 不登记。"""
    pp = _setup_main_env(tmp_path, monkeypatch, [_sig("600103"), _sig("600104")])
    n = _run_main_push(monkeypatch, send_rc=1)
    assert n == 1, "应发起一次发送尝试"
    pushed = json.loads(pp.read_text(encoding="utf-8"))
    assert len(pushed["pending"]) == 2, "失败仍应保留全部 pending"
    assert pushed["codes"] == [], "失败不应登记 codes"
    assert pushed["last_push"] == "", "失败不应刷新 last_push"


def test_main_push_success_registers_codes(tmp_path, monkeypatch):
    """发送成功：codes 登记、pending 结算、last_push 刷新（无回归）。"""
    pp = _setup_main_env(tmp_path, monkeypatch, [_sig("600103")])
    n = _run_main_push(monkeypatch, send_rc=0)
    assert n == 1
    pushed = json.loads(pp.read_text(encoding="utf-8"))
    assert pushed["codes"] == ["600103"], "成功应登记 code"
    assert pushed["pending"] == [], "成功应清空已发 pending"
    assert pushed["last_push"] != "", "成功应刷新 last_push"


def test_main_pending_retry_on_empty_signals(tmp_path, monkeypatch):
    """跨轮回归（issue 4）：即使下一轮 signals 为空，已持久化 pending 仍可重试。"""
    pp = _setup_main_env(tmp_path, monkeypatch, [_sig("600103"), _sig("600104")])
    # 第一轮：有信号，发送失败 → pending 落盘
    n1 = _run_main_push(monkeypatch, send_rc=1)
    assert n1 == 1
    after_fail = json.loads(pp.read_text(encoding="utf-8"))
    assert len(after_fail["pending"]) == 2

    # 第二轮：signals 为空，但 pending 已从文件加载，超时兜底触发重发
    monkeypatch.setattr(ss, "build_signals",
                       lambda d, now, cfg: ([], "morning", 0, 0))
    n2 = _run_main_push(monkeypatch, send_rc=0)
    assert n2 == 1, "空 signals 轮次仍应基于持久化 pending 重发"
    after_retry = json.loads(pp.read_text(encoding="utf-8"))
    assert after_retry["codes"] == ["600103", "600104"], "重发成功应登记 code"
    assert after_retry["pending"] == [], "重发成功应清空 pending"


def test_main_pending_saved_before_send_not_after_only(tmp_path, monkeypatch):
    """即使在 save 之后、os.system 之前进程崩溃，pending 也已落盘（发送前保存的可靠性）。"""
    pp = _setup_main_env(tmp_path, monkeypatch, [_sig("600103")])
    # 模拟发送前保存已完成，但 os.system 抛异常（模拟崩溃）——这里用 send_rc 不影响，
    # 关键是调用顺序：save_pushed(update_last=False) 必须在 os.system 之前。
    # 通过断言 os.system 被调用时文件已含 pending 来验证时序。
    order = []
    real_system = os.system
    def spy_system(*a, **k):
        # 在真正发送的那一刻，文件里应当已经写好了 pending
        if pp.exists():
            order.append(json.loads(pp.read_text(encoding="utf-8")).get("pending"))
        return 0
    monkeypatch.setattr(os, "system", spy_system)
    monkeypatch.setattr(sys, "argv", ["short_spirit.py", "--push"])
    ss.main()
    assert order, "应至少记录一次发送时刻的文件快照"
    assert len(order[0]) == 1, "发送前 pending 应已落盘（含 600103）"
