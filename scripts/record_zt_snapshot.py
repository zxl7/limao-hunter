#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""昨日涨停快照记录（隔日反馈矩阵的数据底座）。

由「盘后复盘」任务在推送后调用（17:00 收盘后）：拉取当日全市场涨停名单，
写入 cache/yesterday_zt.json，供次日「盘中猎手」计算昨日涨停反馈矩阵
（晋级率 / 核按钮 / 昨涨停今均涨幅），以及「财富密码」模块盘后选龙头。

数据源：必盈 API（biyingapi）hslt/ztgc —— 比东财公开接口稳定，且直接返回
连板数(lbc)/行业(hy)/封板资金(zj)，无需二次补全。

⚠️ Token 安全：仅通过环境变量 BIYING_TOKEN 或本机技能配置读取，绝不硬编码。
   详见 common/biying_client.py 顶部说明。

用法:
  python3 record_zt_snapshot.py            # 默认记录当日（date=今天）
  python3 record_zt_snapshot.py --date 20260810
  python3 record_zt_snapshot.py --dry-run  # 只打印结果不写文件
  python3 record_zt_snapshot.py --date 20260810 --force   # 非交易日强制写入（仅限确知的回填）

⛔ 交易日门禁（2026-10-09）：非A股交易日一律拒绝写入（`common.trading_calendar` 为唯一真源）。
   事故：2026-09-25（中秋休市）数据源回吐 09-24 的池 → 落盘幽灵日，污染历史序列。

输出: cache/yesterday_zt.json
"""
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / "cache"
COMMON = ROOT / "common"

# 把 common/ 加入路径，导入共享 biying 客户端（token 不入库）
if str(COMMON) not in sys.path:
    sys.path.insert(0, str(COMMON))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from biying_client import fetch_zt_pool, is_20cm  # noqa: E402
# 规范时间戳（2026-10-14 审计 P1-⑦）：快照 generated_ts 统一 ISO8601 带 +08:00
# （本文件是 defense_gate §12「位置输入=zt_history 逐票序列」的上游，格式混用会波及下游相减）。
from common.market_time import iso_bj  # noqa: E402


def _norm_date(date_arg: str) -> tuple[str, str]:
    """接受 YYYYMMDD 或 YYYY-MM-DD，返回 (pretty, raw)。"""
    s = date_arg.strip().replace("-", "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}", s
    raise ValueError(f"无法识别的日期格式: {date_arg}")


def build_snapshot(date: str) -> dict:
    pool = fetch_zt_pool(date)
    # 连板天梯：按板数分组（板数>=2）
    ladder: dict[int, list[str]] = {}
    for it in pool:
        b = it["boards"]
        if b >= 2:
            ladder.setdefault(b, []).append(it["name"])
    max_boards = max(ladder) if ladder else 0
    top_codes = sorted(pool, key=lambda x: -x["boards"])[:5]
    return {
        "date": date,
        "date_raw": date.replace("-", ""),
        "generated_ts": iso_bj(),
        "zt_count": len(pool),
        "tc": len(pool),  # biyingapi 不直接给全市场涨停总数，以池长度代替
        "max_boards": max_boards,
        "ladder": {str(k): v for k, v in sorted(ladder.items(), reverse=True)},
        "top_codes": [
            {"name": x["name"], "code": x["code"], "boards": x["boards"]}
            for x in top_codes
        ],
        "zt_list": pool,
    }


def main() -> int:
    dry = "--dry-run" in sys.argv[1:]
    date_arg = ""
    if "--date" in sys.argv[1:]:
        date_arg = sys.argv[sys.argv.index("--date") + 1]

    if date_arg:
        pretty, _ = _norm_date(date_arg)
    else:
        pretty = datetime.now().strftime("%Y-%m-%d")

    # 交易日门禁（2026-10-09）：非A股交易日禁止写入涨停快照。
    # 事故：2026-09-25（中秋·周五休市）本脚本 17:19 落盘了一份**与 09-24 逐字段相同**
    # 的幽灵日（数据源在休市日回吐上一交易日池），污染断板/形态/5 日窗序列。
    # `--force` 仅供**确知有数据的手工回填**，日常调度一律不加。
    if "--force" not in sys.argv[1:]:
        try:
            from trading_calendar import is_a_share_trading_day  # COMMON 已在模块顶部入 path
        except Exception:  # pragma: no cover
            is_a_share_trading_day = None
        if is_a_share_trading_day is not None and not is_a_share_trading_day(pretty):
            print(f"[record_zt_snapshot] ⏭ {pretty} 非A股交易日（交易日历），拒绝写入涨停快照"
                  f"（确需强制回填加 --force）")
            return 1

    try:
        snap = build_snapshot(pretty)
    except Exception as e:  # noqa: BLE001
        print(f"[record_zt_snapshot] 拉取失败: {e}")
        return 1

    if not snap["zt_list"]:
        print(f"[record_zt_snapshot] {pretty} 涨停池为空，可能非交易日或接口异常，跳过")
        return 1

    if dry:
        print(
            f"[record_zt_snapshot][dry-run] 涨停数: {snap['zt_count']} | 最高板: {snap['max_boards']} "
            f"| 连板层: {list(snap['ladder'].keys())}"
        )
        print(
            json.dumps(
                {k: v for k, v in snap.items() if k != "zt_list"},
                ensure_ascii=False,
                indent=2,
            )[:1200]
        )
        return 0

    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / "yesterday_zt.json"
    out.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    print(
        f"[record_zt_snapshot] ✅ {pretty} 涨停{snap['zt_count']}只 最高{snap['max_boards']}板 → {out}"
    )

    # 归档到题材沉淀目录（**双目录同写**·合并保留 intraday_points）——单一出口 = scripts/zt_archive.py
    # ⛔ 2026-09-18：此前只写 wealth_code/data/zt_history，而 theme_roll/live_signal 等消费方
    #    读 cache/zt_history → 「双路径分裂」（15:30 读到盘中空壳 → 题材广度当日为 0）。
    #    见 scripts/zt_archive.py 模块头事故链；新增归档目录一律在 ZT_DIRS 登记。
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import zt_archive
        paths = zt_archive.write_archives(snap)
        print("[record_zt_snapshot] 📁 已归档题材沉淀（%d 目录）→ %s"
              % (len(paths), " | ".join(str(p) for p in paths)))
    except Exception as e:  # noqa: BLE001
        print(f"[record_zt_snapshot] ⚠️ 归档失败(不影响主流程): {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
