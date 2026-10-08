# -*- coding: utf-8 -*-
"""板块接力方向（2026-10-08 用户核心功能 · 选项 A「盘中即时」）的回归锁。

用户原话：「当你捕获到这个板块的强势股涨停时，我们要顺势推出应该买什么」。

规则（本测试锁死）：
  ① 只对 top5 主线板块给方向（非主线返回 None —— 体系：不推跟风杂毛）；
  ② ladder   = 已封板身位梯队（limit_up_stocks 同板块，板数降序）；
  ③ tradable = 未封板可接力（leaders 同板块 + 涨幅≥7.5% + 未在涨停清单）；
  ④ 排除：ST / 科创板688 / 黑名单（京能系·金能科技）/ 本轮已推 / 涨幅不足；
  ⑤ ⛔ 开关 ENABLE_SPIRIT_RELAY_HINT=False → 回到「只报异动不给方向」的改动前行为。

隔离约定：文件状态全部落 tmp_path；mock `os.system`，不推送、不联网、不碰生产 cache。
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

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


# ── ② 主线板块 → 输出身位梯队 + 可接力 ──────────────────────────
def test_relay_hint_returns_ladder_and_tradable():
    r = ss._relay_hint([_sig()], LATEST)
    assert r is not None and r["sector"] == "锂电池"
    assert r["zt_count"] == 5, "锂电池今日 5 家封板（简版快照）"
    # 身位行按板数降序
    assert r["ladder"][0]["name"] == "时代万恒" and r["ladder"][0]["boards"] == "4天4板"
    # 可接力只留未封板 + 涨幅达标 + 非ST/688/黑名单
    codes = [x["code"] for x in r["tradable"]]
    assert codes == ["300619", "301599"], f"可接力应为金银河/理奇智能，实际 {codes}"
    assert "300530" not in codes, "已封板票不得出现在可接力（今天买不到）"
    assert "300700" not in codes, "涨幅不足（3.2%）不得进可接力"
    assert "300701" not in codes and "688700" not in codes and "603113" not in codes, \
        "ST / 688 / 黑名单必须排除"


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


# ── ⑤ 卡面渲染 ────────────────────────────────────────────────
def test_build_card_renders_relay_block():
    r = ss._relay_hint([_sig()], LATEST)
    card = ss.build_card([_sig()], "afternoon", NOW, relay=r)
    body = "\n".join((el.get("text") or {}).get("content", "") for el in card["elements"])
    assert "🎯 接力方向｜锂电池" in body
    assert "🔒 已封板" in body and "时代万恒(4天4板)" in body
    assert "🎯 可接力" in body and "金银河(300619)" in body and "理奇智能(301599)" in body


def test_build_card_without_relay_has_no_block():
    """无 relay（非主线/开关关）→ 卡面不得出现接力块（保持改动前形态）。"""
    card = ss.build_card([_sig()], "afternoon", NOW, relay=None)
    body = "\n".join((el.get("text") or {}).get("content", "") for el in card["elements"])
    assert "接力方向" not in body


def test_build_card_all_sealed_hint():
    """全封板 → 输出「等明日竞价看龙一溢价」提示，不得空白。"""
    d = {k: v for k, v in LATEST.items()}
    d["leaders"] = [l for l in LATEST["leaders"] if l["code"] == "300530"]
    r = ss._relay_hint([_sig()], d)
    card = ss.build_card([_sig()], "afternoon", NOW, relay=r)
    body = "\n".join((el.get("text") or {}).get("content", "") for el in card["elements"])
    assert "等明日竞价看龙一溢价" in body


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
    assert "可接力" in body and "金银河" in body


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
