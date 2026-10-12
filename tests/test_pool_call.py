# -*- coding: utf-8 -*-
"""9:25 池内裁决（Pool Call）回归锁 —— 2026-09-21 用户「接力池＝提前缩范围，不是提前拍板」。

锁定：
  ① 裁决链七级短路（无竞价 / 超 5% 硬线 / 预案回避 / 低于预期 / 没红开 / 红开无量 / 跟风闸 / 候选）
  ② 名额由 stance 决定（attack 3 / selective 2 / defend 1 / hibernate 0）
  ③ 线族归并（绿电：电力 + 风电设备 → 同一条线）
  ④ 同线核心识别（load_line_cores）：**含已断板核心**，且 broken 看**核心票自身**最后涨停日
     （⛔ 09-21 实测 bug：别被同线其他票的近期涨停刷掉）
  ⑤ 跟风闸可逆（POOL_CALL_CORE_GATE=False → 回到只看单票强度）
  ⑥ 向后兼容：盘后 plan 只有旧口径 `view` → 反推预案档 `bias`
  ⑦ 开关可逆：POOL_CALL_ENABLED=False → 不产出结论（调用方回退旧口径）
  ⑧ apply_to_views 合并语义（保留 plan_view / bias / 9:25 理由）
  ⑨ ⛔ **persist=False（dry-run / --synthesize）绝不落盘冻结快照**（09-21 血案回归锁）

全隔离：tmp_path + monkeypatch，不触真实 cache / 台账 / 网络。
"""
import json
import sys
from pathlib import Path

WC = Path(__file__).resolve().parents[1] / "wealth_code"
if str(WC) not in sys.path:
    sys.path.insert(0, str(WC))

from core import ai_caliber, nav_ledger, pool_call, strategy_metrics  # noqa: E402
from pipelines import pipeline_verify as pv  # noqa: E402


# ── 夹具 ────────────────────────────────────────────────────────────
def _cand(code, name, boards=1, sector="测试题材", exp=(1.0, 3.0), need=1.0):
    return {"code": code, "name": name, "boards": boards, "sector": sector,
            "bucket_label": "接力池",
            "expectation": {"expected_range": list(exp),
                            "auction_amount_min_yi": need}}


def _q(prev, gap_pct, amt=20.0):
    return {"prev_close": prev,
            "auction_price": round(prev * (1 + gap_pct / 100.0), 3),
            "auction_amount_yi": amt}


def _plan(views, stance="selective"):
    """盘后预案（bias 口径）。views = {code: bias 或 dict}。"""
    out = {}
    for code, v in views.items():
        out[code] = v if isinstance(v, dict) else {"bias": v}
    return {"stance": stance, "views": out}


def _zt(rows):
    return {"zt_list": rows}


def _core(code, name, boards, sector="电力"):
    """涨停池行夹具。默认 `sector="电力"` → 归入「绿电」线族（`line_family` 走 _theme_key）。

    ⚠️ 同一条线里两只票的**行业名可以不同**（闽东电力=电力 / 锡华科技=风电设备），
    归并后同属「绿电」——这正是 09-21 用户抓包的修复点。
    """
    return {"code": code, "name": name, "boards": boards, "sector": sector}


# ── 1) 裁决链七级 ─────────────────────────────────────────────────────
def test_no_quote_goes_watch_not_skip():
    """① 无竞价数据 → 观察（诚实降级，⛔ 不是放弃、不是买入）。"""
    res = pool_call.call_pool({"relay": [_cand("000001", "甲票")], "watch": []}, {})
    v = res["views"]["000001"]
    assert v["view"] == "watch"
    assert "没拿到" in v["reason"]


def test_above_user_line_gives_up():
    """② 竞价 > 用户 5% 硬线 → 放弃（AI 自定上限不参与裁决）。"""
    res = pool_call.call_pool({"relay": [_cand("000001", "甲票")], "watch": []},
                              {"000001": _q(10.0, 6.5)})
    v = res["views"]["000001"]
    assert v["view"] == "skip"
    assert "5%" in v["reason"]


def test_plan_avoid_gives_up():
    """③ 昨日预案＝回避 → 放弃（预案只降不升的否决权）。"""
    res = pool_call.call_pool({"relay": [_cand("000001", "甲票")], "watch": []},
                              {"000001": _q(10.0, 2.0)},
                              plan=_plan({"000001": "avoid"}))
    assert res["views"]["000001"]["view"] == "skip"


