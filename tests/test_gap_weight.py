# -*- coding: utf-8 -*-
"""2026-11-05 21:45 用户「高开 8.9% 无限接近涨停，大于 6、7 个点要非常小心，
降低它的买入权重」回归锁。

⛔ 本改动**不是**新硬闸：用户 17:00 已删「高开 5%」与「必须红开」两条绝对线，
   `gap_zone(DEV)` 仍是 09:25 唯一准入判据。这里只管「真买的话买多重」。

锁死七件事：
  ① 判据唯一源 `ai_caliber.gap_weight`，双因子 = 开幅/涨停幅度 + DEV 透支；
  ② 连续单调、下限 0.20 > 0 —— ⛔ 绝不归零变成隐形硬闸（扬子新材仍在名单里）；
  ③ 20cm 按涨停幅度归一化，⛔ 不给创业板额外加减分（08-13 用户纪律）；
  ④ 缺 gap / 缺 E 诚实降级，不猜 E、不因缺 E 就不降绝对档；
  ⑤ 三处同源：pool_call 仓位 / ai_caliber.decide 留痕 / 卡面理由，且只讲事实不露系数；
  ⑥ 与量能档**取小**不相乘；`AI_GAP_WEIGHT=0` / `POOL_CALL_GAP_WEIGHT_POSITION=0`
     逐字回退旧均分；`AI_EXPECTED_GAP_MODE=absolute` 时连字段都不写；
  ⑦ ⛔ 不改 zone / view / participate / quota —— 降仓不等于否决。

全隔离：不读真实 cache / 台账 / 网络，不推群，不写冻结件。
"""
import sys
from pathlib import Path

import pytest

