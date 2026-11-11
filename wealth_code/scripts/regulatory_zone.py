#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股监管异动线独立计算脚本（沪深交易所严重异常波动 · 官方偏离值口径 · 2026-08-27 用户定）。

═══════════════ 规则来源与"为什么"（完整解释见 config/regulatory_rules.json） ═══════════════
本脚本【不写死任何规则】——认定线/接近档/对应指数/明日触发假设/渲染档位全部从
`wealth_code/config/regulatory_rules.json` 读取，改规则不动代码（与 expectation_rules.json
同款模式）。规则"为什么"也沉淀在该文件（如：为什么用偏离值而非纯涨幅、为什么 10日 100%/
30日 200%、为什么明日触发用指数平盘、为什么渲染分三级）。

规则口径速览（详情见规则文件）：
· 偏离值累计 = (期末收盘/期初前收盘−1)×100% − (对应指数期末/期初前−1)×100%  ← 交易所原文
· 认定线：10日偏离 ≥ +100%(-50%) / 30日偏离 ≥ +200%(-70%)  → 严重异常波动（重点监控/停牌核查）
· 对应指数：沪A→sh000002 上证A指 / 深A→sz399107 深证A指 / 创业板→sz399102 创业板综 /
  科创→sh000688 科创50；北交所无对应不标
· 明日触发（指数平盘假设）：min(10日线, 30日线) 先触者 → trig_gap_pct / trig_price

═══════════════ 用法 ═══════════════
  python3 wealth_code/scripts/regulatory_zone.py                        # 对最近 candidates 计算
  python3 wealth_code/scripts/regulatory_zone.py --codes 600127,002412  # 指定代码
  python3 wealth_code/scripts/regulatory_zone.py --output path.json     # 输出独立结果 JSON
  python3 wealth_code/scripts/regulatory_zone.py --rules custom.json    # 自定义规则文件（默认读 config/regulatory_rules.json）
  from scripts.regulatory_zone import enrich_regulatory                 # pipeline_eod 接入回填

