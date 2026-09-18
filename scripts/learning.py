#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
learning.py — 回测自我学习回写（财神-回测 skill · 2026-09-01）

职责：读回测历史 → 偏差检测 → 产出学习建议（可选自动回写阈值配置）。
定位：只动「阈值配置」（expectation_rules.json 等参数文件），
      ⛔ 绝不碰 shared_rules.md 宪法 / 骨架 / 任务 prompt（中央规则原则）。
      ⛔ 建议模式为默认，auto_apply 必须显式开启且单次改动幅度受限。

可逆性设计（用户铁律：架构改动保留可逆开关）：
  - config/learning_config.json 主开关 LEARNING_AUTO_APPLY（默认 False）
  - 每次 auto_apply 前先写回滚快照 cache/backups/learning_rollback_{ts}.json
  - 回滚：python3 scripts/learning.py --rollback <snapshot>
  - 审计：common.audit_log audit("config", ...) 全记录

偏差检测规则（v1·全部可裁决，阈值可配）：
  R1 区间命中率过低  —— 近 N 日区间命中率 < 40% → 预期区间系统性偏乐观 → 建议 base_range 下移 0.3 / 或收紧 max_range_width
  R2 候选命中率过低  —— 近 N 日候选命中率 < 40% → relay 门槛过松 → 建议 relay score 阈值上调 2 分
  R3 情绪分系统性偏差 —— 近 N 日 |情绪差| 均值 > 1.0 → AI 情绪预判系统性偏高/偏低 → 建议研判提示
  R4 区间命中率过高  —— 近 N 日区间命中率 > 80% → 区间过宽无信息量 → 建议收紧 max_range_width 0.3

产出：
  cache/learning_suggestions.json   学习建议（供回测卡渲染）
  cache/learning_history.jsonl      学习历史（append-only）
  cache/backups/learning_rollback_*.json 回滚快照（auto_apply 时）

用法：
  python3 scripts/learning.py                # 只出建议（默认）
  python3 scripts/learning.py --auto-apply   # 按建议回写阈值配置（可逆）
  python3 scripts/learning.py --rollback <snapshot_path>
