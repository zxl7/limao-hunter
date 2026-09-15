#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""五日线纪律检查（生命线监控）。

对「持仓 + 主线观察池」个股计算 MA5（5日均线）与现价的相对位置：
  - 现价 >= MA5 → 五日线上（生命线守稳）
  - 现价 <  MA5  → 跌破五日线（生命线失守，纪律提示）

数据源：腾讯日K接口（web.ifzq.gtimg.cn，前复权，稳定无UA限制）。
由 pulse_fetcher.sh 每轮调用（内部 300s 缓存），写 cache/ma5_status.json，
供盘中猎手 / 盘后复盘直接读取标注。

用法:
  python3 ma5_check.py             # 计算（300s 缓存短路）
  python3 ma5_check.py --force     # 强制重算
"""
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 架构重构（2026-08-17 P0-3·复用优先）：网络层统一 common.http_client，
# 时间统一 common.market_time，JSON 读写统一 common.json_io。
from common.http_client import http_get
from common.json_io import load_json, save_json
from common.market_time import now

CACHE = ROOT / "cache"
SNAP = CACHE / "pulse_snapshots"

# 主线观察池（2026-07-31 用户主线观察，非持仓）：玻璃基板/金刚石散热
OBSERVE = [
    ("300088", "长信科技"),   # 玻璃基板 TGV
    ("301071", "力量钻石"),   # 金刚石散热
    ("920725", "惠丰钻石"),   # 金刚石散热（北交所）
]
TTL = 300  # 缓存秒数（K线盘中当日收盘随现价更新，300s 重算足够）


def market(code: str) -> str:
    """腾讯市场前缀：沪 6/9→sh，深 0/3→sz，北 8/4/920→bj（统一 common.tencent_kline.tk_market）"""
    from common.tencent_kline import tk_market
    return tk_market(code)


def fetch_kline(code: str):
    """拉最近8根日K（前复权），返回 [(date, close), ...] 正序。
    2026-08-17 架构重构：统一 common.tencent_kline.fetch_kline（消与 feedback_matrix 重复）。"""
    from common.tencent_kline import fetch_kline as _tk_kline
    return _tk_kline(code, n=8)


def ma5_check(code: str, name: str) -> dict:
    closes = []
    try:
        closes = fetch_kline(code)
    except Exception as e:
        return {"code": code, "name": name, "error": f"kline_fail:{type(e).__name__}"}
    if len(closes) < 6:
        return {"code": code, "name": name, "error": "kline_insufficient",
                "closes": closes}
    last5 = closes[-5:]
    price = last5[-1][1]           # 最新收盘（盘中=实时价）
    ma5 = round(sum(c for _, c in last5) / 5, 3)
    above = price >= ma5
    return {
        "code": code,
        "name": name,
        "price": price,
        "ma5": ma5,
        "above_ma5": above,
        "pct_vs_ma5": round((price - ma5) / ma5 * 100, 2) if ma5 else 0,
        "closes": [{"date": dt, "close": c} for dt, c in closes[-6:]],
        "checked_at": now().strftime("%H:%M"),
    }


def main():
    force = "--force" in sys.argv[1:]
    out = CACHE / "ma5_status.json"
    # 缓存短路（统一 json_io）
    if not force:
        old = load_json(out)
        if old and old.get("ts"):
            try:
                age = (now() - datetime.fromisoformat(old["ts"])).total_seconds()
                if age < TTL:
                    print("[ma5_check] ⏭ 缓存未过期(<300s)，跳过")
                    return 0
            except (ValueError, TypeError):
                pass

    # 标的池：持仓(holdings.json) + 观察池，按 code 去重
    codes = {}
    h = load_json(CACHE / "holdings.json") or {}
    for it in (h.get("holdings") or []):
        c = str(it.get("code") or "")
        if c:
            codes[c] = it.get("name") or c
    for c, n in OBSERVE:
        codes.setdefault(c, n)

    items = [ma5_check(c, n) for c, n in codes.items()]
    ok = [x for x in items if "error" not in x]
    broken = [x for x in ok if not x["above_ma5"]]
    data = {
        "ts": now().isoformat(),
        "items": items,
        "total": len(ok),
        "above_count": len(ok) - len(broken),
        "broken_count": len(broken),
        "broken_list": [{"code": x["code"], "name": x["name"],
                         "price": x["price"], "ma5": x["ma5"],
                         "pct_vs_ma5": x["pct_vs_ma5"]} for x in broken],
    }
    save_json(out, data)
    print(f"[ma5_check] ✅ 检查{len(ok)}只 五日线上{data['above_count']} 跌破{data['broken_count']} → {out}")
    for x in broken:
        print(f"  ⚠️ 跌破五日线: {x['name']}({x['code']}) 现价{x['price']} < MA5 {x['ma5']} ({x['pct_vs_ma5']}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
