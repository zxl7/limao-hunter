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

── 2026-10-14 扩契约（数据血缘审计 §十一 P2-10）──────────────────────────
契约从「7 类在线产物」扩到**盘后归档链**：zt / eruption / sector_flow /
theme_rotation / summary_5d / top5（+ top5 的 theme_index 索引）。
动因：这 6 类归档是防守闸门、题材库、情绪轨迹、回流引擎的**共同上游**，
此前完全没有契约，写方一旦改字段名（如 sector_flow 的 flow_yi）无人报警，
会像 09-17 快讯断更那样「静默跑偏」。

设计口径：
  ① 归档文件按日期命名（`<stem>_YYYY-MM-DD.json`），`discover()` **只校验最新一份**
     （全量回扫会因历史版本字段演进误伤；历史对账由 archive_audit 负责）。
  ② 时间戳字段统一用 `_f_ts`：兼容全系统三种真实形态
     （`'2026-10-13 17:19:11'` 空格 / `'2026-10-13T17:19:11.9'` naive / 带 `+08:00`），
     见 `common/market_time.py` 的时间戳字段规范。**类型/可解析性**才是契约，形态不设卡。
  ③ 行级契约 `__rows__`（list 桶）/ `__rowdicts__`（dict 桶）默认**必填**；
     历史确有 null 的字段（top5.zt_count、summary_5d.zt_count/max_boards）显式标 `False`
     降为「有值时校验类型」，避免把回填期的正常空值误报为断链。
