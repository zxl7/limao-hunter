"""digest_daily_verdict —— wealth_code 反吃「短期情绪日报」的板块 verdict。

设计背景：
- 用户纪律「广度≠核心」：板块涨停多（量化铺广度/避险轮动）≠ 真主线，回避追高。
- 日报（盘后复盘 17:00 任务）在「主线 Top3」段对板块下了自然语言 verdict，
  wealth_code 盘后候选卡 17:30 跑，时间上日报已生成 → 应反吃日报判断、以其为准。
- 日报只有 markdown，无结构化 JSON 副产物，故本模块做轻量解析（聚焦「主线 Top3」段）。

匹配策略（稳健，不依赖 sector 字符串精确相等）：
1. 股票名命中：日报 Top3 条目若点名该候选（如「秦安股份2板」），直接用该条 verdict。
2. 板块子串：候选 sector（biyingapi 截断，如「汽车零部」）与日报板块短语（「汽车零部件」）做双向包含匹配。
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

# verdict 关键词 → 归类
_AVOID_KW = ["量化铺广度", "避险轮动", "持续性弱", "回避追高", "铺广度", "轮动"]
_OBSERVE_KW = ["观察级", "未确认", "未达确认", "回流未确认", "待观察", "分歧相位"]
_MAIN_KW = ["唯一穿越主线", "真龙", "双硬催化", "带领性"]


def _classify(text: str) -> List[str]:
    flags: List[str] = []
    for kw in _AVOID_KW:
        if kw in text:
            flags.append("avoid_breadth")
            break
    for kw in _OBSERVE_KW:
        if kw in text:
            flags.append("observe")
            break
    for kw in _MAIN_KW:
        if kw in text:
            flags.append("main")
            break
    # 高评级（⭐⭐⭐⭐⭐）且未被 avoid 标记，视为强主线
    stars = text.count("⭐")
    if stars >= 5 and "avoid_breadth" not in flags and "observe" not in flags:
        if "main" not in flags:
            flags.append("main")
    return flags


def _parse_top3(md: str) -> List[Dict[str, Any]]:
    """解析「## 主线 Top3」段下的编号条目。"""
    items: List[Dict[str, Any]] = []
    # 定位 主线 Top3 段
    m = re.search(r"##\s*主线\s*Top\s*3\s*(.*?)(?=\n##\s|\Z)", md, re.S)
    if not m:
        return items
    block = m.group(1)
    # 逐条：数字. **板块短语** 余下描述
    for line in block.splitlines():
        line = line.strip()
        mm = re.match(r"^\d+\.\s*\*\*(.+?)\*\*[:：]?\s*(.*)$", line)
        if not mm:
            continue
        phrase = mm.group(1).strip()
        desc = mm.group(2).strip()
        full = phrase + " " + desc
        stars = phrase.count("⭐") + desc.count("⭐")
        flags = _classify(full)
        # 点名的股票：匹配「XX股份 / XX药业 / XX科技」等中文名 + 可选板数
        names = re.findall(r"([一-龥]{2,5}(?:股份|药业|医药|科技|通信|电子|设备|发展|集团|新材|材料|生物|电气|智能|环境|传媒|能源))", full)
        items.append({
            "phrase": phrase,
            "stars": stars,
            "flags": flags,
            "named_stocks": names,
            "raw": full,
        })
    return items


def digest_daily_verdict(date: str, base_dir: str) -> Dict[str, Any]:
    """读取日报，返回可供 wealth_code 反吃的 verdict 结构。

    返回：
    {
      "source": "cache/short_term/daily/daily_YYYY-MM-DD.md" | None,
      "available": bool,
      "sector_verdicts": [ {phrase, stars, flags, named_stocks} ],
      "by_stock": { 股票名: {phrase, stars, flags} },  # 点名命中，最可信
      "by_phrase_keyword": [ phrase ],                  # 供 sector 子串匹配
    }
    """
    path = os.path.join(base_dir, "cache", "short_term", "daily", f"daily_{date}.md")
    out: Dict[str, Any] = {
        "source": path if os.path.exists(path) else None,
        "available": os.path.exists(path),
        "sector_verdicts": [],
        "by_stock": {},
        "by_phrase_keyword": [],
    }
    if not out["available"]:
        return out
    try:
        md = open(path, encoding="utf-8").read()
    except Exception:
        return out
    items = _parse_top3(md)
    out["sector_verdicts"] = items
    for it in items:
        # 点名股票 → by_stock
        for nm in it["named_stocks"]:
            out["by_stock"][nm] = {
                "phrase": it["phrase"],
                "stars": it["stars"],
                "flags": it["flags"],
            }
        # 板块短语 → 取核心词（去掉 ⭐ 与括号）供子串匹配
        core = re.sub(r"[⭐（）()/\s、]+", " ", it["phrase"]).strip()
        if core:
            out["by_phrase_keyword"].append(core)
    return out


def match_candidate(name: str, sector: str, verdict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """为单个候选匹配日报 verdict。返回 {phrase, stars, flags, note} 或 None。"""
    if not verdict.get("available"):
        return None
    # 1) 股票名命中（最可信）
    for key, v in verdict.get("by_stock", {}).items():
        if name and name in key:
            return _build_note(v)
    # 2) 板块子串：候选 sector 与日报板块核心词双向包含
    sec = (sector or "").strip()
    if sec:
        for core in verdict.get("by_phrase_keyword", []):
            # 取核心词前 4 字做宽松匹配（biyingapi sector 被截断）
            core_short = core.replace(" ", "")[:4]
            if core_short and (core_short in sec or sec in core.replace(" ", "")):
                # 找该 core 对应的 verdict
                for it in verdict["sector_verdicts"]:
                    ic = re.sub(r"[⭐（）()/\s、]+", " ", it["phrase"]).strip().replace(" ", "")
                    if core_short in ic or ic[:4] in core_short or sec in ic:
                        return _build_note({
                            "phrase": it["phrase"],
                            "stars": it["stars"],
                            "flags": it["flags"],
                        })
    return None


def _build_note(v: Dict[str, Any]) -> Dict[str, Any]:
    flags = v.get("flags", [])
    if "avoid_breadth" in flags:
        note = "所属板块被日报判为量化铺广度/回避追高，仅龙头独立强势，控仓不追板块（接力权重已下调）"
        tag = "⚠️板块弱"
    elif "observe" in flags:
        note = "板块回流未确认，日报判观察级，等确认再加（接力权重暂下调）"
        tag = "📡观察级"
    elif "main" in flags:
        note = "日报判为强主线（穿越主线），与龙头三因子一致（接力权重已上调）"
        tag = "✅主线"
    else:
        note = "日报未给出负面判定"
        tag = ""
    return {
        "phrase": v.get("phrase", ""),
        "stars": v.get("stars", 0),
        "flags": flags,
        "note": note,
        "tag": tag,
    }
