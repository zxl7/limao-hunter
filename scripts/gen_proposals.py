#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_proposals.py — skill 更新提案生成器（财神-回测 skill · 2026-09-01）

定位：提案模式（2026-09-01 用户拍板「提案模式+只动回测白名单」）。
回测发现的偏差分两类处理：
  - 数值阈值（R1/R2/R4 白名单）→ learning.py 自动回写 config（已有）
  - 方法论/语义校准（R3 情绪校准、skill 规则修正）→ 本脚本生成「提案」，
    周五周报 AI 审查 → 用户拍板 → 由 agent 手动应用到对应 skill。

⛔ 权限边界（用户定·最高优先级）：
  - 自动回写白名单 = 仅 expectation_rules.json / relay_score_config.json（财神-回测）
  - 其余 skill（财神-选股/盯盘/复盘/中枢）永不自动修改，只接收提案
  - 提案应用 = 人工确认后 agent 手动改，绝不自动落地

提案生成规则（v1·可裁决·窗口=最近5个回测记录）：
  P1 情绪分系统性偏差：|均值差| > 1.0 且样本≥3 → 提案「财神-选股/复盘 AI 研判情绪校准」
  P2 候选命中率持续走低：均值 < 40% 且样本≥3 → 提案「财神-选股 候选门槛核查」
  P3 区间命中率持续走低：均值 < 40% → 已在 R1 自动回写 config，提案标注「已自动回写·无需人工」
  P4 回测数据缺口：连续≥3 个结算日「真缺口」→ 提案「数据链路补漏」。
     真缺口 = 候选文件缺失（未落盘）/ relay 有票却无 nav 结算 / 竞价快照缺失；
     ⛔ relay 空池（冰点期无 buy 候选→nav 不写账，nav_ledger 设计行为）不算缺口（09-07 误报实证）

产出：
  cache/learning_proposals.jsonl 提案流（append-only·同 rule+date 幂等）
  cache/learning_proposals_latest.json 最新待审提案（周报卡读）

用法：
  python3 scripts/gen_proposals.py               # 生成/更新提案
  python3 scripts/gen_proposals.py --list        # 列出待审提案
  python3 scripts/gen_proposals.py --dismiss <PROPOSAL_ID> [--reason "..."]   # 驳回误报提案（draft/approved→dismissed，带因由可审计）
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/Users/zxl/WorkBuddy/狸猫猎手")
CACHE = ROOT / "cache"
HISTORY_FILE = CACHE / "backtest_history.jsonl"
SUGGEST_FILE = CACHE / "learning_suggestions.json"
PROPOSAL_FILE = CACHE / "learning_proposals.jsonl"
LATEST_FILE = CACHE / "learning_proposals_latest.json"
BJ_TZ = timezone(timedelta(hours=8))
# 候选池目录（与 backtest_engine.py WC_CACHE 口径一致）
WC_CACHE = ROOT / "wealth_code" / "cache"

# ⛔ 2026-10-27 开关：P3（区间命中率）提案产线默认关闭。
#   理由：R1/R4 已于 2026-09-18 冻结（range_hit 与赚钱无因果，仅描述量），
#   且 2026-10-18 用户拍板「命中率不得作对错判据」。但产线仍在跑，
#   09-01→10-26 空堆 34 条（33 approved + 1 applied），标题还写「已自动回写」——
#   与实际（一次都没回写）不符。此开关置 True 可恢复旧行为（无需改代码）。
ENABLE_P3_RANGE_PROPOSAL = os.environ.get("ENABLE_P3_RANGE_PROPOSAL", "0") == "1"

# 提案 → 目标 skill 映射（只读，不自动写）
PROPOSAL_SKILL_MAP = {
    "P1_sentiment_bias": {"skill": "财神-选股", "section": "AI 研判/情绪预判", "note": "情绪分系统性偏差→研判校准"},
    "P2_candidate_low": {"skill": "财神-选股", "section": "候选门槛", "note": "候选命中率持续走低→核查 relay/watch 门槛"},
    "P3_range_low": {"skill": "财神-回测", "section": "R1 自动回写", "note": "区间命中低→R1 已自动回写 config"},
    "P4_data_gap": {"skill": "财神-中枢", "section": "数据链路", "note": "回测数据缺口→补漏"},
}


def load_history(n: int = 5) -> list[dict]:
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
    return rows[-n:]


def load_suggestions() -> list[dict]:
    if not SUGGEST_FILE.exists():
        return []
    try:
        return (json.loads(SUGGEST_FILE.read_text(encoding="utf-8")) or {}).get("suggestions", [])
    except Exception:
        return []


def _load_existing() -> list[dict]:
    if not PROPOSAL_FILE.exists():
        return []
    rows = []
    for line in PROPOSAL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _save(rows: list[dict]):
    PROPOSAL_FILE.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    latest = [r for r in rows if r.get("status") in ("draft", "approved")]
    LATEST_FILE.write_text(json.dumps({"generated_at": datetime.now(BJ_TZ).isoformat(timespec="seconds"),
                                       "pending": len(latest), "proposals": latest},
                                      ensure_ascii=False, indent=2), encoding="utf-8")


