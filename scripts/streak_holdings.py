#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔒 竞价推票·持有·断板退出（命令行）

用法：
    python3 scripts/streak_holdings.py --open            # 09:26 登记买入（优先级前 2）
    python3 scripts/streak_holdings.py --settle          # 收盘检查断板
    python3 scripts/streak_holdings.py --report          # 打印五列表
    python3 scripts/streak_holdings.py --settle --day 2026-11-12

⛔ 系统只登记，不下单；规则见 `wealth_code/core/streak_hold.py`。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))
sys.path.insert(0, str(ROOT / "scripts"))

from core import streak_hold as sh  # noqa: E402

STATE = ROOT / "cache" / "streak_holdings.json"


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"open": [], "closed": []}


def _save(h: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")


def _today() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--settle", action="store_true", help="收盘发断板信号")
    ap.add_argument("--execute", action="store_true", help="下一交易日开盘执行卖出")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--push", action="store_true", help="推送持仓/断板卡")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--webhook", default="feishu_webhook_intraday",
                    help="推送目标（默认异动群；测试用 feishu_webhook_test）")
    ap.add_argument("--day")
    a = ap.parse_args(argv)
    day = a.day or _today()
    h = _load()

    if a.open:
        import promotion_plan as pp
        plan = pp.build(day)
        # 只登记「最终结论=可买」的票（有 verdicts 时）
        _v = plan.get("verdicts")
        if _v:
            _ok = {str(x.get("code")) for x in _v if x.get("conclusion") == "可买"}
            plan = {**plan, "rows": [r for r in (plan.get("rows") or []) if str(r.get("code")) in _ok]}
        qp = ROOT / "wealth_code" / "cache" / f"auction_quotes-{day}.json"
        quotes = json.loads(qp.read_text(encoding="utf-8")) if qp.exists() else {}
        h, opened = sh.open_positions(h, plan.get("rows") or [], quotes, day)
        _save(h)
        for o in opened:
            print(f"[开仓] {o['priority']} {o['name']}({o['code']}) 晋级率 {o['entry_pct']}% @ {o['entry_price']}")
        if not opened:
            print("[开仓] 无可登记标的（晋级率 ≥40% 且未持有）")

    if a.execute:
        opens = {}
        for it in (h.get("open") or []):
            code = str(it.get("code"))
            try:
                bars = json.loads((ROOT / "cache" / "kline_qfq" / f"{code}.json").read_text(encoding="utf-8"))["bars"]
                hit = [b for b in bars if str(b.get("date"))[:10] == day]
                if hit:
                    opens[code] = float(hit[-1]["open"])
            except Exception:  # noqa: BLE001
                pass
        h, done = sh.execute(h, day, opens)
        _save(h)
        for c in done:
            print(f"[T+1 卖出] {c['name']}({c['code']}) 信号 {c.get('exit_signal_date')} → {day} 开盘出")

    if a.settle:
        zp = ROOT / "cache" / "zt_history" / f"zt_{day}.json"
        if not zp.exists():
            print(f"[结算] 缺 {zp.name}（未收盘或未落盘）")
            return 1
        zt = json.loads(zp.read_text(encoding="utf-8")).get("zt_list") or []
        closes = {}
        for it in (h.get("open") or []):
            code = str(it.get("code"))
            try:
                bars = json.loads((ROOT / "cache" / "kline_qfq" / f"{code}.json").read_text(encoding="utf-8"))["bars"]
                hit = [b for b in bars if str(b.get("date"))[:10] == day]
                if hit:
                    closes[code] = float(hit[-1]["close"])
            except Exception:  # noqa: BLE001
                pass
        h, closed = sh.settle(h, zt, day, closes)
        _save(h)
        for c in closed:
            print(f"[断板退出] {c['name']}({c['code']}) 晋级率 {c['entry_pct']}% → 移出推荐")
        print(f"[结算] {day} 断板 {len(closed)} 只 · 在持 {len(h.get('open') or [])} 只")

    if a.push:
        rows = sh.report(h)
        if rows:
            _hm = __import__("datetime").datetime.now().strftime("%H:%M")
            lines = [f"**🔒 持仓跟踪｜{len(rows)} 只**："]
            for r in rows:
                _col = "red" if r["持有状态"] != "已退出" else "green"
                lines.append(f"　<text_tag color='{_col}'>{r['推荐优先级']} {r['名称']}({r['代码']})</text_tag> "
                             f"晋级率 {r['晋级率']}%｜{r['持有状态']}｜{r['断板退出提示']}")
            card = {"title": f"🔒 持仓跟踪 | {_hm}", "color": "purple",
                    "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}],
                    "note": {"tag": "note", "elements": [{"tag": "lark_md",
                             "content": f"📡 狸猫猎手 | 不提前退出 · 断板收盘确认 · T+1 开盘卖 | {_hm}"}]}}
            tmp = Path("/tmp/limao_hold_card.json")
            tmp.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
            if a.dry_run:
                print("[dry] " + "\n".join(lines))
            else:
                import subprocess
                subprocess.run([sys.executable, str(ROOT / "scripts" / "push_card.py"),
                                "--webhook", a.webhook, str(tmp)], check=False)

    if a.report or not (a.open or a.settle or a.execute or a.push):
        rows = sh.report(h)
        if not rows:
            print("[持仓] 空")
        for r in rows:
            print(f"  {r['推荐优先级']:<3}{r['名称']}({r['代码']}) 晋级率 {r['晋级率']}% "
                  f"状态 {r['持有状态']} | {r['断板退出提示']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
