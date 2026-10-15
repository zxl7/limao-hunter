#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
精选快讯速递（2026-09-04 用户「异动群加一点快讯·精选速递」新增）
====================================================
异动群此前只有「结果信号」（涨停/拉升/核按钮），缺「原因信号」（催化快讯）。
本脚本=消息面精选直推：独立抓选股宝快讯（baoer-api v6）→ 只挑「值得播报」的硬快讯
→ 走短线精灵统一出口（short_spirit.py --send·45s 内容防抖+短线精灵品牌标题）推异动群。

精选标准（只播三类·防刷屏）：
  ① 点名 ≥1 只 A 股个股（stocks 非空）—— 个股级催化（最强）
  ② 带题材板块映射（bkj 非空）且标题含重磅词（政策/上调/涨价/发布/获批/突破/首个/
     中标/订单/收购/重组/合作/试点/落地/入选/获批上市 等）—— 板块级事件
  ③ 其余（纯宏观/盘面播报）→ 不进群
频率上限：每轮 ≤2 条；独立当日 id 去重（newsflash_express_pushed.json），
与 intraday_pulse 的早鸟消费（pushed_newsflash.json）各自独立不冲突。

用法: python3 scripts/newsflash_express.py [--dry-run]
守护链: pulse_fetcher.sh 每轮调用（交易时段内）
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BJ = timezone(timedelta(hours=8))
NEWS_URL = ("https://baoer-api.xuangubao.cn/api/v6/message/newsflash"
            "?subj_ids=9,10,723,35,469,821&platform=pcweb&_ts={ts}")
# ⚠️ 2026-10-13：baoer-api 快讯对简化 UA 一律 `Connection reset by peer`
#    （express.log 实证自 09-17 起 227/227 全失败）→ 精选快讯长期为空。
#    改用完整浏览器头。⛔ 与 scripts/intraday_pulse.py::NEWS_HEADERS 必须同值（同一接口契约）。
NEWS_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://xuangubao.cn/",
    "Origin": "https://xuangubao.cn",
    "Connection": "keep-alive",
}
PUSHED = ROOT / "cache" / "newsflash_express_pushed.json"
MAX_PER_ROUND = 2  # 每轮最多播 2 条（防 30 条全量涌出）
# ⛔ 快讯时效门（2026-09-07 用户「快讯也要实时·禁推过时」·核心铁律）：
#    baoer 返回 30 条是 3 小时消息池且顺序≠时间序（实证旧快讯排前），
#    只推 created_at 距今 ≤10min 的当下快讯；过时(>10min)即使首次见也一律不推。
NEWS_MAX_AGE_SEC = 600


def _nf_ts(m) -> float:
    """快讯 created_at → epoch 秒（兼容 秒/毫秒/ISO 字符串）。"""
    ca = m.get("created_at")
    if ca is None:
        return 0.0
    try:
        if isinstance(ca, (int, float)):
            return float(ca if ca < 1e12 else ca / 1000)
        s = str(ca).strip()
        return (datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
                if "T" in s else datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp())
    except Exception:
        return 0.0


def _is_fresh(m) -> bool:
    age = time.time() - _nf_ts(m)
    return 0 <= age <= NEWS_MAX_AGE_SEC

HEAVY_WORDS = ("政策", "上调", "涨价", "发布", "获批", "突破", "首个", "首家", "中标",
               "订单", "收购", "重组", "合作", "试点", "落地", "入选", "入局", "量产",
               "扩产", "提价", "涨价", "禁止", "调查", "停牌", "上市", "获批上市",
               "白名单", "启动", "签署", "投资", "增持", "回购", "中标", "订单增长")


def now() -> datetime:
    return datetime.now(BJ)


def in_trading(dt: datetime | None = None) -> bool:
    dt = dt or now()
    mins = dt.hour * 60 + dt.minute
    # ⛔ 2026-09-07 用户「盘中异动预警停 · 收盘后快讯依然可以推送」：快讯速递时段扩到 15:30
    #    （收盘后 30min 仍推收盘播报/盘后消息面，时效门 10min 自然衰减）；
    #    盘中异动类（短线精灵涨停卡/板块卡）维持交易时段 15:00 停，收盘后不再推盘中行情。
    in_window = (555 <= mins <= 690) or (780 <= mins <= 930)
    if not in_window:
        return False
    # 节假日门禁：法定节假日与周末（含调休补班日）一律不推送（is_a_share_trading_day 为唯一权威）
    try:
        from common.trading_calendar import is_a_share_trading_day
        return is_a_share_trading_day(dt)
    except Exception:
        return dt.weekday() < 5  # trading-day-fallback: 唯一真源不可用时的周末兜底


def http_get(url: str, t: int = 10) -> dict | None:
    try:
        req = urllib.request.Request(url, headers=NEWS_HEADERS)
        with urllib.request.urlopen(req, timeout=t) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as e:
        print(f"[newsflash_express] 抓取失败: {str(e)[:120]}")
        return None


def fetch_messages() -> list[dict]:
    raw = http_get(NEWS_URL.format(ts=str(int(time.time() * 1000))))
    if not isinstance(raw, dict):
        return []
    data = raw.get("data") or {}
    msgs = data.get("messages") or []
    # ⛔ 2026-09-07：baoer 返回顺序≠时间顺序（旧快讯排前），按 created_at 降序(新在前)返回全部；
    #    时效过滤交由 main 的 _is_fresh 门（宁缺毋滥，过时不推）
    return sorted(msgs, key=_nf_ts, reverse=True)