WC = Path(__file__).resolve().parents[1] / "wealth_code"
for _p in (str(WC), str(WC / "pipelines"), str(WC / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import ai_caliber as ac          # noqa: E402
from core import pool_call as pc           # noqa: E402
import build_cards as bc                   # noqa: E402


@pytest.fixture(autouse=True)
def _relative_rules(monkeypatch):
    """显式跑在 11-05 17:00 新判据下（不依赖模块默认值）。

    ⚠️ 2026-11-06 用户「避开开的特别高又没有资格和行情秒板的票（扬子新材 +8.93%）」
    → 新增「顶部空间硬闸」（`tests/test_high_gap_room.py`），**默认开**，会在 ②′
      把 8.93% 这类票直接 skip。⛔ 本文件只锁「降权机制本身」，故在此显式关掉硬闸，
      否则测不到 gap_weight 的仓位缩放（两件事互不替代：一个是「不买」，一个是「买多重」）。
    """
    monkeypatch.setenv("AI_EXPECTED_GAP_MODE", "relative")
    monkeypatch.setattr(ac, "AI_GAP_WEIGHT", True)
    monkeypatch.setattr(pc, "POOL_CALL_HIGH_GAP_REJECT", False)   # 见 docstring
    monkeypatch.setattr(pc, "POOL_CALL_GAP_WEIGHT_POSITION", True)
    monkeypatch.setattr(pc, "POOL_CALL_UNLIMITED_SLOTS", True)
    monkeypatch.setattr(pc, "POOL_CALL_EVEN_ALLOCATION", True)
    monkeypatch.setattr(pc, "POOL_CALL_VOLUME_SCALE_POSITION", False)
    monkeypatch.setattr(pc, "POOL_CALL_LOW_OPEN_GUARD", False)


# ── ① ②③④ 判据唯一源 ────────────────────────────────────────────────────────

def test_bands_and_monotonic():
    """10cm 的 6/7/8% ≡ near 0.60/0.70/0.80；越接近涨停越轻。"""
    assert ac.gap_weight(3.0, expected_gap_pct=1.0)["weight"] == 1.0
    w6 = ac.gap_weight(6.0, expected_gap_pct=6.0)["weight"]
    w7 = ac.gap_weight(7.0, expected_gap_pct=7.0)["weight"]
    w8 = ac.gap_weight(8.0, expected_gap_pct=8.0)["weight"]
    assert (w6, w7, w8) == (0.70, 0.45, 0.30)
    assert w6 > w7 > w8


def test_dev_overdraft_multiplier():
    """DEV ≥4 / ≥6 两档乘子，与绝对档相乘后仍有 0.20 地板。"""
    assert ac.gap_weight(6.5, expected_gap_pct=3.0)["weight"] == 0.70          # DEV 3.5 <4 不降
    assert ac.gap_weight(6.5, expected_gap_pct=2.0)["weight"] == pytest.approx(0.70 * 0.75)  # DEV 4.5
    assert ac.gap_weight(6.5, expected_gap_pct=0.0)["weight"] == pytest.approx(0.70 * 0.60)  # DEV 6.5
    # 扬子新材 11-05 真实：gap 8.93 / E −0.49 / DEV 9.42 → 0.30*0.60=0.18，夹到 0.20
    y = ac.gap_weight(8.93, expected_gap_pct=-0.49)
    assert y["weight"] == ac.GAP_WEIGHT_MIN == 0.20
    assert y["active"] is True


def test_floor_is_not_zero_and_never_blocks():
    """⛔ 再危险也留试错仓；本函数没有 block/zone 字段，不拥有否决权。"""
    out = ac.gap_weight(9.7, expected_gap_pct=-5.0)
    assert out["weight"] >= ac.GAP_WEIGHT_MIN > 0
    assert "block" not in out and "zone" not in out and "view" not in out


def test_20cm_normalized_symmetry():
    """20cm 按涨停幅度折算，同比例同权重，⛔ 不额外打折。"""
    a = ac.gap_weight(6.0, expected_gap_pct=6.0, is_20cm=False)
    b = ac.gap_weight(12.0, expected_gap_pct=12.0, is_20cm=True)
    assert a["abs_scale"] == b["abs_scale"] == 0.70
    assert a["weight"] == b["weight"]
    # 20cm 的 8% 只是 0.40 near，不该按主板的 8% 重罚
    assert ac.gap_weight(8.0, expected_gap_pct=8.0, is_20cm=True)["weight"] == 1.0


def test_missing_data_is_honest():
    assert ac.gap_weight(None, expected_gap_pct=1.0)["weight"] == 1.0
    assert ac.gap_weight("bad", expected_gap_pct=1.0)["active"] is False
    # 缺 E → 只跳相对档，绝对档照降（不因缺 E 就放过接近涨停的票）
    out = ac.gap_weight(8.0, expected_gap_pct=None)
    assert out["dev"] is None and out["dev_scale"] == 1.0
    assert out["abs_scale"] == 0.30 and out["weight"] == 0.30
    out2 = ac.gap_weight(8.0, expected_gap_pct="bad")
    assert out2["dev"] is None and out2["weight"] == 0.30


def test_switch_off_is_passthrough(monkeypatch):
    monkeypatch.setattr(ac, "AI_GAP_WEIGHT", False)
    out = ac.gap_weight(9.0, expected_gap_pct=-2.0)
    assert out["weight"] == 1.0 and out["active"] is False and out["label"] == ""


def test_label_has_no_fake_position_number():
    """label 只讲事实，⛔ 不写「只给 X% 仓」——实际仓位由 position_cap 下发。"""
    assert "%" in ac.gap_weight(8.93, expected_gap_pct=-0.49)["label"]  # 开幅百分数可以有
    assert "只给" not in ac.gap_weight(8.93, expected_gap_pct=-0.49)["label"]
    assert "仓位收紧" in ac.gap_weight(8.93, expected_gap_pct=-0.49)["label"]


# ── ⑥ 三处同源 ──────────────────────────────────────────────────────────────

def test_decide_writes_same_weight():
    dec = {"view": "participate", "is_20cm": False}
    ok, _, meta = ac.decide(dec, 8.93, expected_range=[0, 5],
                            expected_gap_pct=-0.49)
    assert ok is True                      # ⛔ 降权不等于否决
    assert meta["gap_weight"] == 0.20
    assert meta["gap_weight_active"] is True
    assert meta["zone"] == "ok"


def test_absolute_mode_writes_nothing(monkeypatch):
    monkeypatch.setenv("AI_EXPECTED_GAP_MODE", "absolute")
    _, _, meta = ac.decide({"view": "participate"}, 3.0,
                           expected_range=[1.0, 4.0], expected_gap_pct=1.0)
    assert "gap_weight" not in meta and "gap_weight_active" not in meta


# ── ⑤⑦ 主链：只降仓、不改名额/名单 ──────────────────────────────────────────

def _cand(code, gap, e, boards=2, **kw):
    c = {"code": code, "name": f"票{code}", "boards": boards,
         "sector": "测试题材", "bucket_label": "接力池",
         "expectation": {"expected_range": [0.0, 9.0],
                         "auction_amount_min_yi": 0.1}}
    c.update(kw)
    return c, {"prev_close": 10.0,
               "auction_price": round(10.0 * (1 + gap / 100.0), 4),
               "auction_amount_yi": 2.0}, e


def _call(pairs, *, on=True, volume_scale=False):
    cands, quotes, egaps = [], {}, {}
    for c, q, e in pairs:
        cands.append(c)
        quotes[c["code"]] = q
        egaps[c["code"]] = e
    import pytest as _pt
    # 用 monkeypatch 的方式在调用点临时覆盖（本函数自带隔离）
    old = pc.POOL_CALL_GAP_WEIGHT_POSITION, pc.POOL_CALL_VOLUME_SCALE_POSITION
    pc.POOL_CALL_GAP_WEIGHT_POSITION = on
    pc.POOL_CALL_VOLUME_SCALE_POSITION = volume_scale
    try:
        return pc.call_pool(
            {"relay": cands, "watch": []}, quotes,
            plan={"stance": "attack", "views": {}}, stance="attack",
            line_cores={}, sector_flow={}, zt_series={}, zt_days=[],
            market_open=None, relay_plan=None, theme_open_rows=[],
            expected_gaps=egaps)
    finally:
        pc.POOL_CALL_GAP_WEIGHT_POSITION, pc.POOL_CALL_VOLUME_SCALE_POSITION = old


def test_pool_only_scales_cap_not_view():
    """扬子新材型 vs 普通票：名单不变、zone 不变、只把仓位压下来。"""
    normal = _cand("000001", 2.0, 1.0)
    hot = _cand("000002", 8.93, -0.49)
    r_on = _call([normal, hot], on=True)
    r_off = _call([normal, hot], on=False)
    parts_on = {c: v for c, v in r_on["views"].items() if v.get("view") == "participate"}
    parts_off = {c: v for c, v in r_off["views"].items() if v.get("view") == "participate"}
    assert set(parts_on) == set(parts_off), "⛔ 降权不得改变参与名单"
    assert r_on["meta"]["participate"] == r_off["meta"]["participate"]
    for c in parts_on:
        assert parts_on[c]["zone"] == parts_off[c]["zone"]
        assert parts_on[c]["view"] == parts_off[c]["view"]
    assert "000002" in parts_on, "高开票仍在名单里（只是轻仓），⛔ 不许变成隐形硬闸"
    assert parts_on["000002"]["gap_weight"] == 0.20
    assert parts_on["000001"]["gap_weight"] == 1.0
    assert parts_on["000002"]["position_cap"] < parts_off["000002"]["position_cap"]
    assert parts_on["000001"]["position_cap"] == parts_off["000001"]["position_cap"]
    assert "000002" in r_on["meta"]["slot_policy"]["gap_weight_reduced"]


def test_scale_is_min_not_multiplied():
    """与量能档取小：0.5 量能 × 0.2 高开 ⇒ 0.2，不是 0.1。"""
    hot = _cand("000002", 8.93, -0.49)
    r = _call([hot], on=True, volume_scale=True)
    v = r["views"]["000002"]
    # 量足时 volume_scale=1.0，只有高开档生效
    assert v["position_cap"] == round(min(0.5, 1.0 / len(r["meta"]["participate"])) * 0.20, 4)
    assert v["volume_band"] == "full"


def test_reason_is_factual_and_no_legacy_5pct_claim():
    """⛔ 8.93% 的票不许再说「没超 5% 上限」；降权理由只写一次上限。"""
    normal = _cand("000001", 2.0, 1.0)
    hot = _cand("000002", 8.93, -0.49)
    r = _call([normal, hot], on=True)
    txt = r["views"]["000002"]["reason"]
    assert "没超 5% 上限" not in txt
    assert "符合这只票的预期" in txt
    assert txt.count("单票上限") == 1            # ⛔ 同一事实只说一次
    assert "权重" not in txt and "系数" not in txt


def test_switch_off_restores_even_split():
    normal = _cand("000001", 2.0, 1.0)
    hot = _cand("000002", 8.93, -0.49)
    r = _call([normal, hot], on=False)
    assert r["meta"]["slot_policy"]["gap_weight_enabled"] is False
    assert all(v["position_cap"] == 0.5 for v in r["views"].values()
               if v.get("view") == "participate")


def test_card_shows_reduced_cap_not_just_even_split():
    """⛔ 卡面必须说出逐票收紧后的上限，否则「卡面 50% / 账本 10%」不同源。"""
    plan = {"tier": "正常", "per_pick_cap": 0.5, "total_cap": 0.5,
            "drawdown_pct": 0.0, "hints": [], "day_count": 20, "theme_count": 30}
    meta = {"slot_policy": {
        "single_pick": False, "even_allocation": True, "even_cap": 0.5,
        "allocation_count": 2, "gap_weight_enabled": True,
        "gap_weight_reduced": ["000002"],
        "gap_weight_caps": {"000002": 0.10},
    }}
    blocks = bc._relay_position_block(plan, day_word="今天", pool_meta=meta)
    text = "".join(str(b) for b in (blocks or []))
    assert "每只 50%" in text
    assert "上限收紧到最低 10%" in text
    assert "降权" not in text and "系数" not in text   # ⛔ 不露内部口径

    # 开关关时一个字都不加（逐字回退旧卡面）
    meta_off = {"slot_policy": {**meta["slot_policy"],
                                "gap_weight_enabled": False, "gap_weight_caps": {}}}
    text_off = "".join(str(b) for b in
                       (bc._relay_position_block(plan, day_word="今天", pool_meta=meta_off) or []))
    assert "收紧" not in text_off