def test_below_expected_zone_gives_up():
    """④ 低于预期区间下沿 → 放弃。"""
    res = pool_call.call_pool({"relay": [_cand("000001", "甲票", exp=(2.0, 4.0))], "watch": []},
                              {"000001": _q(10.0, -1.0)})
    v = res["views"]["000001"]
    assert v["view"] == "skip"
    assert "低于昨天的预期" in v["reason"]


def test_no_red_open_goes_watch():
    """⑤ 没红开（gap ≤ 0）→ 观察（区间给宽时也不会被当成买入）。"""
    res = pool_call.call_pool({"relay": [_cand("000001", "甲票", exp=(-3.0, 4.0))], "watch": []},
                              {"000001": _q(10.0, -0.5)})
    v = res["views"]["000001"]
    assert v["view"] == "watch"
    assert "没红开" in v["reason"]


def test_red_open_weak_volume_goes_watch():
    """⑥ 红开但量能 < 目标 × VOLUME_WEAK_RATIO → 观察（无量的高开＝诱多）。"""
    res = pool_call.call_pool({"relay": [_cand("000001", "甲票", need=10.0)], "watch": []},
                              {"000001": _q(10.0, 3.0, amt=1.0)})
    v = res["views"]["000001"]
    assert v["view"] == "watch"
    assert "量不够" in v["reason"]


def test_qualified_becomes_participate():
    """⑧ 竞价达标 + 量足 + 预案不回避 → 买入。"""
    res = pool_call.call_pool({"relay": [_cand("000001", "甲票", need=1.0)], "watch": []},
                              {"000001": _q(10.0, 3.0, amt=5.0)})
    v = res["views"]["000001"]
    assert v["view"] == "participate"
    assert "+3.00%" in v["reason"]


# ── 2) 名额 ───────────────────────────────────────────────────────────
def _pool_of(n, start=1):
    return [f"{start + i:06d}" for i in range(n)]


def test_quota_follows_stance():
    """名额 = attack 3 / selective 2 / defend 1 / hibernate 0。"""
    codes = _pool_of(5)
    cands = {"relay": [_cand(c, f"票{c}", boards=5 - i) for i, c in enumerate(codes)],
             "watch": []}
    quotes = {c: _q(10.0, 3.0, amt=5.0) for c in codes}
    for stance, want in (("attack", 3), ("selective", 2), ("defend", 1), ("hibernate", 0)):
        res = pool_call.call_pool(cands, quotes, plan=_plan({}, stance=stance))
        got = [c for c, v in res["views"].items() if v["view"] == "participate"]
        assert len(got) == want, f"{stance} 期望 {want} 只，实得 {len(got)} 只 {got}"


def test_quota_overflow_goes_watch_with_reason():
    """名额外的达标票 → 观察，理由说明"今天只买前 N 只"。"""
    codes = _pool_of(3)
    cands = {"relay": [_cand(c, f"票{c}", boards=3 - i) for i, c in enumerate(codes)],
             "watch": []}
    quotes = {c: _q(10.0, 3.0, amt=5.0) for c in codes}
    res = pool_call.call_pool(cands, quotes, plan=_plan({}, stance="defend"))
    bu = [c for c, v in res["views"].items() if v["view"] == "participate"]
    watch = [v for v in res["views"].values() if v["view"] == "watch"]
    assert len(bu) == 1 and len(watch) == 2
    assert any("只买前 1 只" in v["reason"] for v in watch)


def test_candidate_ranking_prefers_higher_boards():
    """候选排序：同为非核心时，板数高的先占名额。"""
    cands = {"relay": [_cand("000001", "低板票", boards=1),
                       _cand("000002", "高板票", boards=4)],
             "watch": []}
    quotes = {"000001": _q(10.0, 2.0, amt=5.0), "000002": _q(10.0, 2.0, amt=5.0)}
    res = pool_call.call_pool(cands, quotes, plan=_plan({}, stance="defend"))
    assert [c for c, v in res["views"].items() if v["view"] == "participate"] == ["000002"]


