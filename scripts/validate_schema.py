#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据契约校验器（P0-1a · 2026-08-17）
=====================================
对关键 JSON 产物做「必填字段 + 类型」校验，杜绝数据血缘断链/字段缺失
（曾踩坑：auction_quotes 与 candidates 张冠李戴、candidates 丢 price 字段）。

用法:
  python3 scripts/validate_schema.py                    # 校验全部已知产物（自动发现最新版本）
  python3 scripts/validate_schema.py --path <file>       # 校验指定文件
  python3 scripts/validate_schema.py --json             # 机器可读输出（exit code 0=全过 1=有错）
  python3 scripts/validate_schema.py --fail-fast        # 首个错误即退出（供 push 前调用）

返回: 0=全部通过；1=存在错误（配合 push_card.py 推送前阻断）
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 契约定义：{ 文件名模式: { 必填字段: (类型校验函数, 说明) } } ──
def _f_int(v): return isinstance(v, int) and not isinstance(v, bool)
def _f_num(v): return isinstance(v, (int, float)) and not isinstance(v, bool)
def _f_str(v): return isinstance(v, str) and len(v) > 0
def _f_list(v): return isinstance(v, list)
def _f_dict(v): return isinstance(v, dict)

CONTRACTS: dict[str, dict[str, tuple]] = {
    "latest.json": {
        "ts": (_f_str, "快照时间戳"),
        "total_zt": (_f_int, "涨停总数"),
        "sentiment": (_f_str, "情绪标签"),
        "index": (_f_list, "三大指数"),
        "top5": (_f_list, "题材 Top5"),
    },
    "feedback_matrix.json": {
        "generated_ts": (_f_str, "生成时间"),
        "prev_date": (_f_str, "昨日交易日"),
        "prev_zt_count": (_f_int, "昨日涨停数"),
        "promote_rate": (_f_num, "晋级率"),
        "nuke_count": (_f_int, "核按钮数"),
        "broken_rate": (_f_num, "炸板率"),
        "up_rate": (_f_num, "上涨率"),
    },
    "candidates-*.json": {
        "schema": (_f_str, "数据契约版本"),
        "date": (_f_str, "交易日"),
        "regime": (_f_str, "市场体制"),
        "relay": (_f_list, "接力池"),
        "watch": (_f_list, "观察池"),
        # 历史连板因子为候选行级可选字段；这里校验存在时的结构，避免旧历史文件被误伤。
    },
    "auction_quotes-*.json": {
        # dict: code -> {prev_close, auction_price, ...}
        "": (_f_dict, "竞价快照（code->行情）"),
    },
    "holdings.json": {
        "updated": (_f_str, "更新时间"),
        "holdings": (_f_list, "持仓列表"),
    },
    "ai_env.json": {
        "regime": (_f_str, "AI 体制"),
        "score": (_f_num, "研判分"),
    },
    "narratives.json": {
        "global_narrative": (_f_str, "全局旁白"),
    },
}


