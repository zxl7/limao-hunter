#!/usr/bin/env python3
"""人工题材归因的增查工具（`cache/manual_theme.json`）。

## 什么时候用

系统给不出题材时会用**行业名**填空（东方创业 → 「贸易Ⅱ」），然后把那条只有自己的"线"
当成题材线。而有些票的真实逻辑是选股宝根本不给的：命名族（东方系）、谐音/生肖（羊年）。
这类逻辑只能人来认，认出来就登记在这里。

## 用法

    # 个股级（themes）
    python3 scripts/theme_manual.py --list
    python3 scripts/theme_manual.py --add 600278 "东方系·东升西落" --note "带东方涨停 11-09 7只"
    python3 scripts/theme_manual.py --remove 600278

    # 族级（families）—— 归纳规则本身
    python3 scripts/theme_manual.py --families
    python3 scripts/theme_manual.py --add-family 东方系 --kind naming --keys 东方 --note "东升西落"
    python3 scripts/theme_manual.py --add-family 羊年 --kind zodiac --keys 阳,羊,洋,扬 --note "2027 羊年"
    python3 scripts/theme_manual.py --remove-family 羊年

    python3 scripts/theme_manual.py --check          # 校验格式（坏数据不阻断主链，但要知道）

## 铁律（2026-11-10 用户：「玄学可以归纳，但是不加权重，他只是特殊时期的产物」）

- ✅ **可以归纳**：族定义随便加，这是研究工具。
- ⛔ **不加权重**：写入时 `weight` 恒为 0，`--check` 发现非 0 直接红；
  读取层 `common.manual_theme` 也会强制归零（双保险）。
- ⛔ **不进判据、不上卡**：卡面题材名保持系统原值；准入判据一个都不读。
- ⚠️ **会过期**：族活在 `scripts/theme_family_trace.py` 的状态机里（emerging→…→expired），
  死后归档留痕，⛔ 不要拿它当长期规则复用。
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "cache" / "manual_theme.json"
sys.path.insert(0, str(ROOT))

NOTE_HEAD = ("人工题材归因 + 族归纳（2026-11-10 起）。系统看不见的那类逻辑在此登记："
             "选股宝 plates 给不出、但确实在驱动股价的线（命名族/谐音/生肖等）。"
             "themes: 个股级(code→{theme,note,added})；families: 族级(名字规则→每日家数/寿命)。"
             "⛔ 可以归纳，但不加权重（weight 恒 0）、不进判据、不上卡。"
             "⛔ 族会过期，用 scripts/theme_family_trace.py 看寿命。")


def _dw(s: str) -> int:
    """终端显示宽度（中文/全角算 2 列）。⛔ len() 排中文表必然错位。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in str(s))


def _pad(s, width: int, *, right: bool = False) -> str:
    fill = " " * max(0, width - _dw(s))
    return fill + str(s) if right else str(s) + fill


def _read() -> dict:
    if not PATH.exists():
        return {"_note": NOTE_HEAD, "themes": {}}
    try:
        obj = json.loads(PATH.read_text(encoding="utf-8"))
    except Exception as e:                                # noqa: BLE001
        print(f"⛔ 标注文件解析失败（{e}），先修文件：{PATH}")
        raise SystemExit(2)
    if not isinstance(obj, dict):
        obj = {}
    obj.setdefault("_note", NOTE_HEAD)
    if not isinstance(obj.get("themes"), dict):
        obj["themes"] = {}
    if not isinstance(obj.get("families"), dict):
        obj["families"] = {}
    return obj