def test_mech_leader_normal_ranks_with_focus_not_below_it():
    """用户 09-24 确认：强龙 normal 不能被普通 focus 用软理由压掉。

    两票竞价、量能和板数一致，仅强龙有接力池第1 + 09:30秒板 + 带动6家。
    defend 只有一个名额时，应优先强龙；盘后 bias 保留 normal，不能被暗改 focus。
    """
    dragon = _cand("000001", "强龙", boards=3, need=1.0)
    dragon.update({"relay_rank": 1, "fbt": "093000", "carry_cnt": 6, "cje_yi": 10.0})
    ordinary_focus = _cand("000002", "普通重点", boards=3, need=1.0)
    ordinary_focus.update({"relay_rank": 2, "fbt": "101500", "carry_cnt": 1, "cje_yi": 10.0})
    quotes = {"000001": _q(10.0, 2.0, amt=5.0), "000002": _q(10.0, 2.0, amt=5.0)}
    res = pool_call.call_pool({"relay": [dragon, ordinary_focus], "watch": []}, quotes,
                              plan=_plan({"000001": "normal", "000002": "focus"}, stance="defend"),
                              line_cores={})
    winner = res["views"]["000001"]
    assert winner["view"] == "participate" and winner["quota_rank"] == 1
    assert winner["bias"] == "normal", "融合只改09:25排序，不能篡改盘后预案"
    assert winner["mechanical_leader"] is True
    assert "接力池第1" in (winner["mechanical_leader_note"] or "")
    assert "机械强龙" in winner["reason"]
    assert res["views"]["000002"]["view"] == "watch"


def test_mech_leader_fusion_switch_restores_focus_first(monkeypatch):
    """可逆开关关掉后，回到原先 focus → 板数 的排序。"""
    monkeypatch.setattr(pool_call, "ENABLE_MECH_LEADER_FUSION", False)
    dragon = _cand("000001", "强龙", boards=3, need=1.0)
    dragon.update({"relay_rank": 1, "fbt": "093000", "carry_cnt": 6, "cje_yi": 10.0})
    ordinary_focus = _cand("000002", "普通重点", boards=3, need=1.0)
    ordinary_focus.update({"relay_rank": 2, "fbt": "101500", "carry_cnt": 1, "cje_yi": 10.0})
    quotes = {"000001": _q(10.0, 2.0, amt=5.0), "000002": _q(10.0, 2.0, amt=5.0)}
    res = pool_call.call_pool({"relay": [dragon, ordinary_focus], "watch": []}, quotes,
                              plan=_plan({"000001": "normal", "000002": "focus"}, stance="defend"),
                              line_cores={})
    assert res["views"]["000002"]["view"] == "participate"
    assert res["views"]["000001"]["view"] == "watch"


def test_mech_leader_does_not_bypass_hard_flaw():
    """一字/ST/低成交额/纯跟风不能借机械标签绕过风险门。"""
    flawed = _cand("000001", "低成交强票", boards=3, need=1.0)
    flawed.update({"relay_rank": 1, "fbt": "093000", "carry_cnt": 6, "cje_yi": 1.0})
    assert pool_call._is_mech_leader(flawed) is False


# ── 3) 线族归并（绿电）───────────────────────────────────────────────
def test_line_family_merges_power_and_wind_into_one_line():
    """🔌 电力 / 风电设备 / 电网设备 / 光伏设备 → 同一「绿电」线（用户 09-21 抓包）。"""
    fams = {pool_call.line_family({"sector": s})
            for s in ("电力", "风电设备", "电网设备", "光伏设备", "智能电网")}
    assert fams == {"绿电"}, fams


def test_line_family_keeps_lithium_separate():
    """锂电系仍留「新能源」，⛔ 不被绿电吞掉。"""
    assert pool_call.line_family({"sector": "电池"}) == "新能源"


# ── 4) 同线核心识别（含已断板核心）──────────────────────────────────
def _seed_zt(root: Path, day: str, rows: list[dict]) -> None:
    d = root / "cache" / "zt_history"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"zt_{day}.json").write_text(json.dumps(_zt(rows), ensure_ascii=False),
                                      encoding="utf-8")


