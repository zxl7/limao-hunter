#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""载体证伪追踪（2026-08-25 用户「小周期载体·汉森确认·老抱团证伪」思路·#3）。

哲学：市场总龙头（载体）= 矛盾主要方面的具象化。判定标准：
  确认 = 逆环境主动走强 + 带动同属性 + 反推板块（今日仍封板/晋级 = 载体延续）
  证伪 = 昨日载体今日断板/未涨停（被市场抛弃 = 载体迁移）

产出：cache/carrier_state.json
  {
    "date": "2026-08-25",
    "prev_date": "2026-08-24",
    "carriers_today": [           # 今日在板载体（从昨日涨停池 high_board 候选 + 今日确认）
      {"name": "汉森制药", "code": "002412", "boards": 5, "confirmed": true, "carry_cnt": 2}
    ],
    "carriers_dead": [            # 昨日载体今日断板（证伪）
      {"name": "开开实业", "code": "600272", "boards": 1, "dead": true}
    ],
    "carrier_migration": "医药（汉森确认）承接老抱团资金 → 载体迁移"
  }

调用：盘后链 capture_daily.py 之后（复盘任务第 6 步），纯读取+计算，无网络。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / "cache"
OUT_PATH = CACHE / "carrier_state.json"
MIN_BOARDS = 2  # 载体候选 = 连板 ≥2 的辨识度票（3板以上=高标·2板=连板梯队·都算载体候选；
                # 2026-08-25 用户「老抱团高标证伪」实证：天洋新材 2板 也是老抱团成员，
                # 只筛 ≥3 会漏 2板 载体（老抱团 8/24 仅 1-2 板但题材辨识度高））

# 时间工具唯一实现（2026-10-14 审计 P1-⑦）：原 `_bj_now()` 自写 BJ_TZ
# （`timezone(timedelta(hours=8))`）违反 common/market_time 铁律 1（禁再自写 BJ_TZ），
# 且 `.isoformat()` 的偏移形态不稳定，现统一转发唯一实现。
if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.market_time import iso_bj, now as _bj_now  # noqa: E402


def _load_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _prev_trade_date(cur: str) -> str | None:
    """从 zt_history 找 cur 的前一个交易日（文件名 zt_YYYY-MM-DD.json·按字典序即时间序）。"""
    dates = sorted(p.stem.replace("zt_", "") for p in CACHE.glob("zt_history/zt_*.json"))
    for d in reversed(dates):
        if d < cur:
            return d
    return None


def run() -> dict:
    yz = _load_json(CACHE / "yesterday_zt.json") or {}
    date = yz.get("date") or _bj_now().strftime("%Y-%m-%d")
    prev = _prev_trade_date(date)
    yz_list = yz.get("zt_list") or []
    today_codes = {str(it.get("code")) for it in yz_list}
    today_names = {str(it.get("name")) for it in yz_list}

    # 今日在板载体：涨停池中 boards≥MIN_BOARDS 的高标（连板纯度/带动性已由候选层标注）
    carriers_today = []
    for it in yz_list:
        b = int(it.get("boards") or 1)
        if b >= MIN_BOARDS:
            carriers_today.append({
                "name": it.get("name"), "code": str(it.get("code")),
                "boards": b, "zbc": int(it.get("zbc") or 0),
                "sector": it.get("sector") or "",
                "fbt": str(it.get("fbt") or ""),
                "confirmed": True,
                "carry_cnt": int(it.get("carry_cnt") or 0),  # 候选层 annotate_carry 回填
            })

    # 昨日载体证伪：读昨日「载体名单」（昨 ≥3 板高标 + 旧载体状态），今日不在涨停池 = 断板证伪
    # ⛔ 2026-08-25 修正：证伪基准 = **昨日 carriers_today 名单**，不是重新从昨日涨停池筛
    # （老抱团 8/24 只有 1-2 板，按板数筛会被漏掉，但它们是题材辨识度载体——用户的
    # 「老抱团证伪」指的就是这类昨日在板/有辨识度、今日断板的票）。
    carriers_dead = []
    migration_from = ""
    prev_carriers: list[dict] = []
    if prev:
        # ① 昨日 carrier_state 里的确认名单（跨日连续性权威）
        prev_state = _load_json(OUT_PATH)
        if prev_state and prev_state.get("date") == prev:
            prev_carriers = prev_state.get("carriers_today") or []
        # ② 兜底：昨日涨停池 boards≥MIN_BOARDS 高标（首次运行无历史状态时）
        if not prev_carriers:
            prev_zt = _load_json(CACHE / "zt_history" / f"zt_{prev}.json") or {}
            for it in (prev_zt.get("zt_list") or []):
                if int(it.get("boards") or 1) >= MIN_BOARDS:
                    prev_carriers.append({
                        "name": it.get("name"), "code": str(it.get("code")),
                        "boards": int(it.get("boards") or 1), "sector": it.get("sector") or "",
                    })
        for pc in prev_carriers:
            pc_code = str(pc.get("code") or "")
            if pc_code and pc_code not in today_codes:
                carriers_dead.append({
                    "name": pc.get("name"), "code": pc_code,
                    "boards": int(pc.get("boards") or 0), "sector": pc.get("sector") or "",
                    "dead": True,
                })
                migration_from = pc.get("sector") or migration_from

    # 载体迁移小结：今日最高板载体 vs 昨日断板载体
    migration = ""
    if carriers_today and carriers_dead:
        top = max(carriers_today, key=lambda x: x["boards"])
        migration = (f"{top['name']}({top['boards']}板·{top.get('sector')})承接资金"
                     f" → 老载体({len(carriers_dead)}家)证伪迁移")
    elif carriers_today:
        top = max(carriers_today, key=lambda x: x["boards"])
        migration = f"{top['name']}({top['boards']}板)为当前载体·无断板迁移"
    elif carriers_dead:
        migration = f"老载体全部断板({len(carriers_dead)}家)证伪·新载体待确认"

    out = {
        "date": date,
        "prev_date": prev or "",
        "carriers_today": carriers_today,
        "carriers_dead": carriers_dead,
        "carrier_migration": migration,
        "generated_ts": iso_bj(),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[carrier_track] {date} 载体 {len(carriers_today)} 家确认 / "
          f"{len(carriers_dead)} 家证伪 | 迁移: {migration}")
    return out


if __name__ == "__main__":
    run()