def _write(obj: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--add", nargs=2, metavar=("CODE", "THEME"))
    ap.add_argument("--note", default="")
    ap.add_argument("--remove")
    ap.add_argument("--check", action="store_true")
    # 族级操作（2026-11-10 用户「玄学可以归纳，但是不加权重」）
    ap.add_argument("--families", action="store_true", help="列出族定义")
    ap.add_argument("--add-family", metavar="NAME", help="新增/更新族")
    ap.add_argument("--remove-family", metavar="NAME")
    ap.add_argument("--kind", default="other",
                    choices=["naming", "homophone", "zodiac", "other"], help="族的类型")
    ap.add_argument("--keys", default="", help="匹配词，逗号分隔（名字含任一即命中）")
    ap.add_argument("--exclude", default="", help="排除的代码，逗号分隔（防名字误伤）")
    a = ap.parse_args()

    obj = _read()
    themes = obj["themes"]
    fams = obj["families"]

    if a.check:
        bad = []
        for c, item in themes.items():
            if not isinstance(item, dict) or not str(item.get("theme") or "").strip():
                bad.append(c)
        print(f"个股标注 {len(themes)} 条；无效 {len(bad)} 条" + (f"：{bad}" if bad else " ✓"))

        fam_bad = []
        for fname, item in fams.items():
            if not isinstance(item, dict):
                fam_bad.append(f"{fname}(不是对象)")
                continue
            if not item.get("name_any"):
                fam_bad.append(f"{fname}(缺 name_any)")
            if item.get("kind") not in ("naming", "homophone", "zodiac", "other"):
                fam_bad.append(f"{fname}(kind 非法)")
            if item.get("weight") not in (0, None):
                # ⛔ 允许在 JSON 里写非 0 权重 = 允许哪天有人偷偷调它。
                fam_bad.append(f"{fname}(weight={item.get('weight')}≠0，必须为 0)")
        print(f"族定义 {len(fams)} 个；无效 {len(fam_bad)} 个" + (f"：{fam_bad}" if fam_bad else " ✓"))
        print("⛔ 族 weight 必须恒 0（可以归纳，不加权重）")
        return 2 if (bad or fam_bad) else 0

    # ── 族级：新增 / 删除 / 列表 ──────────────────────────────────────────
    if a.remove_family:
        name = a.remove_family.strip()
        if name not in fams:
            print(f"⚠️ 本来就没有这个族：{name}")
            return 0
        fams.pop(name)
        _write(obj)
        print(f"✅ 已删除族「{name}」")
        return 0

    if a.add_family:
        name = a.add_family.strip()
        keys = [k.strip() for k in a.keys.replace("，", ",").split(",") if k.strip()]
        if not name:
            print("⛔ 族名不能为空")
            return 2
        if not keys:
            print("⛔ 必须给 --keys（族靠名字匹配，没有匹配词就永远不命中）")
            return 2
        old = fams.get(name) or {}
        fams[name] = {
            "kind": a.kind,
            "name_any": keys,
            "exclude_codes": [c.strip() for c in a.exclude.replace("，", ",").split(",") if c.strip()],
            "note": a.note.strip() or str(old.get("note") or ""),
            "added": str(old.get("added") or datetime.now().strftime("%Y-%m-%d")),
            # ⛔ 恒 0：不给任何配置入口
            "weight": 0,
        }
        _write(obj)
        print(f"✅ 族「{name}」→ kind={a.kind} keys={keys} weight=0"
              + ("（覆盖更新）" if old else ""))
        return 0

    if a.families:
        if not fams:
            print("（还没有任何族）")
            return 0
        print(f"{_pad('族', 12)}{_pad('类型', 11)}{_pad('匹配词', 20)}"
              f"{_pad('weight', 8, right=True)}  说明")
        print("-" * 92)
        for name in fams:
            it = fams[name]
            note = str(it.get("note") or "")
            print(f"{_pad(name, 12)}{_pad(it.get('kind'), 11)}"
                  f"{_pad('/'.join(it.get('name_any') or []), 20)}{_pad(0, 8, right=True)}  "
                  f"{note[:30]}{'…' if len(note) > 30 else ''}")
        print(f"\n共 {len(fams)} 个族 · ⛔ weight 恒 0、不进判据、不上卡"
              f"\n⚠️ 寿命看：python3 scripts/theme_family_trace.py")
        return 0

    if a.remove:
        code = a.remove.strip()
        if code not in themes:
            print(f"⚠️ {code} 本来就没标注")
            return 0
        themes.pop(code)
        _write(obj)
        print(f"✅ 已删除 {code}")
        return 0

    if a.add:
        code, theme = a.add[0].strip(), a.add[1].strip()
        if not code or not theme:
            print("⛔ code 与 theme 都不能为空")
            return 2
        old = themes.get(code) or {}
        themes[code] = {
            "theme": theme,
            "note": a.note.strip() or str(old.get("note") or ""),
            "added": str(old.get("added") or datetime.now().strftime("%Y-%m-%d")),
        }
        _write(obj)
        print(f"✅ {code} → {theme}" + ("（覆盖更新）" if old else ""))
        return 0

    # 默认 --list
    if not themes:
        print("（还没有任何标注）")
        return 0
    print(f"{_pad('代码', 8)}{_pad('归因', 22)}{_pad('登记', 12)}说明")
    print("-" * 78)
    for c in sorted(themes):
        it = themes[c]
        note = str(it.get("note") or "")
        print(f"{_pad(c, 8)}{_pad(str(it.get('theme'))[:20], 22)}"
              f"{_pad(str(it.get('added') or '—'), 12)}"
              f"{note[:34]}{'…' if len(note) > 34 else ''}")
    print(f"\n共 {len(themes)} 条 · 族 {len(fams)} 个 · ⛔ 不加权重、不进判据、不上卡")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
