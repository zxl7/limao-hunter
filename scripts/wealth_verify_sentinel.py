#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价验证卡 · launchd 哨兵（2026-10-26 立）

背景：2026-10-23 / 10-26 连续两个交易日，LaunchAgent 从 gui 域里消失，
09:25 精准触发整段没跑，卡片改由平台 09:40 兜底才发出，且**全程无人被告知**。
根因（已修）：bootout 后 bootstrap 未生效；覆盖表仍显示 enabled，具有强误导性。

职责：只判、只报，不碰数据。
  1. 非交易日 → 静默退出
  2. 今日日志有 launchd 触发行 → 静默退出（一切正常）
  3. 没触发但卡已被别的链路推出 → 降级告警（橙色·卡在、只是没走精准链）
  4. 没触发且今日无推送标记 → 严重告警（红色·今日卡可能根本不会来）
每交易日最多告警一次（/tmp/wealth_sentinel_alarm_<date> 去重），防刷屏。

⛔ 不推卡、不抓报价、不写 cache —— 推送链唯一归 verify，本脚本只在推送链失联时报警。

用法：
  python3 scripts/wealth_verify_sentinel.py            # 真跑（可能发飞书）
  python3 scripts/wealth_verify_sentinel.py --dry-run  # 只打印结论，不发飞书
  python3 scripts/wealth_verify_sentinel.py --force    # 忽略当日去重标记
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.trading_calendar import is_a_share_trading_day  # noqa: E402

LABEL = "com.limao.wealth-verify"
LOG = ROOT / "cache" / "wealth_verify_launchd.log"
SENTINEL_LOG = ROOT / "cache" / "wealth_verify_sentinel.log"
PUSH_SCRIPT = SCRIPT_DIR / "push_card.py"
FIX_CMD = f"bash {ROOT}/scripts/fix_wealth_verify_launchd.sh"


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        SENTINEL_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SENTINEL_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def agent_loaded() -> bool:
    """唯一定义：launchctl print 成功才算在册。覆盖表 enabled 不算（10-26 实证背离）。"""
    try:
        r = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def fired_today(today: str) -> bool:
    """今日日志里是否出现过 launchd 触发行。"""
    if not LOG.exists():
        return False
    pat = re.compile(rf"^\[{re.escape(today)} [0-9:]+\] (launchd 触发|本日已推送)")
    try:
        with LOG.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if pat.match(line):
                    return True
    except OSError:
        return False
    return False


def last_trigger_line() -> str:
    if not LOG.exists():
        return "(日志文件不存在)"
    try:
        with LOG.open("r", encoding="utf-8", errors="replace") as f:
            for line in reversed(f.readlines()[-4000:]):
                if "launchd 触发" in line or "本日已推送" in line:
                    return line.strip()
    except OSError:
        pass
    return "(近期无触发行)"


def send_alarm(title: str, color: str, body: str, dry_run: bool) -> bool:
    card = {
        "title": title,
        "color": color,
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": body}}],
        "note": "竞价验证卡哨兵 · 仅告警，不推卡",
    }
    if dry_run:
        log(f"[dry-run] 不发送。标题={title}")
        print(json.dumps(card, ensure_ascii=False, indent=1))
        return True
    tmp = Path(f"/tmp/wealth_sentinel_card_{date.today():%Y%m%d}.json")
    tmp.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    try:
        r = subprocess.run(
            [sys.executable, str(PUSH_SCRIPT), str(tmp), "--webhook", "feishu_webhook_test"],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
        )
        ok = r.returncode == 0 and "OK" in (r.stdout + r.stderr).upper()
        log(f"飞书告警 RC={r.returncode} ok={ok} :: {(r.stdout or r.stderr).strip()[:200]}")
        return ok
    except (OSError, subprocess.SubprocessError) as e:
        log(f"飞书告警异常：{e}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印，不发飞书")
    ap.add_argument("--force", action="store_true", help="忽略当日去重标记")
    args = ap.parse_args()

    now = datetime.now()
    today = f"{now:%Y-%m-%d}"
    hhmm = now.hour * 100 + now.minute
    mark = Path(f"/tmp/wealth_sentinel_alarm_{today}")

    if not is_a_share_trading_day(today):
        log(f"{today} 非交易日，跳过")
        print("STATUS=SKIP_NON_TRADING")
        return 0

    # ⛔ 判定窗口是 09:26 之后（早于此刻 launchd 本来就还没跑，早判必然误报）。
    #    但平台调度有 ±15min 抖动，可能提前唤起 → 小睡等到窗口开启，最多等 8 分钟；
    #    超出 8 分钟说明错得离谱（如 09:05 就跑了），不硬等，如实报 TOO_EARLY。
    if hhmm < 926:
        wait_s = (9 * 3600 + 26 * 60 + 30) - (now.hour * 3600 + now.minute * 60 + now.second)
        if 0 < wait_s <= 480:
            log(f"{now:%H:%M:%S} 略早于判定窗口，等待 {wait_s}s 至 09:26:30")
            time.sleep(wait_s)
            now = datetime.now()
        else:
            log(f"{now:%H:%M} 远早于 09:26（差 {wait_s}s），不等待，跳过")
            print("STATUS=TOO_EARLY")
            return 0

    loaded = agent_loaded()
    fired = fired_today(today)
    done = Path(f"/tmp/wealth_verify_done_{today}").exists()
    log(f"loaded={loaded} fired={fired} done_mark={done}")

    if fired:
        log("今日 launchd 已触发，链路正常，静默")
        print("STATUS=OK")
        return 0

    if mark.exists() and not args.force:
        log("今日已告警过，去重退出")
        print("STATUS=ALREADY_ALARMED")
        return 0

    detail = (
        f"**在册**：{'是' if loaded else '否 ← 服务已从 launchd 掉出'}\n"
        f"**今日触发行**：无\n"
        f"**今日推送标记**：{'有' if done else '无'}\n"
        f"**最近一条触发**：`{last_trigger_line()}`"
    )

    if done:
        title = "⚠️ 竞价验证卡：精准链未触发（卡已由其它链路推出）"
        body = (
            f"{detail}\n\n"
            "**影响**：卡片没走 09:26 精准链，时间可能偏晚，但今日卡已在。\n"
            "**处理**：下个交易日 09:25 前把服务注册回来：\n"
            f"`{FIX_CMD}`"
        )
        ok = send_alarm(title, "orange", body, args.dry_run)
        status = "ALARM_DEGRADED"
    else:
        title = "🔴 竞价验证卡：launchd 未触发，今日卡恐缺失"
        body = (
            f"{detail}\n\n"
            "**影响**：09:25 精准链整天没跑；若 09:40 兜底也失败，今日无卡。\n"
            "**处理**：立刻在 Terminal 执行\n"
            f"`{FIX_CMD}`\n"
            "修好后本机下一交易日自动恢复 09:25 触发。"
        )
        ok = send_alarm(title, "red", body, args.dry_run)
        status = "ALARM_CRITICAL"

    # ⛔ dry-run 不落去重标记：否则一次演练会把当天真实告警也压掉（10-26 实测踩到）
    if ok and not args.dry_run:
        mark.write_text(f"{now:%Y-%m-%d %H:%M:%S}\n", encoding="utf-8")
    print(f"STATUS={status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
