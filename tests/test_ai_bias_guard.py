# -*- coding: utf-8 -*-
"""AI 盘后预案 `bias` 的**越权兜底**回归锁（2026-09-22 用户抓包「接力池#1 真龙被判观察」）。

## 事故（实证 2026-09-21 盘后 → 09-22 执行）
- 接力池 **#1** 内蒙新华(603230)：09:25:02 **秒板**、带动 **5 家**、带「先发」tag、龙头分 65.1
  → AI 判 **`normal`（一般/观察）**；
- 接力池 **#6** 世联行(002285)：**13:17 午后**封板、**无「先发」**、龙头分 39.31
  → AI 判 **`focus`（重点看）**。
- AI 的降级理由是「收入降24%、净利降82%、炒名字、砸开3次」——**全部不属于四类硬伤**
  （🤖-1c②：一字买不到 / ST / 成交额<5亿 / 纯跟风无带动；「高位炸板=辨识度」按 P2-9 是**加分**）。
- ⛔ 危害：裁决链第③条「昨日预案＝回避 → **放弃**」是**硬否决** —— 若 AI 给真龙 avoid，
  09:25 会直接不买（09-22 只是恰好给了 normal 才被机械层救回）。

## 本锁覆盖
1. `avoid` 无硬伤 → **降级 normal** + `bias_warning` 留痕；
2. `avoid` 有硬伤 → **保留**（不误伤）；
3. 非 focus 却握有**强龙机械证据** → 只告警留痕，**不改 bias**；
4. ⛔ **判据区分度**：`先发` tag / `fbt≤10:00` **不得**单独触发（实测 16 只里 13 只有「先发」、
   8 只在 09:31~09:56 封板 → 会退化成噪声，同「全他妈打上孤军」的教训）；
5. 开关 `ENABLE_BIAS_AVOID_GUARD=False` → 可逆回退（不降级）。
"""
import importlib.util
import json
from pathlib import Path

WC = Path(__file__).resolve().parents[1] / "wealth_code"
spec = importlib.util.spec_from_file_location("ai_ledger", WC / "scripts" / "ai_ledger.py")
al = importlib.util.module_from_spec(spec)
spec.loader.exec_module(al)


class A:  # 简易 args 容器
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(al, "CACHE", tmp_path)
    monkeypatch.setattr(al, "DECISIONS", tmp_path / "ai_decisions.jsonl")
    monkeypatch.setattr(al, "AI_LEDGER", tmp_path / "ai_nav_history.jsonl")
    monkeypatch.setattr(al, "PY_LEDGER", tmp_path / "account_nav_history.jsonl")
    monkeypatch.setattr(al, "AI_VIEW_DIR", tmp_path)


def _write_cands(tmp_path, date, relay=None, watch=None):
    (tmp_path / f"candidates-{date}.json").write_text(
        json.dumps({"relay": relay or [], "watch": watch or []}, ensure_ascii=False),
        encoding="utf-8",
    )


def _run(tmp_path, picks):
    f = tmp_path / "picks.json"
    f.write_text(json.dumps({"picks": picks}, ensure_ascii=False), encoding="utf-8")
    rc = al.cmd_record(A(file=str(f), decision_date="2026-09-21", trade_date="2026-09-22"))
    assert rc == 0
    rows = [json.loads(x) for x in (tmp_path / "ai_decisions.jsonl").read_text(
        encoding="utf-8").splitlines() if x.strip()]
    return rows


# 内蒙新华的真实机械画像（09-21 接力卡原值）
LONGTOU = {"code": "603230", "name": "内蒙新华", "relay_rank": 1, "tags": ["先发", "空间"],
           "fbt": "092502", "carry_cnt": 5, "cje_yi": 5.57, "boards": 4, "zbc": 3,
           "is_one_word": False, "is_st": False}
# 世联行（午后封板·无先发·排名 6）
FOLLOWER = {"code": "002285", "name": "世联行", "relay_rank": 6, "tags": ["空间"],
            "fbt": "131706", "carry_cnt": 3, "cje_yi": 8.59, "boards": 4, "zbc": 0,
            "is_one_word": False, "is_st": False}


# ── 1) 四类硬伤的机械判定 ──────────────────────────────────────────
def test_hard_flaws_mechanical():
    assert al._mech_hard_flaws({"is_one_word": True}) == ["一字板买不到"]
    assert al._mech_hard_flaws({"is_st": True}) == ["ST"]
    assert al._mech_hard_flaws({"cje_yi": 1.84}) == ["成交额<5亿"]
    assert al._mech_hard_flaws(
        {"sector_is_lone": True, "theme_has_sector_effect": False, "carry_cnt": 0}) == ["纯跟风无带动"]
    # 干净票 → 无硬伤
    assert al._mech_hard_flaws(LONGTOU) == []
    # 题材层有板块效应 → 不算纯跟风
    assert al._mech_hard_flaws(
        {"sector_is_lone": True, "theme_has_sector_effect": True, "carry_cnt": 0}) == []


