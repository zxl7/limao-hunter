#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘面动态记忆库 —— 所有 AI 推送任务（一小时行情 / 盘中猎手 / 盘前盘后）共享读写的单一状态源。

设计目标：
  14 个推送任务原本各自失忆，每次触发都不知道 30 分钟前刚推过中红医疗，导致重复卡疲劳。
  本库用 cache/market_memory_YYYYMMDD.json 充当"盘面前额叶"：
    1) 每次任务先 load（无则建空模板）
    2) 抓取后 detect_events 比对盘面变化，写入 event_log
    3) decide_push 给出 full（全量）/ lite（轻量延续卡）/ skip（静默）
    4) 推送后 record_push 更新 pushed_topics / last_push_by_task / event_log

双入口：
  - 脚本侧：from memory_lib import load_memory, ... 直接调用
  - AI 任务侧（被平台唤醒的 agent）：通过 Bash 调本文件的 CLI
        python3 memory_lib.py load
        python3 memory_lib.py record --task "盘中猎手-0935" --topics '[{"name":"中红医疗","type":"stock","context":"消费防御"}]'
"""
import json
import sys
from datetime import datetime, date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / "cache"
DATE_FMT = "%Y-%m-%d"
MEM_PREFIX = "market_memory_"

# ───────────────────────────── 路径与基础读写 ─────────────────────────────

def today_str() -> str:
    return datetime.now().strftime(DATE_FMT)

def mem_path(d: str = None) -> Path:
    d = d or today_str()
    return CACHE / f"{MEM_PREFIX}{d}.json"

def empty_mem(d: str = None) -> dict:
    d = d or today_str()
    return {
        "date": d,
        "updated_at": "",
        "market_state": {
            "sentiment": "",
            "total_zt": 0,
            "sentiment_change_at": "",
            "dominant_sector": "",
            "index_trend": "",
        },
        "pushed_topics": [],
        "event_log": [],
        "last_push_by_task": {},
    }

def load_memory(d: str = None) -> dict:
    """读取当日记忆；文件不存在则创建空模板并返回。"""
    p = mem_path(d)
    if not p.exists():
        m = empty_mem(d)
        save_memory(m, p)
        return m
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        m = empty_mem(d)
        save_memory(m, p)
        return m

def save_memory(m: dict, p: Path = None) -> None:
    p = p or mem_path(m.get("date"))
    m["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

def read_latest() -> dict:
    """读取 P1 守护进程刷新的盘口快照 cache/pulse_snapshots/latest.json。"""
    p = CACHE / "pulse_snapshots" / "latest.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

# ───────────────────────────── 过期记忆清理 ─────────────────────────────

# 需要轮转清理的缓存文件：前缀 → 日期格式（两种命名约定）
_PRUNE_PATTERNS = [
    (MEM_PREFIX, "%Y-%m-%d"),            # market_memory_2026-08-05.json
    ("pushed_signals_", "%Y%m%d"),       # pushed_signals_20260805.json
]

def prune(retain: int = 5, dry_run: bool = False) -> dict:
    """清除超过 retain 个交易日的动态记忆/信号去重状态文件。

    仅按文件名里的日期判定，不读内容。文件只在交易日生成（盘中链路），
    按日期降序取前 retain 个即最近 retain 个交易日，天然跳过周末/节假日
    （2026-08-12 由「日历日」口径改为「交易日」口径，与 capture_daily 对齐）。
    dry_run=True 只返回待删除清单、不真正删，用于安全预览。
    """
    removed, kept = [], []
    for prefix, fmt in _PRUNE_PATTERNS:
        dated = []
        for p in sorted(CACHE.glob(f"{prefix}*.json")):
            stem = p.name[len(prefix):-5]   # 去前缀 + 去 .json
            try:
                d = datetime.strptime(stem, fmt).date()
            except Exception:
                continue                    # 格式不符跳过，绝不误删
            dated.append((d, p))
        dated.sort(key=lambda x: x[0], reverse=True)   # 最近交易日在前
        for d, p in dated[retain:]:
            if dry_run:
                removed.append(str(p.name))
            else:
                try:
                    p.unlink()
                    removed.append(str(p.name))
                except Exception:
                    pass
        kept.extend(str(p.name) for _, p in dated[:retain])
    return {"retain": retain, "dry_run": dry_run,
            "removed": removed, "removed_count": len(removed),
            "kept_count": len(kept)}

# ───────────────────────────── 事件检测 ─────────────────────────────

def detect_events(latest, prev=None, mem=None) -> list:
    """对比 latest 与上一轮 top5（取自 mem["prev_top5"]），更新 mem 的 market_state / event_log。
    返回本次新检测到的事件列表。幂等：相同 (topic,event) 不重复入 log。"""
    mem = mem or load_memory()
    events = []
    now = datetime.now().strftime("%H:%M")

    # 1) 情绪突变
    cur_sent = latest.get("sentiment", "")
    prev_sent = mem["market_state"].get("sentiment", "")
    if prev_sent and cur_sent and cur_sent != prev_sent:
        events.append({
            "time": now,
            "event": f"市场情绪由「{prev_sent}」转为「{cur_sent}」",
            "severity": "high",
            "topic": "情绪",
        })
        mem["market_state"]["sentiment_change_at"] = now

    # 2) 新板块崛起 / 龙头切换
    prev_top5 = {b.get("name", ""): b for b in (mem.get("prev_top5") or [])}
    baseline = bool(prev_top5)  # 首日/首轮仅建基线，不误报"新主线"
    for b in (latest.get("top5") or []):
        name = b.get("name", "")
        L = b.get("leader") or {}
        lname = L.get("name", "")
        if name in prev_top5:
            pL = prev_top5[name].get("leader") or {}
            if lname and pL.get("name") and pL.get("name") != lname:
                events.append({
                    "time": now,
                    "event": f"{name} 龙头切换：{pL.get('name')} → {lname}",
                    "severity": "high",
                    "topic": name,
                })
        elif baseline and b.get("zt_count", 0) >= 3:
            events.append({
                "time": now,
                "event": f"新主线崛起：{name} 涨停{b.get('zt_count')}只，龙头 {lname}",
                "severity": "high",
                "topic": name,
            })

    # 3) 涨停数骤变（广度剧烈变化）
    cur_zt = latest.get("total_zt", 0)
    prev_zt = mem["market_state"].get("total_zt", 0)
    if prev_zt and abs(cur_zt - prev_zt) >= 15:
        events.append({
            "time": now,
            "event": f"涨停广度骤变 {prev_zt} → {cur_zt}",
            "severity": "mid",
            "topic": "广度",
        })

    # 4) 重挫板块出现（东财跌幅榜新增关注板块领跌）
    weak = latest.get("weak_sectors") or {}
    fd = (latest.get("sector_risk") or {}).get("focus_declining") or []
    if fd:
        names = "、".join(s.get("name", "") for s in fd[:6])
        events.append({
            "time": now,
            "event": f"关注板块集体重挫：{names}",
            "severity": "high",
            "topic": "风险",
        })

    # 更新 market_state
    mem["market_state"]["sentiment"] = cur_sent
    mem["market_state"]["total_zt"] = cur_zt
    if latest.get("top5"):
        mem["market_state"]["dominant_sector"] = latest["top5"][0].get("name", "")
    if latest.get("index"):
        avg = sum(i.get("pct", 0) for i in latest["index"]) / len(latest["index"])
        mem["market_state"]["index_trend"] = "强" if avg > 0.5 else ("弱" if avg < -0.5 else "平")

    # 幂等去重：同一 (topic,event) 当天不重复记录
    existing = {(e.get("topic"), e.get("event")) for e in mem["event_log"]}
    events = [e for e in events if (e.get("topic"), e.get("event")) not in existing]
    mem["event_log"].extend(events)
    mem["prev_top5"] = latest.get("top5") or []
    return events

# ───────────────────────────── 推送决策 ─────────────────────────────

def _last_push_time(mem: dict) -> str:
    times = [v for v in mem["last_push_by_task"].values() if v]
    return max(times) if times else ""

def decide_push(mem: dict) -> str:
    """返回 full（全量推）/ lite（轻量延续卡）/ skip（静默）。
    模式 A（用户选定）：无变化仍保留存在感——仅当『距上次推送<30分钟且无任何新事件』才 skip。"""
    last = _last_push_time(mem)
    if not last:
        return "full"  # 当日首次
    # 上次推送之后是否有新 high 事件
    new_high = [e for e in mem["event_log"]
                if e.get("severity") == "high" and e["time"] > last]
    if new_high:
        return "full"
    new_mid = [e for e in mem["event_log"]
               if e.get("severity") == "mid" and e["time"] > last]
    if new_mid:
        return "lite"
    # 距上次推送 < 30 分钟且完全无新事件 → 静默
    try:
        fmt = "%H:%M"
        lp = datetime.strptime(last, fmt)
        now = datetime.now()
        delta = (now.hour * 60 + now.minute) - (lp.hour * 60 + lp.minute)
        if 0 <= delta < 30:
            return "skip"
    except Exception:
        pass
    return "lite"

# ───────────────────────────── 推送后写回 ─────────────────────────────

def record_push(mem: dict, task_name: str, topics: list, decision: str = "recorded",
                sentiment: str = "") -> None:
    """更新 last_push_by_task 与 pushed_topics。
    topics: [{"name":..., "type":"sector|stock", "context":...}, ...]
    sentiment: 本卡情绪快照（如 "1/10·退潮"），写入 market_state 供下一卡联动对比
    （2026-08-16 用户原则·情绪联动与动态演变）。"""
    now = datetime.now().strftime("%H:%M")
    mem["last_push_by_task"][task_name] = now
    if sentiment:
        mem["market_state"]["sentiment"] = sentiment
        mem["market_state"]["sentiment_change_at"] = now
    existing = {t["name"]: t for t in mem["pushed_topics"]}
    for t in (topics or []):
        nm = t.get("name", "")
        if not nm:
            continue
        if nm in existing:
            existing[nm]["last_pushed"] = now
            existing[nm]["push_count"] = existing[nm].get("push_count", 0) + 1
            if t.get("context"):
                existing[nm]["context"] = t["context"]
        else:
            mem["pushed_topics"].append({
                "name": nm,
                "type": t.get("type", "sector"),
                "first_pushed": now,
                "last_pushed": now,
                "push_count": 1,
                "context": t.get("context", ""),
                "last_catalyst": "",
            })
    save_memory(mem)

# ───────────────────────────── CLI 入口（供 AI 任务 Bash 调用） ─────────────────────────────

def _cli():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "load"
    if cmd == "load":
        m = load_memory()
        print(json.dumps(m, ensure_ascii=False, indent=2))
    elif cmd == "record":
        task, topics, sentiment = None, [], ""
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--task":
                task = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--topics":
                try:
                    topics = json.loads(sys.argv[i + 1])
                except Exception:
                    topics = []
                i += 2
            elif sys.argv[i] == "--sentiment":
                sentiment = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        if not task:
            print("ERROR: --task required")
            sys.exit(1)
        m = load_memory()
        record_push(m, task, topics, sentiment=sentiment)
        print(f"OK recorded task={task} topics={len(topics)} sentiment={sentiment or '—'}")
    elif cmd == "peek":
        # 快速看决策：模拟检测后返回 full/lite/skip（供 AI 预判，可选）
        m = load_memory()
        print(decide_push(m))
    elif cmd == "detect":
        # 守护进程每轮抓取后调用：读快照→检测事件→写回 event_log/market_state→打印 {events,decision}
        m = load_memory()
        latest = read_latest()
        events = detect_events(latest, None, m)
        save_memory(m)
        out = {
            "dt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "events": events,
            "decision": decide_push(m),
            "event_log_len": len(m["event_log"]),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif cmd == "prune":
        # 清理过期记忆/去重状态：python3 memory_lib.py prune [retain] [--dry-run]
        # 默认保留最近 5 天；--dry-run 只预览不删。由 pulse_fetcher.sh 每日调用一次。
        retain = 5
        dry_run = False
        i = 2
        while i < len(sys.argv):
            a = sys.argv[i]
            if a == "--dry-run":
                dry_run = True
            else:
                try:
                    retain = int(a)
                except Exception:
                    pass
            i += 1
        res = prune(retain, dry_run)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"unknown cmd: {cmd}")
        sys.exit(1)

if __name__ == "__main__":
    _cli()