def test_load_line_cores_broken_uses_core_own_last_day(tmp_path):
    """⛔ 09-21 实测 bug 回归：核心（闽东电力 6 板）09-17 断板，⛔ 不得因同线跟风票
    09-18 仍在板而把线族的 last_date 刷成 09-18 → `broken` 误判 False。

    真实场景：闽东电力 000993 09-16 六连板 → 09-17 断板 → 不在 09-18 候选池；
    池内只剩 09-15 才首板的跟风票锡华科技 603248(4板)。
    """
    _seed_zt(tmp_path, "2026-09-15", [_core("000993", "闽东电力", 4),
                                      _core("603248", "锡华科技", 2, "风电设备")])
    _seed_zt(tmp_path, "2026-09-16", [_core("000993", "闽东电力", 6),
                                      _core("603248", "锡华科技", 3, "风电设备")])
    _seed_zt(tmp_path, "2026-09-17", [_core("603248", "锡华科技", 3, "风电设备")])  # 闽东断板
    _seed_zt(tmp_path, "2026-09-18", [_core("603248", "锡华科技", 4, "风电设备")])
    cores = pool_call.load_line_cores("2026-09-18", root=tmp_path)
    lc = cores["绿电"]
    assert lc["code"] == "000993" and lc["boards"] == 6, lc
    assert lc["broken"] is True, f"核心 000993 最后涨停 09-16 ≠ 窗口最新 09-18 → 必须 broken ({lc})"


def test_load_line_cores_core_still_in_board_not_broken(tmp_path):
    """核心最新交易日仍在板 → broken=False（用于「不必硬拦」的排序路径）。"""
    _seed_zt(tmp_path, "2026-09-17", [_core("000993", "闽东电力", 5),
                                      _core("603248", "锡华科技", 3, "风电设备")])
    _seed_zt(tmp_path, "2026-09-18", [_core("000993", "闽东电力", 6),
                                      _core("603248", "锡华科技", 4, "风电设备")])
    cores = pool_call.load_line_cores("2026-09-18", root=tmp_path)
    assert cores["绿电"]["code"] == "000993"
    assert cores["绿电"]["broken"] is False


def test_load_line_cores_no_files_degrades_honestly(tmp_path):
    """窗口内无涨停池文件 → {}（诚实降级，不编造核心）。"""
    assert pool_call.load_line_cores("2026-09-18", root=tmp_path) == {}


# ── 5) 跟风闸 ─────────────────────────────────────────────────────────
def _follower_setup():
    """闽东电力 6 板已断板（核心不在池） + 锡华科技 4 板在池内且竞价达标。"""
    cores = {"绿电": {"code": "000993", "name": "闽东电力", "boards": 6,
                      "last_date": "2026-09-16", "last_boards": 6, "broken": True}}
    cands = {"relay": [_cand("603248", "锡华科技", boards=4, sector="风电设备")],
             "watch": []}
    quotes = {"603248": _q(10.0, 4.5, amt=5.0)}
    return cands, quotes, cores


def test_follower_gate_demotes_to_watch_and_names_core():
    """🐣 核心已断板且板数高出 ≥2 → 跟风票不占名额，理由点名核心是谁。"""
    cands, quotes, cores = _follower_setup()
    res = pool_call.call_pool(cands, quotes, plan=_plan({}, stance="defend"),
                              line_cores=cores)
    v = res["views"]["603248"]
    assert v["view"] == "watch"
    assert v.get("follower_gate") is True
    assert "闽东电力" in v["reason"] and "跟风" in v["reason"]


def test_follower_gate_off_restores_old_behaviour(monkeypatch):
    """⛔ 可逆开关：POOL_CALL_CORE_GATE=False → 回到只看单票强度（该票恢复买入）。"""
    monkeypatch.setattr(pool_call, "POOL_CALL_CORE_GATE", False)
    cands, quotes, cores = _follower_setup()
    res = pool_call.call_pool(cands, quotes, plan=_plan({}, stance="defend"),
                              line_cores=cores)
    assert res["views"]["603248"]["view"] == "participate"


def test_follower_gate_does_not_fire_when_core_still_in_board():
    """核心仍在板（未断板）→ 不硬拦，靠排序表达（避免误杀 2 进 3 倾斜）。"""
    cores = {"绿电": {"code": "000993", "name": "闽东电力", "boards": 6,
                      "last_date": "2026-09-18", "last_boards": 6, "broken": False}}
    cands = {"relay": [_cand("603248", "锡华科技", boards=4, sector="风电设备")],
             "watch": []}
    res = pool_call.call_pool(cands, {"603248": _q(10.0, 4.5, amt=5.0)},
                              plan=_plan({}, stance="defend"), line_cores=cores)
    assert res["views"]["603248"]["view"] == "participate"


