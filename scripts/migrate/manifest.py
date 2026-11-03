#!/usr/bin/env python3
"""manifest.py —— 从 serve.py 的公开路由反推「终端实际会读」的文件，按冷热分层出清单。

为什么按路由反推，而不是直接 rsync 整个 cache：
  · `cache/` 有 283M / 5000+ 文件，全量每 30s 推一次毫无必要；
  · 终端只读 `PUBLIC_ROUTES` 覆盖到的那些文件 —— 实测 **37 个文件 2.9MB** 就撑起绝大多数页面；
  · 分层后：热层 3MB 可以 30s 推一次，冷层（股性全库 76M）一天一次即可。

用法：
  python3 manifest.py            # 生成 T0/T1/T2 三个清单到 dist/migrate/
  python3 manifest.py --print    # 只打印统计
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT_DIR = os.path.join(APP_DIR, "dist", "migrate")

# 目录路由里「体积大、变化慢」的，单独放冷层，不参与高频同步
COLD_PREFIXES = ("cache/zt_genes", "wealth_code/cache", "cache/kline_qfq", "cache/stock_arc")

# 热层吃不下/不该频繁传的目录（它们由 T1/T2 覆盖）
WARM_MAX_FILES = 40  # 每个目录最多取最新 N 个文件，避免历史归档塞爆清单


def load_routes():
    spec = importlib.util.spec_from_file_location("serve_mod", os.path.join(APP_DIR, "serve.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.PUBLIC_ROUTES


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="只打印统计，不写文件")
    args = ap.parse_args()

    routes = load_routes()
    t0: list[str] = []
    t0r: list[str] = []   # 实时子集：cadence=intraday，单文件 1.6MB，每拍都推
    t1: list[str] = []
    t2: list[str] = []

    for route, spec in routes.items():
        realtime = spec.get("cadence") == "intraday"
        # ⚠️ 必须 `and not dir`：`/api/decision/zt-pool` 这类路由**同时**有 file 与 dir，
        #    旧的单文件分支会先命中，把 `zt_pool.json` 当项目根下的文件去找 → 找不到 → 跳过。
        if spec.get("file") and not spec.get("dir"):
            rel = spec["file"]
            if os.path.exists(os.path.join(APP_DIR, rel)):
                t0.append(rel)
                if realtime:
                    t0r.append(rel)
            continue
        if not spec.get("dir"):
            continue
        d = spec["dir"].rstrip("/")
        # ── 子目录型路由：`dir` 下是**日期目录**，真正要的是最新那一期里的某个文件
        #    （例：/api/decision/zt-pool → cache/decision_log/<date>/zt_pool.json）
        #    ⛔ 旧实现只 glob 顶层文件 → 这类路由一个文件都不会进清单，
        #    服务器上接口返回 null（2026-10-31 实测「首封时间在公网不显示」的根因）。
        dir_pattern = spec.get("dirPattern")
        if dir_pattern and spec.get("file"):
            subs = [p for p in glob.glob(os.path.join(APP_DIR, d, "*"))
                    if os.path.isdir(p) and re.search(dir_pattern, os.path.basename(p))]
            subs.sort(reverse=True)          # 目录名是 YYYY-MM-DD，字典序即时间序
            if subs:
                target = os.path.join(subs[0], spec["file"])
                if os.path.isfile(target):
                    (t0r if realtime else t0).append(os.path.relpath(target, APP_DIR))
            continue
        pattern = spec.get("pattern")
        rx = re.compile(pattern) if pattern else None
        files = []
        for p in glob.glob(os.path.join(APP_DIR, d, "*")):
            if not os.path.isfile(p):
                continue
            if rx and not rx.search(os.path.basename(p)):
                continue
            files.append(p)
        if not files:
            continue
        cold = any(d == c or d.startswith(c + "/") for c in COLD_PREFIXES)
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        if cold:
            t2.extend(os.path.relpath(p, APP_DIR) for p in files)
        else:
            t1.extend(os.path.relpath(p, APP_DIR) for p in files[:WARM_MAX_FILES])
            # 实时路由的目录型接口（如盘中时间线/引擎态）：只推最新 2 个文件
            if realtime:
                t0r.extend(os.path.relpath(p, APP_DIR) for p in files[:2])

    def size(paths: list[str]) -> float:
        return sum(os.path.getsize(os.path.join(APP_DIR, p)) for p in paths) / 1048576

    stats = [
        ("T0R 实时（每拍 60s：cadence=intraday 的接口）", t0r),
        ("T0 热（5min：全部单文件路由）", t0),
        ("T1 温（5min：题材/情绪/龙虎榜/资金历史等最新切片）", t1),
        ("T2 冷（每日：股性全库/财富码缓存等大目录）", t2),
    ]
    for label, paths in stats:
        print(f"{label}: {len(paths)} 个文件, {size(paths):.1f}MB")

    if args.print:
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    for name, paths in (("T0R.txt", t0r), ("T0.txt", t0), ("T1.txt", t1), ("T2.txt", t2)):
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as fh:
            fh.write("\n".join(sorted(set(paths))) + "\n")
    print(f"清单已写入 {OUT_DIR}/{{T0R,T0,T1,T2}}.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
