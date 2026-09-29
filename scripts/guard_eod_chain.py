#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘后链落盘守护（2026-09-22 立项 · 用户「题材回流怎么没刷新」事故后补）。

## 为什么需要它（真实事故）

2026-09-21（周一）的「盘后复盘」任务**整条链未执行** —— 实测该日产出：

```
✗ cache/eruption_history/eruption_2026-09-21.json
✗ cache/sector_flow_history/sector_flow_2026-09-21.json
✗ cache/newsflash_history/daily_2026-09-21.json
✗ cache/short_term/daily/emotion_2026-09-21.json
✗ cache/decision_log/2026-09-21/
✓ cache/zt_history/zt_2026-09-21.json      ← 仅此一项（另一任务补写）
```

而 `scripts/theme_rotation.py` 的**数据基准 = `eruption_history` 最新日** ⇒ 回流引擎
静默停更 **3 个交易日**，直到用户 09-22 问「题材回流这块数据怎么没有刷新」才暴露。

⛔ **根因不是脚本坏，而是「链路断裂是静默的」** —— 没有任何一处会因为
「今天该有的产出没出现」而告警。

## ⛔ 判定依据：产出物落盘，**不用** `system_health` 的 last

`scripts/system_health.py --json` 的 `automations.runs[].last` **实测陈旧不可信**
（2026-09-22 该字段显示「盘中快照-1000 = 08-31」而它当天 10:00 明明跑过、
「盘后复盘 = 08-28」而产出物已到 09-18）。故本守护一律**按文件落盘 + 内容 date** 判定。

## 行为

| 情况 | 动作 |
|---|---|
| 非交易日 | 静默退出（⛔ 不报） |
| 产出齐全**且均为盘后口径** | **静默退出**（全绿不刷屏） |
| 任一缺失 / 未盘后覆盖 | 推「⚠️ 盘后链未完成」卡到**测试群** |

## ⛔ 盘后口径判据（2026-09-22 二次立项·修补致命盲区）

**盲区实证**：原判据只查「文件存在 / `date` 字段 == today」。但 `eruption_history` /
`sector_flow_history` / `short_term/daily/emotion` / `theme_rotation.json` / `carrier_state.json`
**盘中也会被写**（盘中回流补跑：`capture_eruption()` + `capture_sector_flow()` 再 `theme_rotation.py`）。
⇒ 盘后链**整条没跑**，只要盘中顺手写过，本守护就认为「文件都在」→ **静默放行**。

实测 2026-09-22：五处产物 mtime 全为 `10:41~11:12`（盘中脏值），而 17:18 盘后链**未执行**
（无复盘卡推送、`card_<date>.json` 自 09-17 后连续 3 个交易日缺失），守护 17:50 **零告警**。

**新判据** = 这些产出必须在**当日 `EOD_COVER_HHMM` 之后**被覆盖过（默认 16:00：
已越过收盘 15:00 与 15:30 接力卡窗口，而盘后链法定时刻为 17:18）。
一句话：**「文件在」不等于「盘后链跑过」，必须看是否被收盘口径覆盖过。**

## 用法

```
python3 scripts/guard_eod_chain.py                 # 校验（缺失则推测试群）
python3 scripts/guard_eod_chain.py --dry-run        # 只打印，不推
python3 scripts/guard_eod_chain.py --date 2026-09-21   # 查历史某日（排障）
```

退出码：0 = 全绿或非交易日；1 = 有缺失（便于 CI / 手动串跑时判断）。
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
sys.path.insert(0, str(ROOT / "scripts"))

# 盘后口径时间线（HHMM）：产出 mtime 必须 ≥ 此值才算「被盘后链覆盖过」
EOD_COVER_HHMM = 1600


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ── 检查清单：`(名称, 相对路径模板, 影响)` ──
# ⚠️ 全部以**当日日期**命名（`{}` 为 `YYYY-MM-DD`）—— 它们是 `capture_daily.py`
#    逐日写出的产出，任一项缺失即代表盘后链没跑完。
#
# ⛔ 刻意**不含 `newsflash_history/daily_{}.json`**：`capture_newsflash` 在
#    `latest.json` 无快讯时**直接 return 0、不写文件**（实测 09-17 之后日常为空），
#    拿它当链路判据会**持续误报**。同理不含 promo/genes/regulatory（可空或按需）。
CHECKS = (
    ("题材爆发快照", "eruption_history/eruption_{}.json", "回流引擎的数据基准（缺它 = theme_rotation 停更）"),
    ("板块资金流", "sector_flow_history/sector_flow_{}.json", "回流判定「资金边际趋势」的地基"),
    ("情绪日快照", "short_term/daily/emotion_{}.json", "五日情绪汇总的结构化地基"),
)

# ── 带内容 date 的产出（文件名不含日期，只能读字段）──
DATED_JSON = (
    ("题材回流引擎", "theme_rotation.json", "date", "题材轮动/回流候选（/reflow、/rotation 页数据源）"),
)

# ── 按**盘后覆盖**判定的产出 ──
# ⚠️ `carrier_state.json` 的内容 `date` 是**涨停池日**（实测 09-22 跑出来
#    `date=2026-09-21`，因为它按 `zt_history` 最新日定基准）⇒ ⛔ **不能用
#    `date == today` 判定**（必然误报）。统一改走 `_cover_state` 的 mtime 判据。
#    （原「30 小时内写过」已废弃：它同样放行盘中写入。）
FRESH_FILES = (
    ("载体证伪", "carrier_state.json", None, "载体确认/证伪（盘后链后段 `carrier_track.py`）"),
)