def _mk_proposal(rule: str, title: str, evidence: str, suggest: str) -> dict:
    meta = PROPOSAL_SKILL_MAP.get(rule, {"skill": "待定", "section": "待定", "note": ""})
    return {
        "proposal_id": f"{rule}_{datetime.now(BJ_TZ).strftime('%Y%m%d')}",
        "rule": rule,
        "title": title,
        "target_skill": meta["skill"],
        "target_section": meta["section"],
        "evidence": evidence,
        "suggest": suggest,
        "status": "draft",           # draft → approved（用户拍板）→ applied（agent 手动改完）
        "created_at": datetime.now(BJ_TZ).isoformat(timespec="seconds"),
    }


def _is_vacuous_day(row: dict) -> bool:
    """当日「零参与」= 正常业务，不算数据缺口（2026-10-27 新增）。

    与 backtest_engine._vacuous_reason 同口径：只认 `pool_call.participate`
    **存在且为空列表**；字段缺失（老版本）或非列表 → 不算真空仓，落回原判。
    """
    settle = str(row.get("settle_date") or "")
    if not settle:
        return False
    p = WC_CACHE / f"pool_call-{settle}.json"
    if not p.exists():
        return False
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    meta = j.get("meta") or {}
    part = meta.get("participate")
    if part is None:
        part = j.get("participate")
    return isinstance(part, list) and len(part) == 0


def _is_real_gap(row: dict) -> bool:
    """单日是否为「真数据缺口」（P4 用）。

    2026-09-07 修复：relay 空池（冰点无 buy 候选→nav 不写账）是正常业务，不算缺口。
    2026-10-27 修复（第二次误报）：`pool_call.participate=[]` 的**零参与日**同样正常
    ——当日满仓防守/买不进，一笔没成交，nav 本来就没有账可写。旧实现只看
    「relay 有票」，于是把「有候选但决定不开仓」全线判成链路断，P4 天天假阳性。
    判据顺序：竞价快照缺失 → 真缺口；有结算 → 非缺口；零参与 → 非缺口；
    候选文件缺失 → 真缺口；relay 空池 → 非缺口；其余（有票无结算）→ 真缺口。
    """
    m = row.get("metrics", {})
    # 竞价快照缺失 = 真缺口（range_hit 评估 watch 池，与 relay 空无关）
    if not m.get("range_hit", {}).get("available"):
        return True
    ch = m.get("candidate_hit", {})
    if ch.get("available"):
        return False
    # 零参与（真空仓）= 正常业务，不是缺口
    if _is_vacuous_day(row):
        return False
    # candidate_hit 不可用 → 看当日候选池
    rec_date = str(row.get("rec_date") or "")
    p = WC_CACHE / f"candidates-{rec_date}.json"
    if not p.exists():
        return True  # 候选文件未落盘 = 链路断
    try:
        cand = json.loads(p.read_text(encoding="utf-8"))
        relay = cand.get("relay") or []
        if not relay:
            return False  # relay 空池 = 无 buy 候选，nav 无账可写，正常
        return True       # relay 有票却无 nav 结算 = 结算链路缺口
    except Exception:
        return True  # 解析异常保守按缺口


