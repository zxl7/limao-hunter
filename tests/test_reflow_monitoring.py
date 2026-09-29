# -*- coding: utf-8 -*-
"""资金回流与题材回流分层：资金只是预警，扩散和核心票响应才确认。"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("live_signal_reflow_monitor", ROOT / "scripts" / "live_signal.py")
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)


def _sector(*, delta="surging", zt=2, pct=5.2):
    return {"name": "机器人", "delta": delta, "zt_count": zt,
            "leader": {"name": "核心股", "pct": pct}}


def test_price_confirmation_needs_active_sector_diffusion_and_core_strength():
    ok = ls._reflow_price_evidence("机器人", [_sector()])
    assert ok["confirmed"] is True

    assert ls._reflow_price_evidence("机器人", [_sector(zt=1)])["confirmed"] is False
    assert ls._reflow_price_evidence("机器人", [_sector(pct=4.9)])["confirmed"] is False
    assert ls._reflow_price_evidence("机器人", [_sector(delta="ongoing")])["confirmed"] is False


def test_no_current_top5_match_is_not_a_failure():
    evidence = ls._reflow_price_evidence("机器人", [{"name": "半导体"}])
    assert evidence == {"matched": False, "confirmed": False}


def test_short_generic_sector_name_does_not_confirm_specific_theme():
    evidence = ls._reflow_price_evidence("电子布", [{"name": "电子", "delta": "surging", "zt_count": 3,
                                                    "leader": {"name": "X", "pct": 8}}])
    assert evidence["matched"] is False


def test_reflow_card_groups_monitoring_stages():
    card = ls.build_reflow_card([
        {"theme": "机器人", "_reflow_stage": "题材确认", "_reflow_price": {"sector": "机器人", "zt_count": 2,
            "leader": {"name": "核心股"}, "leader_pct": 5.2}, "watch_pool": []},
        {"theme": "半导体", "_reflow_stage": "资金试探", "_reflow_live_hits": [], "watch_pool": []},
        {"theme": "光模块", "_reflow_stage": "回流失效", "_reflow_fail_reason": "资金转弱", "watch_pool": []},
    ], "10:30")
    text = card["elements"][0]["text"]["content"]
    assert "题材确认" in text and "资金试探" in text and "回流失效" in text
