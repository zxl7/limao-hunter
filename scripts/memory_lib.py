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
import os
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
            # 联动证据基准（2026-10-14 新增·供 `_linkage_evidence` 比对上一轮）
            "index_avg": None,
            "breadth_up": None,
            "breadth_down": None,
            "broken": None,
        },
        "pushed_topics": [],
        "event_log": [],
        "last_push_by_task": {},
        # 当日已报过「新主线崛起」的题材（2026-09-21 新增）。
        # ⛔ 不加这个去重会每轮重复报（事件文案含涨停家数，逐轮都在变 → `(topic,event)` 去重拦不住）。
        "rose_topics": [],
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
    except Exception as e:
        # ⛔ **不得静默覆盖**（2026-10-14 修）：原实现直接 `empty_mem + save`，
        #    一旦读到坏文件（并发半写 / 手工损坏）就把已有内容**整份清掉**。
        #    改为：把坏文件**改名留证**（`.corrupt-<时间>`）+ 告警，返回空模板但**不写盘**；
        #    下次调用时 `p.exists()` 为假 → 正常新建空模板，不会丢证据。
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            p.replace(p.with_suffix(p.suffix + f".corrupt-{stamp}"))
            print(f"[memory_lib] ⚠️ {p.name} 解析失败({type(e).__name__}) → 已备份为 .corrupt-{stamp}，未覆盖")
        except Exception:
            pass
        return empty_mem(d)

def _is_trading_day(d: str) -> bool:
    """A股交易日 —— **唯一真源 = common.trading_calendar**（2026-10-09 唯一化）。

    长假幽灵源：`load_memory` 在文件缺失时会**创建空模板**，于是休市日也会落一份
    `market_memory_<休市日>.json`（实测 2026-10-05/06/07 三份，`total_zt` 还是上一
    交易日的 52 —— 接口回吐），污染「情绪联动对比」基准。写入端必须按交易日拦截。
    """
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from common.trading_calendar import is_a_share_trading_day
        return is_a_share_trading_day(d)
    except Exception:  # pragma: no cover - 极端环境兜底
        try:
            return datetime.strptime(d, DATE_FMT).weekday() < 5  # trading-day-fallback
        except Exception:
            return True


def save_memory(m: dict, p: Path = None, *, allow_non_trading: bool = False) -> None:
    """写当日记忆。**非交易日拒写**（防长假幽灵产物）；确有需要可显式 allow_non_trading=True。"""
    d = (m or {}).get("date") or ""
    if d and not allow_non_trading and not _is_trading_day(d):
        print(f"[memory_lib] ⛔ {d} 非A股交易日 → 拒绝写入 {MEM_PREFIX}{d}.json（防幽灵日产物）")
        return
    p = p or mem_path(m.get("date"))
    m["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    p.parent.mkdir(parents=True, exist_ok=True)
    # ⛔ **原子写**（2026-10-14 修）：原 `p.write_text(...)` 直接覆写，并发下会被读到
    #    **半写文件** → 另一方 `json.loads` 失败 → `load_memory` 静默重置为空模板
    #    ⇒ 整份 event_log 丢失。改为「同目录临时文件 + os.replace」（POSIX 原子）。
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)

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

# ══════════════════ 判据纠正（2026-10-14 用户定 · 概念层） ══════════════════
#
# 用户两条论断（原话）：
#   ①「**情绪变化就是因为没有整体联动**」
#   ②「**龙头切换就是错误的概念，那只是短期的爆发个股而已！**」
#
# ① 的含义：情绪是**指数/板块/个股/龙头 四层联动的综合结果**。原实现只比对
#    单个 `sentiment` 字符串（还是混口径的），**任何单层抖动都被当成"情绪变化"**
#    → 10-14 一天记 10 次，其中 4 次是「裸标签 ↔ x/10·标签」的口径切换假事件。
#    ⇒ 修法：情绪变化必须有 **≥LINKAGE_MIN_LAYERS 层同向证据**；无联动不记。
#
# ② 的含义：真正的龙头是**身份**（应稳定）；某票今天涨得多只是**状态**（短期爆发）。
#    原实现把「top5 里 leader 名字变了」记成「龙头切换」＝ 把短期爆发误报成身份更替，
#    还会**来回切**（10-14 染料/传媒各来回一次）。
#    ⇒ 修法：**不再产出「龙头切换」事件**；只在该票**真的爆发**（达涨停线附近）时，
#    记一条「**个股爆发**」——只陈述事实，不含"换人"含义。
#
# ⛔ 两个开关默认都按新判据；回退旧行为只需改这里的常量（勿删检查）。
ENABLE_SENTIMENT_LINKAGE_GATE = True   # 情绪变化需多层联动证据（关闭 = 回到"字符串不等就记"）
ENABLE_LEADER_SWITCH_EVENT = False     # ⛔ 旧概念：默认关。「龙头切换」不再产出。
LINKAGE_MIN_LAYERS = 2                 # 情绪变化至少需要的**同向层数**（四层里取 2）
SENTIMENT_COOLDOWN_MIN = 30            # 同方向情绪事件冷却（防每轮刷屏）；方向反转立即记