def validate_one(path: Path, fail_fast: bool = False) -> tuple[bool, list[str]]:
    """校验单个文件。返回 (是否通过, 错误列表)。"""
    errors: list[str] = []
    name = path.name
    if not path.exists():
        return False, [f"{name}: 文件不存在"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, [f"{name}: JSON 解析失败 - {e}"]

    # 匹配契约（支持 glob 模式与空 key 表示"整体为 dict"）
    contract = None
    for pat, spec in CONTRACTS.items():
        if pat in ("", "latest.json", "feedback_matrix.json", "holdings.json",
                   "ai_env.json", "narratives.json"):
            if name == pat:
                contract = spec
                break
        else:
            # glob 模式：candidates-*.json / auction_quotes-*.json
            import fnmatch
            if fnmatch.fnmatch(name, pat):
                contract = spec
                break
    if contract is None:
        return True, []  # 不在契约内的文件不校验（保守通过）

    if not isinstance(data, dict):
        return False, [f"{name}: 应为 dict，实为 {type(data).__name__}"]

    # 守卫态豁免（2026-09-02）：error 标记文件是「未到计算时点/数据缺失」的占位，
    # 天然不带完整契约字段（如 pre_auction 的 feedback_matrix.json 只有 error+msg）。
    # 这些文件由 AI 任务按 shared_rules 读 error 跳过对应模块——校验器只查占位结构，
    # 不再把「缺 up_rate」这类情况误报为数据血缘故障（此前早盘 08:58 守卫写文件 → 质量门红）。
    if data.get("error"):
        guard_contract = {
            "generated_ts": (_f_str, "生成时间"),
            "error": (_f_str, "守卫态标记"),
        }
        for field, (check, desc) in guard_contract.items():
            if field not in data:
                errors.append(f"{name}: 守卫态缺字段 [{field}]（{desc}）")
            elif not check(data[field]):
                errors.append(f"{name}: 守卫态字段 [{field}] 异常（{desc}）")
        return len(errors) == 0, errors

    # 候选行级契约：历史连板字段存在时必须为正确类型/范围。
    if name.startswith("candidates-"):
        for bucket in ("relay", "watch"):
            for idx, row in enumerate(data.get(bucket, [])):
                if not isinstance(row, dict):
                    errors.append(f"{name}: {bucket}[{idx}] 应为 dict")
                    continue
                for field, check, desc in (
                    ("history_relay_prob", _f_num, "历史晋级概率"),
                    ("history_relay_samples", _f_int, "历史同高度样本数"),
                    ("history_relay_factor", _f_num, "历史连板因子分"),
                ):
                    if field in row and row[field] is not None and not check(row[field]):
                        errors.append(f"{name}: {bucket}[{idx}].{field} 类型异常（{desc}）")
                if "history_relay_prob" in row and row["history_relay_prob"] is not None and not 0 <= row["history_relay_prob"] <= 1:
                    errors.append(f"{name}: {bucket}[{idx}].history_relay_prob 超出 [0,1]")

    for field, (check, desc) in contract.items():
        if not field:
            continue  # 空 key 表示已在上方校验 dict 结构
        if field not in data:
            errors.append(f"{name}: 缺必填字段 [{field}]（{desc}）")
            if fail_fast:
                break
        elif not check(data[field]):
            v = data[field]
            errors.append(f"{name}: 字段 [{field}] 类型/取值异常（{desc}）: {type(v).__name__}={str(v)[:40]}")
            if fail_fast:
                break
    return len(errors) == 0, errors


def discover() -> list[Path]:
    """发现所有已知产物的最新版本文件。"""
    files: list[Path] = []
    # 固定名文件
    for name in ("latest.json", "feedback_matrix.json", "holdings.json",
                 "ai_env.json", "narratives.json"):
        p = ROOT / "cache" / "pulse_snapshots" / name
        if not p.exists():
            p = ROOT / "cache" / name
        if p.exists():
            files.append(p)
    # glob 文件（取最新）
    for pat, base in [("candidates-*.json", ROOT / "wealth_code" / "cache"),
                      ("auction_quotes-*.json", ROOT / "wealth_code" / "cache")]:
        cands = sorted(base.glob(pat))
        if cands:
            files.append(cands[-1])
    # 去重保序
    seen, out = set(), []
    for f in files:
        if f.resolve() not in seen:
            seen.add(f.resolve())
            out.append(f)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=None, help="校验指定文件")
    ap.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    ap.add_argument("--fail-fast", action="store_true", help="首个错误即停")
    args = ap.parse_args()

    paths = [Path(args.path)] if args.path else discover()
    results = []
    all_ok = True
    for p in paths:
        ok, errs = validate_one(p, args.fail_fast)
        results.append({"path": str(p), "ok": ok, "errors": errs})
        if not ok:
            all_ok = False

    if args.json:
        print(json.dumps({"all_ok": all_ok, "files": results}, ensure_ascii=False, indent=2))
    else:
        for r in results:
            status = "✅" if r["ok"] else "❌"
            print(f"{status} {r['path']}")
            for e in r["errors"]:
                print(f"    - {e}")
        print("\n" + ("✅ 全部契约校验通过" if all_ok else f"❌ 存在 {sum(1 for r in results if not r['ok'])} 个文件未通过"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
