"""人工题材归因（`cache/manual_theme.json`）—— 给系统看不见的那类逻辑留个入口。

## 为什么需要它（2026-11-10）

用户抓包：东方创业(600278) 被系统标成「贸易Ⅱ」，卡面还写「这条线最高板」。
查下去发现它的选股宝 `plates` 只有 `['其他']`、`theme` 是空串 —— **它真的没有题材**，
系统用申万二级行业「贸易Ⅱ」把空位填了，然后在一个只有它自己的"线"里称王。

但用户指出：它其实属于「东方系」这条线（东升西落，名字带"东方"）。
数据支持他：带「东方」的涨停 11-05 的 1 只 → 11-06 的 6 只 → 11-09 的 7 只。

同一类还有「羊年」（阳/羊/洋/扬）：10-26 起 8→7→10→10→**11 只**（11-03 峰值），
阳光照明(600261) 在 11-03 就已经是 4 板 —— 系统 11-09 把它当「2 板低开符合预期」买，
完全不知道它的真实身份。

这类逻辑的共同点：**不由选股宝板块驱动**（题材分散 6/8），所以任何基于 plates 的
自动归类都抓不到它，而基于统计的信号（同字家数）又会被「东方财富」这类正常公司名误伤。

## 定位（2026-11-10 用户定调：「玄学可以归纳，但是不加权重，他只是特殊时期的产物」）

三句话拆成三个硬约束：

| 用户原话 | 落地 |
|---|---|
| **可以归纳** | 按**族**归纳（东方系 / 羊年），给每日家数、最高板、题材分散度 —— 归纳是允许的 |
| **不加权重** | `weight` 恒为 **0**，代码里强制、上传文件写 1 也不认；不进 pool_call / 板块龙 / 身位 / 仓位 |
| **特殊时期的产物** | 每族有**寿命**：按家数自动判 emerging → active → fading → weak → **expired**，过期即归档不再跟踪 |

⛔ 仍然**不上卡**（2026-11-10 上一轮已定：连卡面题材名都不用人工归因，600278 照旧显示系统原值）。
归纳结果只在 `scripts/theme_family_trace.py` 里看得到 —— 是**研究者视角**，不是读者视角。

为什么权重必须是 0 而不是「小权重」：小权重也是权重。0.1 的权重在 8 只票的族里能让它挤掉一个真题材票，
而这条线 11-04 一天从 11 只掉到 2 只 —— 拿它分配仓位，等于把系统押在谐音梗上。

## 文件格式

```json
{
  "themes":  {"600278": {"theme": "东方系·东升西落", "note": "…", "added": "2026-11-10"}},
  "families": {
    "东方系": {"kind": "naming", "name_any": ["东方"], "note": "…", "weight": 0},
    "羊年":   {"kind": "zodiac", "name_any": ["阳","羊","洋","扬"], "note": "…", "weight": 0}
  }
}
```

- `kind`：`naming`(命名族) / `homophone`(谐音) / `zodiac`(生肖) / `other`
- `name_any`：名字含任一字符即命中（英文/数字同样按子串处理）
- `exclude_codes`：可选，排除「名字像但不是这条线」的票（如东方财富）
- `weight`：**读出来一律是 0**，文件写别的值也不认

⛔ 文件缺失 / 格式坏 / 该 code 没标注 → 一律返回 None 或空列表，调用方回落原逻辑（不阻断主链）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "cache" / "manual_theme.json"

_CACHE: dict[str, Any] | None = None
_CACHE_MTIME: float | None = None


def load(path: Path | None = None, *, refresh: bool = False) -> dict[str, dict[str, str]]:
    """读全量标注表。带 mtime 缓存：文件没改就不重复解析（每日多次调用）。"""
    global _CACHE, _CACHE_MTIME
    p = Path(path) if path else PATH
    try:
        mtime = p.stat().st_mtime
    except OSError:
        _CACHE, _CACHE_MTIME = {}, None
        return {}

    if not refresh and _CACHE is not None and _CACHE_MTIME == mtime:
        return _CACHE
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                     # noqa: BLE001 —— 坏文件不阻断主链
        _CACHE, _CACHE_MTIME = {}, mtime
        return {}

    themes = obj.get("themes") if isinstance(obj, dict) else None
    if not isinstance(themes, dict):
        # ⛔ 写成 list / 字符串 / null 都不算标注表 —— 静默当空，不抛
        themes = {}
    out: dict[str, dict[str, str]] = {}
    for code, item in (themes or {}).items():
        if not isinstance(item, dict):
            continue
        theme = str(item.get("theme") or "").strip()
        if not code or not theme:
            continue                                  # 空 theme 视为无效标注
        out[str(code).strip()] = {
            "theme": theme,
            "note": str(item.get("note") or "").strip(),
            "added": str(item.get("added") or "").strip(),
        }
    _CACHE, _CACHE_MTIME = out, mtime
    return out


def lookup(code: str, path: Path | None = None) -> dict[str, str] | None:
    """单票查询。没标注 → None（调用方回落原题材，⛔ 不编造）。"""
    c = str(code or "").strip()
    if not c:
        return None
    return load(path).get(c)


def label(code: str, *, fallback: str = "", path: Path | None = None) -> str:
    """展示用：有标注用标注，否则用 fallback（通常是系统原题材）。"""
    hit = lookup(code, path)
    return hit["theme"] if hit else str(fallback or "")


# ─────────────────────────────────────────────────────────────────────────────
# 族归纳（families）
#
# ⛔ 全部族的权重恒为 0 —— 不是「默认 0 可配置」，是**读出来就是 0**。
#    文件里写 `"weight": 1` 也无效，这样「不加权重」不依赖任何人记得住规矩。
# ─────────────────────────────────────────────────────────────────────────────

FAMILY_KINDS = ("naming", "homophone", "zodiac", "other")

_FAM_CACHE: dict[str, dict[str, Any]] | None = None
_FAM_MTIME: float | None = None


def load_families(path: Path | None = None, *, refresh: bool = False) -> dict[str, dict[str, Any]]:
    """读族定义（`families` 段）。每族 `weight` 强制为 0。

    坏数据一律跳过而非抛错：族的用途是「观察 + 留档」，
    一个手写坏的族不该让整张表读不出来。
    """
    global _FAM_CACHE, _FAM_MTIME
    p = Path(path) if path else PATH
    try:
        mtime = p.stat().st_mtime
    except OSError:
        _FAM_CACHE, _FAM_MTIME = {}, None
        return {}
    if not refresh and _FAM_CACHE is not None and _FAM_MTIME == mtime:
        return _FAM_CACHE
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                     # noqa: BLE001 —— 坏文件不阻断主链
        _FAM_CACHE, _FAM_MTIME = {}, mtime
        return {}

    fams = obj.get("families") if isinstance(obj, dict) else None
    if not isinstance(fams, dict):
        fams = {}
    out: dict[str, dict[str, Any]] = {}
    for fname, item in fams.items():
        name = str(fname or "").strip()
        if not name or not isinstance(item, dict):
            continue
        keys = item.get("name_any")
        if not isinstance(keys, list):
            keys = []
        keys = [str(k) for k in keys if str(k or "").strip()]
        if not keys:
            continue                                  # 没有匹配规则 = 永远不会命中，视为无效族
        kind = str(item.get("kind") or "other").strip().lower()
        if kind not in FAMILY_KINDS:
            kind = "other"
        excl = item.get("exclude_codes")
        out[name] = {
            "name": name,
            "kind": kind,
            "name_any": keys,
            "exclude_codes": [str(c).strip() for c in (excl or []) if str(c).strip()]
            if isinstance(excl, list) else [],
            "note": str(item.get("note") or "").strip(),
            "added": str(item.get("added") or "").strip(),
            # ⛔ 恒 0：不读文件里的值，也就不存在「谁把权重调成 0.3」这种事故
            "weight": 0,
        }
    _FAM_CACHE, _FAM_MTIME = out, mtime
    return out


def match_families(name: str, code: str = "", *,
                   path: Path | None = None) -> list[str]:
    """这只票（按名字）命中哪些族。返回族名列表，没命中返回 `[]`。

    ⛔ 只做名字子串匹配，不猜语义：`东方财富` 这种正常公司名会命中「东方」，
    要靠族的 `exclude_codes` 排除 —— 自动语义识别不可靠，宁可人工列黑名单。
    """
    nm = str(name or "").strip()
    cd = str(code or "").strip()
    if not nm:
        return []
    hit: list[str] = []
    for fname, fam in load_families(path).items():
        if cd and cd in fam["exclude_codes"]:
            continue
        if any(k in nm for k in fam["name_any"]):
            hit.append(fname)
    return hit