数据即事实：失败/数据不足 → 对应字段不写，渲染层诚实降级不标注，绝不编造。
"""
from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent          # wealth_code/scripts
ROOT = HERE.parent.parent                        # 狸猫猎手/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.http_client import http_get          # noqa: E402
from common.tencent_kline import fetch_kline     # noqa: E402

DEFAULT_RULES = ROOT / "wealth_code/config/regulatory_rules.json"
IDX_KLINE_URL = "https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={sec},day,,,{n},qfq"


@lru_cache(maxsize=4)
def load_rules(path: Path | str | None = None) -> dict[str, Any]:
    """加载监管规则（默认 config/regulatory_rules.json，可 --rules 指定自定义）。
    结构见规则文件；此处不做默认值回退——规则缺失/字段缺失即报错，
    防止『写死的兜底值』悄悄替代规则（规则即事实）。"""
    p = Path(path) if path else DEFAULT_RULES
    if not p.exists():
        raise FileNotFoundError(f"[regulatory_zone] 规则文件不存在: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _index_map(rules: dict[str, Any]) -> dict[str, str]:
    """由规则 index_map 构建 代码前缀 → 指数 sec 映射（prefix 长优先匹配）。"""
    m: dict[str, str] = {}
    for entry in (rules.get("index_map") or {}).values():
        for pre in entry.get("prefix") or []:
            m[str(pre)] = str(entry["sec"])
    # 长前缀优先（如 300 先于 3）
    return dict(sorted(m.items(), key=lambda kv: -len(kv[0])))


def _index_sec(code: str, rules: dict[str, Any] | None = None) -> str | None:
    """对应指数（由规则 index_map 决定）。北交所等无匹配 → None（不标）。"""
    rules = rules or load_rules()
    idx_map = _index_map(rules)
    for pre, sec in idx_map.items():
        if code.startswith(pre):
            return sec
    return None


def _fetch_index_kline(sec: str, n: int | None = None, rules: dict[str, Any] | None = None) -> list[tuple[str, float]]:
    """拉对应指数 n 根日K（指数无前复权·取 day），返回 [(date, close), ...] 正序。"""
    rules = rules or load_rules()
    n = n or int(rules.get("kline_bars", 35))
    url = IDX_KLINE_URL.format(sec=sec, n=n)
    try:
        r = http_get(url, t=10, headers={"User-Agent": "Mozilla/5.0"}, retries=1)
    except Exception:
        return []
    if not isinstance(r, dict):
        return []
    d = (r.get("data") or {}).get(sec) or {}
    kl = d.get("day") or d.get("qfqday") or []
    out = []
    for x in kl:
        try:
            out.append((str(x[0]), float(x[2])))
        except Exception:
            continue
    return out


def _aligned(closes: list[tuple[str, float]],
             idx_closes: list[tuple[str, float]],
             need: int) -> tuple[list[float], list[float], list[str]] | None:
    """按共同交易日对齐，取最近 need 个共同日。返回 (个股收盘, 指数收盘, 日期) 正序。"""
    c_map = {d: c for d, c in closes}
    i_map = {d: c for d, c in idx_closes}
    common = sorted(set(c_map) & set(i_map))
    if len(common) < need:
        return None
    recent = common[-need:]
    return [c_map[d] for d in recent], [i_map[d] for d in recent], recent


def _compute(code: str, rules: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """单只计算（规则驱动·官方偏离值口径）。失败/数据不足返回 None。"""
    rules = rules or load_rules()
    bars = int(rules.get("kline_bars", 35))
    closes = fetch_kline(code, n=bars)
    if len(closes) < 11:
        return None
    idx_sec = _index_sec(code, rules)
    if idx_sec is None:
        return None
    idx_closes = _fetch_index_kline(idx_sec, n=bars, rules=rules)
    if len(idx_closes) < 31:
        return None
    al = _aligned(closes, idx_closes, 31)
    if not al:
        return None
    c, i, dates = al
    cur, icur = c[-1], i[-1]
    if cur <= 0 or icur <= 0:
        return None
    out: dict[str, Any] = {"kline_asof": dates[-1], "idx_sec": idx_sec}

    # 官方公式两项（当前窗口：最近10/30个交易日）
    # 🆕 2026-11-11 用户「身位要看最近一个月或最近 5 天的累计涨幅」：
    #    5/20 日累计涨幅与 10/30 日同一数据源、同一口径，只多算两个窗口。
    #    ⛔ 只落字段，不参与任何判据（排序/权重另议）。
    if len(c) >= 6 and c[-6] > 0:
        out["ret_5d_pct"] = round((cur / c[-6] - 1) * 100, 1)
    if len(c) >= 21 and c[-21] > 0:
        out["ret_20d_pct"] = round((cur / c[-21] - 1) * 100, 1)
    if c[-11] > 0:
        out["ret_10d_pct"] = round((cur / c[-11] - 1) * 100, 1)
    if c[-31] > 0:
        out["ret_30d_pct"] = round((cur / c[-31] - 1) * 100, 1)
    if i[-11] > 0:
        out["idx_10d_pct"] = round((icur / i[-11] - 1) * 100, 1)
    if i[-31] > 0:
        out["idx_30d_pct"] = round((icur / i[-31] - 1) * 100, 1)

    # 偏离值累计（监管口径 = 个股 − 指数）
    r10, r30 = out.get("ret_10d_pct"), out.get("ret_30d_pct")
    ix10, ix30 = out.get("idx_10d_pct"), out.get("idx_30d_pct")
    if r10 is not None and ix10 is not None:
        out["dev_10d_pct"] = round(r10 - ix10, 1)
    if r30 is not None and ix30 is not None:
        out["dev_30d_pct"] = round(r30 - ix30, 1)
    # 下跌认定线（-50%/-70%·规则 lines.down_*）
    d10, d30 = out.get("dev_10d_pct"), out.get("dev_30d_pct")
    dn10 = float(rules["lines"]["down_10d"]["value"])
    dn30 = float(rules["lines"]["down_30d"]["value"])
    if (isinstance(d10, (int, float)) and d10 <= dn10) or \
       (isinstance(d30, (int, float)) and d30 <= dn30):
        out["dev_hit_down"] = True

    # 明日触发（指数平盘假设·规则 trigger 定义）：
    # 明日收盘后 10日窗口(含明日)=最近10个共同日 → 基准=第10个共同日(c[-10]/i[-10])
    #   dev10' = +100% → C1 = c[-10]×(1.0 + I1/i[-10]) → g10 = (c[-10]/cur)(up10/100 + icur/i[-10]) − 1
    # 30日窗口 → 基准=第30个共同日(c[-30]/i[-30])；认定线 +200% →
    #   g30 = (c[-30]/cur)(up30/100 + icur/i[-30]) − 1  （⚠️ 曾误用 1.0 被用户反推抓 bug）
    # 触发 = min(g10, g30) 先触者。
    up10 = float(rules["lines"]["dev_10d"]["value"])   # 100%
    up30 = float(rules["lines"]["dev_30d"]["value"])   # 200%
    trig_gaps: list[tuple[str, float]] = []
    if c[-10] > 0 and i[-10] > 0:
        g10 = (c[-10] / cur) * (up10 / 100.0 + icur / i[-10]) - 1
        trig_gaps.append(("10日", g10))
    if c[-30] > 0 and i[-30] > 0:
        g30 = (c[-30] / cur) * (up30 / 100.0 + icur / i[-30]) - 1
        trig_gaps.append(("30日", g30))
    if trig_gaps:
        line, g = min(trig_gaps, key=lambda x: x[1])
        out["trig_line"] = line
        out["trig_gap_pct"] = round(g * 100, 1)
        out["trig_price"] = round(cur * (1 + g), 2)

    if not any(k in out for k in ("dev_10d_pct", "dev_30d_pct", "trig_gap_pct")):
        return None
    return out


def enrich_regulatory(cands: list[dict[str, Any]], rules: dict[str, Any] | None = None) -> int:
    """给候选列表批量回填监管异动线字段（幂等·失败跳过不中断选股）。返回回填数。
    回填字段：ret_10d/30d_pct、idx_10d/30d_pct、dev_10d/30d_pct（监管口径偏离值）、
    dev_hit_down（下跌线）、trig_line/trig_gap_pct/trig_price（明日触发·指数平盘）、
    kline_asof（数据截止日）、idx_sec（对应指数）。"""
    rules = rules or load_rules()
    n = 0
    for c in cands:
        code = str(c.get("code") or "")
        if not code:
            continue
        try:
            r = _compute(code, rules)
        except Exception:
            r = None
        if r:
            c.update(r)
            n += 1
    return n


def main() -> int:
    argv = [a for a in sys.argv[1:]]
    out_path: Path | None = None
    if "--output" in argv:
        i = argv.index("--output")
        out_path = Path(argv[i + 1]) if i + 1 < len(argv) else None
        del argv[i:i + 2]
    codes: list[str] | None = None
    if "--codes" in argv:
        i = argv.index("--codes")
        codes = [c.strip() for c in argv[i + 1].split(",") if c.strip()] if i + 1 < len(argv) else []
        del argv[i:i + 2]
    rules_path: Path | None = None
    if "--rules" in argv:
        i = argv.index("--rules")
        rules_path = Path(argv[i + 1]) if i + 1 < len(argv) else None
        del argv[i:i + 2]

    rules = load_rules(rules_path)
    if codes:
        cands: list[dict[str, Any]] = [{"code": c, "name": c} for c in codes]
        src = f"--codes {len(codes)} 只"
    else:
        import glob
        files = sorted(glob.glob(str(ROOT / "wealth_code/cache/candidates-*.json")))
        if not files:
            print("[regulatory_zone] 无 candidates 文件（可用 --codes 指定代码）")
            return 1
        payload = json.load(open(files[-1], encoding="utf-8"))
        cands = (payload.get("relay") or []) + (payload.get("watch") or [])
        src = Path(files[-1]).name

    n = enrich_regulatory(cands, rules)
    print(f"[regulatory_zone] 计算 {n} 只（源 {src}）· 规则文件 {Path(rules_path) if rules_path else DEFAULT_RULES.name}")
    items = []
    up10 = float(rules["lines"]["dev_10d"]["value"])
    up30 = float(rules["lines"]["dev_30d"]["value"])
    for c in cands:
        d10, d30 = c.get("dev_10d_pct"), c.get("dev_30d_pct")
        tg, tp, tl = c.get("trig_gap_pct"), c.get("trig_price"), c.get("trig_line")
        hit_up = (isinstance(d10, (int, float)) and d10 >= up10) or \
                 (isinstance(d30, (int, float)) and d30 >= up30)
        if any(v is not None for v in (d10, d30, tg)):
            item = {k: c.get(k) for k in (
                "code", "name", "ret_5d_pct", "ret_20d_pct", "ret_10d_pct", "ret_30d_pct", "idx_10d_pct", "idx_30d_pct",
                "dev_10d_pct", "dev_30d_pct", "dev_hit_down", "trig_line", "trig_gap_pct",
                "trig_price", "kline_asof", "idx_sec")}
            items.append(item)
            d10t = f"10日偏离{d10:+.0f}%" if isinstance(d10, (int, float)) else "—"
            d30t = f"30日偏离{d30:+.0f}%" if isinstance(d30, (int, float)) else "—"
            tgt = f"明日{tg:+.1f}%·{tp}元触线({tl})" if isinstance(tg, (int, float)) else "—"
            flag = "⚠️触监管线" if hit_up else ("⚠️触下跌线" if c.get("dev_hit_down") else "")
            print(f"  {c.get('name','?')}({c.get('code','?')}) {d10t} {d30t} | {tgt} {flag}")
    if out_path and items:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(
            {"schema": "regulatory_zone_v2", "generated_at_bj": str(datetime_now_bj()),
             "rules_file": str(rules_path) if rules_path else str(DEFAULT_RULES),
             "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[regulatory_zone] 已写独立结果 → {out_path}")
    return 0


def datetime_now_bj():
    from datetime import datetime
    from common.market_time import BJ_TZ
    return datetime.now(BJ_TZ)


if __name__ == "__main__":
    sys.exit(main())
