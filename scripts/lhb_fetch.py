#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""龙虎榜抓取（2026-08-25 用户「龙虎榜·改卡片时间满足刷新」·盘后 18:00+ 披露）。

数据源：腾讯自选股 westock-data-skillhub `lhb` 命令（盘后数据·非实时）。
产出：cache/lhb_YYYY-MM-DD.json
  {
    "date": "2026-08-25",
    "institution": [ {code,name,inst_buy_yi,net_yi,days_on_board} ],  # 机构榜
    "hotmoney":    [ {seat,stocks:[{code,name}],amount_yi} ],        # 游资席位榜
    "generated_ts": "..."
  }

调用时机：盘后复盘卡（用户将 15:10 改到 18:00+ 以满足龙虎榜披露）。失败/未披露 → 返回空
结构并打印提示（诚实降级不编造）。机构买入(inst_buy_yi>0)=资金确认(红)；净卖(green)=分歧。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
# ⛔ 禁止硬编码 node 版本路径（2026-09-12 事故：托管运行时 22.22.2-2 → 22.22.2-3 迁移，
# 硬编码失效 → 龙虎榜数据源静默失败）。统一走 common/npx_path.find_npx（运行时解析）。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from common.npx_path import find_npx, npx_env  # noqa: E402

NPX = find_npx()
PKG = "westock-data-skillhub@1.0.5"


def _bj() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _run(args: list[str], timeout: int = 30) -> str | None:
    """失败留痕（不静默吞错·2026-09-12 统一）。"""
    if not NPX or not Path(NPX).exists():
        print(f"[lhb_fetch] npx 不可用: {NPX!r}")
        return None
    try:
        # ⛔ 必须带 npx_env()：用户 ~/.npm 权限损坏时，裸调用会 EPERM 静默失效
        #    （2026-11-09 龙虎榜断更 41 天的直接原因，见 common/npx_path.py）
        r = subprocess.run([NPX, "-y", PKG] + args,
                           capture_output=True, text=True, timeout=timeout,
                           env=npx_env())
        if r.returncode != 0:
            print(f"[lhb_fetch] npx 失败 rc={r.returncode}: {args[:2]} | {r.stderr.strip()[:160]}")
            return None
        return r.stdout
    except Exception as e:
        print(f"[lhb_fetch] npx 异常: {e}")
        return None


def _parse_institution(raw: str) -> list[dict]:
    """机构榜 markdown 表 → [{code,name,inst_buy_yi,net_yi,days}]。"""
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("|") or "机构买入额" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 10 or all(c in ("", "-", "---") for c in cells):
            continue
        def _num(s: str) -> float:
            # 支持负号（机构净卖出为负值，如 -7.74亿；丢负号会误判为净买入）
            neg = "-" in (s or "")
            m = re.search(r"([\d.]+)亿", s or "")
            if m:
                v = float(m.group(1))
                return -v if neg else v
            m2 = re.search(r"([\d.]+)万", s or "")
            v2 = float(m2.group(1)) / 1e4 if m2 else 0.0
            return -v2 if neg and m2 else v2
        code = re.sub(r"\D", "", cells[1])
        out.append({
            "code": code,
            "name": cells[2],
            "days_on_board": cells[3],
            "inst_buy_yi": _num(cells[5]),
            "net_yi": _num(cells[8]) if len(cells) > 8 else 0.0,
        })
    return out


def _parse_hotmoney(raw: str) -> list[dict]:
    """游资席位榜（activeseat·活跃席位 128个）→ [{seat, stocks:[{code,name}], amount_yi}]。

    表头：| 席位ID | 席位名称 | 股票代码 | 股票名称 | 买入金额 |（5列·2026-08-25 实测）。
    """
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("|") or "席位ID" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5 or all(c in ("", "-", "---") for c in cells):
            continue
        codes = [re.sub(r"\D", "", c) for c in cells[2].split(";") if c]
        names = [n for n in cells[3].split(";") if n]
        stocks = [{"code": c, "name": n} for c, n in zip(codes, names)]
        def _amt(s: str) -> float:
            m = re.search(r"([\d.]+)亿", s or "")
            return float(m.group(1)) if m else 0.0
        out.append({"seat": cells[1], "stocks": stocks, "amount_yi": _amt(cells[4])})
    return out


def run(date: str | None = None) -> dict:
    date = date or _bj().strftime("%Y-%m-%d")
    out = {"date": date, "institution": [], "hotmoney": [], "generated_ts": ""}
    # 机构榜
    raw_inst = _run(["lhb", "--date", date])
    if raw_inst and "机构榜" in raw_inst:
        out["institution"] = _parse_institution(raw_inst)
    # 游资席位榜
    raw_hot = _run(["lhb", "--type", "activeseat", "--date", date])
    if raw_hot and ("活跃席位" in raw_hot or "席位ID" in raw_hot):
        out["hotmoney"] = _parse_hotmoney(raw_hot)
    out["generated_ts"] = _bj().isoformat(timespec="seconds")
    out["ok"] = bool(out["institution"] or out["hotmoney"])
    if not out["ok"]:
        # ⛔ 未披露/接口异常时**不落盘**：写一个空文件会把「数据缺失」伪装成「今天有文件」，
        #    新鲜度面板与守护脚本就都看不出来了（09-29 起 41 天无人发现的同类盲区）。
        print(f"[lhb] {date} 龙虎榜未披露或接口异常（披露时间约 18:00-20:00）")
        return out
    print(f"[lhb] {date} 机构 {len(out['institution'])} 只 / 游资席位 {len(out['hotmoney'])} 家")
    (CACHE / f"lhb_{date.replace('-', '')}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    import sys
    _out = run(sys.argv[1] if len(sys.argv) > 1 else None)
    # 退出码给调度/守护用：0=已落盘，2=未披露或抓取失败（⛔ 不再「空数据也 exit 0」）
    raise SystemExit(0 if _out.get("ok") else 2)
