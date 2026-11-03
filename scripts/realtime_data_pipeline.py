#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实时数据层：采集、计算并原子发布数据就绪标记；不发送任何消息。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
PULSE = CACHE / "pulse_snapshots"
READY = PULSE / "data_ready.json"

# 规范时间戳（2026-10-14 审计 P1-⑦）：data_ready.generated_ts 是 link_guard 的断档判据，
# 统一带 +08:00（原 `.astimezone()` 形态依赖本机时区，换机即漂移）。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from common.market_time import iso_bj  # noqa: E402

STEPS = (
    ("intraday_pulse", ("scripts/intraday_pulse.py", "--mode", "intra", "--json-only", "--force"), "intraday.log"),
    ("gen_candidates", ("scripts/gen_candidates.py",), "candidates.log"),
    ("feedback_matrix", ("scripts/feedback_matrix.py",), "feedback.log"),
    # 卖出侧峰值采集：只记录封单/板块家数的当日峰值，不参与就绪判据、不发卡、不裁决。
    # 必须排在 feedback 后，封单仅存在于 feedback.quotes。
    ("intraday_exit_peaks", ("scripts/intraday_exit_peaks.py",), "exit_peaks.log"),
    # 赚钱/亏钱路径归因（EffectEngine §3.1b）：依赖上一行 feedback.quotes，必须排在其后。
    # ⛔ 旁路观测，不进 _compute_ready 的 required 集合，失败不影响数据就绪。
    ("effect_paths", ("scripts/effect_paths.py",), "effect_paths.log"),
    ("ma5_check", ("scripts/ma5_check.py",), "ma5.log"),
    ("memory_detect", ("scripts/memory_lib.py", "detect"), "detect.log"),
    ("intraday_timeline", ("scripts/intraday_timeline.py",), "timeline.log"),
    ("realtime_analytics", ("scripts/realtime_analytics.py",), "analytics.log"),
)


def _publish_theme_series() -> int:
    """发布题材序列及当日盘中时间点；展示端只读取已发布 JSON。"""
    try:
        # 本脚本以 `scripts/realtime_data_pipeline.py` 启动时 sys.path[0]=scripts/，
        # 不含仓库根 → `import common.*` 会 ModuleNotFoundError（2026-09-30 事故）。
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from common.theme_series import export_series_json, record_intraday_point

        record_intraday_point()
        export_series_json()
        return 0
    except Exception as exc:  # noqa: BLE001 — 单独记入数据就绪状态，禁止旧序列冒充新产物
        print(f"[theme-series] publish failed: {exc}", file=sys.stderr)
        return 1


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _write_ready(value: dict) -> None:
    tmp = READY.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, READY)


def _consistency_error(*, latest: dict, feedback: dict, zt_history: dict) -> str | None:
    """校验同一交易日的涨停池计数，阻止不同来源静默混用。"""
    if not latest or not feedback or not zt_history:
        return None
    # 盘前/接口异常标记没有今日涨停集合，不能拿空字段与上一交易日归档对账。
    if feedback.get("error"):
        return None
    dates = {str(x.get("date") or "")[:10] for x in (feedback, zt_history) if x}
    source_ts = str(latest.get("ts") or "")[:10]
    if len(dates) == 1 and source_ts and source_ts not in dates:
        return f"日期不一致 latest={source_ts}, feedback/zt={next(iter(dates))}"
    code_count = len(feedback.get("today_zt_codes") or [])
    feedback_count = feedback.get("now_zt_count")
    zt_count = zt_history.get("zt_count")
    counts = {int(x) for x in (feedback_count, zt_count, code_count) if isinstance(x, (int, float))}
    if len(counts) > 1:
        return f"涨停计数不一致 feedback={feedback_count}, codes={code_count}, zt_history={zt_count}"
    return None


def _compute_ready(*, results: dict, feedback_error, rotation_date, consistency_error=None) -> tuple[bool, str]:
    """计算数据是否就绪（纯函数，便于回归测试）。

    ⛔ 2026-10-08 修复前：`same_source` 拿 `rotation.source_ts` 与每 30s 刷新的
       `latest.ts` 做**精确相等**比较——两者由不同进程（realtime_analytics / pulse_fetcher）
       按不同节奏写，存在天然竞态，实际几乎永远不相等 → `ready` 永远 False → 守卫每个
       交易日误报「盘面数据断了」。

    现改为：
      · `same_source` 用**日期级**新鲜度：题材轮动引擎已产出今日快照（date==今日）即视为
        同步，与盘中实时时间戳解耦，彻底消除竞态。
      · feedback_matrix 在 09:26 前盘前刻意写 `error="pre_auction"`（防未开盘假象），属
        **良性盘前标记**而非数据断档；只有真实错误才阻断就绪。
    """
    required_ok = all(
        results.get(label) == 0
        for label in ("intraday_pulse", "feedback_matrix", "realtime_analytics", "theme_series")
    )
    same_source = bool(rotation_date == datetime.now().strftime("%Y-%m-%d"))
    feedback_ok = not (feedback_error and feedback_error != "pre_auction")
    consistency_ok = not consistency_error
    ready = bool(required_ok and same_source and feedback_ok and consistency_ok)
    if ready:
        reason = ""
    elif not required_ok:
        reason = "关键实时计算步骤未全部完成"
    elif not same_source:
        reason = f"题材轮动引擎今日快照缺失(date={rotation_date})"
    elif not feedback_ok:
        reason = f"反馈矩阵报错：{feedback_error}"
    elif not consistency_ok:
        reason = f"实时数据口径不一致：{consistency_error}"
    else:
        reason = "关键实时数据未完成"
    return ready, reason


def _run(label: str, args: tuple[str, ...], log_name: str) -> int:
    log = PULSE / log_name
    with log.open("a", encoding="utf-8") as out:
        out.write(f"[{datetime.now().strftime('%H:%M:%S')}] >>> {label}\n")
        return subprocess.run([sys.executable, *args], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT).returncode


def main() -> None:
    PULSE.mkdir(parents=True, exist_ok=True)
    results = {label: _run(label, args, log) for label, args, log in STEPS}
    results["theme_series"] = _publish_theme_series()
    latest = _json(PULSE / "latest.json")
    feedback = _json(CACHE / "feedback_matrix.json")
    rotation = _json(CACHE / "theme_rotation.json")
    source_ts = latest.get("ts")
    zt_history = _json(CACHE / "zt_history" / f"zt_{str(source_ts or '')[:10]}.json")
    consistency_error = _consistency_error(
        latest=latest,
        feedback=feedback,
        zt_history=zt_history,
    )
    ready, reason = _compute_ready(
        results=results,
        feedback_error=feedback.get("error"),
        rotation_date=rotation.get("date"),
        consistency_error=consistency_error,
    )
    # 推送只认这份标记；任一步关键计算失败，保留原因而不把旧数据当作新数据推送。
    _write_ready({
        "ready": ready,
        "date": str(source_ts or "")[:10],
        "source_ts": source_ts,
        "generated_ts": iso_bj(),
        "steps": results,
        "reason": reason,
    })
    print(f"[realtime-data] ready={ready} source={source_ts} steps={results}")


if __name__ == "__main__":
    main()
