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
    "note": "回测自我学习主开关。auto_apply=False 时只出建议不改参数；True 时按建议单次小幅回写阈值配置（先备份后改·可回滚）。",
    "auto_apply": False,
    "max_adjust_step": 0.3,        # 单次最大调整幅度（base_range 下移等）
    "window_days": 5,              # 偏差检测窗口（最近 N 个回测记录）
    "min_samples": 3,              # 样本量守卫：候选/区间评估样本 < 此值不触发 high 建议（防小样本误学习）
    "rules": {
        "R1_range_low":   {"enabled": True, "hit_rate_threshold": 40, "suggest": "base_range 下移 0.3（预期系统性偏乐观）", "target": "expectation_rules.json.base_range", "step": 0.3},
        "R2_cand_low":    {"enabled": True, "hit_rate_threshold": 40, "suggest": "relay first_choice_min 门槛上调 2 分", "target": "relay_score_config.json.bands.first_choice_min", "step": 2},
        "R3_sentiment_bias": {"enabled": True, "abs_diff_threshold": 1.0, "suggest": "AI 情绪预判存在系统性偏差（见 diff 方向）", "target": "ai_judge", "step": None},
        "R4_range_high":  {"enabled": True, "hit_rate_threshold": 80, "suggest": "max_range_width 收紧 0.3（区间过宽无信息量）", "target": "expectation_rules.json.max_range_width", "step": 0.3},
    },
}


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
    return {"applied": True, "target": target, "before": before, "after": after,
            "snapshot": snapshot, "suggest": sug["suggest"]}


# ---------------- 主流程 ----------------

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
    applied = []
    if auto_apply and cfg.get("auto_apply"):
        for sug in suggestions:
            if sug["severity"] == "high":
                applied.append(apply_suggestion(sug, cfg))

    result = {
        "generated_at": datetime.now(BJ_TZ).isoformat(timespec="seconds"),
        "window": window,
        "auto_apply": bool(auto_apply and cfg.get("auto_apply")),
        "suggestions": suggestions,
        "applied": applied,
    }
    SUGGEST_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 学习历史 append
    rec = {k: v for k, v in result.items() if k != "applied"}
    if applied:
        rec["applied_summary"] = [f"{a.get('target')}: {a.get('before')}→{a.get('after')}" for a in applied]
    with open(LEARN_HIST, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    for s in suggestions:
        print(f"[learning] {s['rule']} {s['evidence']} → {s['suggest']}")
    for a in applied:
        print(f"[learning] ✅ 已回写 {a['target']}: {a['before']} → {a['after']} (快照 {a['snapshot']})")
    if not suggestions:
        print("[learning] 窗口内无显著偏差，无需学习建议")
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