"""
import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/Users/zxl/WorkBuddy/狸猫猎手")
CACHE = ROOT / "cache"
HISTORY_FILE = CACHE / "backtest_history.jsonl"
LEARNING_CONF = ROOT / "config" / "learning_config.json"
EXPECT_RULES = ROOT / "wealth_code" / "config" / "expectation_rules.json"
RELAY_RULES = ROOT / "wealth_code" / "config" / "relay_score_config.json"
SUGGEST_FILE = CACHE / "learning_suggestions.json"
LEARN_HIST = CACHE / "learning_history.jsonl"
BACKUP_DIR = CACHE / "backups"

sys.path.insert(0, str(ROOT / "common"))
from audit_log import audit  # noqa: E402

BJ_TZ = timezone(timedelta(hours=8))


# ---------------- 配置 ----------------

DEFAULT_LEARNING_CONFIG = {
    "schema": "learning_config_v1",
    "updated": None,
    "note": ("回测自我学习开关。**auto_apply_mode 为唯一模式开关**："
             "off=只出建议不改参数（最保守）；tiered=**分级自动**（数值阈值自动回写·语义类仅建议·默认）；"
             "high_only=只应用 high 级（旧行为）。所有回写均先备份后改·可回滚。"),
    "auto_apply": False,           # 向后兼容：缺 auto_apply_mode 时按此推导模式
    # ── 自动学习分级（2026-09-17 用户「全方位自动学习+迭代」）──────────────────
    # 背景：此前 19 次学习全「建议 N 条 / 应用 0 条」（auto_apply=False）→ 闭环断裂、学而不改。
    # 分级原则：**数值阈值可自动**（影响面小·幅度钳制·快照可回滚）；**语义/结构仅建议**（防误改宪法类）。
    "auto_apply_mode": "tiered",
    "tier_policy": {"low": True, "medium": True, "high": False},
    "max_apply_per_run": 2,        # 单次最多自动应用条数（防批量误改·配合 max_adjust_step 双重钳制）
    "apply_cooldown_days": 3,      # ⛔ 应用冷却：同规则 N 日内已自动应用过 → 不再重复应用
                                   #   （防"命中率连续低 → 连日各下移 0.3"过度调整·5 天累计 1.5）
    "track_effect": True,          # PDCA：记录应用后效果并对上轮做命中率对比
    "max_adjust_step": 0.3,        # 单次最大调整幅度（base_range 下移等）
    "window_days": 5,              # 偏差检测窗口（最近 N 个回测记录）
    "min_samples": 3,              # 样本量守卫：候选/区间评估样本 < 此值不触发 high 建议（防小样本误学习）
    # ⚠️ 术语澄清：`severity`（规则产出的 high/low）= **偏差严重程度**（要不要重视）；
    #    `RULE_RISK`（下方常量）= **自动改动的风险等级**（敢不敢自动改）——两者**解耦**，
    #    切勿混用（曾误用 severity 做自动判据 → 数值类 R1 被划入「仅建议」而不落地）。
    "rules": {
        "R1_range_low":   {"enabled": True, "hit_rate_threshold": 40, "suggest": "base_range 下移 0.3（预期系统性偏乐观）", "target": "expectation_rules.json.base_range", "step": 0.3},
        "R2_cand_low":    {"enabled": True, "hit_rate_threshold": 40, "suggest": "relay first_choice_min 门槛上调 2 分", "target": "relay_score_config.json.bands.first_choice_min", "step": 2},
        "R3_sentiment_bias": {"enabled": True, "abs_diff_threshold": 1.0, "suggest": "AI 情绪预判存在系统性偏差（见 diff 方向）", "target": "ai_judge", "step": None},
        "R4_range_high":  {"enabled": True, "hit_rate_threshold": 80, "suggest": "max_range_width 收紧 0.3（区间过宽无信息量）", "target": "expectation_rules.json.max_range_width", "step": 0.3},
        # R5 AI 判定质量（2026-09-17 用户「让 AI 学会自动学习」·语义类·仅建议不自动改）
        "R5_ai_quality":  {"enabled": True, "participate_hit_min": 40, "watch_miss_max": 40,
                           "suggest": "校准 AI 判定口径（参与门槛 / 观察降级规则）", "target": "ai_judge", "step": None},
        # R6 因子有效性归因（2026-09-18·语义类·仅建议）：分池胜率差 + 反向因子（小样本只作观察）
        "R6_factor_attr": {"enabled": True, "pool_gap_pp": 30, "reverse_diff": 3.0,
                           "suggest": "复核因子权重（观察·小样本不直接改）", "target": "factor_weight", "step": None},
        # R7 卡面质量（2026-09-18·语义类·仅建议）：重复文案/黑话/结构异常
        "R7_card_quality": {"enabled": True, "days": 3,
                            "suggest": "修卡面文案（重复段去重 / 黑话白话化 / 补齐结构）", "target": "card_copy", "step": None},
    },
}


def _next_trade_day(date10: str) -> str:
    """次一交易日（仅跳周末·与 ai_caliber.next_trade_day / pipeline_verify._next_day 同算法）。"""
    try:
        d = datetime.strptime(str(date10), "%Y-%m-%d") + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d.strftime("%Y-%m-%d")
    except Exception:
        return str(date10)


def _ai_quality_stats() -> dict | None:
    """R5 数据源：**AI 判定质量**统计（2026-09-17·「让 AI 学会自动学习」核心之一）。

    口径：AI 决策台账 `ai_decisions.jsonl` 的 `view` × 该票**次日是否进涨停池**（`zt_history`）：
      · `participate.rate` = AI 判「参与」的票次日涨停占比 → 低 ⇒ AI 参与判定偏松 / 环境差
      · `watch.rate`       = AI 判「观察」的票次日涨停占比 → 高 ⇒ **AI 过度保守**（把好票压成观察）
    样本 < 3 → None（不触发·防小样本误学习）。只读落盘文件，不外呼。
    """
    p = ROOT / "wealth_code" / "cache" / "ai_decisions.jsonl"
    try:
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        return None
    zt_dir = CACHE / "zt_history"
    stat: dict[str, list[int]] = {"participate": [0, 0], "watch": [0, 0], "skip": [0, 0]}
    cache_zt: dict[str, set[str]] = {}
    for r in rows:
        code = str(r.get("code") or "")
        view = str(r.get("view") or "participate")
        dd = str(r.get("decision_date") or "")
        if len(code) != 6 or not dd:
            continue
        nd = _next_trade_day(dd)
        if nd not in cache_zt:
            try:
                zt = json.loads((zt_dir / f"zt_{nd}.json").read_text(encoding="utf-8"))
                cache_zt[nd] = {str(x.get("code") or "") for x in (zt.get("zt_list") or [])}
            except Exception:
                cache_zt[nd] = set()
        if not cache_zt[nd]:
            continue                      # 次日归档缺料 → 跳过该票（不编造）
        stat.setdefault(view, [0, 0])
        stat[view][1] += 1
        if code in cache_zt[nd]:
            stat[view][0] += 1
    total = sum(v[1] for v in stat.values())
    if total < 3:
        return None
    out: dict = {"_total": total}
    for k, (h, n) in stat.items():
        out[k] = {"hit": h, "n": n, "rate": round(h / n * 100, 1) if n else None}
    return out


def load_learning_config() -> dict:
    if LEARNING_CONF.exists():
        try:
            return json.loads(LEARNING_CONF.read_text(encoding="utf-8"))
        except Exception:
            pass
    cfg = DEFAULT_LEARNING_CONFIG
    cfg["updated"] = datetime.now(BJ_TZ).isoformat(timespec="seconds")
    LEARNING_CONF.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def load_history(n: int | None = None) -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    rows = []
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    rows.sort(key=lambda r: r.get("settle_date", ""))
    if n:
        rows = rows[-n:]
    return rows


# ---------------- 偏差检测 ----------------

def detect_biases(hist: list[dict], cfg: dict) -> list[dict]:
    """逐规则检测，产出偏差建议列表。"""
    suggestions = []
    rules = cfg["rules"]
    min_samples = cfg.get("min_samples", 3)

    range_rates = [r["summary"].get("range_hit_rate") for r in hist
                   if r["metrics"]["range_hit"].get("available") and r["metrics"]["range_hit"].get("evaluable", 0) > 0]
    cand_rates = [r["summary"].get("candidate_hit_rate") for r in hist
                  if r["metrics"]["candidate_hit"].get("available")]
    cand_counts = [r["metrics"]["candidate_hit"].get("stock_count", 0) for r in hist
                   if r["metrics"]["candidate_hit"].get("available")]
    diffs = [r["summary"].get("sentiment_diff") for r in hist
             if r["metrics"]["sentiment_align"].get("available") and r["summary"].get("sentiment_diff") is not None]

    # R1 区间命中率过低
    if rules["R1_range_low"]["enabled"] and range_rates:
        avg = sum(range_rates) / len(range_rates)
        if avg < rules["R1_range_low"]["hit_rate_threshold"]:
            suggestions.append({
                "rule": "R1_range_low",
                "evidence": f"近{len(range_rates)}日区间命中率均值 {avg:.1f}% < {rules['R1_range_low']['hit_rate_threshold']}%",
                "suggest": rules["R1_range_low"]["suggest"],
                "target": rules["R1_range_low"]["target"],
                "step": rules["R1_range_low"]["step"],
                "severity": "high",
            })

    # R2 候选命中率过低（带样本量守卫：累计评估样本 ≥ min_samples 才触发）
    if rules["R2_cand_low"]["enabled"] and cand_rates:
        avg = sum(cand_rates) / len(cand_rates)
        total_samples = sum(cand_counts)
        if avg < rules["R2_cand_low"]["hit_rate_threshold"]:
            if total_samples < min_samples:
                suggestions.append({
                    "rule": "R2_cand_low",
                    "evidence": f"近{len(cand_rates)}日候选命中率均值 {avg:.1f}% 但样本仅 {total_samples} 只（<{min_samples}），暂不触发回写",
                    "suggest": "样本不足，继续观察（候选命中率偏低方向）",
                    "target": rules["R2_cand_low"]["target"],
                    "step": None,
                    "severity": "low",
                })
            else:
                suggestions.append({
                    "rule": "R2_cand_low",
                    "evidence": f"近{len(cand_rates)}日候选命中率均值 {avg:.1f}% < {rules['R2_cand_low']['hit_rate_threshold']}%（样本 {total_samples} 只）",
                    "suggest": rules["R2_cand_low"]["suggest"],
                    "target": rules["R2_cand_low"]["target"],
                    "step": rules["R2_cand_low"]["step"],
                    "severity": "high",
                })

    # R3 情绪分系统性偏差
    if rules["R3_sentiment_bias"]["enabled"] and diffs:
        avg = sum(diffs) / len(diffs)
        if abs(avg) > rules["R3_sentiment_bias"]["abs_diff_threshold"]:
            direction = "偏高" if avg > 0 else "偏低"
            suggestions.append({
                "rule": "R3_sentiment_bias",
                "evidence": f"近{len(diffs)}日情绪差均值 {avg:+.2f}（预判{direction}）",
                "suggest": f"AI 情绪预判系统性{direction} {abs(avg):.1f} 分，研判时注意校准",
                "target": rules["R3_sentiment_bias"]["target"],
                "step": None,
                "severity": "medium",
            })

    # R4 区间命中率过高（区间过宽）
    if rules["R4_range_high"]["enabled"] and range_rates:
        avg = sum(range_rates) / len(range_rates)
        if avg > rules["R4_range_high"]["hit_rate_threshold"]:
            suggestions.append({
                "rule": "R4_range_high",
                "evidence": f"近{len(range_rates)}日区间命中率均值 {avg:.1f}% > {rules['R4_range_high']['hit_rate_threshold']}%",
                "suggest": rules["R4_range_high"]["suggest"],
                "target": rules["R4_range_high"]["target"],
                "step": rules["R4_range_high"]["step"],
                "severity": "low",
            })

    # R5 AI 判定质量（2026-09-17·语义类·仅建议）：AI 参与命中率过低 / 观察漏判率过高
    r5 = rules.get("R5_ai_quality", {})
    if r5.get("enabled", True):
        q = _ai_quality_stats()
        if q:
            msgs = []
            pa = q.get("participate") or {}
            wa = q.get("watch") or {}
            if pa.get("n", 0) >= min_samples and pa.get("rate") is not None \
                    and pa["rate"] < r5.get("participate_hit_min", 40):
                msgs.append(f"AI 判「参与」的票次日涨停率仅 {pa['rate']}%（{pa['hit']}/{pa['n']}）")
            if wa.get("n", 0) >= min_samples and wa.get("rate") is not None \
                    and wa["rate"] >= r5.get("watch_miss_max", 40):
                msgs.append(f"AI 判「观察」的票次日涨停率高达 {wa['rate']}%（{wa['hit']}/{wa['n']}）→ 疑似过度保守")
            if msgs:
                suggestions.append({
                    "rule": "R5_ai_quality",
                    "evidence": "；".join(msgs),
                    "suggest": r5.get("suggest", "校准 AI 判定口径（参与门槛 / 观察降级规则）"),
                    "target": "ai_judge",
                    "step": None,          # 语义类·无参数可回写 → 仅建议
                    "severity": "high",
                })

    # R6 因子有效性归因（2026-09-18·语义类·仅建议）：分池胜率差 + 反向因子
    r6 = rules.get("R6_factor_attr", {})
    if r6.get("enabled", True):
        fa = _factor_attr_stats()
        if fa:
            msgs = []
            bb = fa.get("by_bucket") or {}
            _rl = bb.get("relay") or {}
            _wa = bb.get("watch") or {}
            if _rl.get("win_rate_pct") is not None and _wa.get("win_rate_pct") is not None:
                gap = float(_rl["win_rate_pct"]) - float(_wa["win_rate_pct"])
                if gap >= r6.get("pool_gap_pp", 30):
                    msgs.append(f"relay 池胜率 {_rl['win_rate_pct']}% 高出 watch 池 "
                                f"{_wa['win_rate_pct']}% 达 {gap:.0f}pp → watch 池宜收严/降权")
            _rev = [f for f in (fa.get("numeric") or [])
                    if f.get("diff") is not None and float(f["diff"]) <= -float(r6.get("reverse_diff", 3.0))
                    and min(int(f.get("n_win") or 0), int(f.get("n_lose") or 0)) >= 10]
            if _rev:
                _rev.sort(key=lambda x: x["diff"])
                msgs.append("反向因子（值越高越易亏·考虑降权）："
                            + "、".join(f"{f.get('label')} {f['diff']:+.2f}" for f in _rev[:3]))
            if msgs:
                suggestions.append({
                    "rule": "R6_factor_attr",
                    "evidence": (f"样本 {fa.get('n_all')} 笔·胜率 {fa.get('win_rate_pct')}%；"
                                 + "；".join(msgs)),
                    "suggest": r6.get("suggest", "复核因子权重（观察·小样本不直接改）"),
                    "target": "factor_weight",
                    "step": None,          # 语义类·无参数可回写 → 仅建议
                    "severity": "high",
                })

    # R7 卡面质量（2026-09-18·语义类·仅建议）：重复文案 / 黑话禁项 / 结构异常
    r7 = rules.get("R7_card_quality", {})
    if r7.get("enabled", True):
        cq = _card_quality_stats(int(r7.get("days", 3)))
        if cq:
            _cards = cq.get("cards") or []
            rep = sum(len(c.get("repeat") or []) for c in _cards)
            jar_words = sorted({j for c in _cards for j in (c.get("jargon") or [])})
            struct = sum(len(c.get("struct") or []) for c in _cards)
            parts = []
            if rep:
                _samp = next((d["sentence"] for c in _cards for d in (c.get("repeat") or [])), "")
                parts.append(f"{rep} 处重复文案（如「{_samp[:36]}…」）")
            if jar_words:
                parts.append(f"黑话词 {len(jar_words)} 个：{'、'.join(jar_words[:6])}")
            if struct:
                parts.append(f"{struct} 处结构异常")
            if parts:
                suggestions.append({
                    "rule": "R7_card_quality",
                    "evidence": f"近{cq.get('days')}天扫描 {cq.get('checked')} 张卡·{cq.get('issues')} 张有问题；"
                                + "；".join(parts),
                    "suggest": r7.get("suggest", "修卡面文案（重复段去重/黑话白话化/补齐结构）"),
                    "target": "card_copy",
                    "step": None,
                    "severity": "high",
                })

    return suggestions


# ---------------- 回写（可逆） ----------------

def backup_snapshot(tag: str) -> str:
    """回滚快照：备份将被修改的配置文件到 cache/backups/。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(BJ_TZ).strftime("%Y%m%d_%H%M%S")
    dst = BACKUP_DIR / f"learning_rollback_{ts}_{tag}.json"
    shutil.copy2(EXPECT_RULES, dst)
    return str(dst)