def _cover_state(p: Path, today: str) -> tuple[bool, str]:
    """返回 `(是否已被盘后口径覆盖, 人读说明)`。

    判据：mtime 的**日期 == today** 且**时刻 ≥ `EOD_COVER_HHMM`**。
    ⛔ 只用 `exists()` 会漏掉「盘中写过、盘后链没跑」这一最危险的形态（文件看着齐、值全是盘中脏值）。
    """
    if not p.exists():
        return False, "文件不存在"
    mt = datetime.fromtimestamp(p.stat().st_mtime)
    stamp = mt.strftime("%H:%M")
    if mt.strftime("%Y-%m-%d") != today:
        return False, f"mtime={mt.strftime('%m-%d %H:%M')}（非当日）"
    if mt.hour * 100 + mt.minute < EOD_COVER_HHMM:
        return False, f"mtime={stamp}（盘中口径·未被盘后覆盖，需 ≥{EOD_COVER_HHMM // 100:02d}:00）"
    return True, stamp


def _missing_of(today: str, live: bool = True):
    """返回缺失/未覆盖项列表 `[(名称, 相对路径+说明, 影响)]`。

    `live=False`（即 `--date <过去某日>` 排障查询）：⛔ **跳过「只保留最新快照」的两类产出**
    （`theme_rotation.json` / `carrier_state.json` 无法回看历史，拿它们判过去某日必然误报）。
    此时判据只依赖按日期命名的三件套（eruption / sector_flow / emotion）——
    它们才是「那一天盘后链跑没跑」的可回溯证据。
    """
    missing = []
    for name, tmpl, impact in CHECKS:
        rel = tmpl.format(today)
        ok, why = _cover_state(CACHE / rel, today)
        if not ok:
            missing.append((name, f"{rel}（{why}）", impact))
    if not live:
        return missing
    for name, rel, key, impact in DATED_JSON:
        p = CACHE / rel
        got = ""
        if p.exists():
            try:
                got = str((json.loads(p.read_text(encoding="utf-8")) or {}).get(key) or "")
            except Exception:
                got = ""
        # ⚠️ 读了字段就必须与今日相等（「文件在、内容是昨天」= 同样危害，常见于链路半途而废）
        if got != today:
            missing.append((name, f"{rel}（date={got or '读取失败'}）", impact))
            continue
        ok, why = _cover_state(p, today)
        if not ok:
            missing.append((name, f"{rel}（date={got} 但 {why}）", impact))
    for name, rel, _hours, impact in FRESH_FILES:
        ok, why = _cover_state(CACHE / rel, today)
        if not ok:
            missing.append((name, f"{rel}（{why}）", impact))
    return missing


def _build_card(today: str, missing) -> dict:
    lines = "\n".join(f"· **{n}** → `{p}`\n　（{imp}）" for n, p, imp in missing)
    return {
        "title": f"⚠️ 盘后链未完成 · {today}",
        "color": "orange",
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**缺失产出** {len(missing)} 项：\n{lines}"}},
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        "**影响**：题材回流 / 复盘卡会取到**上一交易日**或**盘中脏值**（静默降级）。\n"
                        "⚠️ 标「盘中口径」者 = 文件在但**盘后链没覆盖它**（最常见形态：整条盘后链未执行）。\n"
                        "**补跑**：`python3 scripts/capture_daily.py && python3 scripts/theme_rotation.py`\n"
                        "⚠️ 仅**盘后**整脚本跑；盘中补跑只调 `capture_eruption()` + `capture_sector_flow()` "
                        "两环（否则 emotion/promo/genes 会抓到盘中脏值）。"
                    ),
                },
            },
        ],
        "note": "内部验证卡 · 落盘守护（非交易决策卡）",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="校验指定日期（默认今日 · 排障用）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不推卡")
    args = ap.parse_args()

    today = args.date or _today()
    live = args.date is None or args.date == _today()

    # 交易日守卫：复用 capture_daily 的既定实现（⛔ 不另写一套）
    try:
        from capture_daily import _guard_trading_day  # noqa: E402
        if args.date is None and not _guard_trading_day(today):
            print(f"[guard_eod_chain] {today} 非交易日，跳过")
            return 0
    except Exception as e:  # 守卫不可用时不阻断（宁可多查一次）
        print(f"[guard_eod_chain] 交易日守卫不可用（{e}），按交易日继续校验")

    if not live:
        print(f"[guard_eod_chain] --date {today} 为历史查询：跳过「只保留最新快照」的 "
              f"theme_rotation / carrier_state（无法回看，避免误报）")

    missing = _missing_of(today, live=live)
    if not missing:
        print(f"[guard_eod_chain] {today} 盘后链产出齐全 ✓（{len(CHECKS) + len(DATED_JSON)} 项）")
        return 0

    print(f"[guard_eod_chain] {today} 缺失 {len(missing)} 项：")
    for n, p, _ in missing:
        print(f"  ✗ {n}: {p}")

    if args.dry_run:
        print("[guard_eod_chain] --dry-run，不推卡")
        return 1

    card_path = CACHE / "guard_eod_chain_card.json"
    card_path.write_text(json.dumps(_build_card(today, missing), ensure_ascii=False), encoding="utf-8")
    # ⛔ 内部验证卡 → 必须显式走**测试群**（shared_rules 选群铁律）
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "push_card.py"), str(card_path),
         "--webhook", "feishu_webhook_test"],
        capture_output=True, text=True, timeout=90,
    )
    print((r.stdout or "").strip() or (r.stderr or "").strip()[:300])
    return 1


if __name__ == "__main__":
    sys.exit(main())
