#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中链路断档守卫（2026-09-30 事故后新增）。

⛔ 事故背景（为什么需要它）：
    2026-09-30 早盘，`scripts/intraday_pulse.py` 因未定义名（`_load` / `CACHE`）每轮崩溃，
    `latest.json` 时间戳卡在昨日 15:00 → `data_ready.ready=False` → 推送层 **98 轮全 skip**，
    异动群 09:15-09:41 一张卡都没出，且**全程零告警**——靠用户自己发现，早盘最关键的
    26 分钟异动信号永久丢失。链路静默失效比报错更危险，本脚本专职堵这个洞。

职责：只在**交易时段**内，判断盘中数据链路是否持续不就绪；是则主动推一张大白话告警卡。

两类断档（任一命中即计数）：
    A. `data_ready.ready == False` —— 链路在跑，但数据没就绪（今天这起就属此类）
    B. `data_ready.generated_ts` 超过 STALE_SECONDS 未刷新 —— 守护进程可能根本没在跑

防误报 / 防刷屏：
    · 连续 ≥ ALERT_STREAK 轮才告警（30s/轮 ≈ 2.5 分钟，躲开瞬时抖动）
    · 告警后冷却 ALERT_COOLDOWN 秒
    · 任意一轮恢复即清零

回滚：环境变量 `LINK_GUARD=0` 直接关闭（不推、不改状态）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PULSE = ROOT / "cache" / "pulse_snapshots"
READY = PULSE / "data_ready.json"
STATE = PULSE / ".link_guard_state.json"
PUSH_CARD = ROOT / "scripts" / "push_card.py"
ALERT_CARD = PULSE / "link_guard_card.json"

# ── 阈值（单一源：要调就改这里）─────────────────────────────────
SESSION_START_HHMM = 915     # 交易时段起（含竞价前的链路预热）
SESSION_END_HHMM = 1505      # 交易时段止（含尾盘闭合）
ALERT_STREAK = 5             # 连续多少轮不就绪才告警（×30s ≈ 2.5 分钟）
ALERT_COOLDOWN = 1800        # 告警冷却（秒），防刷屏
STALE_SECONDS = 300          # 标记文件多久没刷新算「守护没在跑」

# 链路异常文案（大白话，禁止机器黑话。见 shared_rules【🗣️ 卡片话术铁律】）
TITLE = "⚠️ 盘面数据断了"
NOTE = "数据/推送任务异常时自动提醒。恢复正常后本卡不再出现。"


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _save(path: Path, value) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _in_session(now: datetime) -> bool:
    """仅交易日 + 交易时段内生效（周末与盘后不打扰）。"""
    if now.weekday() >= 5:
        return False
    return SESSION_START_HHMM <= now.hour * 100 + now.minute < SESSION_END_HHMM


def _age_seconds(generated_ts: str, now: datetime) -> float | None:
    try:
        return (now - datetime.fromisoformat(str(generated_ts))).total_seconds()
    except (ValueError, TypeError):
        return None


def _diagnose(now: datetime) -> tuple[bool, str]:
    """返回 (是否断档, 大白话原因)。"""
    marker = _load(READY, {})
    if not marker:
        return False, ""          # 标记文件还没生成过（守护首轮尚未落地），不计断档

    age = _age_seconds(marker.get("generated_ts"), now)
    if age is not None and age > STALE_SECONDS:
        return True, f"数据链路已 {int(age / 60)} 分钟没有刷新，后台任务可能没在跑"

    if not marker.get("ready"):
        reason = str(marker.get("reason") or "数据没准备好")
        date = str(marker.get("date") or "?")
        return True, f"行情数据没准备好（数据日期 {date}）：{reason}"

    return False, ""


def _push_alert(detail: str, streak: int) -> bool:
    card = {
        "title": f"{TITLE} {datetime.now().strftime('%H:%M')}",
        "color": "orange",
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**情况**：{detail}"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**影响**：已连续 {streak} 轮（约 {streak * 30 // 60 or 1} 分钟）推不出异动提醒"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": "**建议**：检查后台数据/推送任务是否在跑，修好后会自动恢复"}},
        ],
        "note": NOTE,
    }
    _save(ALERT_CARD, card)
    proc = subprocess.run(
        [sys.executable, str(PUSH_CARD), str(ALERT_CARD), "--webhook", "feishu_webhook_intraday"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"[link-guard] 告警推送失败 rc={proc.returncode}: {(proc.stderr or '').strip()[:200]}", file=sys.stderr)
        return False
    print(f"[link-guard] 链路断档告警已推送：{detail}")
    return True


def main() -> None:
    if os.environ.get("LINK_GUARD", "1") == "0":
        return

    now = datetime.now()
    state = _load(STATE, {}) or {}
    streak = int(state.get("fail_streak") or 0)

    if not _in_session(now):
        if streak:
            _save(STATE, {**state, "fail_streak": 0})
        return

    broken, detail = _diagnose(now)
    if not broken:
        if streak:
            print(f"[link-guard] 链路已恢复（此前连续 {streak} 轮异常）")
            _save(STATE, {**state, "fail_streak": 0, "recovered_ts": now.isoformat(timespec="seconds")})
        return

    streak += 1
    last_alert = float(state.get("last_alert_ts") or 0)
    should_alert = streak >= ALERT_STREAK and (now.timestamp() - last_alert) >= ALERT_COOLDOWN

    if should_alert and _push_alert(detail, streak):
        state["last_alert_ts"] = now.timestamp()
        state["last_alert_detail"] = detail

    _save(STATE, {**state, "fail_streak": streak, "last_check_ts": now.isoformat(timespec="seconds"), "last_detail": detail})


if __name__ == "__main__":
    main()
