import importlib.util
import json
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("live_signal", SCRIPTS / "live_signal.py")
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)


def _sector(stage="first", zt=7, name="大农业", family=False, sub=""):
    return {"name": name, "zt_count": zt, "_sector_stage": stage,
            "leader": {"name": "金健米业", "code": "600127.SS", "pct": 10.0, "boards": "16天8板"},
            "_family": family, "sub_text": sub}


def test_stage_titles_distinguish_semantics():
    c1 = ls.build_sector_boost_card([_sector("first")], "10:00", {})
    c2 = ls.build_sector_boost_card([_sector("peak")], "10:00", {})
    c3 = ls.build_sector_boost_card([_sector("gradual")], "10:00", {})
    s = "".join(e["text"]["content"] for e in c1["elements"] if e.get("text"))
    assert "板块强势" in s and "强度新高" not in s and "持续加强" not in s
    s = "".join(e["text"]["content"] for e in c2["elements"] if e.get("text"))
    assert "强度新高" in s
    s = "".join(e["text"]["content"] for e in c3["elements"] if e.get("text"))
    assert "持续加强" in s


def test_family_sub_text_rendered():
    card = ls.build_sector_boost_card([_sector("first", 9, "泛农业", family=True, sub="大农业7+养猪2")], "10:00", {})
    s = "".join(e["text"]["content"] for e in card["elements"] if e.get("text"))
    assert "泛农业〔大农业7+养猪2〕" in s
    assert "9只涨停" in s


def _no_pool():
    """隔离真实盘中资金池（sector_fund_pool.json）→ 走 sector_risk top10 兜底路径。

    2026-09-08 起资金行优先查本地全量资金池（_load_fund_pool），若盘中 pool 存在且新鲜，
    top10 兜底语义无法被触发；本 helper 把 pool 指向不存在路径强制走兜底，测完恢复。
    """
    bak = ls.FUND_POOL
    ls.FUND_POOL = Path("/tmp/_sfp_nonexistent_test.json")
    Path("/tmp/_sfp_nonexistent_test.json").unlink(missing_ok=True)
    return bak


def test_flow_line_hit_inflow_when_enabled():
    bak = _no_pool()
    try:
        latest = {"sector_risk": {"inflow_top": [{"name": "大农业", "flow_yi": 12.5}], "outflow_top": []}}
        txt = ls._sector_money_txt(latest, "大农业", enabled=True)
        assert "净流入" in txt and "+12.5亿" in txt
        # 开关关 → 空（不加行，与改动前一致）
        assert ls._sector_money_txt(latest, "大农业", enabled=False) == ""
    finally:
        ls.FUND_POOL = bak


def test_flow_line_no_rank_is_honest():
    bak = _no_pool()
    try:
        latest = {"sector_risk": {"inflow_top": [{"name": "通信技术", "flow_yi": 162.0}], "outflow_top": []}}
        txt = ls._sector_money_txt(latest, "大农业", enabled=True)
        assert "资金未入榜" in txt  # 诚实标注，不编造、不消失
    finally:
        ls.FUND_POOL = bak


def test_flow_line_suffix_strip_match():
    # top5 板块名与资金榜名相差泛尾缀时也能匹配（概念/板块）
    bak = _no_pool()
    try:
        latest = {"sector_risk": {"inflow_top": [{"name": "农业种植概念", "flow_yi": 8.0}], "outflow_top": []}}
        assert "净流入" in ls._sector_money_txt(latest, "农业种植", enabled=True)
    finally:
        ls.FUND_POOL = bak


def test_no_individual_leader_line():
    # ⛔ 2026-09-07 用户铁律：板块卡纯板块级，不得列个股龙头（防存量连板龙头当旧闻推）
    card = ls.build_sector_boost_card(
        [_sector("peak", 9, "泛农业", family=True, sub="大农业7+养猪2")], "10:00", {})
    s = "".join(e["text"]["content"] for e in card["elements"] if e.get("text"))
    assert "龙头:" not in s
    assert "金健米业" not in s


def test_zt_stocks_listed():
    # ✅ 2026-09-08 用户「这种涨停的话，把涨停的个股列出来」：列的是**当前涨停成分**
    # zt_stocks（live 当下状态），不是存量龙头；与「不列龙头」铁律正交。
    sec = _sector("gradual", 7, "医药", family=False, sub="")
    sec["zt_stocks"] = [
        {"name": "亚泰集团", "code": "600881.SS", "pct": 10.11},
        {"name": "精华制药", "code": "002349.SZ", "pct": 10.07},
        {"name": "澳洋健康", "code": "002172.SZ", "pct": 9.94},
    ]
    card = ls.build_sector_boost_card([sec], "14:00", {})
    s = "".join(e["text"]["content"] for e in card["elements"] if e.get("text"))
    assert "涨停3家" in s and "亚泰集团(600881)+10.1%" in s
    assert "精华制药(002349)" in s and "澳洋健康(002172)+9.9%" in s
    assert "龙头:" not in s  # 仍不渲染存量龙头行


def test_zt_stocks_missing_graceful():
    # 无 zt_stocks 数据（旧数据/降级源）→ 不加行不报错
    card = ls.build_sector_boost_card([_sector("first", 6, "大农业")], "10:00", {})
    s = "".join(e["text"]["content"] for e in card["elements"] if e.get("text"))
    assert "涨停 6 只" in s
    assert "🔒" not in s  # 无成分时整行省略


def test_zt_stocks_truncated_over_12():
    # 涨停>12 只时截断并标注「等N只」（防大板块成分爆行长）
    sec = _sector("peak", 17, "泛农业", family=True, sub="大农业17")
    sec["zt_stocks"] = [{"name": f"票{i:02d}", "code": f"60000{i%10}.SS", "pct": 10.0}
                        for i in range(17)]
    card = ls.build_sector_boost_card([sec], "10:00", {})
    s = "".join(e["text"]["content"] for e in card["elements"] if e.get("text"))
    assert "涨停17家" in s and "等5只" in s
    assert "票11(600001" in s      # 前12只完整（00-11）
    assert "票12(" not in s        # 截断在 12 只处
