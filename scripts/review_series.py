#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""📈 复盘多日序列（写 JSON 供终端画图）"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "wealth_code"))
from core import review_series as rs  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    a = ap.parse_args(argv)
    d = rs.build(a.days)
    body = json.dumps(d, ensure_ascii=False, indent=1)
    (ROOT / "cache" / "review_series.json").write_text(body, encoding="utf-8")
    print(f"[series] {d['days']} 天 · 情绪分覆盖 "
          f"{sum(1 for r in d['rows'] if r['sentiment'] is not None)} 天")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
