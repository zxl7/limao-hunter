# -*- coding: utf-8 -*-
"""后排预判（2026-10-14 用户「今天有可能涨停的后排·辨识度+地位+温和放量」）回归锁。

规则（本测试锁死）：
  ① 候选来源 = **原始涨幅榜**（pool），而非 signals（signals 只含已异动票）；
  ② 三判据交集：温和放量(量比区间) ∧ 有辨识度(题材成分/人气榜/连板/行业热点) ∧ 有地位(板块内涨幅前列)；
  ③ 必须「已启动未封板」：主板 [4.0, 9.9)、20cm [7.0, 19.5)；
  ④ ⛔ 排除 ST/退市/科创板688/黑名单(京能·金能)/次新/已在 signals 的票；
  ⑤ 卡面：板块联动段每行后追加「↳ 跟风：…」；新增「📈 今日可能涨停（后排）」段；
  ⑥ ⛔ 开关 ENABLE_SPIRIT_BACKSEAT=False（或 backseat=None）→ 两处均无（回到改动前形态）；
  ⑦ 异常一律降级：绝不抛出、不影响主卡。

隔离约定：仅调纯函数/纯渲染，不推送、不联网、不碰生产 cache。
"""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location(
    "short_spirit_backseat_pick", SCRIPTS / "short_spirit.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

NOW = ss.datetime(2026, 10, 14, 10, 0)
CFG = {"amount_morning_yi": 5, "amount_afternoon_yi": 10, "min_market_cap_yi": 100}

# top5 两个题材：传媒（联动板块·已有涨停票）/ 医药（另一条线 → 只进「可能涨停」段）
# ⚠️ 2026-11-03：第二个题材原为「影视」——族归并后 影视 与 传媒 同属传媒族，
#    会被当成本板块跟风吃掉。改用「医药」保持「另一条线的后排」这个测试意图。
D = {
    "top5": [
        {"name": "传媒", "leader": {"code": "601811"}, "followers": [{"code": "600088"}],
         "zt_stocks": [{"code": "601811.SS"}]},
        {"name": "医药", "leader": {"code": "002343"}, "followers": [], "zt_stocks": []},
    ],
    "leaders": [{"code": "601811", "name": "新华文轩", "sector": "传媒", "pct": 9.98}],
    "limit_up_stocks": [{"code": "601811.SS", "name": "新华文轩", "sector": "传媒",
                         "boards": "3天2板", "pct": 9.98}],
}


def _sig(name, code, sector, pct, typ="快速拉升", strength=0.5, theme=None):
    return {"name": name, "code": code, "sector": sector, "theme": theme,
            "market_cap_yi": 50.0, "amount_yi": 8.0, "pct": pct, "type": typ, "tags": [],
            "resonance": 50, "flow_yi": 1.0, "strength": strength, "reason": f"共振[{sector}]"}


def _pool(name, code, pct, vol_ratio, industry, amount=8.0, cap=80.0,
          list_date="20100101", flow=1.0):
    return {"name": name, "code": code, "pct": pct, "price": 10.0,
            "amount_yi": amount, "market_cap_yi": cap, "float_cap_yi": cap,
            "list_date": list_date, "prev_close": 9.0, "main_flow_yi": flow,
            "industry": industry, "vol_ratio": vol_ratio, "turnover": 5.0}


# 新华文轩已涨停（signals），时代出版同板块异动 → 传媒成「联动板块」
SIGS = [_sig("新华文轩", "601811", "传媒", 9.98, "涨停", 0.9, theme="传媒"),
        _sig("时代出版", "600551", "传媒", 6.0, "快速拉升", 0.5, theme="传媒")]

# 歌华有线（传媒·跟风候选）｜慈文传媒（**医药**·「可能涨停」候选·另一条线）
# ⚠️ 2026-11-03：慈文传媒原标「影视院线」——族归并后 影视院线 与 传媒 同属传媒族，
#    会被当成本板块跟风吃掉，导致「📈 今日可能涨停」段空、本用例失去意义。
#    改用非传媒族行业，保持「另一条线的后排」这个测试意图；族归并本身见
#    test_family_merge_rolls_media_industries_together / test_card_follower_matches_by_family。
POOL = [_pool("歌华有线", "600088", 5.2, 1.6, "传媒"),
        _pool("慈文传媒", "002343", 6.5, 2.2, "化学制药")]


def _codes(items):
    return [x["code"] for x in items]


def _content(card):
    return card["elements"][0]["text"]["content"]


# ── ① 选票内核 ──────────────────────────────────────────────
def test_pick_returns_both_candidates():
    out = ss.backseat_pick(POOL, SIGS, D, "morning", CFG)
    assert set(_codes(out["candidates"])) == {"600088", "002343"}
    assert out["potential"], "至少应有一只「今日可能涨停」候选"


def test_pick_sector_from_theme_map():
    """sector 口径 = 题材名优先（top5 成分反查），非 top5 票落东财行业。"""
    out = ss.backseat_pick(POOL, SIGS, D, "morning", CFG)
    m = {c["code"]: c["sector"] for c in out["candidates"]}
    assert m["002343"] == "医药"        # top5 leader → 题材名
    assert m["600088"] == "传媒"        # top5 followers → 题材名


def test_pick_excludes_sealed_and_unstarted():
    """已封板（≥9.9）与未启动（<4.0）都不是「后排可冲板」。"""
    pool = [_pool("已封板", "600088", 10.0, 2.0, "传媒"),
            _pool("没启动", "002343", 3.0, 2.0, "影视院线")]
    out = ss.backseat_pick(pool, SIGS, D, "morning", CFG)
    assert out["potential"] == [] and out["candidates"] == []


def test_pick_excludes_volume_abnormal():
    """缩量（<1.3）无动能、爆量（>6.0）抛压重 —— 均剔除。"""
    pool = [_pool("缩量票", "600088", 5.5, 0.8, "传媒"),
            _pool("爆量票", "002343", 6.0, 8.0, "影视院线")]
    out = ss.backseat_pick(pool, SIGS, D, "morning", CFG)
    assert out["candidates"] == []


def test_pick_excludes_st_star_and_blacklist():
    pool = [_pool("ST某某", "600088", 5.5, 2.0, "传媒"),
            _pool("科创票", "688111", 5.5, 2.0, "传媒"),
            _pool("京能置业", "002343", 5.5, 2.0, "影视院线")]
    out = ss.backseat_pick(pool, SIGS, D, "morning", CFG)
    assert out["candidates"] == [], "ST/688/黑名单 均不得入选"


def test_pick_requires_identify():
    """无任何辨识度（非题材成分、无人气榜、无连板、行业不命中热点）→ 不列。"""
    pool = [_pool("无关票", "600999", 5.5, 2.0, "化纤行业")]
    out = ss.backseat_pick(pool, SIGS, D, "morning", CFG)
    assert out["candidates"] == []


def test_pick_excludes_already_in_signals():
    """signals 里已报过的票不重复进后排候选。"""
    out = ss.backseat_pick(POOL, SIGS + [_pool("慈文传媒", "002343", 6.5, 2.2, "影视院线")],
                           D, "morning", CFG)
    assert "002343" not in _codes(out["candidates"])


def test_pick_excludes_new_stock():
    recent = (ss.datetime.now() - ss.timedelta(days=100)).strftime("%Y%m%d")
    pool = [_pool("次新票", "600088", 6.0, 2.0, "传媒", list_date=recent)]
    out = ss.backseat_pick(pool, SIGS, D, "morning", CFG)
    assert out["potential"] == []


def test_pick_position_rank_limits_per_sector():
    """地位：同板块内只留涨幅前列 BS_SECTOR_RANK 只（第 4 名起剔除）。"""
    pool = [_pool(f"传媒{i}", f"60000{i}", 8.5 - i, 2.0, "传媒") for i in range(1, 6)]
    out = ss.backseat_pick(pool, [], D, "morning", CFG)
    assert len(out["candidates"]) == ss.BS_SECTOR_RANK
    assert _codes(out["candidates"])[:3] == ["600001", "600002", "600003"]   # 涨幅降序取前 3


def test_pick_empty_pool_and_bad_input_never_raise():
    assert ss.backseat_pick([], SIGS, D, "morning", CFG) == {"potential": [], "candidates": []}
    assert ss.backseat_pick(None, None, {}, "morning", None) == {"potential": [], "candidates": []}


# ── ② 卡面渲染 ──────────────────────────────────────────────
def test_card_renders_followers_and_potential():
    bs = ss.backseat_pick(POOL, SIGS, D, "morning", CFG)
    content = _content(ss.build_card(SIGS, "morning", NOW, backseat=bs))
    assert "🔥 板块联动" in content
    assert "↳ 跟风：" in content and "歌华有线" in content      # 联动段追加跟风
    assert "📈 今日可能涨停（后排）" in content
    assert "慈文传媒" in content                                  # 另一条线的后排候选


def test_card_no_duplicate_between_followers_and_potential():
    """同卡不自我重复：进了「跟风」的票不再出现在「可能涨停」段。"""
    bs = ss.backseat_pick(POOL, SIGS, D, "morning", CFG)
    content = _content(ss.build_card(SIGS, "morning", NOW, backseat=bs))
    assert content.count("歌华有线") == 1


def test_card_without_backseat_unchanged():
    """backseat=None → 两处均无（改动前形态）。"""
    content = _content(ss.build_card(SIGS, "morning", NOW))
    assert "↳ 跟风：" not in content
    assert "今日可能涨停" not in content
    assert "🔥 板块联动" in content          # 原段仍在


def test_switch_off_hides_both_sections(monkeypatch):
    bs = ss.backseat_pick(POOL, SIGS, D, "morning", CFG)
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_BACKSEAT", False)
    content = _content(ss.build_card(SIGS, "morning", NOW, backseat=bs))
    assert "↳ 跟风：" not in content
    assert "今日可能涨停" not in content


def test_render_error_degrades_without_breaking_card(monkeypatch):
    """渲染层异常不得影响主卡（盘中链路安全第一）。"""
    def _boom(_a, _b):
        raise RuntimeError("boom")
    monkeypatch.setattr(ss, "_sector_match", _boom)
    bs = ss.backseat_pick(POOL, SIGS, D, "morning", CFG)
    content = _content(ss.build_card(SIGS, "morning", NOW, backseat=bs))
    assert "新华文轩" in content              # 主卡仍正常


# ── ③ 文案合规（白话门禁）────────────────────────────────────
def test_backseat_copy_passes_plain_gate():
    """新段文案不得命中 Tier-1 机器黑话（scripts/card_copy_rules）。"""
    import importlib.util as _iu
    _spec = _iu.spec_from_file_location("ccr", SCRIPTS / "card_copy_rules.py")
    ccr = _iu.module_from_spec(_spec)
    _spec.loader.exec_module(ccr)
    bs = ss.backseat_pick(POOL, SIGS, D, "morning", CFG)
    content = _content(ss.build_card(SIGS, "morning", NOW, backseat=bs))
    fails, _warns = ccr.scan(content)
    assert fails == [], f"新段命中机器黑话: {fails}"
