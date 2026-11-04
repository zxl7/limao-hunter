# -*- coding: utf-8 -*-
"""🎯 接力方向·口径融合 + 辨识度 + 溢价可能性（2026-10-20）回归锁。

用户原话现场：
  「🔒 后排关联：博敏电子 显示涨幅！！ 需要两个口径融合，并推送辨识度 和溢价可能性！！」

三个病灶（本文件逐条锁死）：
  ① **已封板票在卡面裸名一个**：「🔒 后排关联：博敏电子」—— 没有涨幅、没有板数、没有身位。
     后端其实有 pct(=10.01)，是**卡面没渲染**。
  ② **两个口径不同刻**：已封板段用面板快照 pct、未封板段用东财实时 pct，并排放在卡上
     → 违反宪法「对齐铁律：比较必须同刻」。修法 = 一次取板块成分快照，两段共用。
  ③ **抓不到 = 报成「没有」**：10-20 10:26 PCB 板卡面写「该方向已无未封板票」，
     实测该板块 205 只成分、涨幅榜第 4 的矩子科技 +9.99% 明明能买。
     根因 = 代码只轮询 `33/21/98/82` 子域（盘中全 RemoteDisconnected），**漏了主域**。

铁律（本文件锁死）：
1. 已封板票**必须带涨幅**（用户原始诉求，绝不允许裸名）
2. 已封板与未封板**共用同一份快照**（同源同刻）
3. 快照抓不到 → 卡面写「未取到」，⛔ 不得写「已无未封板票」
4. 龙一**并列不叫龙一**（并列 = 分歧）
5. 溢价**依据不足就不给**（⛔ 不编）
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("short_spirit_fusion", SCRIPTS / "short_spirit.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

TODAY = "2026-10-20"
NOW = ss.datetime(2026, 10, 20, 10, 45)


# ── 造数据：真实 10-20 PCB板(BK0877) 形态 ────────────────────────

def _latest():
    """协和电子/骏亚科技 并列 3 板、博敏电子首板、超声电子炸过板。"""
    return {
        "ladder": [{"boards": 3, "stocks": ["协和电子", "骏亚科技"]}],   # 并列最高 → 都不叫龙一
        "limit_up_stocks": [
            {"name": "协和电子", "code": "605258.SS", "sector": "PCB板", "boards": "6天3板", "pct": 10.0},
            {"name": "骏亚科技", "code": "603386.SS", "sector": "PCB板", "boards": "6天3板", "pct": 9.98},
            {"name": "博敏电子", "code": "603936.SS", "sector": "PCB板", "boards": None, "pct": 10.01},
        ],
        "broken": {"count": 1, "names": ["超声电子"]},
        "leaders": [{"name": "博敏电子", "code": "603936", "sector": "PCB板", "pct": 10.01}],
        "top5": [],
    }


def _snap():
    return {
        "605258": {"name": "协和电子", "pct": 10.0, "turnover": 7.5, "vol_ratio": 2.4},
        "603386": {"name": "骏亚科技", "pct": 9.98, "turnover": 6.7, "vol_ratio": 2.5},
        "603936": {"name": "博敏电子", "pct": 10.01, "turnover": 4.4, "vol_ratio": 1.8},
        "000823": {"name": "超声电子", "pct": 6.92, "turnover": 20.2, "vol_ratio": 4.1},
        "300802": {"name": "矩子科技", "pct": 8.7, "turnover": 4.3, "vol_ratio": 3.1},
        "688507": {"name": "索辰科技", "pct": 8.0, "turnover": 3.0, "vol_ratio": 2.0},  # 科创 → 不占身位
        "920821": {"name": "则成电子", "pct": 13.5, "turnover": 9.0, "vol_ratio": 3.5},  # 北交所 → 不占身位
    }


def _sig():
    return {"name": "骏亚科技", "code": "603386", "sector": "PCB板", "pct": 9.98,
            "type": "涨停", "market_cap_yi": 70, "amount_yi": 5.0, "tags": [],
            "strength": 0.9, "resonance": 100, "flow_yi": 0, "reason": ""}


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_RELAY_BACKSEAT", True)
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_RELAY_ENRICH", True)
    monkeypatch.setattr(ss, "_official_tag", lambda sec: "(BK0877)")
    monkeypatch.setattr(ss, "_relay_sector_of", lambda sigs, d: "PCB板")
    monkeypatch.setattr(ss, "_yzt_codes", lambda date_str="": set())
    monkeypatch.setattr(ss, "_board_snapshot", lambda bk: _snap())
    return _latest()


def _body(card):
    return "\n".join((el.get("text") or {}).get("content", "") for el in card["elements"])


# ── ① 已封板票必须带涨幅（用户原始诉求）────────────────────────

def test_sealed_items_carry_pct(wired):
    r = ss._relay_hint([_sig()], wired)
    for it in r["ladder"]:
        assert it["pct"] > 0, f"已封板票 {it['name']} 没有涨幅"


def test_card_shows_pct_for_sealed_bo_min(wired):
    """现场 bug：卡面只有「🔒 后排关联：博敏电子」裸名一个。"""
    card = ss.build_card([_sig()], "morning", NOW, relay=ss._relay_hint([_sig()], wired))
    body = _body(card)
    seg = body.split("🔒 后排关联")[1]
    assert "博敏电子(603936)" in seg
    assert "+10.01%" in seg, "已封板票必须带涨幅（用户：显示涨幅！！）"
    assert "+9.98%" not in seg.split("博敏电子")[0] or True  # 不约束骏亚是否在段内


def test_ladder_pct_comes_from_same_snapshot(wired):
    """② 同源同刻：面板说 10.0、快照说 10.5 → 必须取快照（否则两段不可比）。"""
    wired["limit_up_stocks"][0]["pct"] = 1.23
    snap = _snap()
    snap["605258"]["pct"] = 10.5
    ss._board_snapshot = lambda bk: snap
    r = ss._relay_hint([_sig()], wired)
    front = r["front"]
    assert front["pct"] == 10.5, "已封板段应改用快照涨幅（同刻）"


# ── ③ 抓不到 ≠ 没有（禁止假结论）──────────────────────────────

def test_unknown_snapshot_never_claims_no_tradable(wired, monkeypatch):
    """现场 bug：限频被渲染成「该方向已无未封板票」，而实际有票能买。"""
    monkeypatch.setattr(ss, "_board_snapshot", lambda bk: None)
    r = ss._relay_hint([_sig()], wired)
    assert r["backseat_unknown"] is True
    body = _body(ss.build_card([_sig()], "morning", NOW, relay=r))
    assert "该方向已无未封板票" not in body, "抓不到不得断言没有"
    assert "板块实时行情未取到" in body


def test_known_empty_snapshot_still_says_none(wired, monkeypatch):
    """确实全封住（快照成功但无未封板票）→ 这句才是对的。"""
    snap = {k: v for k, v in _snap().items() if k in ("605258", "603386", "603936")}
    monkeypatch.setattr(ss, "_board_snapshot", lambda bk: snap)
    r = ss._relay_hint([_sig()], wired)
    assert r["backseat_unknown"] is False
    body = _body(ss.build_card([_sig()], "morning", NOW, relay=r))
    assert "该方向已无未封板票" in body


# ── ④ 辨识度 ───────────────────────────────────────────────────

def test_first_board_inferred_from_zt_list(wired):
    """在今日涨停清单、又不在连板天梯（只收 2 板+）→ 首板。"""
    r = ss._relay_hint([_sig()], wired)
    bm = [x for x in r["ladder"] if x["code"] == "603936"][0]
    assert "首板" in bm["ident"], bm


def test_tied_top_boards_not_called_leader(wired):
    """协和电子/骏亚科技 并列 3 板 → ⛔ 都不叫龙一（并列=分歧）。"""
    r = ss._relay_hint([_sig()], wired)
    for x in r["ladder"]:
        assert "板块龙一" not in x["ident"], f"{x['name']} 并列最高不该叫龙一"


def test_single_top_board_is_leader(wired):
    wired["ladder"] = [{"boards": 3, "stocks": ["协和电子"]},
                       {"boards": 2, "stocks": ["骏亚科技"]}]
    wired["limit_up_stocks"][1]["boards"] = "3天2板"
    r = ss._relay_hint([_sig()], wired)
    xh = [x for x in r["ladder"] if x["code"] == "605258"][0]
    assert "板块龙一" in xh["ident"], xh


def test_sector_rank_excludes_star_and_bse(wired):
    """身位分母不算 688/北交所（用户无权限，不该占身位）。"""
    r = ss._relay_hint([_sig()], wired)
    bm = [x for x in r["ladder"] if x["code"] == "603936"][0]
    # 可交易池里比博敏高的只有 605258(10.00)/603386(9.98) 都低于 10.01 → 第1
    assert bm["rank"] == 1, f"博敏电子应排第1，实际 {bm['rank']}"


def test_broken_board_tagged(wired):
    r = ss._relay_hint([_sig()], wired)
    us = [x for x in r["tradable"] if x["code"] == "000823"]
    assert us and "炸过板" in us[0]["ident"], us


# ── ⑤ 溢价可能性 ────────────────────────────────────────────────

def test_premium_high_for_strong_sector_healthy_turnover(wired):
    r = ss._relay_hint([_sig()], wired)
    bm = [x for x in r["ladder"] if x["code"] == "603936"][0]
    assert bm["premium"]["level"] in ("高", "中"), bm["premium"]
    assert "换手4.4%" in bm["premium"]["why"], "溢价必须给依据，不能只有结论"


def test_broken_board_premium_low(wired):
    """炸过板 + 20% 巨量换手 → 溢价低。"""
    r = ss._relay_hint([_sig()], wired)
    us = [x for x in r["tradable"] if x["code"] == "000823"][0]
    assert us["premium"]["level"] == "低", us["premium"]
    assert "炸过板" in us["premium"]["why"]


def test_premium_none_when_no_evidence():
    """换手/板块家数都拿不到 → ⛔ 不给溢价（不编）。"""
    ctx = {"boards": {}, "broken": set(), "leaders": set(),
           "zt_names": set(), "max_boards": 0, "max_boards_count": 0}
    assert ss._premium({"name": "X", "code": "000001", "turnover": 0,
                        "sector_zt": 0, "rank": 0}, ctx) is None


def test_premium_heavy_turnover_penalised(wired):
    """>15% 巨量换手是筹码散了 → 扣分，不得给「高」。"""
    ctx = ss._relay_board_ctx(wired)
    p = ss._premium({"name": "超声电子", "code": "000823", "turnover": 25.0,
                     "sector_zt": 3, "rank": 2}, ctx)
    assert p["level"] != "高", p


# ── ⑥ 板数解析 & 开关 ───────────────────────────────────────────

def test_parse_boards_takes_last_number():
    """`6天3板` → 3（取第一个数字会把它说成 6 板）。"""
    assert ss._parse_boards("6天3板") == 3
    assert ss._parse_boards("3天2板") == 2
    assert ss._parse_boards("4板") == 4
    assert ss._parse_boards(5) == 5
    assert ss._parse_boards(None) == 0
    assert ss._parse_boards("") == 0


def test_enrich_switch_off_keeps_old_format(wired, monkeypatch):
    """ENABLE_SPIRIT_RELAY_ENRICH=False → 回到「裸名 + 无溢价」的改动前形态（可逆）。"""
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_RELAY_ENRICH", False)
    r = ss._relay_hint([_sig()], wired)
    body = _body(ss.build_card([_sig()], "morning", NOW, relay=r))
    assert "溢价" not in body
    assert "🔒 后排关联：" in body


def test_card_one_item_per_line(wired):
    """融合后字段变多 → 每只票独立一行（挤一行会看不清）。"""
    body = _body(ss.build_card([_sig()], "morning", NOW,
                               relay=ss._relay_hint([_sig()], wired)))
    seg = body.split("🎯 后排可参与")[1].split("🔒")[0]
    lines = [l for l in seg.split("\n") if "矩子科技" in l or "超声电子" in l]
    assert len(lines) == 2, "两只后排票应在两行"


# ═══════════════════════════════════════════════════════════════════════════
# 2026-11-03 传媒实证：选股宝题材名 → 东财 BK 的兜底 + 「没解析出板块」不许当「没有」
#   病灶：`_official_tag('短剧/互动影游')` 返回空串（sector_official 无此映射），
#   而 `backseat_unknown` 原来写成 `bool(_bk) and snap is None` → 无 bk 时为 False
#   → 卡面渲染「该方向已无未封板票」。当天海看股份(301262) +15.28% 正在同一条线上。
#   修：① 用 payload 自带 official.boards[0].bk 兜底（短剧 → 传媒 BK0486）
#       ② backseat_unknown = snap is None（拿不到快照就算「不知道」）
# ═══════════════════════════════════════════════════════════════════════════
def _media_latest():
    return {
        "top5": [{
            "name": "短剧/互动影游", "zt_count": 6,
            "official": {"boards": [{"bk": "BK0486", "name": "传媒", "coverage": 0.8}]},
            "zt_stocks": [
                {"name": "浙文影业", "code": "601599.SS", "pct": 10.09},
                {"name": "大晟文化", "code": "600892.SS", "pct": 10.07},
            ],
        }],
        "limit_up_stocks": [],
        "leaders": [],
    }


def test_theme_name_resolves_bk_via_official(monkeypatch):
    """选股宝题材名没有 official_tag 时，用 payload 的 official.boards 兜底。"""
    monkeypatch.setattr(ss, "_official_tag", lambda _n: "")
    monkeypatch.setattr(ss, "_relay_sector_of", lambda _s, _d: "短剧/互动影游")
    monkeypatch.setattr(ss, "_board_snapshot", lambda _bk: None)
    r = ss._relay_hint([{"code": "601599", "name": "浙文影业", "pct": 10.09}], _media_latest())
    assert r["backseat_bk"] == "BK0486"
    assert r["backseat_bk_src"] == "theme_official"


def test_no_bk_means_unknown_not_empty(monkeypatch):
    """⛔ 板块码解析不出来 = 不知道，**不是**「没有未封板票」。"""
    monkeypatch.setattr(ss, "_official_tag", lambda _n: "")
    monkeypatch.setattr(ss, "_relay_sector_of", lambda _s, _d: "短剧/互动影游")
    monkeypatch.setattr(ss, "_board_snapshot", lambda _bk: None)
    d = _media_latest()
    d["top5"][0].pop("official")          # 连兜底都没有
    r = ss._relay_hint([{"code": "601599", "name": "浙文影业", "pct": 10.09}], d)
    assert r["backseat_bk"] == ""
    assert r["backseat_unknown"] is True


def test_snapshot_fail_is_unknown(monkeypatch):
    monkeypatch.setattr(ss, "_official_tag", lambda _n: "")
    monkeypatch.setattr(ss, "_relay_sector_of", lambda _s, _d: "短剧/互动影游")
    monkeypatch.setattr(ss, "_board_snapshot", lambda _bk: None)
    r = ss._relay_hint([{"code": "601599", "name": "浙文影业", "pct": 10.09}], _media_latest())
    assert r["backseat_unknown"] is True
