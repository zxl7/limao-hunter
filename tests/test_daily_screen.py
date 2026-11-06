"""涨停股全量过筛（`core.daily_screen`）测试。

用户 2026-11-04：
「我们其实每天要把涨停的个股全部过一遍，尽量过二连板以上的票。首板其实也能过，
  但我们尽量还是看二板以上的票权重稍微高一点，看的时候就过一下这些股性还有逻辑。
  基于你这个逻辑的话，这个"紫竹"根本就不会进入我们系统的范围里面，直接就 pass 掉了。」

守的边界：
1. **全量输入**：48 只涨停全部给结论，不是只筛候选池那 7 只
2. **紫竹必须 reject**，且理由是「换手板成交额不足」这类可核查硬伤，⛔ 不是「评分低」
3. **字段映射**：zt_history 用 `seal`/`lt_yi`，seal_gate 用 `seal_amt`/`lt_mktcap`，不转就判不了
4. **一字板用日K判**：zt_history 没有 `is_one_word`；⛔ 不拿「竞价即封」当一字
5. **身位只对 ≥2 板生效**：⛔ 不能用它把首板整层删掉（用户「首板也能过」）
6. **缺数据不判**：进 unknown，⛔ 不当证据
7. 只读：不写候选、不改排序
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "wealth_code") not in sys.path:
    sys.path.insert(0, str(ROOT / "wealth_code"))

from core import daily_screen as DS  # noqa: E402


def zt(code, name, boards, *, cje=8.0, seal=2.0, lt=40.0, fbt="092500", zbc=0,
       sector="电池", is_20cm=False):
    """zt_history 形状（注意：seal / lt_yi 是原始字段名，无 is_one_word）。"""
    return {"code": code, "name": name, "boards": boards, "cje_yi": cje,
            "seal": seal, "lt_yi": lt, "fbt": fbt, "zbc": zbc,
            "sector": sector, "is_20cm": is_20cm, "hs": 5.0}


def gene_dir(tmp_path, code, *, promo=57.1, zt250=8, zt_total=46):
    d = tmp_path / "zt_genes"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"hist_{code}.json").write_text(json.dumps({
        "code": code, "first_day": "20200818", "last_day": "20261103",
        "zt_total": zt_total, "zt_250": zt250, "zt_500": 21,
        "max_boards_hist": 5, "promo_rate_hist": promo,
        "prem_open_hist": 3.68, "prem_close_hist": 5.3,
        "first_prem_open_hist": 2.41, "sample_n": 1500,
    }), encoding="utf-8")
    return d


# ── ① 字段映射 ─────────────────────────────────────────────────────────────
def test_field_alias_seal_and_lt():
    """zt_history 的 seal/lt_yi 必须映射成 seal_amt/lt_mktcap，否则封板质量算不出来。"""
    row = DS._coerce_fields({"seal": 2.5, "lt_yi": 50.0})
    assert row["seal_amt"] == 2.5
    assert row["lt_mktcap"] == 50.0


def test_field_alias_does_not_override():
    row = DS._coerce_fields({"seal": 9.9, "seal_amt": 1.1})
    assert row["seal_amt"] == 1.1      # 已有值优先


def test_coerce_does_not_mutate_input():
    src = {"seal": 2.5, "lt_yi": 50.0, "cje": 2.54}
    before = dict(src)
    out = DS._coerce_fields(src)
    assert src == before
    assert out is not src
    assert out["cje_yi"] == 2.54


@pytest.mark.parametrize("src, expected", [
    ({"cje": 2.54}, 2.54),
    ({"cje_yi": 8.0, "cje": 2.54}, 8.0),
    ({"cje_yi": 0, "cje": 2.54}, 0),
    ({"cje_yi": None, "cje": 2.54}, 2.54),
    ({"cje": 0}, 0),
    ({"cje": None}, None),
    ({}, None),
])
def test_turnover_alias_preserves_units_and_missing(src, expected):
    before = dict(src)
    assert DS._coerce_fields(src).get("cje_yi") == expected
    assert src == before


@pytest.mark.parametrize("cje", [2.54, 5.0, 8.0, 0, None])
def test_raw_and_standard_turnover_shapes_are_equivalent(tmp_path, cje):
    raw = {"dm": "600001", "mc": "测试票", "lbc": 2, "cje": cje,
           "zj": 2.0, "lt": 40.0, "hy": "电池", "is_one_word": False,
           "fbt": "093000", "zbc": 0, "hs": 5.0}
    standard = {"code": "600001", "name": "测试票", "boards": 2, "cje_yi": cje,
                "seal_amt": 2.0, "lt_mktcap": 40.0, "sector": "电池",
                "is_one_word": False, "fbt": "093000", "zbc": 0, "hs": 5.0}
    before = json.dumps([raw, standard], sort_keys=True)
    kwargs = {"gene_cache": tmp_path / "zt_genes", "root": tmp_path,
              "trade_date": "2026-11-03"}
    raw_one = DS.screen_one(raw, market_max_boards=2,
                            theme_members={"电池": 3}, **kwargs)
    standard_one = DS.screen_one(standard, market_max_boards=2,
                                 theme_members={"电池": 3}, **kwargs)
    assert raw_one == standard_one
    assert raw_one["facts"]["cje_yi"] == cje
    assert raw_one["facts"]["floor_yi"] == 5.0
    assert ("成交额缺失" in raw_one["unknown"]) is (cje is None)
    assert any("换手板成交额" in r for r in raw_one["rejects"]) is (
        cje is not None and cje < 5.0)
    assert DS.screen_pool([raw], **kwargs) == DS.screen_pool([standard], **kwargs)
    assert json.dumps([raw, standard], sort_keys=True) == before


# ── ② 一字板从日K反推 ───────────────────────────────────────────────────────
def _write_kline(root: Path, code: str, date: str, o, h, l, c):
    d = root / "cache" / "kline_qfq"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{code}.json").write_text(json.dumps({"code": code, "bars": [
        {"date": date, "open": o, "high": h, "low": l, "close": c, "vol": 1000}]}),
        encoding="utf-8")


def test_is_one_word_true_when_no_intraday_range(tmp_path):
    _write_kline(tmp_path, "600001", "2026-11-03", 10.0, 10.0, 10.0, 10.0)
    assert DS._is_one_word_from_kline("600001", "2026-11-03", tmp_path) is True


def test_is_one_word_false_when_range_exists(tmp_path):
    _write_kline(tmp_path, "600001", "2026-11-03", 9.2, 10.0, 9.1, 10.0)
    assert DS._is_one_word_from_kline("600001", "2026-11-03", tmp_path) is False


def test_is_one_word_none_when_missing(tmp_path):
    assert DS._is_one_word_from_kline("999999", "2026-11-03", tmp_path) is None
    _write_kline(tmp_path, "600002", "2026-11-01", 1, 1, 1, 1)
    assert DS._is_one_word_from_kline("600002", "2026-11-03", tmp_path) is None


# ── ③ 紫竹真实案例：必须 reject，理由是量能 ────────────────────────────────
def test_zizhu_real_case_rejected_by_turnover(tmp_path):
    """002058 紫竹高科（2026-11-03）：2 板换手、2.54 亿、封板晚、封单薄。

    这不是复述历史结论——是把它放进**全量过筛**里跑一遍，
    确认它会在过筛阶段被 pass 掉，而不是等 09:25 靠「情绪」闸碰巧拦下。
    """
    g = gene_dir(tmp_path, "002058")
    _write_kline(tmp_path, "002058", "2026-11-03", 19.26, 21.13, 19.10, 21.13)  # 换手板
    rows = [
        zt("002058", "紫竹高科", 2, cje=2.54, seal=0.7333, lt=30.29, fbt="094848", zbc=0),
        zt("002086", "东方海洋", 4, cje=7.88, seal=2.16, lt=54.07, fbt="092500", zbc=3),
        zt("600001", "陪跑", 1, cje=1.0, seal=0.2, lt=20.0),
    ]
    res = DS.screen_pool(rows, gene_cache=g, trade_date="2026-11-03", root=tmp_path)
    z = next(s for s in res["screened"] if s["code"] == "002058")
    assert z["verdict"] == "reject"
    joined = " ".join(z["rejects"])
    assert "换手板成交额" in joined, z["rejects"]
    # 股性不该是 reject 理由（它晋级率 57.1%，是连板龙）
    assert any("连板龙" in s for s in z["supports"]), z["supports"]


# ── ④ 身位只对 ≥2 板生效 ───────────────────────────────────────────────────
def test_board_gap_not_applied_to_first_board(tmp_path):
    """市场最高 4 板时，1 板必然差 3 —— ⛔ 不能用它删掉整层首板。"""
    g = gene_dir(tmp_path, "600001")
    rows = [zt("600001", "首板票", 1, cje=8.0, seal=2.0, lt=40.0),
            zt("600002", "高板", 4, cje=9.0, seal=3.0, lt=50.0, sector="其他")]
    res = DS.screen_pool(rows, gene_cache=g, trade_date="2026-11-03", root=tmp_path)
    first = next(s for s in res["screened"] if s["code"] == "600001")
    assert not any("身位差" in r for r in first["rejects"]), first["rejects"]


def test_board_gap_applied_to_second_board(tmp_path, monkeypatch):
    """身位差 ≥2 板：**旧口径**判 reject，**11-06 新口径**只写事实不 reject。

    2026-11-06 用户 A 后，过筛不再因身位判死（职责还给 09:25 降权），
    这里同时锁住两条路径，确认开关真能逐字回退。
    """
    g = gene_dir(tmp_path, "600002")
    rows = [zt("600002", "二板后排", 2, cje=8.0, seal=2.0, lt=40.0),
            zt("600009", "最高板", 4, cje=9.0, seal=3.0, lt=50.0, sector="其他")]

    # 旧口径回退：仍按全市场最高板判「后排」
    monkeypatch.setattr(DS, "POS_SCREEN_FACT_ONLY", False)
    res = DS.screen_pool(rows, gene_cache=g, trade_date="2026-11-03", root=tmp_path)
    two = next(s for s in res["screened"] if s["code"] == "600002")
    assert any("身位差" in r for r in two["rejects"]), two["rejects"]

    # 新口径（默认）：不 reject，只把事实留给 09:25
    monkeypatch.setattr(DS, "POS_SCREEN_FACT_ONLY", True)
    res2 = DS.screen_pool(rows, gene_cache=g, trade_date="2026-11-03", root=tmp_path)
    two2 = next(s for s in res2["screened"] if s["code"] == "600002")
    assert not any("身位差" in r for r in two2["rejects"]), two2["rejects"]
    assert two2["facts"]["position_gap"]["market_gap"] == 2


# ── ⑤ 一字板豁免成交额 ─────────────────────────────────────────────────────
def test_one_word_low_turnover_not_rejected(tmp_path):
    """一字板 0.59 亿：封死所致，⛔ 不算缺陷。"""
    g = gene_dir(tmp_path, "000995")
    _write_kline(tmp_path, "000995", "2026-11-03", 17.16, 17.16, 17.16, 17.16)
    rows = [zt("000995", "皇台酒业", 3, cje=0.59, seal=1.38, lt=30.44, sector="白酒")]
    res = DS.screen_pool(rows, gene_cache=g, trade_date="2026-11-03", root=tmp_path)
    s = res["screened"][0]
    assert not any("换手板成交额" in r for r in s["rejects"]), s["rejects"]


# ── ⑥ 缺数据不判 ───────────────────────────────────────────────────────────
def test_missing_gene_goes_unknown_not_reject(tmp_path):
    empty = tmp_path / "nonexistent"
    rows = [zt("600001", "无股性", 2, cje=8.0, seal=2.0, lt=40.0)]
    res = DS.screen_pool(rows, gene_cache=empty, trade_date="2026-11-03", root=tmp_path)
    s = res["screened"][0]
    assert any("股性" in u for u in s["unknown"]), s["unknown"]
    assert not any("股性" in r for r in s["rejects"]), s["rejects"]


def test_missing_turnover_goes_unknown(tmp_path):
    g = gene_dir(tmp_path, "600001")
    rows = [{**zt("600001", "缺额", 2), "cje_yi": None}]
    res = DS.screen_pool(rows, gene_cache=g, trade_date="2026-11-03", root=tmp_path)
    s = res["screened"][0]
    assert any("成交额" in u for u in s["unknown"]), s["unknown"]


# ── ⑦ 全量 & 只读 ──────────────────────────────────────────────────────────
def test_all_rows_screened_not_just_candidates(tmp_path):
    """⛔ 全量过筛：48 只就得 48 条结论，不能只筛候选池那几只。"""
    g = gene_dir(tmp_path, "600100")
    rows = [zt(f"6001{i:02d}", f"票{i}", 1 + i % 4) for i in range(20)]
    res = DS.screen_pool(rows, gene_cache=g, trade_date="2026-11-03", root=tmp_path)
    assert res["zt_count"] == 20
    assert len(res["screened"]) == 20
    assert sum(res["counts"].values()) == 20


def test_input_rows_not_mutated(tmp_path):
    g = gene_dir(tmp_path, "600001")
    rows = [zt("600001", "票A", 2)]
    before = json.dumps(rows, sort_keys=True)
    DS.screen_pool(rows, gene_cache=g, trade_date="2026-11-03", root=tmp_path)
    assert json.dumps(rows, sort_keys=True) == before


def test_reject_sorted_last(tmp_path):
    g = gene_dir(tmp_path, "600001")
    rows = [zt("600001", "好票", 4, cje=9.0, seal=3.0, lt=50.0),
            zt("600002", "坏票", 2, cje=0.5, seal=0.05, lt=20.0, sector="其他")]
    res = DS.screen_pool(rows, gene_cache=g, trade_date="2026-11-03", root=tmp_path)
    verdicts = [s["verdict"] for s in res["screened"]]
    order = {"pass": 0, "watch_only": 1, "reject": 2}
    assert verdicts == sorted(verdicts, key=lambda v: order[v])


def test_build_from_files_missing_returns_none(tmp_path):
    assert DS.build_from_files(tmp_path, "2026-11-03") is None


def test_build_from_files_empty_pool_returns_none(tmp_path):
    """节假日占位（zt_list 为空）→ 不产结论，⛔ 不编造「全部 reject」。"""
    d = tmp_path / "cache" / "zt_history"
    d.mkdir(parents=True)
    (d / "zt_2026-11-03.json").write_text(json.dumps({"date": "2026-11-03", "zt_list": []}),
                                          encoding="utf-8")
    assert DS.build_from_files(tmp_path, "2026-11-03") is None


def test_switch_off():
    assert DS.enabled() in (True, False)   # 环境变量可控；默认开


# ── ⑧ 封板弱不是否决理由（2026-11-04 重大修正）────────────────────────────
def test_weak_seal_is_not_a_reject(tmp_path):
    """⛔ 实测：封板弱 n=242 开收 **+0.15%** 胜率 **49.6%**，比被拒整体（41.4%）还高。

    原因：封板强 → 高开多 → 买不到；封板弱 → 能买到，且买入后反而有溢价。
    把封板弱当硬伤 = 系统性扔掉「买得到且赚」的那批。
    """
    g = gene_dir(tmp_path, "600001")
    # 晚封 10:30 + 封单极薄 + 炸板 2 次 → seal_gate 分数很低
    rows = [zt("600001", "封板弱", 2, cje=8.0, seal=0.2, lt=50.0, fbt="103000", zbc=2)]
    res = DS.screen_pool(rows, gene_cache=g, trade_date="2026-11-03", root=tmp_path)
    s = res["screened"][0]
    assert not any("封板弱" in r for r in s["rejects"]), s["rejects"]
    # 但要留痕，不能装作没看见
    assert any("封板偏弱" in u for u in s["unknown"]), s["unknown"]


def test_strong_seal_recorded_as_support(tmp_path):
    g = gene_dir(tmp_path, "600001")
    rows = [zt("600001", "封板强", 4, cje=9.0, seal=3.0, lt=50.0, fbt="092500", zbc=0)]
    res = DS.screen_pool(rows, gene_cache=g, trade_date="2026-11-03", root=tmp_path)
    s = res["screened"][0]
    assert any("封板强" in x for x in s["supports"]), s["supports"]


# ── ⑨ 跨日追踪：今天被拒、明天又涨停 ───────────────────────────────────────
def test_reject_followup_tracks_promotion(tmp_path):
    """用户「明天如果它还涨停了，我们就会进一步观察判断，总会被我们发现」。"""
    zt_dir = tmp_path / "cache" / "zt_history"
    zt_dir.mkdir(parents=True)
    sc_dir = tmp_path / "cache" / "daily_screen"
    sc_dir.mkdir(parents=True)
    # 昨日过筛：600001 被拒（2 板）
    (sc_dir / "screen_2026-11-02.json").write_text(json.dumps({
        "date": "2026-11-02", "screened": [
            {"code": "600001", "name": "甲", "boards": 2, "verdict": "reject",
             "rejects": ["换手板成交额 1.0亿 < 5亿"], "supports": [], "unknown": []},
            {"code": "600002", "name": "乙", "boards": 2, "verdict": "reject",
             "rejects": ["未成线"], "supports": [], "unknown": []},
        ]}), encoding="utf-8")
    # 今日涨停池：600001 晋级到 3 板，600002 断板
    (zt_dir / "zt_2026-11-03.json").write_text(json.dumps({
        "date": "2026-11-03", "zt_list": [{"code": "600001", "name": "甲", "boards": 3}],
    }), encoding="utf-8")

    fu = DS.reject_followup(tmp_path, "2026-11-03")
    assert fu["available"] is True
    assert [x["code"] for x in fu["still_zt"]] == ["600001"]
    assert [x["code"] for x in fu["promoted"]] == ["600001"]
    assert fu["promoted"][0]["prev_boards"] == 2
    assert fu["promoted"][0]["now_boards"] == 3
    # ⛔ 必须明说不是黑名单
    assert "不是黑名单" in fu["note"]


def test_reject_followup_without_prev_returns_unavailable(tmp_path):
    (tmp_path / "cache" / "zt_history").mkdir(parents=True)
    fu = DS.reject_followup(tmp_path, "2026-11-03")
    assert fu["available"] is False


def test_reject_followup_no_promotion(tmp_path):
    zt_dir = tmp_path / "cache" / "zt_history"
    zt_dir.mkdir(parents=True)
    sc_dir = tmp_path / "cache" / "daily_screen"
    sc_dir.mkdir(parents=True)
    (sc_dir / "screen_2026-11-02.json").write_text(json.dumps({
        "date": "2026-11-02", "screened": [
            {"code": "600001", "name": "甲", "boards": 2, "verdict": "reject",
             "rejects": ["x"], "supports": [], "unknown": []}]}), encoding="utf-8")
    (zt_dir / "zt_2026-11-03.json").write_text(json.dumps({
        "date": "2026-11-03", "zt_list": [{"code": "999999", "name": "别", "boards": 1}]}),
        encoding="utf-8")
    fu = DS.reject_followup(tmp_path, "2026-11-03")
    assert fu["available"] is True
    assert fu["still_zt"] == []
    assert fu["promoted"] == []


# ══════════════════════════════════════════════════════════════════════════
# ⑩ 字段归一化：东财原始 ztgc（dm/mc/lbc）必须能被吃下（2026-11-04 重大修复）
# ══════════════════════════════════════════════════════════════════════════
# 症状（上线即空转，前端与主链路均无感）：
#   `pipeline_eod` 传的是 `zt["ztgc"]`，字段是东财原始的 `{dm, mc, lbc, zj, lt, cje}`，
#   而本模块按 `{code, name, boards}` 读 → `zt_count` 恒为 0 →
#   调用方「涨停池里没有这只票」分支把所有候选当 unknown **原样放行**。
#   ⇒ 过筛闸装了等于没装。`pool_admit._norm` 的注释早在 10-21 就警告过同一坑。
def test_eastmoney_ztgc_fields_are_normalized(tmp_path):
    rows = [
        {"dm": "002058", "mc": "紫竹高科", "lbc": 2, "cje": 2.54, "hs": 8.7,
         "zbc": 0, "fbt": "094848", "zj": 0.73, "lt": 30.29, "hy": "电池"},
        {"dm": "002086", "mc": "东方海洋", "lbc": 4, "cje": 7.88, "hs": 14.6,
         "zbc": 3, "fbt": "092500", "zj": 2.16, "lt": 54.07, "hy": "农产品加"},
    ]
    res = DS.screen_pool(rows, gene_cache=tmp_path / "zt_genes",
                         trade_date="2026-11-03", root=tmp_path)
    assert res["zt_count"] == 2, "东财字段未被识别 → 闸门静默失效"
    assert {s["code"] for s in res["screened"]} == {"002058", "002086"}
    assert {s["name"] for s in res["screened"]} == {"紫竹高科", "东方海洋"}
    assert res["market_max_boards"] == 4
    # 封单/流通必须映射成功，否则封板形态整段进 unknown
    zz = next(s for s in res["screened"] if s["code"] == "002058")
    assert zz["facts"]["seal_ratio_pct"] is not None


def test_zt_history_fields_still_work(tmp_path):
    """⛔ 归一化不得破坏原有 `zt_list` 形状（code/name/boards）。"""
    rows = [{"code": "600001", "name": "甲", "boards": 2, "cje_yi": 8.0, "seal": 2.0,
             "lt_yi": 50.0, "fbt": "093000", "zbc": 0, "sector": "机器人"}]
    res = DS.screen_pool(rows, gene_cache=tmp_path / "zt_genes",
                         trade_date="2026-11-03", root=tmp_path)
    assert res["zt_count"] == 1
    assert res["screened"][0]["name"] == "甲"


# ══════════════════════════════════════════════════════════════════════════
# ⑪ 落盘：必须带日期，缺日期拒绝写（2026-11-04）
# ══════════════════════════════════════════════════════════════════════════
def test_screen_pool_returns_date(tmp_path):
    """`persist` 用 result["date"] 拼文件名，缺了就写出 `screen_None.json`，前端永远匹配不上。"""
    res = DS.screen_pool([{"code": "600001", "name": "甲", "boards": 2}],
                         gene_cache=tmp_path / "zt_genes", trade_date="2026-11-03",
                         root=tmp_path)
    assert res["date"] == "2026-11-03"


def test_persist_writes_dated_file(tmp_path):
    res = DS.screen_pool([{"code": "600001", "name": "甲", "boards": 2}],
                         gene_cache=tmp_path / "zt_genes", trade_date="2026-11-03",
                         root=tmp_path)
    p = DS.persist(tmp_path, res)
    assert p.name == "screen_2026-11-03.json"
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8"))["date"] == "2026-11-03"


def test_persist_refuses_missing_date(tmp_path):
    with pytest.raises(ValueError):
        DS.persist(tmp_path, {"zt_count": 1})
    assert not list((tmp_path / "cache" / "daily_screen").glob("*")) if (
        tmp_path / "cache" / "daily_screen").exists() else True


# ══════════════════════════════════════════════════════════════════════════
# ⑫ 独苗高标：≥4 板不再「按独立龙头看待」 (2026-11-04 重大修正)
# ══════════════════════════════════════════════════════════════════════════
# 实测（32 配对日，可成交口径 0<gap≤5%）：
#   · 独苗 ≥4 板  n=32  可成交 5  开收 −1.17%  胜率 **20.0%**
#   · 独苗 3 板   n=52  可成交 8  开收 +1.31%  胜率 62.5%
#   · 成线 ≥3 家  n=58  可成交 12 开收 +1.26%  胜率 **75.0%**
# ⇒ 「高」不是问题，「独苗 + 高位」才是。原实现把 ≥3 板独苗一律当利好放行，
#   于是 11-03 的东方海洋（4 板·同题材 1 家）被判 pass —— 次日 −5.85%、半日换手 36%。
def test_lone_high_board_is_watch_only_not_pass(tmp_path):
    g = gene_dir(tmp_path, "600001")
    rows = [zt("600001", "独苗四板", 4, cje=7.88, seal=2.0, lt=54.0, fbt="092500", zbc=0)]
    res = DS.screen_pool(rows, gene_cache=g, trade_date="2026-11-03", root=tmp_path)
    s = res["screened"][0]
    assert s["verdict"] == "watch_only", s
    assert any("独苗高标" in w for w in s["watch_reasons"]), s["watch_reasons"]
    assert not any("按独立龙头看待" in x for x in s["supports"]), s["supports"]


def test_lone_three_board_keeps_support(tmp_path):
    """3 板独苗该档胜率 62.5%，不能一起打死。"""
    g = gene_dir(tmp_path, "600001")
    rows = [zt("600001", "独苗三板", 3, cje=7.88, seal=2.0, lt=54.0, fbt="092500", zbc=0)]
    res = DS.screen_pool(rows, gene_cache=g, trade_date="2026-11-03", root=tmp_path)
    s = res["screened"][0]
    assert any("独苗高标（3 板）" in x for x in s["supports"]), s["supports"]


def test_lone_low_board_still_rejected(tmp_path):
    g = gene_dir(tmp_path, "600001")
    rows = [zt("600001", "独苗二板", 2, cje=8.0, seal=2.0, lt=50.0, fbt="093000", zbc=0)]
    res = DS.screen_pool(rows, gene_cache=g, trade_date="2026-11-03", root=tmp_path)
    s = res["screened"][0]
    assert s["verdict"] == "reject"
    assert any("未成线" in r for r in s["rejects"])


# ══════════════════════════════════════════════════════════════════════════
# ⑬ watch_only 必须失去接力资格（映射既有 observe_only 身份）
# ══════════════════════════════════════════════════════════════════════════
# ⛔ 语义：「只能看」= 买不到/不该接，绝不能进接力池占名额。
#    `pipeline_eod` 把 `verdict == watch_only` 映射成
#    `participation_eligibility="observe_only"`（`two_pool` 单一源），
#    `pool_admit.admit` 的 `not two_pool.observation_only(r)` 即把它落到观察池。
def test_watch_only_loses_relay_eligibility_via_pool_admit():
    import core.pool_admit as pa
    import core.two_pool as tp

    # 前排（同组最强）+ 2 板 → 正常情况下够格进接力池
    normal = {"code": "600001", "name": "正常", "boards": 2, "carry_cnt": 5,
              "fbt": "092500", "core_theme": "机器人", "theme_mates_cnt": 6,
              "sector_mates_cnt": 6, "score": 70.0, "dragon_rank": 1}
    parked = dict(normal, code="600002", name="只看", score=69.0, dragon_rank=2,
                  participation_eligibility="observe_only",
                  watch_only=True, buyable=False)
    relay, watch, _diag = pa.admit([normal, parked], [])
    relay_codes = {r["code"] for r in relay}
    watch_codes = {r["code"] for r in watch}
    assert "600001" in relay_codes
    assert "600002" not in relay_codes, "watch_only 票不该进接力池占名额"
    assert "600002" in watch_codes, "watch_only 票应留在观察池可见"
    assert tp.observation_only(parked) is True