# 个股爆发门槛（按各自涨停线·与 short_spirit.signal_type 同量级）
_BURST_MIN_PCT = {"main": 9.0, "cm20": 19.0}


def _parse_hm(s) -> int | None:
    """`"14:48"` → 分钟数（用于冷却比较）。解析不了返回 None（**不猜**）。"""
    try:
        h, m = str(s).split(":")[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _cooldown_ok(mem: dict, direction: str, now: str) -> bool:
    """同方向情绪事件是否已过冷却（`SENTIMENT_COOLDOWN_MIN`）。

    ⛔ 判据 = **方向指纹 + 时间冷却**，缺一不可（同「reflow 指纹去重」教训：
    只靠时间会漏掉方向反转，只靠文案会因数字每次都变而永不重复）。
    """
    last_dir, last_t = "", ""
    for e in mem.get("event_log") or []:
        if e.get("topic") == "情绪" and "市场情绪" in e.get("event", ""):
            last_dir = "转好" if "转好" in e["event"] else ("转差" if "转差" in e["event"] else "")
            last_t = e.get("time", "")
    if last_dir != direction:
        return True                      # 方向变了（含首次）→ 立即记
    t0, t1 = _parse_hm(last_t), _parse_hm(now)
    if t0 is None or t1 is None:
        return True                      # 时刻不可比 → 不拦（宁可多记，不静默丢）
    return (t1 - t0) >= SENTIMENT_COOLDOWN_MIN


def _burst_min_pct(code: str) -> float:
    """按代码前缀给爆发门槛：创业板/科创 20cm → 19.0，其余 → 9.0。"""
    c = str(code or "").split(".")[0]
    return _BURST_MIN_PCT["cm20"] if c[:3] in ("300", "301", "688") else _BURST_MIN_PCT["main"]


def _index_avg(latest: dict):
    """三大指数均涨幅；无数据返回 None（**不填 0**·契约：缺失不填零）。"""
    idx = latest.get("index") or []
    if not idx:
        return None
    try:
        return sum(float(i.get("pct", 0)) for i in idx) / len(idx)
    except Exception:
        return None


def _linkage_evidence(latest: dict, prev: dict) -> tuple[int, list]:
    """四层联动的**同向证据**（2026-10-14 用户「情绪变化就是因为没有整体联动」）。

    返回 `(net, evidence)`：`net > 0` 转好 / `net < 0` 转差 / `0` 中性（无联动）。
    ⛔ 全部取**同刻**数据：`latest` 是本次采集，`prev` 是**上一轮**存下的 market_state
    （跨时点比较口径见宪法§245【时间线模型·对齐铁律】）。
    ⛔ 任一层缺数据 → **该层不产生证据**（不填零、不猜方向）。
    """
    ev, net = [], 0

    # ① 指数层（同刻三指数均值 vs 上一轮）
    avg = _index_avg(latest)
    prev_avg = prev.get("index_avg")
    if isinstance(avg, (int, float)) and isinstance(prev_avg, (int, float)):
        d = avg - prev_avg
        if d >= 0.3:
            net += 1; ev.append(f"指数{prev_avg:+.2f}%→{avg:+.2f}%")
        elif d <= -0.3:
            net -= 1; ev.append(f"指数{prev_avg:+.2f}%→{avg:+.2f}%")

    # ② 广度层（全市场涨跌家数比·越靠前越早反映）
    b = latest.get("breadth") or {}
    up, dn = b.get("up"), b.get("down")
    pu, pd_ = prev.get("breadth_up"), prev.get("breadth_down")
    if all(isinstance(x, int) for x in (up, dn, pu, pd_)) and dn > 0 and pd_ > 0:
        r_now, r_prev = up / dn, pu / pd_
        if r_now - r_prev >= 0.15:
            net += 1; ev.append(f"涨跌比{r_prev:.2f}→{r_now:.2f}")
        elif r_prev - r_now >= 0.15:
            net -= 1; ev.append(f"涨跌比{r_prev:.2f}→{r_now:.2f}")

    # ③ 个股层（炸板数：升=承接变差；与涨停数变化分开取，避免互相抵消）
    bk = (latest.get("broken") or {}).get("count")
    pbk = prev.get("broken")
    if isinstance(bk, int) and isinstance(pbk, int):
        d = bk - pbk
        if d >= 3:
            net -= 1; ev.append(f"炸板{pbk}→{bk}")
        elif d <= -3:
            net += 1; ev.append(f"炸板{pbk}→{bk}")

    # ④ 龙头/个股高度层（最高板变化·身份维度）
    zt = latest.get("total_zt")
    pzt = prev.get("total_zt")
    if isinstance(zt, int) and isinstance(pzt, int) and pzt > 0:
        d = zt - pzt
        if d >= 8:
            net += 1; ev.append(f"涨停{pzt}→{zt}")
        elif d <= -8:
            net -= 1; ev.append(f"涨停{pzt}→{zt}")

    return net, ev


def detect_events(latest, prev=None, mem=None) -> list:
    """对比 latest 与上一轮 top5（取自 mem["prev_top5"]），更新 mem 的 market_state / event_log。
    返回本次新检测到的事件列表。幂等：相同 (topic,event) 不重复入 log。"""
    mem = mem or load_memory()
    events = []
    now = datetime.now().strftime("%H:%M")

    # 1) 情绪变化 —— ⛔ 用户定「情绪变化就是因为没有整体联动」
    #    判据 = **四层联动的同向证据 ≥ LINKAGE_MIN_LAYERS**（指数/广度/炸板/涨停）。
    #    ⛔ **不再依赖单个 `sentiment` 字符串** —— 那正是"只比单层"的错法；且该字段被
    #      `record_push`（feedback 口径）与 `detect_events`（pulse 口径）**双写**，
    #      字符串比较会把**口径切换**误报成情绪变化（10-14 十条里占四条）。
    #    冷却：同方向 SENTIMENT_COOLDOWN_MIN 分钟内不重复（防每轮刷屏）；方向反转立即记。
    cur_sent = latest.get("sentiment", "")
    net, ev = _linkage_evidence(latest, mem["market_state"])
    if not ENABLE_SENTIMENT_LINKAGE_GATE:
        # 回退旧行为（字符串不等即记）—— 仅在显式关闭门禁时
        prev_sent = mem["market_state"].get("sentiment", "")
        if prev_sent and cur_sent and cur_sent != prev_sent:
            events.append({
                "time": now,
                "event": f"市场情绪由「{prev_sent}」转为「{cur_sent}」",
                "severity": "high",
                "topic": "情绪",
            })
            mem["market_state"]["sentiment_change_at"] = now
    elif abs(net) >= LINKAGE_MIN_LAYERS and _cooldown_ok(mem, "转好" if net > 0 else "转差", now):
        events.append({
            "time": now,
            "event": f"市场情绪{'转好' if net > 0 else '转差'}（{'；'.join(ev)}）",
            "severity": "high",
            "topic": "情绪",
        })
        mem["market_state"]["sentiment_change_at"] = now

    # 2) 新板块崛起 / 龙头切换
    prev_top5 = {b.get("name", ""): b for b in (mem.get("prev_top5") or [])}
    baseline = bool(prev_top5)  # 首日/首轮仅建基线，不误报"新主线"
    # ⛔ 2026-09-21 修（用户抓包「医药爆发这么猛，时间轴上居然没有医药题材」）
    #    原判据把 rise 挂在 `elif`（= **上一轮 top5 里没有**）之下，于是：
    #      **一开盘就是主线、全天霸榜的题材反而不发声** —— 它从首轮就在 top5、`prev_top5`
    #      里也有（既不满足「新面孔」），龙头又没换（连「龙头切换」也没有）
    #      → 事件日志对它**零记录**。实证：09-21 医药 top1 涨停 19 只（第 2 名仅 6 只）、
    #      `dominant_sector=医药`、推送台账 10:06 就首推，而 `event_log` 医药 **0 条**
    #      → 时间轴上完全不可见（时间轴的题材层唯一来源就是 event_log）。
    #    现判据 = **当日首次达到强度阈值（涨停 ≥3）即记一次**，与「是否新面孔」无关；
    #    `rose_topics` 保证每个题材每天只报一次（随日文件天然重置）。
    rose = set(mem.get("rose_topics") or [])
    for b in (latest.get("top5") or []):
        name = b.get("name", "")
        L = b.get("leader") or {}
        lname = L.get("name", "")
        if name in prev_top5:
            pL = prev_top5[name].get("leader") or {}
            if lname and pL.get("name") and pL.get("name") != lname:
                # ⛔ 概念纠正（2026-10-14 用户：「龙头切换就是错误的概念，
                #    那只是短期的爆发个股而已！」）
                #    · 真龙头是**身份**（应稳定）；某票今天涨得多只是**状态**。
                #    · 原实现把「top5 里 leader 名字变了」记成「龙头切换」＝ 把短期爆发
                #      误报成身份更替，且会**来回切**（10-14 染料/传媒各来回一次）。
                #    ⇒ 默认**不再产出「龙头切换」**；改为：该票**真的爆发**（达各自
                #      涨停线附近）时记一条「个股爆发」——只陈述事实、不含"换人"含义。
                if ENABLE_LEADER_SWITCH_EVENT:
                    events.append({
                        "time": now,
                        "event": f"{name} 龙头切换：{pL.get('name')} → {lname}",
                        "severity": "high",
                        "topic": name,
                    })
                else:
                    pct = L.get("pct")
                    floor = _burst_min_pct(L.get("code"))
                    if isinstance(pct, (int, float)) and pct >= floor:
                        events.append({
                            "time": now,
                            "event": f"{name} 个股爆发：{lname} +{pct:.1f}%"
                                     f"（该题材原领涨 {pL.get('name') or '—'}）",
                            # ⚠️ mid 而非 high：用户口径「只是短期的爆发个股而已」
                            #    → 不构成"必须推全量卡"的理由（high 才触发 full）
                            "severity": "mid",
                            "topic": name,
                        })
        if baseline and b.get("zt_count", 0) >= 3 and name not in rose:
            events.append({
                "time": now,
                "event": f"新主线崛起：{name} 涨停{b.get('zt_count')}只，龙头 {lname}",
                "severity": "high",
                "topic": name,
            })
            rose.add(name)

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
    # ⛔ `sentiment` **不在此写**（2026-10-14 根治双写）：唯一写入方 = `record_push`
    #    （feedback 口径 `x/10·标签`）。本函数只**读**它以便对比，写入会让
    #    pulse 口径（裸标签）与 feedback 口径交替覆盖 → 下一轮字符串比较产生假事件。
    mem["market_state"]["total_zt"] = cur_zt
    if latest.get("top5"):
        mem["market_state"]["dominant_sector"] = latest["top5"][0].get("name", "")
    ia = _index_avg(latest)
    if ia is not None:
        mem["market_state"]["index_avg"] = round(ia, 3)
        mem["market_state"]["index_trend"] = "强" if ia > 0.5 else ("弱" if ia < -0.5 else "平")
    # 联动证据的「上一轮」基准（2026-10-14 用户「情绪变化就是因为没有整体联动」）：
    # 供下一轮 `_linkage_evidence` 比对，判断情绪变化是否有**四层同向**支持。
    # ⛔ 只在取到值时写（缺失不填零·契约 §1：缺失不得冒充有效数据）
    _b = latest.get("breadth") or {}
    if isinstance(_b.get("up"), int):
        mem["market_state"]["breadth_up"] = _b["up"]
    if isinstance(_b.get("down"), int):
        mem["market_state"]["breadth_down"] = _b["down"]
    _bk = (latest.get("broken") or {}).get("count")
    if isinstance(_bk, int):
        mem["market_state"]["broken"] = _bk

    # 幂等去重：同一 (topic,event) 当天不重复记录
    existing = {(e.get("topic"), e.get("event")) for e in mem["event_log"]}
    events = [e for e in events if (e.get("topic"), e.get("event")) not in existing]
    mem["event_log"].extend(events)
    mem["prev_top5"] = latest.get("top5") or []
    mem["rose_topics"] = sorted(rose)
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
        # ⛔ 本字段的**唯一写入方**（2026-10-14 根治双写）：口径 = feedback `x/10·标签`。
        #    `detect_events` 已不再写它（原先两处交替写 → 口径切换被误报为情绪变化）。
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
