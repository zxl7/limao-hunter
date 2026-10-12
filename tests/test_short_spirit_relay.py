# -*- coding: utf-8 -*-
"""板块接力方向（2026-10-08 用户核心功能 · 选项 A「盘中即时」）的回归锁。

用户原话：「当你捕获到这个板块的强势股涨停时，我们要顺势推出应该买什么」。

规则（本测试锁死）：
  ① 只对 top5 主线板块给方向（非主线返回 None —— 体系：不推跟风杂毛）；
  ② front    = 前排（谁领涨 = 已封板里板数最高）+ front_in_signal（是否就是本轮捕捉那只）；
  ③ ladder   = 其余已封板（limit_up_stocks 同板块，板数降序）；
  ④ tradable = 后排可参与（leaders 同板块 + 涨幅≥7.5% + 未在涨停清单，带涨幅）；
  ⑤ 排除：ST / 科创板688 / 黑名单（京能系·金能科技）/ 本轮已推 / 涨幅不足；
  ⑥ ⛔ 开关 ENABLE_SPIRIT_RELAY_HINT=False → 回到「只报异动不给方向」的改动前行为。

卡面固定三段（2026-10-08 用户「带上可参与后排的涨幅，逻辑就很清晰」）：
  👑 前排：谁领涨 ｜🎯 后排可参与：买谁 + 涨幅 ｜🔒 其余已封板：参考

隔离约定：文件状态全部落 tmp_path；mock `os.system`，不推送、不联网、不碰生产 cache。
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("short_spirit_relay", SCRIPTS / "short_spirit.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

REAL_DT = ss.datetime
TODAY = "2026-10-08"
NOW = REAL_DT(2026, 10, 8, 14, 5)


def _freeze(monkeypatch, dt):
    class FakeDT(REAL_DT):
        @classmethod
        def now(cls, tz=None):
            return dt
    monkeypatch.setattr(ss, "datetime", FakeDT)


def _sig(name="领湃科技", code="300530", sector="锂电池", pct=19.98, typ="涨停"):
    return {"name": name, "code": code, "sector": sector, "market_cap_yi": 41.4,
            "amount_yi": 3.47, "pct": pct, "type": typ, "tags": [],
            "resonance": 100, "flow_yi": 1.0, "strength": 0.9, "reason": f"共振[{sector}]"}


def _body(card):
    """卡片正文全文（各 element 的 lark_md 内容拼接）。"""
    return "\n".join((el.get("text") or {}).get("content", "") for el in card["elements"])


# 今日真实形态快照（简版）：锂电池主线 + 已封板梯队 + leaders 里未封板的同板块强势票
LATEST = {
    "top5": [{"name": "锂电池", "zt_count": 8},
             {"name": "大消费", "zt_count": 6}],
    "limit_up_stocks": [
        {"name": "时代万恒", "code": "600241.SS", "sector": "锂电池", "boards": "4天4板", "pct": 9.98},
        {"name": "紫竹高科", "code": "002058.SZ", "sector": "锂电池", "boards": "3天3板", "pct": 10.0},
        {"name": "传艺科技", "code": "002866.SZ", "sector": "锂电池", "boards": "3天3板", "pct": 10.0},
        {"name": "龙蟠科技", "code": "603906.SS", "sector": "锂电池", "boards": "2天2板", "pct": 9.98},
        {"name": "领湃科技", "code": "300530.SZ", "sector": "锂电池", "boards": None, "pct": 19.98},
        {"name": "九阳股份", "code": "002242.SZ", "sector": "大消费", "boards": "4天4板", "pct": 10.0},
    ],
    "leaders": [
        {"name": "金银河", "code": "300619", "sector": "锂电池", "pct": 16.53},
        {"name": "理奇智能", "code": "301599", "sector": "锂电池", "pct": 11.04},
        {"name": "领湃科技", "code": "300530", "sector": "锂电池", "pct": 19.98},   # 已封板 → 不作可接力
        {"name": "弱锂电", "code": "300700", "sector": "锂电池", "pct": 3.2},       # 涨幅不足 → 排除
        {"name": "*ST锂电", "code": "300701", "sector": "锂电池", "pct": 12.0},     # ST → 排除
        {"name": "科创锂电", "code": "688700", "sector": "锂电池", "pct": 15.0},    # 688 → 排除
        {"name": "金能科技", "code": "603113", "sector": "锂电池", "pct": 12.5},    # 黑名单 → 排除
        {"name": "某大消费票", "code": "002999", "sector": "大消费", "pct": 9.0},   # 非当前板块 → 不出现
    ],
    "boards": {},
}


# ── ① 板块名匹配（防裸双向子串假命中）────────────────────────────
def test_sector_match_exact_and_containment():
    assert ss._sector_match("锂电池", "锂电池") is True
    assert ss._sector_match("锂电池", "锂电池概念") is True     # 短名≥3字被包含 → 命中
    assert ss._sector_match("锂电池概念", "锂电池") is True
    assert ss._sector_match("电子", "电子布") is False          # 泛行业名裸子串 → 挡
    assert ss._sector_match("电网", "智能电网") is False        # 同上（2字名不参与包含判定）
    assert ss._sector_match("", "锂电池") is False


# ── ② 主线板块 → 输出前排 + 其余已封板 + 后排可参与 ──────────────
def test_relay_hint_returns_ladder_and_tradable():
    r = ss._relay_hint([_sig()], LATEST)
    assert r is not None and r["sector"] == "锂电池"
    assert r["zt_count"] == 5, "锂电池今日 5 家封板（简版快照）"
    # 前排 = 已封板里板数最高（身位最高），不是本轮捕捉的那只首板
    assert r["front"]["name"] == "时代万恒" and r["front"]["boards"] == "4天4板"
    assert r["front_in_signal"] is False, "本轮捕捉 领湃科技(首板) 不是前排；前排是时代万恒(4板)"
    # 身位行按板数降序
    assert r["ladder"][0]["name"] == "时代万恒" and r["ladder"][0]["boards"] == "4天4板"
    # 可接力只留未封板 + 涨幅达标 + 非ST/688/黑名单
    codes = [x["code"] for x in r["tradable"]]
    assert codes == ["300619", "301599"], f"可接力应为金银河/理奇智能，实际 {codes}"
    assert "300530" not in codes, "已封板票不得出现在可接力（今天买不到）"
    assert "300700" not in codes, "涨幅不足（3.2%）不得进可接力"
    assert "300701" not in codes and "688700" not in codes and "603113" not in codes, \
        "ST / 688 / 黑名单必须排除"


def test_relay_front_in_signal_when_captured_is_front():
    """用户场景「如果他是前排」：本轮捕捉的正是前排 → front_in_signal=True。"""
    sig = _sig(name="时代万恒", code="600241", pct=9.98)
    r = ss._relay_hint([sig], LATEST)
    assert r["front"]["code"] == "600241"
    assert r["front_in_signal"] is True


def test_relay_front_falls_back_to_signal_when_no_sealed(monkeypatch):
    """板块在 top5 但涨停清单里无同板块记录（ladder 空）→ front 退回本轮信号，zt_count=0。"""
    d = {k: v for k, v in LATEST.items()}
    d["limit_up_stocks"] = [x for x in LATEST["limit_up_stocks"] if x["sector"] != "锂电池"]
    r = ss._relay_hint([_sig()], d)
    assert r is not None and r["zt_count"] == 0
    assert r["front"]["code"] == "300530" and r["front_in_signal"] is True
    card = ss.build_card([_sig()], "afternoon", NOW, relay=r)
    body = _body(card)
    assert "今日 0 家涨停" not in body, "无已封板记录时不显示「今日 0 家涨停」"
    assert "👑 前排：领湃科技(300530)" in body


def test_relay_tradable_sorted_by_pct_desc():
    r = ss._relay_hint([_sig()], LATEST)
    pcts = [x["pct"] for x in r["tradable"]]
    assert pcts == sorted(pcts, reverse=True), "可接力按涨幅降序"


def test_relay_excludes_already_pushed_codes():
    """本轮已推的票不再作为「可接力」重复提示（避免自我指涉）。"""
    r = ss._relay_hint([_sig(), _sig(name="金银河", code="300619", pct=16.53)], LATEST)
    assert [x["code"] for x in r["tradable"]] == ["301599"]


# ── ③ 非主线 → 不给方向（不推跟风杂毛）──────────────────────────
def test_relay_none_for_non_mainline_sector():
    """信号所属 sector 不在 top5 → None（体系：只推主线梯队）。"""
    r = ss._relay_hint([_sig(name="某票", code="600000", sector="纺织服装", pct=10.0)], LATEST)
    assert r is None


def test_relay_none_when_no_signal():
    assert ss._relay_hint([], LATEST) is None
    assert ss._relay_hint(None, LATEST) is None


# ── ③b 跨命名体系对齐（2026-10-08 生产实证·接力块整块没出来）──────────
def test_relay_sector_resolved_by_code_when_industry_name_differs():
    """信号 sector = 东财**行业名**「电池」，top5 = **概念名**「锂电池」→ 名称匹配判不中，
    必须靠 code 反查 limit_up_stocks/leaders 的概念名对齐（否则 领湃科技 那张卡没有接力块）。"""
    assert ss._sector_match("锂电池", "电池") is False, "2 字泛名不参与包含判定（防裸子串）"
    sig = _sig(sector="电池")                       # 300530 在 limit_up_stocks/leaders 里 sector=锂电池
    assert ss._relay_sector_of([sig], LATEST) == "锂电池", "code 反查须跨体系对齐"
    r = ss._relay_hint([sig], LATEST)
    assert r is not None and r["sector"] == "锂电池"
    body = _body(ss.build_card([sig], "afternoon", NOW, relay=r))
    assert "🎯 接力方向｜锂电池" in body and "🎯 后排可参与：" in body


def test_relay_sector_still_none_for_unknown_code_and_name():
    """未放宽口径：名称不匹配且 code 不在任何板块清单里 → 仍不给方向（不误推）。"""
    sig = _sig(name="某票", code="600000", sector="纺织服装")
    assert ss._relay_sector_of([sig], LATEST) is None

    d = {k: v for k, v in LATEST.items()}
    d["top5"] = [{"name": "锂电池", "zt_count": 8}]
    d["limit_up_stocks"] = []
    d["leaders"] = []
    assert ss._relay_hint([sig], d) is None


def test_relay_ladder_only_when_no_tradable():
    """该方向全封板（leaders 无未封板同板块票）→ ladder 有、tradable 空，仍返回（不返回 None）。"""
    d = {k: v for k, v in LATEST.items()}
    d["leaders"] = [l for l in LATEST["leaders"] if l["code"] in ("300530", "300700")]
    r = ss._relay_hint([_sig()], d)
    assert r is not None and r["ladder"] and r["tradable"] == []


# ── ④ 开关可逆 ────────────────────────────────────────────────
def test_switch_off_returns_none(monkeypatch):
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_RELAY_HINT", False)
    assert ss._relay_hint([_sig()], LATEST) is None, "关开关应回到「只报异动不给方向」"


def test_production_default_is_on():
    """用户 2026-10-08 明确要「A 盘中即时」→ 本能力默认启用。"""
    src = (SCRIPTS / "short_spirit.py").read_text(encoding="utf-8")
    assert "ENABLE_SPIRIT_RELAY_HINT = True" in src


# ── ⑤ 卡面渲染：👑 前排 / 🎯 后排可参与（带涨幅）/ 🔒 其余已封板 ────
def test_build_card_renders_relay_block():
    r = ss._relay_hint([_sig()], LATEST)
    card = ss.build_card([_sig()], "afternoon", NOW, relay=r)
    body = _body(card)
    assert "🎯 接力方向｜锂电池" in body and "今日 5 家涨停" in body
    # ① 前排 = 谁领涨（带板数与涨幅）；本轮捕捉的是后排 → 明确标出，逻辑无歧义
    assert "👑 前排：时代万恒(600241) 4天4板" in body
    assert "（本轮这只属后排）" in body, "本轮捕捉的领湃非前排 → 卡面须点明"
    # ② 后排可参与 = 买谁 + 涨幅（用户核心诉求：「带上可参与后排的涨幅」）
    #    涨幅走 _pct_tag → 卡面是 `<text_tag color='red'>+16.53%</text_tag>`，故分别断言。
    back = body.split("🎯 后排可参与：")[1].split("\n")[0]
    assert "金银河(300619)" in back and "理奇智能(301599)" in back
    assert "+16.53%" in back and "+11.04%" in back, "后排必须带可参与涨幅"
    # ③ 其余已封板 = 参考（前排/本轮已捕捉的不重复列）
    assert "🔒 后排关联：" in body and "紫竹高科(3天3板)" in body
    assert "时代万恒" not in body.split("🔒 后排关联：")[1], "前排不重复出现在后排关联"
    assert "领湃科技" not in body.split("🔒 后排关联：")[1], "本轮捕捉的不重复出现在后排关联"


def test_build_card_marks_front_when_captured_this_round():
    """本轮捕捉的就是前排 → 👑 行带「← 本轮捕捉」标记（用户「如果他是前排」）。"""
    sig = _sig(name="时代万恒", code="600241", pct=9.98)
    r = ss._relay_hint([sig], LATEST)
    body = _body(ss.build_card([sig], "afternoon", NOW, relay=r))
    assert "👑 前排：时代万恒(600241) 4天4板" in body and "← 本轮捕捉" in body


def test_build_card_without_relay_has_no_block():
    """无 relay（非主线/开关关）→ 卡面不得出现接力块（保持改动前形态）。"""
    card = ss.build_card([_sig()], "afternoon", NOW, relay=None)
    body = _body(card)
    assert "接力方向" not in body


def test_build_card_all_sealed_hint():
    """全封板 → 输出「等明日竞价看前排溢价」提示，不得空白。"""
    d = {k: v for k, v in LATEST.items()}
    d["leaders"] = [l for l in LATEST["leaders"] if l["code"] == "300530"]
    r = ss._relay_hint([_sig()], d)
    card = ss.build_card([_sig()], "afternoon", NOW, relay=r)
    body = _body(card)
    assert "等明日竞价看前排溢价" in body


# ── ⑥ 触发口径：只认「强势股涨停」，快速拉升不给方向 ─────────────
def test_relay_requires_limit_up_type():
    """用户原话「捕获到板块强势股涨停时」→ 快速拉升（低强度）不带方向。"""
    assert ss._relay_hint([_sig(typ="快速拉升")], LATEST) is None
    assert ss._relay_hint([_sig(typ="接近涨停")], LATEST) is not None
    assert ss._relay_hint([_sig(typ="涨停")], LATEST) is not None


# ── ⑦ 限频：冷却 ∧ 内容指纹变化（09-15 铁律，禁纯时间冷却）────────
def _relay_env(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "ROOT", tmp_path)
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)


def test_relay_dedup_identical_content_blocked_within_cooldown(tmp_path, monkeypatch):
    """同板块、内容指纹不变、未过冷却 → 不重发（防同板块连卡刷屏）。"""
    _relay_env(tmp_path, monkeypatch)
    r = ss._relay_hint([_sig()], LATEST)
    emit, fp = ss._relay_should_emit(r, NOW)
    assert emit is True, "首次应发"
    ss._relay_mark(r, fp, NOW)
    emit2, _ = ss._relay_should_emit(r, NOW + __import__("datetime").timedelta(minutes=3))
    assert emit2 is False, "3 分钟后内容未变 → 冷却未过，不重发"


def test_relay_dedup_fingerprint_change_emits_immediately(tmp_path, monkeypatch):
    """内容指纹变了（如可接力票涨停进梯队）→ 立即重发，不被冷却挡住（信息不丢）。"""
    _relay_env(tmp_path, monkeypatch)
    r = ss._relay_hint([_sig()], LATEST)
    _, fp = ss._relay_should_emit(r, NOW)
    ss._relay_mark(r, fp, NOW)
    # 内容变化：金银河涨停 → 进涨停清单（离开可接力）
    d2 = dict(LATEST)
    d2["limit_up_stocks"] = LATEST["limit_up_stocks"] + [
        {"name": "金银河", "code": "300619.SZ", "sector": "锂电池", "boards": None, "pct": 20.0}]
    r2 = ss._relay_hint([_sig()], d2)
    emit2, _ = ss._relay_should_emit(r2, NOW + __import__("datetime").timedelta(minutes=1))
    assert emit2 is True, "指纹变化应即时重发"


def test_relay_cooldown_elapsed_emits_again(tmp_path, monkeypatch):
    """内容不变但冷却已过 → 重发（信息按时提醒）。"""
    _relay_env(tmp_path, monkeypatch)
    r = ss._relay_hint([_sig()], LATEST)
    _, fp = ss._relay_should_emit(r, NOW)
    ss._relay_mark(r, fp, NOW)
    emit2, _ = ss._relay_should_emit(r, NOW + __import__("datetime").timedelta(minutes=25))
    assert emit2 is True


def test_relay_cooldown_zero_always_emits(tmp_path, monkeypatch):
    """RELAY_COOLDOWN_MIN=0 → 等于关闭限频（每张相关卡都显示）。"""
    _relay_env(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, "RELAY_COOLDOWN_MIN", 0)
    r = ss._relay_hint([_sig()], LATEST)
    ss._relay_mark(r, ss._relay_fingerprint(r), NOW)
    assert ss._relay_should_emit(r, NOW)[0] is True


# ── ⑧ 端到端：真实 _run 出卡（tmp 隔离，不推送）──────────────────
def _env(tmp_path, monkeypatch, signals, latest):
    monkeypatch.setattr(ss, "ROOT", tmp_path)
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    snap = tmp_path / "latest.json"
    snap.write_text(json.dumps(latest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ss, "SNAP", snap)
    pp = tmp_path / "pushed.json"
    monkeypatch.setattr(ss, "PUSHED_PATH", pp)
    monkeypatch.setattr(ss, "OUT_PATH", tmp_path / "out.json")
    monkeypatch.setattr(ss, "in_trading", lambda: True)
    monkeypatch.setattr(ss, "load_cfg", lambda: {
        "min_pct": 3.0, "min_pct_down": 7.5, "min_market_cap_yi": 30, "amount_morning_yi": 3.0,
        "amount_afternoon_yi": 2.0, "batch_threshold": 999, "batch_timeout_min": 30,
        "max_signals": 5, "top_per_sector": 2, "weight": {}})
    monkeypatch.setattr(ss, "build_signals",
                        lambda d, n, c: (signals, "afternoon", len(signals), len(signals)))
    monkeypatch.setattr(ss, "build_signals_down", lambda d, n, c: ([], "afternoon", 0, 0))
    _freeze(monkeypatch, NOW)
    pp.write_text(json.dumps({"date": TODAY, "codes": [], "last_push": "",
                              "pending": [], "pending_ts": ""}), encoding="utf-8")
    return pp


def _run(monkeypatch):
    def fake_system(*a, **k):
        return 0
    monkeypatch.setattr(os, "system", fake_system)
    monkeypatch.setattr(sys, "argv", ["short_spirit.py", "--push", "--force"])
    ss.main()


def test_e2e_card_file_contains_relay_block(tmp_path, monkeypatch):
    """端到端：真实 _run 路径产出的卡文件必须含接力块（防『接线漏了』）。"""
    _env(tmp_path, monkeypatch, [_sig()], LATEST)
    _run(monkeypatch)
    cf = tmp_path / "cache" / "short_spirit_card.json"
    assert cf.exists(), "应产出卡文件"
    body = json.dumps(json.loads(cf.read_text(encoding="utf-8")), ensure_ascii=False)
    assert "接力方向｜锂电池" in body
    assert "后排可参与" in body and "金银河" in body
    assert "👑 前排：" in body, "卡面须先给前排（谁领涨）"


def test_e2e_switch_off_card_has_no_relay(tmp_path, monkeypatch):
    """开关关 → 端到端卡文件不含接力块（可逆性端到端证明）。"""
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_RELAY_HINT", False)
    _env(tmp_path, monkeypatch, [_sig()], LATEST)
    _run(monkeypatch)
    cf = tmp_path / "cache" / "short_spirit_card.json"
    body = json.dumps(json.loads(cf.read_text(encoding="utf-8")), ensure_ascii=False)
    assert "接力方向" not in body


def test_e2e_second_identical_card_drops_relay(tmp_path, monkeypatch):
    """端到端限频：连续两张内容相同的卡 → 第二张不带接力块（防同板块连卡刷屏）。"""
    _env(tmp_path, monkeypatch, [_sig()], LATEST)
    _run(monkeypatch)
    cf = tmp_path / "cache" / "short_spirit_card.json"
    assert "接力方向" in json.dumps(json.loads(cf.read_text(encoding="utf-8")), ensure_ascii=False)
    # 第二张：另一个新 code（保证能推），但接力内容指纹不变
    _env(tmp_path, monkeypatch, [_sig(name="另一只锂电", code="300531")], LATEST)
    _run(monkeypatch)
    body = json.dumps(json.loads(cf.read_text(encoding="utf-8")), ensure_ascii=False)
    assert "接力方向" not in body, "同内容第二张卡不应重复带接力块"


# ── ⑥ 东财官方板块码（2026-10-08 用户「系统自造聚合名改为东财官方板块名！！」）──
@pytest.fixture(autouse=True)
def _isolate_official(monkeypatch):
    """官方码来源必须隔离 —— 不得读生产 cache/sector_official_map.json（默认不挂码）。"""
    monkeypatch.setattr(ss, "_official_tag", None)


def _card_body(card):
    return "\n".join((el.get("text") or {}).get("content", "") for el in card["elements"])


def test_card_appends_official_code(monkeypatch):
    """映射层可用 → 接力方向行末挂官方 BK 码（名字对齐东财）。"""
    monkeypatch.setattr(ss, "_official_tag",
                        lambda n: "(BK1303)" if n == "锂电池" else "")
    r = ss._relay_hint([_sig()], LATEST)
    body = _card_body(ss.build_card([_sig()], "afternoon", NOW, relay=r))
    assert "🎯 接力方向｜锂电池(BK1303)" in body


def test_card_omits_code_when_mapper_missing():
    """映射层缺失（_official_tag=None）→ 不挂码，卡面形态同改动前。"""
    r = ss._relay_hint([_sig()], LATEST)
    body = _card_body(ss.build_card([_sig()], "afternoon", NOW, relay=r))
    assert "🎯 接力方向｜锂电池**" in body
    assert "BK" not in body, "映射层不可用时不得出现任何 BK 码"