# ── 2) 判据区分度（⛔ 防噪声告警）──────────────────────────────────
def test_leader_marks_are_discriminating():
    # 接力池第1 + 真秒板 + 带动 → 命中
    m = al._mech_leader_marks(LONGTOU)
    assert "接力池第1" in m and "09:25秒板" in m and "带动5家" in m
    # ⛔ 只有「先发」tag + 09:31 封板（relay_rank=4）→ **不得**触发
    assert al._mech_leader_marks(
        {"relay_rank": 4, "tags": ["先发"], "fbt": "093100", "carry_cnt": 0, "_bucket": "relay"}) == ""
    # ⛔ 09:56 封板 + 带动4家（无 rank1/秒板）→ 不触发
    assert al._mech_leader_marks(
        {"relay_rank": 3, "tags": ["先发"], "fbt": "095600", "carry_cnt": 4, "_bucket": "watch"}) == ""
    # 真秒板（09:30）→ 触发
    assert "09:30秒板" in al._mech_leader_marks({"fbt": "093000", "carry_cnt": 6, "_bucket": "watch"})


# ── 3) avoid 无硬伤 → 降级 normal ────────────────────────────────
def test_avoid_without_hard_flaw_downgraded(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _write_cands(tmp_path, "2026-09-21", relay=[LONGTOU])
    rows = _run(tmp_path, [{"code": "603230", "name": "内蒙新华", "bias": "avoid",
                            "reason": "收入降24%、净利降82%、炒名字"}])
    r = rows[0]
    assert r["bias"] == "normal", "无硬伤却给 avoid → 必须降级（avoid 会在 9:25 硬否决买入）"
    assert r["view"] is None, "彻底不派：avoid 降级后 ⛔ 不再派生 view（终态由 09:25 裁决）"
    assert "降级 normal" in (r["bias_warning"] or "")


# ── 4) avoid 有硬伤 → 保留（不误伤 AI 的正当回避）──────────────────
def test_avoid_with_hard_flaw_kept(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    st = {"code": "600604", "name": "市北高新", "relay_rank": 5, "tags": [],
          "fbt": "131706", "carry_cnt": 0, "cje_yi": 1.84, "is_st": False, "is_one_word": False}
    _write_cands(tmp_path, "2026-09-21", relay=[st])
    rows = _run(tmp_path, [{"code": "600604", "name": "市北高新", "bias": "avoid", "reason": "成交额过小"}])
    r = rows[0]
    assert r["bias"] == "avoid", "命中硬伤（成交额<5亿）→ 应保留 avoid"
    assert not r["bias_warning"]


# ── 5) normal + 强龙证据：排序已融合，静默留痕而非告警 ────────────────
def test_normal_with_leader_evidence_is_silent(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _write_cands(tmp_path, "2026-09-21", relay=[LONGTOU])
    rows = _run(tmp_path, [{"code": "603230", "name": "内蒙新华", "bias": "normal", "reason": "炒名字"}])
    r = rows[0]
    assert r["bias"] == "normal", "静默处理 ⛔ 不得篡改 AI 原始预案"
    assert not r["bias_warning"], "09:25融合排序已消化 normal/强龙差异，不能制造待处理告警"


# ── 6) focus / 弱票 → 零告警 ──────────────────────────────────────
def test_focus_and_weak_stock_no_warning(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _write_cands(tmp_path, "2026-09-21", relay=[LONGTOU, FOLLOWER])
    rows = _run(tmp_path, [
        {"code": "603230", "name": "内蒙新华", "bias": "focus", "reason": "真龙"},
        {"code": "002285", "name": "世联行", "bias": "normal", "reason": "午后封板·降级"},
    ])
    assert not rows[0]["bias_warning"], "focus 是最高档 → 不应告警"
    assert not rows[1]["bias_warning"], "弱票（无 rank1/无秒板）判 normal 属正常 → 不应告警"


# ── 7) 可逆开关 ─────────────────────────────────────────────────
def test_guard_switch_reversible(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(al, "ENABLE_BIAS_AVOID_GUARD", False)
    _write_cands(tmp_path, "2026-09-21", relay=[LONGTOU])
    rows = _run(tmp_path, [{"code": "603230", "name": "内蒙新华", "bias": "avoid", "reason": "x"}])
    assert rows[0]["bias"] == "avoid", "开关关掉 → 回到改动前行为（不降级）"


# ── 8) 取不到候选文件 → 不降级（宁松不误伤）───────────────────────
def test_missing_candidate_file_does_not_downgrade(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)  # 不写 candidates 文件
    rows = _run(tmp_path, [{"code": "603230", "name": "内蒙新华", "bias": "avoid", "reason": "x"}])
    assert rows[0]["bias"] == "avoid"
    assert not rows[0]["bias_warning"]


# ── 9) normal + 强龙的静默差异随 ai_view 留原始 bias，不落告警 ─────────
def test_silent_difference_lands_in_ai_view_without_warning(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _write_cands(tmp_path, "2026-09-21", relay=[LONGTOU])
    _run(tmp_path, [{"code": "603230", "name": "内蒙新华", "bias": "normal", "reason": "炒名字"}])
    v = json.loads((tmp_path / "ai_view_2026-09-22.json").read_text(encoding="utf-8"))
    assert v["views"]["603230"]["bias"] == "normal"
    assert not v["views"]["603230"]["bias_warning"]
