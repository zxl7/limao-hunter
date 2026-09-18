#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘中节拍汇总卡（2026-09-04 用户「异动群 15 分钟至少要推一次」新增）
====================================================
背景：异动群=高频事件直推（状态跃迁才推）→ 盘面延续期可能出现 >15min 空窗
（实证 09-04 10:00-10:12 无新爆发/无跃迁 = 0 推送，用户要求群保持节奏）。
职责：交易时段内若「距上次异动群推送（事件卡或本节拍）≥15min」→ 推一条轻量
节拍汇总卡（无事件时的节奏播报），有事件推送则自动顺延不重复。
- 读 latest.json（涨停/题材/情绪）+ feedback_matrix.json（核按钮）+ audit.jsonl
  （上次 intraday 推送时间）——纯客观数据，禁编造。
- 节拍状态记 pushed_signals_{今日}.json['intraday_beat_ts']（防自身重复）。
- 推送 → feishu_webhook_intraday（异动群）；间隔 BEAT_SEC=900（15min）。
用法: python3 scripts/intraday_beat.py   （守护每轮调用·独立可跑）
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
SNAP = CACHE / "pulse_snapshots"
BJ = timezone(timedelta(hours=8))
PY = sys.executable
PUSH = ROOT / "scripts" / "push_card.py"
BEAT_SEC = 900  # 15min（2026-09-04 用户定：10min 粒度检查·至少 15min 一次）

# ── 同源抑制（2026-09-15 降噪迭代·可逆）──
# 背景：空窗也每 15min 推"无新异动"→单日 46 条重复空卡（垃圾信息主体之一）。
# 新增：记录上次节拍卡的关键状态（涨停数/核按钮/情绪label/前3主线），若本轮状态与上次
#   完全一致且核按钮<3（无预警价值）→ 跳过本轮（仅更新时间戳·不推空卡）；有实质变化才推。
# ⛔ 可逆：BEAT_SUPPRESS_SAME=False 即回退"每15min必推"旧行为。
BEAT_SUPPRESS_SAME = True

sys.path.insert(0, str(ROOT))
from common.json_io import save_json  # noqa: E402


def now() -> datetime:
    return datetime.now(BJ)


def in_trading(dt: datetime | None = None) -> bool:
    dt = dt or now()
    if dt.weekday() >= 5:
        return False
    mins = dt.hour * 60 + dt.minute
    return (555 <= mins <= 690) or (780 <= mins <= 900)  # 09:15-11:30 / 13:00-15:00


def last_intraday_push_ts() -> float:
    """audit.jsonl 中最近一次推到异动群(feishu_webhook_intraday)的时间戳。"""
    aud = CACHE / "audit.jsonl"
    if not aud.exists():
        return 0.0
    last = 0.0
    try:
        lines = aud.read_text(encoding="utf-8").splitlines()
    except Exception:
        return 0.0
    for line in lines[-800:]:  # 只看尾部（当日记录集中在尾部）
        try:
            r = json.loads(line)
        except Exception:
            continue
        if str(r.get("webhook") or "") != "feishu_webhook_intraday":
            continue
        ts = r.get("ts") or r.get("time") or ""
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            last = max(last, t.timestamp())
        except Exception:
            pass
    return last


