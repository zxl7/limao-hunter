#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选股宝「快讯」接口（baoer-api）可用性诊断 · 2026-10-13 新增。

背景
----
`latest.json.newsflash` 自 2026-09-17 起长期为空 → `capture_daily.capture_newsflash`
因 `if not nf: return 0` 提前返回、**不写文件** → `cache/newsflash_history/` 断更
（最新仍是 daily_2026-09-17.json），使「事件/催化」维度失去数据源。

实证：`cache/pulse_snapshots/express.log` 自 09-17 起 **227/227** 全为
      `抓取失败: <urlopen error [Errno 54] Connection reset by peer>`。
      两个独立抓取实现（intraday_pulse / newsflash_express）同域同败 → 指向域名级。

用法
----
    python3 scripts/diag_newsflash.py

逐组请求头实测，输出 HTTP 状态 / 字节数 / 失败原因 + 解析出的快讯条数。
⛔ 显式绕过环境代理（真实定时任务由 launchd/cron 直连，不经 shell 代理），
   使结果与生产一致。
"""
import json
import sys
import time
import urllib.request

URL = ("https://baoer-api.xuangubao.cn/api/v6/message/newsflash"
       "?subj_ids=9,10,723,35,469,821&platform=pcweb&_ts={ts}")

BROWSER = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://xuangubao.cn/",
    "Origin": "https://xuangubao.cn",
    "Connection": "keep-alive",
}

CASES = [
    ("修复前（简化 UA）", {"User-Agent": "Mozilla/5.0"}),
    ("修复后（完整浏览器头）", BROWSER),
    ("修复前（iPhone UA · intraday_pulse 原值）",
     {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"}),
]


def probe(label: str, headers: dict) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕代理=生产口径
    url = URL.format(ts=int(time.time() * 1000))
    try:
        req = urllib.request.Request(url, headers=headers)
        with opener.open(req, timeout=20) as r:
            raw = r.read().decode("utf-8", "replace")
        d = json.loads(raw)
        msgs = (((d.get("data") or {}).get("messages"))
                or ((d.get("newsflash") or {}).get("data") or {}).get("messages")
                or [])
        withstk = sum(1 for m in msgs if m.get("stocks") or m.get("bkj_infos"))
        print(f"  [{label}] ✅ HTTP {getattr(r, 'status', '?')} | {len(raw)}B | "
              f"messages={len(msgs)} | 带个股/板块={withstk}")
    except Exception as e:
        print(f"  [{label}] ❌ {type(e).__name__}: {str(e)[:110]}")


def main() -> int:
    print("选股宝快讯接口诊断（baoer-api）· 绕代理直连")
    print(f"URL: {URL.format(ts='<ts>')}")
    for label, hdrs in CASES:
        probe(label, hdrs)
    print("\n判定：任一组 ✅ 且 messages>0 → 接口可用（修复生效）；全 ❌ reset/timeout → "
          "域名级不可用，需换源（可接 common/http_client 或改走财联社/东财快讯）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