# ── 6) 向后兼容 ───────────────────────────────────────────────────────
def test_bias_backfilled_from_legacy_view():
    """旧格式 plan（只有 view 终态）→ 反推预案档：participate→focus / skip→avoid。

    实证：`cache/ai_view_2026-09-21.json` 由 09-18 15:40 旧代码写入（无 bias 字段），
    09-21 09:25 池内裁决必须照常工作，⛔ 不得因为缺 bias 而静默失效。
    """
    plan = {"stance": "defend",
            "views": {"000001": {"view": "participate", "name": "甲票"},
                      "000002": {"view": "skip", "name": "乙票"},
                      "000003": {"view": "watch", "name": "丙票"}}}
    assert pool_call._bias_of(plan, "000001") == "focus"
    assert pool_call._bias_of(plan, "000002") == "avoid"
    assert pool_call._bias_of(plan, "000003") == "normal"
    # 回避档生效：乙票即便竞价达标也放弃
    cands = {"relay": [_cand("000002", "乙票", need=1.0)], "watch": []}
    res = pool_call.call_pool(cands, {"000002": _q(10.0, 2.0, amt=5.0)}, plan=plan)
    assert res["views"]["000002"]["view"] == "skip"


def test_missing_plan_entry_defaults_normal():
    """预案里没有该票 → normal（宽容·不否决）。"""
    assert pool_call._bias_of(_plan({}), "999999") == "normal"
    assert pool_call._bias_of(None, "999999") == "normal"


# ── 7) 开关可逆 ───────────────────────────────────────────────────────
def test_switch_off_produces_no_verdict():
    """⛔ POOL_CALL_ENABLED=False → 不产出结论（调用方据此回退旧口径）。"""
    res = pool_call.call_pool({"relay": [_cand("000001", "甲票")], "watch": []},
                              {"000001": _q(10.0, 3.0)}, enabled=False)
    assert res["enabled"] is False and res["views"] == {}


# ── 8) apply_to_views 合并 ────────────────────────────────────────────
def test_apply_to_views_keeps_plan_trail():
    """合并后：`view` 被 9:25 结论覆写、原口径留在 `plan_view`、`bias` 与理由随行，池外票不动。"""
    res = pool_call.call_pool({"relay": [_cand("000001", "甲票", need=1.0)], "watch": []},
                              {"000001": _q(10.0, 3.0, amt=5.0)},
                              plan=_plan({"000001": "focus"}))
    views = {"000001": {"view": "watch", "reason": "旧口径理由"},
             "000777": {"view": "participate", "reason": "池外票"}}
    out = pool_call.apply_to_views(res, views)
    assert out["000001"]["view"] == "participate"
    assert out["000001"]["plan_view"] == "watch"
    assert out["000001"]["bias"] == "focus"
    assert out["000001"]["reason_925"]
    assert out["000777"] == {"view": "participate", "reason": "池外票"}