def pick(msg: dict) -> dict | None:
    """精选：点名个股 → 必播；带板块+重磅词 → 播；否则跳过。"""
    title = (msg.get("title") or "").strip()
    if not title or len(title) < 8:
        return None
    stks = msg.get("stocks") or []
    bkjs = msg.get("bkj_infos") or []
    stocks_txt = "、".join(s.get("name", "") for s in stks[:4]) if stks else ""
    bkj_txt = "、".join(b.get("name", "") for b in bkjs[:4]) if bkjs else ""
    if stocks_txt:
        return {"id": str(msg.get("id") or msg.get("msg_id") or msg.get("news_id") or title[:40]),
                "title": title, "stocks": stocks_txt, "bkj": bkj_txt, "tier": 1}
    if bkj_txt and any(w in title for w in HEAVY_WORDS):
        return {"id": str(msg.get("id") or msg.get("msg_id") or msg.get("news_id") or title[:40]),
                "title": title, "stocks": stocks_txt, "bkj": bkj_txt, "tier": 2}
    return None


def load_pushed() -> dict:
    if PUSHED.exists():
        try:
            return json.loads(PUSHED.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_pushed(p: dict) -> None:
    PUSHED.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")


def _direction(title: str) -> str:
    """快讯方向判定（2026-09-04 用户「快讯匹配涨跌：涨→个股红·跌→个股绿」）：
    标题含跌类词（跌停/下跌/跌超/重挫/利空/减持/跳水…）→ down；
    否则含涨类词（涨停/走高/大涨/利好/中标/提价…）→ up；
    均无 → neutral（中性·grey 标注不误导）。"""
    down_w = ("跌停", "下跌", "走低", "跌超", "下滑", "重挫", "大跌", "杀跌", "走弱",
              "利空", "减持", "跳水", "翻绿", "亏损", "回落", "下挫", "暴跌", "新低", "承压", "调整")
    up_w = ("涨停", "走高", "上涨", "拉升", "大涨", "利好", "反弹", "新高", "翻红", "走强",
            "冲高", "增长", "中标", "提价", "涨价", "上调", "放量", "封板", "回封")
    if any(w in title for w in down_w):
        return "down"
    if any(w in title for w in up_w):
        return "up"
    return "neutral"


def build_card(items: list[dict]) -> dict:
    hhmm = now().strftime("%H:%M")
    lines = [f"**⚡ 快讯速递 · {hhmm}**（精选 {len(items)} 条）"]
    for it in items:
        t = it["title"]
        if len(t) > 72:
            t = t[:72] + "…"
        lines.append(f"▸ {t}")
        parts = []
        if it["stocks"]:
            d = _direction(it["title"])
            color = {"up": "red", "down": "green", "neutral": "grey"}[d]
            mark = "点名" if d == "neutral" else ("涨·点名" if d == "up" else "跌·点名")
            parts.append(f"<text_tag color='{color}'>{it['stocks']}</text_tag>（{mark}）")
        if it["bkj"]:
            parts.append(it["bkj"])
        if parts:
            lines.append("　📎 " + " · ".join(parts))
    card = {
        "title": f"⚡短线精灵·快讯速递 {hhmm}",
        "color": "blue",
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
                     {"tag": "note", "elements": [{"tag": "plain_text", "content": "⚡ 消息面速递（机器精选·选股宝快讯·个股红涨绿跌）"}]}],
    }
    return card


def main() -> int:
    dry = "--dry-run" in sys.argv
    if not in_trading() and not dry:
        print("[newsflash_express] 非交易时段·跳过")
        return 0
    msgs = fetch_messages()
    pushed = load_pushed()
    day = now().strftime("%Y-%m-%d")
    seen = set(pushed.get(day, []))
    items = []
    for m in msgs:
        if not _is_fresh(m):       # ⛔ 时效门：过时快讯(>10min)即使首次见也不推（用户「快讯也要实时」）
            continue
        it = pick(m)
        if not it:
            continue
        if it["id"] in seen:
            continue
        items.append(it)
        seen.add(it["id"])
        if len(items) >= MAX_PER_ROUND:
            break
    if not items:
        print(f"[newsflash_express] 无新精选快讯（消息{len(msgs)}条→精选0）")
        return 0
    # ⛔ 2026-09-07 修复：去重记录移到「推送成功(rc==0)」后才落盘——
    #   原代码在收集后无条件 save_pushed：①--dry-run 也污染 seen（把没推的快讯
    #   记成已推）；②推送失败/锁冲突时 id 已记录 → 该快讯永不再推（漏推）。
    #   实证 09-07 下午：当下快讯(传媒/培育钻石/半导体涨停) id 被预记 → express 全天精选0。
    print(f"[newsflash_express] 精选 {len(items)} 条: " + " / ".join(x["title"][:36] for x in items))
    if dry:
        print(json.dumps(build_card(items), ensure_ascii=False)[:200])
        return 0
    card = build_card(items)
    cf = Path(os.environ.get("TMPDIR", "/tmp")) / "limao_newsflash_express.json"
    # 2026-09-04 并发双推保险：锁目录原子占用——多守护实例并行时只放行一个（>90s stale 自动清）。
    # （实证 13:00:33 同秒双推：两实例同时读 send 防抖缓存竞态穿透 → 此处先串行化。）
    lk = ROOT / "cache" / ".express_push.lock"
    for _try in range(2):
        try:
            lk.mkdir()
            break
        except FileExistsError:
            stale = time.time() - lk.stat().st_mtime
            if stale > 90:
                lk.rmdir()
                continue
            print(f"[newsflash_express] 另一实例推送中（锁龄 {stale:.0f}s）·跳过防双推")
            return 0
    try:
        cf.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
        rc = os.system(f'"{sys.executable}" "{ROOT / "scripts" / "short_spirit.py"}" --send "{cf}"')
    finally:
        if lk.exists():
            lk.rmdir()
    if rc == 0:
        pushed[day] = list(seen)
        save_pushed(pushed)
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
