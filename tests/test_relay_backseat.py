# -*- coding: utf-8 -*-
"""🎯 接力方向·真后排池（2026-10-20 用户「推的票都涨停了 没意义」）回归锁。

病灶：旧 `tradable` 只从 `latest.leaders` 取（连板龙头/人气榜，常年已封板），
再加 `RELAY_HINT_MIN_PCT=7.5` 门槛 → 低空经济这种「2 家涨停、其余没动」的板块必然全空，
卡面只剩买不进的前排 → 对用户等于无信息。

修法：取该板块**东财 BK 成分涨幅榜**（一次请求），按短线精灵后排三判据筛：
  ① 已启动未封（3% ≤ pct < 涨停线−0.2）
  ② 放量不爆量（量比 1.3 ~ 15；缺量比放行）
  ③ **不断板**（昨日涨停、今日没封 = 高度衰减，⛔ 不算后排可买）
排除：ST / 688 / 北交所 920·8x·4x / 黑名单 / 本轮已推 / 已在涨停清单。

铁律（本文件锁死）：
1. 涨到封板的票 **绝不** 出现在「后排可参与」里（本次核心）
2. 昨日涨停今日没封的票（断板）**绝不**出现
3. 北交所/科创板/ST 一律不出现（用户无权限 / 不看 ST）
4. 东财接口失败 → 退回旧 leaders 路径，⛔ 不抛异常、不空卡
5. 开关 `ENABLE_SPIRIT_RELAY_BACKSEAT=False` → 完全不联网
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("short_spirit_backseat", SCRIPTS / "short_spirit.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


# ── 造数据 ────────────────────────────────────────────────

def _row(code, name, pct, vr=5.0, amt=3.0, turn=1.0):
    return {"f12": code, "f14": name, "f3": pct, "f10": vr, "f6": amt * 1e8, "f8": turn}


def _resp(rows):
    return {"data": {"diff": rows}}


@pytest.fixture
def fake_board(monkeypatch):
    """默认：低空经济真实形态（东安动力封板除外仍列出 → 必须被过滤）。"""
    rows = [
        _row("600178", "东安动力", 10.02, 18.4, 0.63, 1.1),   # 封板 → 必须剔除
        _row("603197", "保隆科技", 4.24, 95.0, 4.50, 6.2),    # 爆量 → 剔除
        _row("000559", "万向钱潮", 3.43, 14.0, 1.44, 0.4),    # 入选
        _row("301248", "杰创智能", 3.33, 6.9, 0.49, 0.9),     # 入选
        _row("920493", "并行科技", 5.16, 13.2, 0.28, 0.7),    # 北交所 → 剔除
        _row("688507", "索辰科技", 3.70, 23.8, 4.10, 5.7),    # 科创 → 剔除
        _row("002886", "沃特股份", 1.54, 14.0, 2.51, 5.0),    # 涨幅不足 → 剔除
        _row("600212", "绿能慧充", 0.75, 11.8, 0.11, 0.3),    # 没动 → 剔除
        _row("002146", "荣盛发展", 8.80, 12.0, 1.00, 3.0),    # 昨板今断 → 剔除
    ]
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_RELAY_BACKSEAT", True)
    monkeypatch.setattr(ss, "http_get", lambda url, t=8: _resp(rows))
    monkeypatch.setattr(ss, "_yzt_codes", lambda date_str="": {"002146"})
    return rows


def test_backseat_picks_unsealed_movers(fake_board):
    out = ss._board_backseat("BK1166", {"600178"}, set(), 2)
    codes = [x["code"] for x in out]
    assert codes == ["000559", "301248"], out
    assert out[0]["name"] == "万向钱潮"


def test_sealed_never_in_backseat(fake_board):
    """① 核心铁律：封住的票不进「后排可参与」。"""
    out = ss._board_backseat("BK1166", {"600178"}, set(), 2)
    assert "600178" not in [x["code"] for x in out]


def test_broken_board_excluded(fake_board):
    """③ 昨板今断（002146 +8.8%）不得入选。"""
    out = ss._board_backseat("BK1166", set(), set(), 5)
    assert "002146" not in [x["code"] for x in out]


def test_north_and_star_excluded(fake_board):
    out = ss._board_backseat("BK1166", set(), set(), 9)
    codes = [x["code"] for x in out]
    assert "920493" not in codes and "688507" not in codes


def test_burst_volume_excluded(fake_board):
    """量比 95（爆量）剔除；缺量比则放行。"""
    out = ss._board_backseat("BK1166", set(), set(), 9)
    assert "603197" not in [x["code"] for x in out]
    assert all(x["vol_ratio"] <= ss.RELAY_BACKSEAT_VOL_MAX for x in out)


def test_missing_vol_ratio_passes(monkeypatch):
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_RELAY_BACKSEAT", True)
    monkeypatch.setattr(ss, "http_get", lambda url, t=8: _resp([
        {"f12": "000559", "f14": "万向钱潮", "f3": 3.4, "f6": 1e8},
    ]))
    monkeypatch.setattr(ss, "_yzt_codes", lambda date_str="": set())
    out = ss._board_backseat("BK1166", set(), set(), 2)
    assert [x["code"] for x in out] == ["000559"]


def test_network_failure_returns_empty(monkeypatch):
    """⛔ 东财限频/断网 → 空列表，不抛异常（盘中链路安全第一）。"""
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_RELAY_BACKSEAT", True)

    def boom(url, t=8):
        raise RuntimeError("RemoteDisconnected")

    monkeypatch.setattr(ss, "http_get", boom)
    assert ss._board_backseat("BK1166", set(), set(), 2) == []


def test_switch_off_never_calls_network(monkeypatch):
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_RELAY_BACKSEAT", False)
    called = []
    monkeypatch.setattr(ss, "http_get", lambda url, t=8: called.append(url) or _resp([]))
    assert ss._board_backseat("BK1166", set(), set(), 2) == []
    assert called == []


def test_pushed_codes_excluded(fake_board):
    """本轮已推过的票不重复出现在后排。"""
    out = ss._board_backseat("BK1166", set(), {"000559"}, 2)
    assert [x["code"] for x in out] == ["301248"]


# ── 与 _relay_hint / 卡面联动 ─────────────────────────────

def _d():
    return {
        "top5": [{"name": "低空经济", "zt_count": 2,
                  "zt_stocks": [{"name": "吉大正元", "code": "003029.SZ", "pct": 10.0},
                                {"name": "东安动力", "code": "600178.SS", "pct": 10.02}]}],
        "limit_up_stocks": [
            {"name": "东安动力", "code": "600178.SS", "sector": "低空经济", "boards": "3天3板", "pct": 10.02},
            {"name": "吉大正元", "code": "003029.SZ", "sector": "低空经济", "boards": "4天4板", "pct": 10.0},
        ],
        "leaders": [],
    }


def test_relay_hint_uses_board_backseat(monkeypatch, fake_board):
    monkeypatch.setattr(ss, "_official_tag", lambda sec: "(BK1166)")
    monkeypatch.setattr(ss, "_relay_sector_of", lambda sigs, d: "低空经济")
    sig = {"name": "东安动力", "code": "600178", "sector": "低空经济", "pct": 10.02,
           "type": "涨停", "market_cap_yi": 58, "amount_yi": 0.6, "tags": [], "strength": 0.9}
    relay = ss._relay_hint([sig], _d())
    assert relay is not None
    assert relay["backseat_src"] == "board"
    assert relay["backseat_bk"] == "BK1166"
    codes = [x["code"] for x in relay["tradable"]]
    assert codes == ["000559", "301248"]


def test_relay_hint_falls_back_to_leaders(monkeypatch):
    """板块 BK 取不到 → 退回旧 leaders 路径（行为与改动前一致）。"""
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_RELAY_BACKSEAT", True)
    monkeypatch.setattr(ss, "http_get", lambda url, t=8: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(ss, "_official_tag", lambda sec: "(BK1166)")
    monkeypatch.setattr(ss, "_relay_sector_of", lambda sigs, d: "低空经济")
    d = _d()
    d["leaders"] = [{"name": "万向钱潮", "code": "000559", "sector": "低空经济", "pct": 8.0}]
    sig = {"name": "东安动力", "code": "600178", "sector": "低空经济", "pct": 10.02,
           "type": "涨停", "market_cap_yi": 58, "amount_yi": 0.6, "tags": [], "strength": 0.9}
    relay = ss._relay_hint([sig], d)
    assert relay["backseat_src"] == "leaders"
    assert [x["code"] for x in relay["tradable"]] == ["000559"]


def test_relay_hint_no_bk_falls_back(monkeypatch):
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_RELAY_BACKSEAT", True)
    monkeypatch.setattr(ss, "_official_tag", lambda sec: "")      # 无官方码
    monkeypatch.setattr(ss, "_relay_sector_of", lambda sigs, d: "低空经济")
    d = _d()
    d["leaders"] = [{"name": "万向钱潮", "code": "000559", "sector": "低空经济", "pct": 8.0}]
    sig = {"name": "东安动力", "code": "600178", "sector": "低空经济", "pct": 10.02,
           "type": "涨停", "market_cap_yi": 58, "amount_yi": 0.6, "tags": [], "strength": 0.9}
    relay = ss._relay_hint([sig], d)
    assert relay["backseat_src"] == "leaders"


def test_card_shows_unsealed_label():
    """真后排池 → 卡面标「未封板·现在能买」并带换手。"""
    relay = {
        "sector": "低空经济", "zt_count": 2,
        "front": {"name": "吉大正元", "code": "003029", "boards": "4天4板", "pct": 10.0},
        "front_in_signal": False, "signal_in_ladder": True,
        "backseat_src": "board",
        "tradable": [{"name": "万向钱潮", "code": "000559", "pct": 3.43, "turnover": 0.43},
                     {"name": "杰创智能", "code": "301248", "pct": 3.33, "turnover": 0.87}],
        "ladder": [],
    }
    sig = {"name": "东安动力", "code": "600178", "sector": "低空经济", "pct": 10.02, "type": "涨停",
           "market_cap_yi": 58, "amount_yi": 0.6, "tags": [], "strength": 0.9,
           "resonance": 100, "flow_yi": 0, "reason": ""}
    card = ss.build_card([sig], "morning", ss.datetime(2026, 10, 20, 10, 30), relay=relay)
    body = "\n".join((el.get("text") or {}).get("content", "") for el in card["elements"])
    assert "🎯 后排可参与（未封板·现在能买）" in body
    assert "万向钱潮(000559)" in body and "换手0.4%" in body
    assert "杰创智能(301248)" in body


def test_card_empty_backseat_message():
    relay = {
        "sector": "低空经济", "zt_count": 2,
        "front": {"name": "吉大正元", "code": "003029", "boards": "4天4板", "pct": 10.0},
        "front_in_signal": False, "signal_in_ladder": True,
        "backseat_src": "", "tradable": [], "ladder": [],
    }
    sig = {"name": "东安动力", "code": "600178", "sector": "低空经济", "pct": 10.02, "type": "涨停",
           "market_cap_yi": 58, "amount_yi": 0.6, "tags": [], "strength": 0.9,
           "resonance": 100, "flow_yi": 0, "reason": ""}
    card = ss.build_card([sig], "morning", ss.datetime(2026, 10, 20, 10, 30), relay=relay)
    body = "\n".join((el.get("text") or {}).get("content", "") for el in card["elements"])
    assert "该方向已无未封板票" in body