def gen_proposals() -> list[dict]:
    hist = load_history(5)
    sugs = load_suggestions()
    existing = _load_existing()
    new_proposals: list[dict] = []

    # P1 情绪分系统性偏差（来自 R3 建议·high/medium 才提案）
    r3 = [s for s in sugs if s["rule"] == "R3_sentiment_bias"]
    if r3:
        # 近 5 日情绪差
        diffs = [r["summary"].get("sentiment_diff") for r in hist
                 if r["metrics"]["sentiment_align"].get("available") and r["summary"].get("sentiment_diff") is not None]
        if len(diffs) >= 3:
            avg = sum(diffs) / len(diffs)
            direction = "偏高" if avg > 0 else "偏低"
            np_ = _mk_proposal(
                "P1_sentiment_bias",
                f"AI 情绪预判系统性{direction} {abs(avg):.1f} 分",
                f"近{len(diffs)}日情绪差均值 {avg:+.2f}（已记录 {len(diffs)} 日）",
                f"财神-选股/复盘 AI 研判的情绪预判需校准：{direction}方向 {abs(avg):.1f} 分。建议人工审阅研判口径后手动改 skill 对应章节。",
            )
            # 幂等：同 rule 同日期已存在则跳过
            if not any(e.get("proposal_id") == np_["proposal_id"] for e in existing):
                new_proposals.append(np_)

    # P2 候选命中率持续走低
    cand_rates = [r["summary"].get("candidate_hit_rate") for r in hist
                  if r["metrics"]["candidate_hit"].get("available")]
    cand_counts = [r["metrics"]["candidate_hit"].get("stock_count", 0) for r in hist
                   if r["metrics"]["candidate_hit"].get("available")]
    if cand_rates and sum(cand_counts) >= 3:
        avg = sum(cand_rates) / len(cand_rates)
        if avg < 40:
            np_ = _mk_proposal(
                "P2_candidate_low",
                f"候选命中率持续走低（均值 {avg:.0f}%）",
                f"近{len(cand_rates)}日候选命中率均值 {avg:.1f}%（已记录 {sum(cand_counts)} 只）",
                "财神-选股 relay/watch 候选门槛核查：命中率连续走低，建议人工审阅选股逻辑后手动调整。",
            )
            if not any(e.get("proposal_id") == np_["proposal_id"] for e in existing):
                new_proposals.append(np_)

    # P3 区间命中率低 —— ⛔ 2026-10-27 起默认停用（见文件头 ENABLE_P3_RANGE_PROPOSAL）。
    #    理由：R1/R4 已 09-18 冻结、10-18 拍板「命中率不得作对错判据」，
    #    但产线空堆 34 条且标题写「已自动回写」（实际一次都没回写）。
    #    区间落区在回测卡上照常显示「评估 N 只｜N 只落在区间内」，只是不再生成提案。
    range_rates = [r["summary"].get("range_hit_rate") for r in hist
                   if r["metrics"]["range_hit"].get("available")]
    if ENABLE_P3_RANGE_PROPOSAL and range_rates and sum(range_rates) / len(range_rates) < 40:
        avg = sum(range_rates) / len(range_rates)
        np_ = _mk_proposal(
            "P3_range_low",
            f"区间落区均值 {avg:.0f}%（仅描述量，非判据）",
            f"近{len(range_rates)}日落区均值 {avg:.1f}%",
            "区间落区仅作描述量（R1/R4 已冻结·10-18 拍板不得作对错判据）。如需深度核查可审阅竞价预期引擎。",
        )
        np_["status"] = "draft"  # ⛔ 不再自动 approved：本规则无任何自动回写动作，不得冒充已处理
        if not any(e.get("proposal_id") == np_["proposal_id"] for e in existing):
            new_proposals.append(np_)

    # P4 数据缺口（连续 3 日「真缺口」；relay 空池 / 池内零参与都不算——09-07、10-27 两次误报修复）
    gap_days = [r for r in hist if _is_real_gap(r)]
    if len(gap_days) >= 3:
        np_ = _mk_proposal(
            "P4_data_gap",
            "回测数据连续缺口",
            f"近{len(hist)}日中 {len(gap_days)} 日存在真数据缺口（候选文件缺失/有候选无结算/竞价快照缺失；零参与日已排除）",
            "数据链路补漏：检查 candidates/auction_quotes/nav 落盘是否中断，建议中枢体检关注。",
        )
        if not any(e.get("proposal_id") == np_["proposal_id"] for e in existing):
            new_proposals.append(np_)

    if new_proposals:
        _save(existing + new_proposals)
    return new_proposals


def dismiss_proposal(proposal_id: str, reason: str) -> bool:
    """驳回误报提案：status draft/approved → dismissed（带 dismiss_reason，可审计）。
    返回 True=已驳回；False=未找到或已是终态。"""
    rows = _load_existing()
    hit = None
    for r in rows:
        if r.get("proposal_id") == proposal_id:
            hit = r
            break
    if hit is None:
        print(f"[gen_proposals] 未找到提案 {proposal_id}")
        return False
    if hit.get("status") in ("dismissed", "applied"):
        print(f"[gen_proposals] {proposal_id} 已是终态 {hit.get('status')}，不可驳回")
        return False
    hit["status"] = "dismissed"
    hit["dismiss_reason"] = reason or "（未填）"
    hit["dismissed_at"] = datetime.now(BJ_TZ).isoformat(timespec="seconds")
    _save(rows)
    print(f"[gen_proposals] 已驳回 {proposal_id}（draft→dismissed）")
    return True


def main():
    ap = argparse.ArgumentParser(description="skill 更新提案生成器")
    ap.add_argument("--list", action="store_true", help="列出待审提案")
    ap.add_argument("--dismiss", metavar="PROPOSAL_ID", help="驳回误报提案（如 P4_data_gap_20260907）")
    ap.add_argument("--reason", default="", help="驳回原因（--dismiss 配套，可审计）")
    args = ap.parse_args()
    if args.dismiss:
        dismiss_proposal(args.dismiss, args.reason)
        return
    if args.list:
        rows = _load_existing()
        pending = [r for r in rows if r.get("status") in ("draft", "approved")]
        if not pending:
            print("[gen_proposals] 无待审提案")
            return
        for p in pending:
            print(f"[{p['status']}] {p['proposal_id']} {p['title']} → {p['target_skill']}:{p['target_section']}")
            print(f"    证据: {p['evidence']}")
        return
    added = gen_proposals()
    if added:
        for p in added:
            print(f"[gen_proposals] 新增提案 {p['proposal_id']} → {p['target_skill']}:{p['target_section']}（{p['status']}）")
    else:
        print("[gen_proposals] 窗口内无新提案（幂等）")


if __name__ == "__main__":
    main()
