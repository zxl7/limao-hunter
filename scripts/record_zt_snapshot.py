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
from biying_client import fetch_zt_pool, is_20cm  # noqa: E402


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
        "generated_ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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

    # 归档到财富密码自建文件夹（题材沉淀地基，复用已抓数据，不重抓）
    try:
        archive_dir = ROOT / "wealth_code" / "data" / "zt_history"
        archive_dir.mkdir(parents=True, exist_ok=True)
        ap = archive_dir / f"zt_{pretty}.json"
        ap.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        print(f"[record_zt_snapshot] 📁 已归档题材沉淀 → {ap}")
    except Exception as e:  # noqa: BLE001
        print(f"[record_zt_snapshot] ⚠️ 归档失败(不影响主流程): {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