# ── 9) ⛔ persist=False 绝不落盘（09-21 血案回归锁）────────────────────
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(pv, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(nav_ledger, "LEDGER_PATH", tmp_path / "nav.jsonl")
    monkeypatch.setattr(strategy_metrics, "METRICS_PATH", tmp_path / "metrics.json")
    # 不依赖真实 zt 归档：线核心置空（本组只验落盘门，不验核心识别）
    monkeypatch.setattr(pool_call, "load_line_cores", lambda *a, **k: {})
    monkeypatch.setattr(ai_caliber, "load_ai_decisions",
                        lambda td: {"000001": {"view": "participate", "name": "甲票",
                                               "reason": "单测", "entry_max_gap": 5.0}})


def _rc():
    cands = {"date": "2026-09-14", "environment": {"regime": "neutral"},
             "relay": [_cand("000001", "甲票", need=1.0)], "watch": []}
    quotes = {"000001": _q(10.0, 3.0, amt=5.0)}
    return cands, quotes


def test_run_verify_dry_run_does_not_freeze(monkeypatch, tmp_path):
    """⛔⛔ 09-21 血案：`persist=False`（dry-run / `--synthesize`）语义＝"只计算不落盘"，
    但原实现**无条件** `save_pool_call` → 一次离线重渲染就把结论冻进生产
    `pool_call-<date>.json`，随后被 settle / 盘后重跑当"当日真实裁决"读回。

    断言：dry-run 既不写冻结快照，也不把已有快照读进来（必须用传入 quotes 现算）。
    """
    _isolate(monkeypatch, tmp_path)
    cands, quotes = _rc()
    # 预置一份"当日已冻结"快照（内容与传入 quotes 不同）——dry-run 必须忽略它
    (tmp_path / "pool_call-2026-09-15.json").write_text(
        json.dumps({"views": {"000001": {"view": "skip", "reason": "冻结快照"}}},
                   ensure_ascii=False), encoding="utf-8")
    res = pv.run_verify(candidates=cands, quotes=quotes, out_nav=False, persist=False)
    assert res["summary"]["pool_call"] is not None            # 结论照常算出来供渲染
    frozen = json.loads((tmp_path / "pool_call-2026-09-15.json").read_text(encoding="utf-8"))
    assert frozen["views"]["000001"]["reason"] == "冻结快照", "dry-run 不得覆写已冻结快照"
    assert res["summary"]["pool_views"]["000001"]["view"] == "participate", \
        "dry-run 必须用传入 quotes 现算，⛔ 不得读回真实冻结结论"


def test_run_verify_real_run_freezes_once(monkeypatch, tmp_path):
    """真实运行（persist=True）→ 首次计算并冻结；再跑 → 读同一份（幂等·结论唯一）。"""
    _isolate(monkeypatch, tmp_path)
    cands, quotes = _rc()
    pv.run_verify(candidates=cands, quotes=quotes, out_nav=False, persist=True)
    p1 = tmp_path / "pool_call-2026-09-15.json"
    assert p1.exists(), "真实运行必须冻结结论（卡面/账本/AI 轨三方同源）"
    first = json.loads(p1.read_text(encoding="utf-8"))
    assert first["meta"]["caliber"] == "pool_call_925"
    # 二次运行给一份"竞价变差"的数据 → 仍读已冻结那份（当日结论唯一）
    quotes2 = {"000001": _q(10.0, -5.0, amt=5.0)}
    pv.run_verify(candidates=cands, quotes=quotes2, out_nav=False, persist=True)
    assert json.loads(p1.read_text(encoding="utf-8")) == first


# ── ⑩ 跟风闸「归并一致性守卫」（2026-10-12 传媒事故回归锁）──────────────────
def test_core_gate_blocks_normal_follower():
    """跟风闸正常生效：核心 3 板已断板、候选 1 板、线族归并一致 → 拦（不占名额）。"""
    cand = _cand("000001", "真跟风", boards=1, sector="半导体")
    res = pool_call.call_pool(
        {"relay": [cand], "watch": []}, {"000001": _q(10.0, 2.0)},
        plan=_plan({"000001": "normal"}, stance="defend"),
        line_cores={"AI算力": {"code": "666666", "name": "传艺科技",
                               "boards": 3, "broken": True}})
    assert res["views"]["000001"].get("follower_gate") is True


def test_core_gate_skipped_when_line_inconsistent():
    """⛔ 10-12 传媒事故回归：候选 core_theme 归线 与 sector 归线**矛盾**
    （core_theme 被误标「AI算力」、sector=数字媒体→传媒）→ 归并可疑 → ⛔ 不硬拦。"""
    cand = _cand("000001", "疑似误归", boards=1, sector="数字媒体")
    cand["core_theme"] = "AI算力"          # 模拟被已删的泛「AI」规则误标
    res = pool_call.call_pool(
        {"relay": [cand], "watch": []}, {"000001": _q(10.0, 2.0)},
        plan=_plan({"000001": "normal"}, stance="defend"),
        line_cores={"AI算力": {"code": "666666", "name": "传艺科技",
                               "boards": 3, "broken": True}})
    assert not res["views"]["000001"].get("follower_gate"), "归并矛盾 → 守卫应阻止硬拦"


def test_core_gate_consistency_switch_off(monkeypatch):
    """⛔ 可逆：POOL_CALL_LINE_CONSISTENCY_GATE=False → 回到「只看板数差 + 断板」硬拦。"""
    monkeypatch.setattr(pool_call, "POOL_CALL_LINE_CONSISTENCY_GATE", False)
    cand = _cand("000001", "疑似误归", boards=1, sector="数字媒体")
    cand["core_theme"] = "AI算力"
    res = pool_call.call_pool(
        {"relay": [cand], "watch": []}, {"000001": _q(10.0, 2.0)},
        plan=_plan({"000001": "normal"}, stance="defend"),
        line_cores={"AI算力": {"code": "666666", "name": "传艺科技",
                               "boards": 3, "broken": True}})
    assert res["views"]["000001"].get("follower_gate") is True, "关开关 → 恢复硬拦"
