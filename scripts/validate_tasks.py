#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务注册表校验器（P1-⑨ · 2026-08-17）
对照 automation DB 校验 config/tasks.yaml 一致性：
  ① yaml 任务是否都在 DB 中存在（id 匹配）
  ② DB 中 ACTIVE 任务是否都登记在 yaml（防漏登记）
  ③ rrule 是否与 DB 一致
  ④ 任务名是否与 DB 一致
  ⑤ 骨架引用是否正确（盘中猎手=骨架B / 盘中快照=骨架B.5）
用法：
  python3 scripts/validate_tasks.py            # 校验并输出报告
  python3 scripts/validate_tasks.py --json     # JSON 输出（供体检/中枢）
exit=0 全过；exit=1 有差异（仅报告，不阻断）。
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path.home() / ".workbuddy" / "workbuddy.db"
TASKS_YAML = ROOT / "config" / "tasks.yaml"

# 骨架引用规则：任务名前缀 → 期望骨架
SKELETON_RULE = [
    (("盘中猎手",), "骨架B"),
    (("盘中快照",), "骨架B.5"),
    (("盘前预测",), "骨架A"),
    (("盘后复盘",), "骨架C"),
    (("财富密码",), "pipeline"),
    (("盘中调度哨兵",), "脚本"),
    (("推测回测",), "脚本"),
    (("机构投研",), "机构投研"),
    (("机构研报",), "机构投研"),
    (("机构晚报",), "机构投研"),   # 2026-09-03 新建：Gangtise 晚报 evening 21:00 速览卡（aut ed5fd591）
    (("龙虎榜速览",), "复盘系"),   # 2026-09-03 R3：盘后龙虎榜独立速览小卡（挂财神-复盘）
]


def _load_yaml(path: Path):
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _db_tasks():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id,name,rrule,status FROM automations WHERE deleted_at IS NULL"
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def expected_kind(name: str) -> str:
    for prefix, kind in SKELETON_RULE:
        if _norm_name(name).startswith(prefix):
            return kind
    return "?"


def _norm_name(name: str) -> str:
    """剥离【时段】分类前缀（2026-09-03 任务标题分类）：【盘中】盘中猎手-0925 → 盘中猎手-0925。
    分类前缀为显示层组织，规则匹配/kind 判断一律按剥离后原名。"""
    return re.sub(r"^【[^】]*】", "", name) if name else name


def _rrule_struct_same(a: str, b: str) -> bool:
    """结构级 rrule 比较（2026-09-03）：BYDAY 集合/BYHOUR 相同且 BYMINUTE 差 ≤10 视为同一调度
    （容忍宿主库分钟级抖动），结构漂移（换日/小时/周几）返回 False。"""
    def _parse(r: str):
        kv = {}
        for seg in (r or "").split(";"):
            if "=" in seg:
                k, _, v = seg.partition("=")
                kv[k] = v
        return kv
    ka, kb = _parse(a), _parse(b)
    if ka.get("BYDAY") != kb.get("BYDAY") or ka.get("BYHOUR") != kb.get("BYHOUR"):
        return False
    try:
        ma, mb = int(ka.get("BYMINUTE", 0) or 0), int(kb.get("BYMINUTE", 0) or 0)
    except (TypeError, ValueError):
        return ka == kb
    return abs(ma - mb) <= 10