def apply_suggestion(sug: dict, cfg: dict) -> dict:
    """按建议回写阈值配置（v1 支持 expectation_rules base_range/max_range_width + relay bands.first_choice_min）。"""
    target = sug.get("target", "")
    step = sug.get("step")
    if step is None:
        return {"applied": False, "reason": f"{target} 为 AI 研判建议，非参数回写"}
    max_step = cfg.get("max_adjust_step", 0.3)
    step = max(-max_step, min(max_step, step))  # 幅度受限

    if target == "expectation_rules.json.base_range":
        conf = json.loads(EXPECT_RULES.read_text(encoding="utf-8"))
        br = conf.get("base_range", [0.0, 2.5])
        before = list(br)
        # R1 下移：整体下移 step；R4 收紧宽度：仅动上沿
        if "下移" in sug["suggest"]:
            conf["base_range"] = [round(br[0] - step, 2), round(br[1] - step, 2)]
        else:
            conf["base_range"] = [round(br[0], 2), round(br[1] - step, 2)]
        after = conf["base_range"]
        target_file, target_key = EXPECT_RULES, "base_range"
    elif target == "expectation_rules.json.max_range_width":
        conf = json.loads(EXPECT_RULES.read_text(encoding="utf-8"))
        mw = conf.get("max_range_width", 2.0)
        before = mw
        conf["max_range_width"] = round(max(0.5, mw - step), 2)
        after = conf["max_range_width"]
        target_file, target_key = EXPECT_RULES, "max_range_width"
    elif target == "relay_score_config.json.bands.first_choice_min":
        conf = json.loads(RELAY_RULES.read_text(encoding="utf-8"))
        bands = conf.setdefault("bands", {})
        cur = bands.get("first_choice_min", 80.0)
        before = cur
        bands["first_choice_min"] = round(max(60.0, min(95.0, cur + step)), 2)  # 钳制 60-95
        after = bands["first_choice_min"]
        target_file, target_key = RELAY_RULES, "bands.first_choice_min"
    else:
        return {"applied": False, "reason": f"暂不支持回写目标 {target}"}

    snapshot = backup_snapshot(target.split(".")[0])
    target_file.write_text(json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8")
    audit("config", module="learning", action="auto_apply", rule=sug["rule"],
          target=target, before=before, after=after, snapshot=snapshot)
    return {"applied": True, "rule": sug.get("rule"), "target": target,
            "before": before, "after": after, "snapshot": snapshot, "suggest": sug["suggest"]}


# ---------------- 主流程 ----------------

# ── 规则 → **自动应用风险等级**（2026-09-17·与 severity 严重程度解耦）──────────
# low    = 数值阈值·影响面小（预期区间参数）→ 可自动回写
# medium = 影响面中等（选股池门槛）→ 可自动回写 + 审计留痕
# high   = 语义/结构类 → **仅建议**（防误改宪法/骨架/口径）
RULE_RISK = {
    "R1_range_low": "low",
    "R4_range_high": "low",
    "R2_cand_low": "medium",
    "R3_sentiment_bias": "high",
    "R5_ai_quality": "high",       # AI 判定质量·语义类→仅建议
    "R6_factor_attr": "high",      # 因子归因·语义类→仅建议
    "R7_card_quality": "high",     # 卡面质量·语义类→仅建议
}


def _factor_attr_stats() -> dict | None:
    """R6 数据源：**因子有效性归因**（2026-09-18 用户「做上去」·A 项）。

    调用 `scripts/factor_attribution.py --json`（只读净值账本 + 候选快照·零新增抓取），
    取：`by_bucket`（分池胜率）+ `numeric[].diff`（胜单−败单 因子均值差·负=反向拖后腿）。
    ⛔ 诚实边界（脚本自述）：样本小 → 只作**观察**；本函数 `n_all < 20` 直接 None 不触发。
    """
    import subprocess
    p = ROOT / "scripts" / "factor_attribution.py"
    if not p.exists():
        return None
    try:
        r = subprocess.run([sys.executable, str(p), "--json"],
                           capture_output=True, text=True, timeout=90)
        d = json.loads(r.stdout)
    except Exception:
        return None
    if not isinstance(d, dict) or int(d.get("n_all") or 0) < 20:
        return None
    return d


def _card_quality_stats(days: int = 3) -> dict | None:
    """R7 数据源：**卡面质量自检**（2026-09-18 用户「做上去」·B 项）。

    调用 `scripts/card_quality_check.py --json --days N`，取**门禁生效日后**生成卡的
    重复文案 / 机器黑话（Tier-1 真问题）/ 行业术语（Tier-2 措辞提示）/ 结构异常。
    两类都进建议（措辞提示虽不阻断推送，但正是「卡面白话化」要持续收敛的部分）。
    ⛔ 只读：自检脚本本身不改卡，本规则也只出建议（语义类）。
    """
    import subprocess
    p = ROOT / "scripts" / "card_quality_check.py"
    if not p.exists():
        return None
    try:
        r = subprocess.run([sys.executable, str(p), "--json", "--days", str(days)],
                           capture_output=True, text=True, timeout=90)
        d = json.loads(r.stdout)
    except Exception:
        return None
    if not isinstance(d, dict) or (not d.get("issues") and not d.get("hinted")):
        return None
    return d


def _recent_applied_rules(days: int = 3) -> set[str]:
    """近 N 日内已自动应用过的**规则名**集合 —— 应用冷却（防同一参数被连日重复下移）。

    数据源 = `learning_history.jsonl` 的 `applied_rules` + `generated_at`。
    """
    try:
        rows = [json.loads(l) for l in LEARN_HIST.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        return set()
    cutoff = datetime.now(BJ_TZ) - timedelta(days=max(0, int(days)))
    out: set[str] = set()
    for r in rows:
        try:
            t = datetime.fromisoformat(str(r.get("generated_at") or ""))
        except Exception:
            continue
        if t >= cutoff:
            for name in (r.get("applied_rules") or []):
                out.add(str(name))
            # 兼容旧记录（只有 applied_summary·无 applied_rules）→ 从 target 反推规则
            if not r.get("applied_rules"):
                for summ in (r.get("applied_summary") or []):
                    s = str(summ)
                    if "base_range" in s and "下移" not in s:
                        out.add("R1_range_low")
    return out


def _resolve_mode(cfg: dict) -> str:
    """解析学习模式：**auto_apply_mode 优先**；缺省时按旧 auto_apply 推导（向后兼容）。

    off=只建议 / tiered=分级自动（默认） / high_only=只应用 high（旧行为）。
    """
    m = str(cfg.get("auto_apply_mode") or "").strip().lower()
    if m in ("off", "tiered", "high_only"):
        return m
    return "high_only" if cfg.get("auto_apply") else "off"


def _track_effect(hist: list[dict]) -> dict | None:
    """PDCA 效果追踪：与上一次「有自动应用」的学习记录对比区间命中率变化。

    返回 {"since","applied","range_hit_rate","improved"}；无历史/不可比 → None。
    ⛔ 只读不写：效果差时**不自动回滚**（人工用 `--rollback <快照>` 决定），防"自动改+自动回滚"互相打架。
    """
    try:
        prev = [json.loads(l) for l in LEARN_HIST.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        return None
    prev = [r for r in prev if r.get("applied_summary") and r.get("range_hit_rate_avg") is not None]
    if not prev:
        return None
    rates = [r["summary"].get("range_hit_rate") for r in hist
             if (r.get("metrics") or {}).get("range_hit", {}).get("available")
             and (r["metrics"]["range_hit"] or {}).get("evaluable", 0) > 0]
    if not rates:
        return None
    cur = sum(rates) / len(rates)
    was = float(prev[-1]["range_hit_rate_avg"])
    return {"since": prev[-1].get("generated_at"),
            "applied": prev[-1].get("applied_summary"),
            "range_hit_rate": f"{was:.1f}% → {cur:.1f}%",
            "improved": cur > was}


def run_learning(auto_apply: bool = False, window: int | None = None) -> dict:
    cfg = load_learning_config()
    window = window or cfg.get("window_days", 5)
    hist = load_history(window)
    if not hist:
        result = {"status": "no_history", "suggestions": []}
        SUGGEST_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[learning] 无回测历史，跳过（先跑 backtest_engine）")
        return result

    suggestions = detect_biases(hist, cfg)
    mode = _resolve_mode(cfg)
    # CLI --auto-apply 可把 off 临时提升为 tiered（定时任务用）；配置写 off 时仍尊重 off
    if auto_apply and mode == "off":
        mode = "tiered"

    applied: list[dict] = []
    result_cooled: list[str] = []
    if mode == "tiered":
        # 分级自动（2026-09-17）：低/中风险「数值阈值」自动回写；高风险（语义/结构类）仅建议
        tiers = cfg.get("tier_policy") or DEFAULT_LEARNING_CONFIG["tier_policy"]
        limit = int(cfg.get("max_apply_per_run", 2))
        # ⛔ 判据 = `RULE_RISK[rule]`（改动风险），**不是** severity（偏差严重度·两者解耦）
        _cool = _recent_applied_rules(int(cfg.get("apply_cooldown_days", 3)))
        cands = [s for s in suggestions
                 if tiers.get(RULE_RISK.get(str(s.get("rule")), "high"), False)
                 and s.get("step") is not None
                 and str(s.get("rule")) not in _cool]      # 冷却：同规则 N 日内不重复应用
        for sug in cands[:limit]:
            applied.append(apply_suggestion(sug, cfg))
        result_cooled = [str(s.get("rule")) for s in suggestions if str(s.get("rule")) in _cool]
    elif mode == "high_only" and (auto_apply or cfg.get("auto_apply")):
        for sug in suggestions:
            if sug["severity"] == "high":
                applied.append(apply_suggestion(sug, cfg))

    # 窗口命中率均值（供下次 `_track_effect` 对比·PDCA 基准）
    _rates = [r["summary"].get("range_hit_rate") for r in hist
              if (r.get("metrics") or {}).get("range_hit", {}).get("available")
              and (r["metrics"]["range_hit"] or {}).get("evaluable", 0) > 0]
    range_avg = round(sum(_rates) / len(_rates), 1) if _rates else None

    result = {
        "generated_at": datetime.now(BJ_TZ).isoformat(timespec="seconds"),
        "window": window,
        "mode": mode,
        "auto_apply": mode != "off",
        "suggestions": suggestions,
        "applied": applied,
    }
    if result_cooled:
        result["cooled_rules"] = result_cooled      # 因冷却期未重复应用的规则（留痕）
    if range_avg is not None:
        result["range_hit_rate_avg"] = range_avg
    if cfg.get("track_effect", True):
        eff = _track_effect(hist)
        if eff:
            result["effect"] = eff
    SUGGEST_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 学习历史 append（含 range_hit_rate_avg / effect → PDCA 可回溯）
    rec = {k: v for k, v in result.items() if k != "applied"}
    if applied:
        rec["applied_summary"] = [f"{a.get('target')}: {a.get('before')}→{a.get('after')}" for a in applied]
        rec["applied_rules"] = [a.get("rule") for a in applied if a.get("rule")]   # 供 `_recent_applied_rules` 冷却
    with open(LEARN_HIST, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    for s in suggestions:
        print(f"[learning] {s['rule']} {s['evidence']} → {s['suggest']}")
    for a in applied:
        print(f"[learning] ✅ 已回写 {a['target']}: {a['before']} → {a['after']} (快照 {a['snapshot']})")
    if not suggestions:
        print("[learning] 窗口内无显著偏差，无需学习建议")
    elif not applied and mode != "off":
        print(f"[learning] ⚠️ 模式 {mode} 下无自动应用（高风险仅建议 / 无 step / 超上限）")
    return result


def rollback(snapshot: str):
    """回滚指定快照（restore 配置）。"""
    src = Path(snapshot)
    if not src.exists():
        print(f"[learning] 快照不存在: {snapshot}")
        return
    shutil.copy2(src, EXPECT_RULES)
    audit("config", module="learning", action="rollback", snapshot=str(src))
    print(f"[learning] ✅ 已回滚 expectation_rules.json ← {snapshot}")


def main():
    ap = argparse.ArgumentParser(description="回测自我学习")
    ap.add_argument("--auto-apply", action="store_true", help="按建议回写阈值配置（需 config/learning_config.json auto_apply=True）")
    ap.add_argument("--window", type=int, default=None, help="检测窗口天数（默认取配置）")
    ap.add_argument("--rollback", metavar="SNAPSHOT", help="回滚指定快照")
    args = ap.parse_args()
    if args.rollback:
        rollback(args.rollback)
        return
    run_learning(auto_apply=args.auto_apply, window=args.window)


if __name__ == "__main__":
    main()