def build_beat_card() -> tuple[dict, dict]:
    """轻量节拍卡：涨停/主线/核按钮 客观现状，无交易结论。

    返回 (card, state)：state 为关键状态快照（涨停数/核按钮/情绪label/前3主线），
    供「同源抑制」比对——状态与上一张完全一致且核按钮<3 时，主流程跳过推空卡。
    """
    la = {}
    try:
        la = json.loads((SNAP / "latest.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    fb = {}
    try:
        fb = json.loads((CACHE / "feedback_matrix.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    hhmm = now().strftime("%H:%M")
    total = la.get("total_zt") or 0
    top = (la.get("top5") or [])[:3]
    top_names = [t.get("name") for t in top]
    top_txt = " · ".join(
        f"{t.get('name')} {t.get('zt_count')}家"
        + (f"({(t.get('leader') or {}).get('name')})" if (t.get('leader') or {}).get('name') else "")
        for t in top
    ) or "暂无题材数据"
    nuke = fb.get("nuke_count") or 0
    brk = fb.get("break_count") or 0
    score = fb.get("sentiment_score")
    label = fb.get("sentiment_label") or ""
    score_txt = f"{score}/10·{label}" if score is not None else "—"
    line2 = f"▸ 涨停 {total} 家｜情绪 {score_txt}"
    if nuke >= 3:
        line3 = f"▸ ⚠️ 核按钮 {nuke} 只" + (f"·{brk} 只破位（高位杀跌）" if brk >= 3 else "·无真破位（分歧回踩）")
    else:
        line3 = f"▸ ⚠️ 核按钮 {nuke} 只（未达预警线）"
    content = (
        f"**📡 定时巡查 · {hhmm}**（异动群 15 分钟无新事件·自动巡查）\n\n"
        f"▸ 延续主线：{top_txt}\n"
        f"{line2}\n"
        f"{line3}\n"
        f"📎 无新爆发板块/新跃迁信号 · 盘面延续消化 · 有新事件即时播报"
    )
    els = [
        {"tag": "div", "text": {"tag": "lark_md", "content": content}},
    ]
    card = {
        "title": f"📡 定时巡查 {hhmm} | 涨停{total} · 无新异动",
        "color": "grey",
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": f"📡 定时巡查 {hhmm} | 涨停{total} · 无新异动"}, "template": "grey"},
        "elements": els,
    }
    state = {"total": total, "nuke": nuke, "label": label, "top3": top_names}
    return card, state


def sig_path() -> Path:
    return CACHE / f"pushed_signals_{now().strftime('%Y%m%d')}.json"


def main() -> int:
    if not in_trading():
        print("[intraday_beat] 非交易时段·跳过")
        return 0
    sig = {}
    sp = sig_path()
    if sp.exists():
        try:
            sig = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            sig = {}
    last_event = last_intraday_push_ts()
    last_beat = float(sig.get("intraday_beat_ts") or 0)
    last_any = max(last_event, last_beat)
    gap = now().timestamp() - last_any
    if gap < BEAT_SEC:
        print(f"[intraday_beat] 距上次异动群推送 {int(gap)}s < {BEAT_SEC}s·跳过")
        return 0
    card, cur_state = build_beat_card()
    # ── 同源抑制（2026-09-15 降噪）：状态与上一张完全一致且核按钮<3 → 跳过推空卡 ──
    if BEAT_SUPPRESS_SAME and nuke_no_warn(cur_state) and sig.get("intraday_beat_state") == cur_state:
        sig["intraday_beat_ts"] = now().timestamp()  # 仅更新时间戳，避免下轮重复比对
        save_json(sp, sig)
        print(f"[intraday_beat] 同源抑制：状态无变化（空窗 {int(gap)}s）·跳过空卡")
        return 0
    cf = Path(os.environ.get("TMPDIR", "/tmp")) / "limao_intraday_beat.json"
    cf.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    rc = os.system(f'"{PY}" "{ROOT / "scripts" / "short_spirit.py"}" --send "{cf}"')
    if rc == 0:
        sig["intraday_beat_ts"] = now().timestamp()
        sig["intraday_beat_state"] = cur_state
        save_json(sp, sig)
        print(f"[intraday_beat] 节拍卡已推（空窗 {int(gap)}s）")
    else:
        print(f"[intraday_beat] 推送失败 rc={rc}（状态未更新·下轮重试）")
    return 0 if rc == 0 else 1


def nuke_no_warn(state: dict) -> bool:
    """核按钮未达预警线（<3）→ 该节拍卡无预警价值，可同源抑制。"""
    return (state or {}).get("nuke", 0) < 3


if __name__ == "__main__":
    sys.exit(main())
