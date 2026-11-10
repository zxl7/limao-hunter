#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯自选股查询层（2026-08-25 用户「查询用腾讯自选股」·eod 资金/筹码补强）。

职责：封装 npx westock-data-skillhub 批量查询（fund flow / chip），供 pipeline_eod
回填候选个股的资金/筹码维度。与盘中实时接口（东财 push2/腾讯 qt）分工：
**实时盘口类接口管盘中盯盘，本层管盘后查询分析**（数据当日可用·非实时推送）。

命令：
  npx -y westock-data-skillhub@1.0.5 fund flow <codes>   # 个股资金流（当日/5日/10日/20日主力）
  npx -y westock-data-skillhub@1.0.5 chip <codes>        # 筹码分布（获利盘/平均成本/集中度）

⚠️ 解析要点：
  - fund flow 批量返回 [Batch] 宽表（symbol/code/name/MainNetFlow/MainNetFlow5D...）
  - chip 批量返回**每列带前缀**（sz002412.chipProfitRate）——按 code 前缀分桶
  - 失败/空 → 返回空 dict（下游诚实降级不编造）
"""
from __future__ import annotations

import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

# ⛔ 禁止硬编码 node 版本路径（2026-09-12 事故：托管运行时 22.22.2-2 → 22.22.2-3 迁移，
# 硬编码路径失效 → P2-19 风险归档与龙虎榜数据源当日全链路静默失败）。
# 统一走 common/npx_path.find_npx（运行时解析·版本迁移自动跟随）。
_ROOT = Path(__file__).resolve().parents[2]      # 狸猫猎手/
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from common.npx_path import find_npx, npx_env  # noqa: E402

NPX = find_npx()
PKG = "westock-data-skillhub@1.0.5"


def _warn(msg: str) -> None:
    """故障留痕（统一日志·缺失退化 print）——⛔ 不得静默吞错。"""
    try:
        from common.logging_setup import get_logger
        get_logger("tx_query").warning(msg)
    except Exception:
        print(f"[tx_query] {msg}")


def _run(args: list[str], timeout: int = 30) -> str | None:
    """执行 westock CLI。⛔ 失败必须留痕（原实现静默返回 None·2026-09-12 排障全靠猜）。"""
    if not NPX or not Path(NPX).exists():
        _warn(f"npx 不可用: {NPX!r}（检查 common/npx_path.find_npx）")
        return None
    try:
        # ⛔ 带 npx_env()：用户 ~/.npm 权限损坏时裸调用会 EPERM 静默失效
        r = subprocess.run([NPX, "-y", PKG] + args,
                           capture_output=True, text=True, timeout=timeout,
                           env=npx_env())
    except subprocess.TimeoutExpired:
        _warn(f"npx 超时 {timeout}s: {args[:2]}")
        return None
    except Exception as e:
        _warn(f"npx 异常: {e}")
        return None
    if r.returncode != 0:
        _warn(f"npx 失败 rc={r.returncode}: {args[:2]} | {r.stderr.strip()[:200]}")
        return None
    return r.stdout


def _norm_code(code: str) -> str:
    """600172 → sh600172 / 000017 → sz000017 / 300xxx → sz300xxx / 688xxx → sh688xxx / pt 板块 → 原样。"""
    c = str(code).strip()
    if c.lower().startswith(("sh", "sz")):
        return c.lower()
    if c.lower().startswith("pt"):   # 腾讯板块代码（申万/概念）原样透传
        return c.lower()
    if c.startswith(("6", "9", "5", "68")):
        return f"sh{c}"
    return f"sz{c}"


def fund_flow(codes: list[str]) -> dict[str, dict[str, Any]]:
    """批量个股资金流 → {code: {main_flow_yi(当日), main_flow_5d_yi, main_flow_10d_yi, main_flow_20d_yi}}。

    MainNetFlow=当日主力净流入(元) / MainNetFlow5D/10D/20D=对应区间主力净流入(元)。
    """
    if not codes:
        return {}
    raw = _run(["fund", "flow", ",".join(_norm_code(c) for c in codes)])
    if not raw:
        return {}
    out: dict[str, dict[str, Any]] = {}
    # 表头行定位字段索引
    header_line = next((l for l in raw.splitlines() if "MainNetFlow" in l), "")
    if not header_line:
        return {}
    headers = [h.strip() for h in header_line.strip("|").split("|")]
    idx = {h: i for i, h in enumerate(headers)}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("|") or "MainNetFlow" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        # 跳过 markdown 分隔行（|---|）与空行
        if len(cells) < len(headers) or all(c in ("", "-", "---") for c in cells):
            continue
        code_raw = cells[idx.get("code", 0)]
        # code 可能带前缀（sz002837）→ 提取纯数字（容错：去非数字）；pt 板块代码（pt01801050）原样保留
        code = code_raw if str(code_raw).lower().startswith("pt") else re.sub(r"\D", "", code_raw)
        if not code:
            continue
        def _f(k: str) -> float:
            v = cells[idx[k]] if k in idx else ""
            try:
                return round(float(v) / 1e8, 2)  # 元→亿
            except (ValueError, TypeError, IndexError):
                return 0.0
        out[code] = {
            "main_flow_yi": _f("MainNetFlow"),
            "main_flow_5d_yi": _f("MainNetFlow5D"),
            "main_flow_10d_yi": _f("MainNetFlow10D"),
            "main_flow_20d_yi": _f("MainNetFlow20D"),
        }
    return out


def chip(codes: list[str]) -> dict[str, dict[str, Any]]:
    """批量筹码分布 → {code: {chip_profit_rate(获利盘%), chip_avg_cost(平均成本), chip_conc90}}。

    chip 批量返回宽表（每列带 `sz002412.` 前缀），按前缀分桶解析。
    """
    if not codes:
        return {}
    raw = _run(["chip", ",".join(_norm_code(c) for c in codes)])
    if not raw:
        return {}
    out: dict[str, dict[str, Any]] = {}
    # 表头行：sz002412.chipProfitRate | sz002412.chipAvgCost | ...
    header_line = next((l for l in raw.splitlines() if ".chipProfitRate" in l), "")
    if not header_line:
        return {}
    headers = [h.strip() for h in header_line.strip("|").split("|")]
    # 找数据行
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("|") or ".chipProfitRate" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < len(headers):
            continue
        # 按前缀分桶
        buckets: dict[str, dict[str, Any]] = {}
        for h, v in zip(headers, cells):
            if "." not in h or v in ("", "-"):
                continue
            prefix, field = h.split(".", 1)
            code = prefix.replace("sz", "").replace("sh", "")
            buckets.setdefault(code, {})[field] = v
        for code, vals in buckets.items():
            code = re.sub(r"\D", "", code)  # 前缀可能含非数字
            if not code:
                continue
            def _f(field: str, default: float = 0.0) -> float:
                try:
                    return float(vals.get(field, default))
                except (ValueError, TypeError):
                    return default
            out[code] = {
                "chip_profit_rate": _f("chipProfitRate"),      # 获利盘 %
                "chip_avg_cost": _f("chipAvgCost"),            # 平均成本（元）
                "chip_conc90": _f("chipConcentration90"),      # 90%筹码集中度
            }
    return out


def enrich_candidates(cands: list[dict[str, Any]]) -> int:
    """给候选列表批量回填资金/筹码字段（幂等·失败跳过不中断）。返回回填数。"""
    codes = [str(c.get("code") or "") for c in cands]
    codes = [c for c in codes if c]
    if not codes:
        return 0
    flows = fund_flow(codes)
    chips = chip(codes)
    n = 0
    for c in cands:
        code = str(c.get("code") or "")
        f = flows.get(code)
        if f:
            c.update(f)
            n += 1
        ch = chips.get(code)
        if ch:
            c.update(ch)
            n += 1
    return n


# ============ P2-19 腾讯 westock 深化：风险事件 / 公告 / 两融（2026-09-10 落地） ============
# 08-18 定序第 4 批：公告异动 + 风险事件（风险类别）+ 两融，供盘后链抽取并入复盘卡风险区。
# ⛔ 边界：本层=盘后/盘前查询分析（非实时盘口，分工铁律 2026-08-25 不变）；
#         纯新增函数，不改动既有 fund_flow/chip 契约与调用方（pipeline_eod enrich 不受影响）。
# 实测返回格式（2026-09-10）：
#   risk   → 按「## 类别」分段，每段一张 Markdown 表（股权质押/高管变动/…）
#   notice → 头部「共 N 条」+ Markdown 表（id/symbol/title/time/type/url…）
#   margin → 单张 Markdown 表（code/name/融资余额/融券余额/融资买入/融资余额环比(%)）


def _parse_md_table(text: str) -> list[dict[str, str]]:
    """Markdown 表格 → list[dict]（跳过 --- 分隔行）。无有效表返回 []。"""
    rows: list[list[str]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue  # 分隔行（|---|---|）
        rows.append(cells)
    if len(rows) < 2:
        return []
    header = rows[0]
    out: list[dict[str, str]] = []
    for r in rows[1:]:
        if len(r) < len(header):
            r = r + [""] * (len(header) - len(r))
        out.append({header[i]: r[i] for i in range(len(header))})
    return out


def _parse_risk_sections(raw: str) -> dict[str, Any]:
    """risk 返回文本 → {labels, sections, pledge_pct}（分段表解析·无段返回 {}）。"""
    sections: dict[str, list[dict[str, str]]] = {}
    cur: str | None = None
    buf: list[str] = []
    for line in raw.splitlines():
        if line.startswith("## "):
            # 分段结束 → 落表（内联 flush：避免闭包捕获旧值导致末段丢失）
            if cur and buf:
                rows = _parse_md_table("\n".join(buf))
                if rows:
                    sections[cur] = rows
            cur = line[3:].strip()
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur and buf:  # 末段
        rows = _parse_md_table("\n".join(buf))
        if rows:
            sections[cur] = rows
    if not sections:
        return {}
    pledge = None
    for row in sections.get("股权质押", []):
        m = re.search(r"([\d.]+)%", row.get("质押比例") or "")
        if m:
            try:
                pledge = float(m.group(1))
            except ValueError:
                pledge = None
            break
    return {"labels": list(sections.keys()), "sections": sections, "pledge_pct": pledge}


_RISK_TICKER_RE = re.compile(r"^#\s+(.+?)\s*\(([a-z]{2}\d{6})\)\s*-\s*风险事件\s*$")
_NOTICE_TICKER_RE = re.compile(r"^\*\*([a-z]{2}\d{6})\*\*\s*$")


def _plain(code: str) -> str:
    """sh600865/sz000001 → 纯 6 位代码（统一输出键）。"""
    return re.sub(r"^(sh|sz)", "", str(code).lower())


def risk_events(codes: list[str], limit: int = 8, workers: int = 1) -> dict[str, dict[str, Any]]:
    """批量个股风险事件（**单次批量调用**·[Batch] 模式）→
    {code: {labels:[风险类别…], sections:{类别:[行]}, pledge_pct: float|None, checked: bool}}
    + 键 `_unavailable`: [未返回的 code]（查询失败 ≠ 无风险，下游须区分）。

    实测（2026-09-10）：`risk sh600865,sh603042` → `[Batch] 成功 2/2`，票间以
    `# 名称 (代码) - 风险事件` 分节、票内以 `## 类别` 分表。
    ⚠️ 性能：逐票串行 ~35s/次（npx 冷启动），**批量 1 次调用覆盖全部票**（13 次 → 3 次）。
    labels 直接来自上游分段标题（客观不加工）；pledge_pct=股权质押比例(%) 首行值。
    """
    targets = [c for c in codes[:limit] if c]
    if not targets:
        return {}
    raw = _run(["risk", ",".join(_norm_code(c) for c in targets)], timeout=120)
    if not raw:
        return {"_unavailable": [_plain(_norm_code(c)) for c in targets]}
    chunks: dict[str, list[str]] = {}
    cur: str | None = None
    for line in raw.splitlines():
        m = _RISK_TICKER_RE.match(line.strip().lower())
        if m:
            cur = _plain(m.group(2))
            chunks[cur] = []
        elif cur is not None:
            chunks[cur].append(line)
    out: dict[str, dict[str, Any]] = {}
    for code, lines in chunks.items():
        parsed = _parse_risk_sections("\n".join(lines))
        out[code] = parsed or {"labels": [], "sections": {}, "pledge_pct": None, "checked": True}
    missing = [_plain(_norm_code(c)) for c in targets if _plain(_norm_code(c)) not in out]
    if missing:
        out["_unavailable"] = missing
    return out


def notices(codes: list[str], ntype: int | None = 6, limit: int = 8,
            workers: int = 1) -> dict[str, dict[str, Any]]:
    """批量公告列表（**单次批量调用**）→ {code: {count, upstream_total, items:[{id,title,time,type}]}}

    ⚠️ ntype 默认 **6（风险类公告）**——实测 ntype=None 时上游报的是**全部历史口径**
    （桂林旅游 1262 / 红棉股份 1933，无风险区参考价值）；风险区用途只取 6。
    口径同上游 --type：1 财务 / 2 配股 / 3 增发 / 4 股权变动 / 5 重大 / 6 风险 / 7 其他。

    实测：多票 `notice list sh600865,sh603042 --type 6` → `[Batch] 成功 2/2` + `**sh603042**` 分票；
    单票无分票标志 → 走整段解析回退。count=本次返回条数；upstream_total=上游「共 N 条」（可溯源）。
    """
    targets = [c for c in codes[:limit] if c]
    if not targets:
        return {}
    args = ["notice", "list", ",".join(_norm_code(c) for c in targets)]
    if ntype is not None:
        args += ["--type", str(ntype)]
    raw = _run(args, timeout=120)
    if not raw:
        return {}
    chunks: dict[str, list[str]] = {}
    cur: str | None = None
    for line in raw.splitlines():
        m = _NOTICE_TICKER_RE.match(line.strip().lower())
        if m:
            cur = _plain(m.group(1))
            chunks[cur] = []
        elif cur is not None:
            chunks[cur].append(line)
    if not chunks and len(targets) == 1:            # 单票回退（无 **code** 分票标志）
        chunks[_plain(_norm_code(targets[0]))] = raw.splitlines()
    out: dict[str, dict[str, Any]] = {}
    for code, lines in chunks.items():
        text = "\n".join(lines)
        m = re.search(r"共\s*(\d+)\s*条", text)
        rows = _parse_md_table(text)
        if not rows and not m:
            continue
        items = [{"id": r.get("id"), "title": r.get("title"), "time": r.get("time"),
                  "type": r.get("type")} for r in rows if r.get("title")]
        out[code] = {"count": len(items),
                     "upstream_total": int(m.group(1)) if m else None,
                     "items": items}
    return out


def margin(codes: list[str]) -> dict[str, dict[str, Any]]:
    """批量两融（一次调用·逗号拼接）→ {code: {rz_balance_yi, rq_balance_yi, rz_buy_yi, rz_chg_pct}}
    单位换算：融资/融券余额、融资买入 → 亿元（÷1e8·round 2）；环比保留上游百分数。"""
    if not codes:
        return {}
    raw = _run(["fund", "margin", ",".join(_norm_code(c) for c in codes)], timeout=40)
    if not raw:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in _parse_md_table(raw):
        code = (row.get("code") or "").lower()
        if not code:
            continue
        plain = re.sub(r"^(sh|sz)", "", code)

        def _f(key: str):
            try:
                return round(float(row.get(key) or 0) / 1e8, 2)
            except (TypeError, ValueError):
                return None

        out[plain] = {"rz_balance_yi": _f("融资余额"), "rq_balance_yi": _f("融券余额"),
                      "rz_buy_yi": _f("融资买入"), "rz_chg_pct": row.get("融资余额环比(%)")}
    return out


def market_margin() -> dict[str, Any] | None:
    """全市场两融余额多周期变动（market-overview --type margin）。
    ⚠️ 实测盘前可能「后端今日尚未更新」→ 返回 None（下游按缺失处理，禁编造）。"""
    raw = _run(["market-overview", "--type", "margin"], timeout=40)
    if not raw or "暂无数据" in raw:
        return None
    rows = _parse_md_table(raw)
    return {"rows": rows} if rows else None


if __name__ == "__main__":
    import json as _json
    import sys as _sys
    _args = _sys.argv[1:]
    if _args:
        _cmd, _codes = _args[0], (_args[1:] or ["600519"])
        if _cmd == "risk":
            print(_json.dumps(risk_events(_codes), ensure_ascii=False, indent=1))
        elif _cmd == "notice":
            print(_json.dumps(notices(_codes, ntype=6), ensure_ascii=False, indent=1))
        elif _cmd == "margin":
            print(_json.dumps(margin(_codes), ensure_ascii=False, indent=1))
        elif _cmd == "market-margin":
            print(_json.dumps(market_margin(), ensure_ascii=False, indent=1))
        else:
            print("用法: tx_query.py {risk|notice|margin|market-margin} <codes...>")
    else:
        demo = [{"code": "002412"}, {"code": "002837"}, {"code": "003040"}]
        n = enrich_candidates(demo)
        print(f"回填 {n} 字段:")
        for c in demo:
            print(f"  {c['code']}: main_flow={c.get('main_flow_yi')} 5d={c.get('main_flow_5d_yi')} "
                  f"获利盘={c.get('chip_profit_rate')}% 成本={c.get('chip_avg_cost')}")