"""
from __future__ import annotations
import argparse, fnmatch, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:          # 作为脚本直跑时 sys.path[0] 是 scripts/，需补项目根
    sys.path.insert(0, str(ROOT))
try:
    from common.market_time import parse_dt as _parse_dt  # noqa: E402
except Exception:                       # pragma: no cover - common 不可导入时退化为纯字符串校验
    _parse_dt = None

# ── 契约定义：{ 文件名模式: { 必填字段: (类型校验函数, 说明) } } ──
def _f_int(v): return isinstance(v, int) and not isinstance(v, bool)
def _f_num(v): return isinstance(v, (int, float)) and not isinstance(v, bool)
def _f_str(v): return isinstance(v, str) and len(v) > 0
def _f_list(v): return isinstance(v, list)
def _f_dict(v): return isinstance(v, dict)


def _f_ts(v):
    """时间戳：非空字符串且可被 market_time.parse_dt 解析。

    兼容三种真实形态（空格分隔 / naive ISO / 带 +08:00）；parse_dt 不可用时退化
    为「非空字符串」，避免把环境问题误报成数据断链。
    """
    if not isinstance(v, str) or not v:
        return False
    if _parse_dt is None:               # 兜底：仅校验非空字符串
        return True
    return _parse_dt(v) is not None


# 归档文件严格命名（P2-10）：<stem>_YYYY-MM-DD.json，防 theme_index/备份等杂项混入 discover()
_ARCHIVE_RE = re.compile(r"^(?:zt|eruption|sector_flow|top5)_\d{4}-\d{2}-\d{2}\.json$")

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
    # ── 盘后归档/派生类（P2-10 · 2026-10-14 数据血缘审计扩契约）──
    # 行级契约元素 = (校验函数, 说明[, required])；required 缺省 True。
    "zt_*.json": {
        "date": (_f_str, "交易日"),
        "zt_count": (_f_int, "涨停家数"),
        "max_boards": (_f_int, "最高连板数"),
        "ladder": (_f_dict, "连板梯队"),
        "zt_list": (_f_list, "涨停明细"),
        "__rows__": {
            "zt_list": {
                "code": (_f_str, "代码"),
                "name": (_f_str, "名称"),
                "boards": (_f_int, "连板数"),
            },
        },
    },
    "eruption_*.json": {
        "date": (_f_str, "交易日"),
        "generated_ts": (_f_ts, "生成时间"),
        "themes": (_f_list, "爆发题材"),
        "__rows__": {
            "themes": {
                "name": (_f_str, "题材名"),
                "stocks": (_f_list, "成分股"),
            },
        },
    },
    "sector_flow_*.json": {
        "date": (_f_str, "交易日"),
        "generated_ts": (_f_ts, "生成时间"),
        "boards": (_f_dict, "板块资金流（板块名→行情）"),
        "__rowdicts__": {
            "boards": {
                "pct": (_f_num, "板块涨幅%"),
                "flow_yi": (_f_num, "当日主力净额(亿)"),
            },
        },
    },
    "theme_rotation.json": {
        "date": (_f_str, "交易日"),
        "generated_ts": (_f_ts, "生成时间"),
        "candidates": (_f_list, "回流候选"),
        "sector_zt_trends": (_f_list, "板块涨停趋势"),
        "market_context": (_f_dict, "市场环境"),
        "data_meta": (_f_dict, "数据来源元信息"),
        "__rows__": {
            "candidates": {
                "theme": (_f_str, "题材名"),
                "phase": (_f_str, "周期阶段"),
            },
            "sector_zt_trends": {
                "theme": (_f_str, "题材名"),
            },
        },
    },
    "summary_5d.json": {
        "generated_ts": (_f_ts, "生成时间"),
        "window_days": (_f_int, "窗口天数"),
        "days": (_f_list, "逐日情绪"),
        "trajectory": (_f_dict, "情绪轨迹"),
        "__rows__": {
            "days": {
                "date": (_f_str, "交易日"),
                # 历史有 null（缺当日反馈矩阵），降为「有值时校验」
                "sentiment_score": (_f_num, "情绪分", False),
                "zt_count": (_f_int, "涨停数", False),
                "max_boards": (_f_int, "最高连板", False),
            },
        },
    },
    "top5_*.json": {
        "date": (_f_str, "交易日"),
        "source": (_f_str, "数据来源"),
        "archived_at": (_f_ts, "归档时刻"),
        "themes": (_f_list, "题材榜单"),
        "__rows__": {
            "themes": {
                "name": (_f_str, "题材名"),
                "codes": (_f_list, "成分股代码"),
                # 早期回填归档 zt_count 为 null，降为「有值时校验」
                "zt_count": (_f_int, "题材涨停家数", False),
            },
        },
    },
    "theme_index.json": {
        "updated_at": (_f_ts, "更新时间"),
        "days": (_f_list, "覆盖交易日"),
        "theme_count": (_f_int, "题材总数"),
        "themes": (_f_dict, "题材→统计"),
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

    # 匹配契约（glob 模式走 fnmatch；无通配符者视为精确文件名；空 key 不参与匹配）
    contract = None
    for pat, spec in CONTRACTS.items():
        if not pat:                     # 空 key 仅作「整体为 dict」标记（auction_quotes）
            continue
        if any(ch in pat for ch in "*?["):
            matched = fnmatch.fnmatch(name, pat)   # glob：candidates-*.json / zt_*.json …
        else:
            matched = (name == pat)                # 精确：latest.json / theme_rotation.json …
        if matched:
            contract = spec
            break
    if contract is None:
        return True, []  # 不在契约内的文件不校验（保守通过）

    # zt_*.json 双形态兼容（2026-10-15 实测）：同一文件名 `zt_YYYY-MM-DD.json`
    # 被两种形态复用——
    #   ① 日频收盘形态（capture_daily 17:0x 落盘）：必含 zt_count/max_boards/ladder/zt_list；
    #   ② 盘中快照形态（theme_data 盘中写入、theme_series 消费）：intraday_only=true +
    #      intraday_points[]。当前交易日 capture_daily 未跑时文件是盘中形态，不能删
    #     （否则时间轴看板断料），故契约按 intraday_only 分支走盘中校验。
    if name.startswith("zt_") and data.get("intraday_only"):
        return _validate_zt_intraday(name, data, errors)

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

    for field, spec in contract.items():
        if not field or field.startswith("__"):
            continue  # 空 key = 已在上方校验 dict 结构；__rows__/__rowdicts__ = 下行级校验
        check, desc = spec[0], spec[1]
        if field not in data:
            errors.append(f"{name}: 缺必填字段 [{field}]（{desc}）")
            if fail_fast:
                break
        elif not check(data[field]):
            v = data[field]
            errors.append(f"{name}: 字段 [{field}] 类型/取值异常（{desc}）: {type(v).__name__}={str(v)[:40]}")
            if fail_fast:
                break

    # 行级契约（P2-10）：字段级已在上方；此处校验 list/dict 桶内每一行的关键字段。
    if not (fail_fast and errors):
        _check_rows(name, contract, data, errors)
    return len(errors) == 0, errors


def _validate_zt_intraday(name: str, data: dict, errors: list[str]) -> tuple[bool, list[str]]:
    """zt_*.json 盘中快照形态契约（intraday_only=true + intraday_points[]）。

    与日频收盘形态互斥：当前交易日 capture_daily 未落盘时，文件是盘中形态，
    由 theme_data 写入、theme_series 消费，不可删改，故单独校验其结构而非套用
    日频契约（会误报缺 zt_count/max_boards/ladder）。
    """
    top = {
        "date": (_f_str, "交易日"),
        "date_raw": (_f_str, "交易日(纯数字)"),
        "intraday_points": (_f_list, "盘中采样点序列"),
    }
    for field, (check, desc) in top.items():
        if field not in data:
            errors.append(f"{name}: 缺必填字段 [{field}]（{desc}）")
        elif not check(data[field]):
            v = data[field]
            errors.append(f"{name}: 字段 [{field}] 类型异常（{desc}）: {type(v).__name__}={str(v)[:40]}")
    # 行级：每个采样点需含 window/ts/themes/total/max_boards
    counter = [0]
    for idx, pt in enumerate(data.get("intraday_points") or []):
        if not isinstance(pt, dict):
            counter[0] += 1
            if counter[0] <= _ROW_ERR_CAP:
                errors.append(f"{name}: intraday_points[{idx}] 应为 dict，实为 {type(pt).__name__}")
            continue
        _row_fields_check(errors, name, "intraday_points", f"[{idx}]", pt, {
            "window": (_f_str, "采样窗"),
            "ts": (_f_ts, "采样时刻"),
            "themes": (_f_dict, "题材分布"),
            "total": (_f_int, "涨停总数"),
            "max_boards": (_f_int, "最高连板"),
        }, counter)
    if counter[0] > _ROW_ERR_CAP:
        errors.append(f"{name}: …(行级问题共 {counter[0]} 处，此处仅列前 {_ROW_ERR_CAP} 处)")
    return len(errors) == 0, errors


# ── 行级契约执行器（P2-10）────────────────────────────────────────────────
_ROW_ERR_CAP = 5   # 单文件行级错误上限（防 sector_flow 357 行同类问题刷屏）


def _row_fields_check(errors, name, bucket, where, row, spec, counter) -> None:
    for field, item in spec.items():
        check, desc = item[0], item[1]
        required = item[2] if len(item) > 2 else True
        if field not in row or row[field] is None:
            if not required:
                continue
            counter[0] += 1
            if counter[0] <= _ROW_ERR_CAP:
                errors.append(f"{name}: {bucket}{where}.{field} 缺失（{desc}）")
            continue
        if not check(row[field]):
            counter[0] += 1
            if counter[0] <= _ROW_ERR_CAP:
                errors.append(
                    f"{name}: {bucket}{where}.{field} 类型异常（{desc}）: "
                    f"{type(row[field]).__name__}={str(row[field])[:30]}")


def _check_rows(name: str, contract: dict, data: dict, errors: list[str]) -> None:
    """执行契约里的 __rows__（list 桶）与 __rowdicts__（dict 桶）行级校验。"""
    counter = [0]
    for bucket, spec in (contract.get("__rows__") or {}).items():
        rows = data.get(bucket)
        if not isinstance(rows, list):
            continue            # 桶本身缺失/类型错已由字段级契约报出，此处不重复
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                counter[0] += 1
                if counter[0] <= _ROW_ERR_CAP:
                    errors.append(f"{name}: {bucket}[{idx}] 应为 dict，实为 {type(row).__name__}")
                continue
            _row_fields_check(errors, name, bucket, f"[{idx}]", row, spec, counter)
    for bucket, spec in (contract.get("__rowdicts__") or {}).items():
        mp = data.get(bucket)
        if not isinstance(mp, dict):
            continue
        for key, row in mp.items():
            if not isinstance(row, dict):
                counter[0] += 1
                if counter[0] <= _ROW_ERR_CAP:
                    errors.append(f"{name}: {bucket}['{key}'] 应为 dict，实为 {type(row).__name__}")
                continue
            _row_fields_check(errors, name, bucket, f"['{key}']", row, spec, counter)
    if counter[0] > _ROW_ERR_CAP:
        errors.append(f"{name}: …(行级问题共 {counter[0]} 处，此处仅列前 {_ROW_ERR_CAP} 处)")


def discover() -> list[Path]:
    """发现所有已知产物的最新版本文件。"""
    files: list[Path] = []
    # 固定名文件（在线产物）
    for name in ("latest.json", "feedback_matrix.json", "holdings.json",
                 "ai_env.json", "narratives.json"):
        p = ROOT / "cache" / "pulse_snapshots" / name
        if not p.exists():
            p = ROOT / "cache" / name
        if p.exists():
            files.append(p)
    # 固定名文件（派生/索引类 · P2-10）
    for p in (ROOT / "cache" / "theme_rotation.json",
              ROOT / "cache" / "short_term" / "summary_5d.json",
              ROOT / "cache" / "top5_history" / "theme_index.json"):
        if p.exists():
            files.append(p)
    # glob 文件（取最新一份；归档类仅认 `_YYYY-MM-DD.json` 严格命名，防杂项误入）
    archive_pats = {
        "zt_*.json", "eruption_*.json", "sector_flow_*.json", "top5_*.json",
    }
    for pat, base in [("candidates-*.json", ROOT / "wealth_code" / "cache"),
                      ("auction_quotes-*.json", ROOT / "wealth_code" / "cache"),
                      ("zt_*.json", ROOT / "cache" / "zt_history"),
                      ("eruption_*.json", ROOT / "cache" / "eruption_history"),
                      ("sector_flow_*.json", ROOT / "cache" / "sector_flow_history"),
                      ("top5_*.json", ROOT / "cache" / "top5_history")]:
        cands = sorted(base.glob(pat))
        if pat in archive_pats:
            cands = [c for c in cands if _ARCHIVE_RE.match(c.name)]
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