def validate(verbose=True) -> dict:
    try:
        import yaml  # noqa: F401 (确保 yaml 可用)
    except ImportError:
        hint = ("缺少 PyYAML：请先安装（项目隔离环境）\n"
                "  ~/.workbuddy/binaries/python/envs/default/bin/pip install PyYAML pytest\n"
                "或系统 python3 -m pip install PyYAML")
        if verbose:
            print(f"❌ {hint}")
        return {"ok": False, "errors": [hint], "warnings": []}
    try:
        data = _load_yaml(TASKS_YAML)
        yaml_tasks = data.get("tasks", [])
    except Exception as e:
        return {"ok": False, "errors": [f"tasks.yaml 解析失败: {e}"], "warnings": []}
    retired = [t for t in yaml_tasks if t.get("retired")]
    yaml_tasks = [t for t in yaml_tasks if not t.get("retired")]

    db_tasks = _db_tasks()
    db_by_id = {t["id"]: t for t in db_tasks}
    db_by_name = {t["name"]: t for t in db_tasks}
    yaml_by_id = {t["id"]: t for t in yaml_tasks}
    yaml_by_name = {t["name"]: t for t in yaml_tasks}

    errors, warnings = [], []

    # ① yaml 任务在 DB 存在
    for t in yaml_tasks:
        tid = t.get("id", "?")
        if tid not in db_by_id:
            errors.append(f"[yaml→db] {t.get('name','?')} ({tid}) 在 automation DB 中不存在")
            continue
        d = db_by_id[tid]
        # ④ 任务名一致（分类前缀属显示层，比对剥离前缀后的名字）
        if _norm_name(d["name"]) != _norm_name(t["name"]):
            errors.append(f"[name] {tid} yaml 名={t['name']} ≠ DB 名={d['name']}")
        # ③ rrule 一致（仅 ACTIVE 比对·2026-09-03）：宿主库分钟级抖动（实测 9:20↔9:25、9:25↔9:30
        # 随运行回写漂移）按结构比较忽略——BYDAY/BYHOUR 相同且 BYMINUTE 差 ≤10min 视为同一调度；
        # 结构级漂移（换日/换小时/删周）才告警。
        if d["status"] == "ACTIVE" and (d["rrule"] or "") != (t.get("rrule") or ""):
            if not _rrule_struct_same(d["rrule"], t.get("rrule", "")):
                warnings.append(f"[rrule] {t['name']}: yaml={t.get('rrule','')} ≠ db={d['rrule']}")
        # ⑤ 骨架引用
        want = expected_kind(t["name"])
        if t.get("kind") != want:
            warnings.append(f"[kind] {t['name']}: yaml kind={t.get('kind')} ≠ 按规则应为 {want}")

    # ② DB ACTIVE 任务都登记
    for d in db_tasks:
        if d["status"] != "ACTIVE":
            continue
        if d["id"] not in yaml_by_id:
            errors.append(f"[db→yaml] ACTIVE 任务 {d['name']} ({d['id']}) 未登记到 tasks.yaml")

    # 重复 id / 重复名检查
    seen_id, seen_name = set(), set()
    for t in yaml_tasks:
        if t["id"] in seen_id:
            errors.append(f"[dup] yaml 重复 id: {t['id']}")
        seen_id.add(t["id"])
        if t["name"] in seen_name:
            errors.append(f"[dup] yaml 重复 name: {t['name']}")
        seen_name.add(t["name"])

    ok = not errors
    if verbose:
        act = sum(1 for t in db_tasks if t["status"] == "ACTIVE")
        print(f"tasks.yaml 登记 {len(yaml_tasks)} 个（退役 {len(retired)} 个）| "
              f"DB 有效 {len(db_tasks)} 个 | ACTIVE {act} 个")
        print(f"结果: {'✅ 一致性通过' if ok else '❌ 存在差异'}")
        for w in warnings:
            print(f"  ⚠️ {w}")
        for e in errors:
            print(f"  ❌ {e}")
        if retired:
            print("  ℹ️ 已退役（DB 已删除，仅留档）："
                  + "、".join(str(t.get("name")) for t in retired))
    return {"ok": ok, "errors": errors, "warnings": warnings,
            "yaml_count": len(yaml_tasks), "db_count": len(db_tasks),
            "retired": [t.get("name") for t in retired]}


if __name__ == "__main__":
    res = validate(verbose="--json" not in sys.argv)
    if "--json" in sys.argv:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res["ok"] else 1)
