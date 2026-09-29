#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""狸猫猎手 · 系统健康体检（客观数据层，不产出任何交易结论）

用法:
  python3 scripts/system_health.py            # 人类可读报告
  python3 scripts/system_health.py --json     # 结构化 JSON（供 财神-中枢 skill / 主会话路由）

体检五维（脚本给客观数据，AI 给路由判断——数据/AI 边界）:
  1. automation 任务台账: 仅统计有效任务(deleted_at IS NULL)；rrule 合规（铁律: FREQ=HOURLY 忽略 BYHOUR/BYMINUTE，盘中精确调度须 WEEKLY）
  2. launchd 守护: com.limao.* 注册 + 是否实际加载运行（未加载=8/13 式停摆风险，由 P0-3 兜底扫盘覆盖）
  3. cache 数据新鲜度: 按类别基准判定（post_close 读 date 字段 vs 最近交易日 / intraday 按今日刷新窗口）
  4. upgrade_plan 待办进度: 已落地表 ✅ 计数 + 待办表未落地清单
  5. 质量门: 调用 scripts/quality_gate.py（配置/任务/契约/单测四项，自动选可用解释器）
     —— 2026-09-02 补：此前质量门维度缺失，pytest/yaml 缺失类问题无法在体检中暴露。
     加 --skip-gate 可跳过（例行快速体检用）。

⚠️ 政策边界（2026-09-23 标注·不改行为）:
  本脚本第 1 维直接读 `~/.workbuddy/workbuddy.db` 的 automations 表。项目规则要求
  「自动化只通过平台管理接口查询，禁止直接访问其数据库」→ 因此**agent 不得把它当默认
  体检入口**（改用平台自动化接口 + 项目日志 + 文件业务日期分项核验，见
  `config/skill_contract.md`）。本脚本改由 `gen_optimization_plan.py`（周五优化方案卡）
  在任务内调用，属既有设计，未在本轮变更。
  另注：第 1 维的 runs[].last 已被 `guard_eod_chain.py` 判定为**陈旧不可信**，判断任务
  是否真跑过应以产出物落盘为准，不要引用该字段下结论。
"""
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path("/Users/zxl/WorkBuddy/狸猫猎手")
CACHE = ROOT / "cache"
DB = Path.home() / ".workbuddy" / "workbuddy.db"
UPGRADE = ROOT / "config" / "upgrade_plan.md"

# 核心数据文件 → (类别, 期望基准说明)
CORE_FILES = {
    "yesterday_zt.json": ("post_close", "最近交易日盘后(record_zt_snapshot 沉淀)"),
    "theme_rotation.json": ("post_close", "最近交易日盘后(capture_daily→theme_rotation 沉淀)"),
    "theme_series.json": ("post_close", "最近交易日盘后"),
    "feedback_matrix.json": ("intraday", "盘中守护/猎手刷新；周末为 non_trading_day 守卫态"),
    "pulse_snapshots/latest.json": ("intraday", "盘中守护(pulse_fetcher)/猎手刷新"),
    "ma5_status.json": ("intraday", "盘中守护刷新"),
    "pulse_buffer.json": ("intraday", "盘中聚合缓冲"),
}


def last_trading_day(now: datetime):
    wd = now.weekday()
    if wd >= 5:  # 周六(5)/周日(6) → 上一周五
        return (now - timedelta(days=wd - 4)).date()
    return now.date()


def prev_trading_day(d: datetime.date):
    """d 的前一个交易日（跳过周末）。"""
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def is_trading_hours(now: datetime) -> bool:
    """交易时段内（周一~五 09:25–15:00）。"""
    return now.weekday() < 5 and (now.hour > 9 or (now.hour == 9 and now.minute >= 25)) and (
        now.hour < 15 or (now.hour == 15 and now.minute == 0)
    )


def check_automations():
    """读 automation SQLite（只读）——仅有效任务(deleted_at IS NULL)。"""
    out = {"total": 0, "active": 0, "paused": [], "rrule_violations": []}
    if not DB.exists():
        out["error"] = f"automation DB 不存在: {DB}"
        return out
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id,name,rrule,status,schedule_type,last_run_at,next_run_at "
            "FROM automations WHERE deleted_at IS NULL ORDER BY name"
        ).fetchall()
        out["total"] = len(rows)
        for r in rows:
            if r["status"] == "ACTIVE":
                out["active"] += 1
            else:
                out["paused"].append({"id": r["id"], "name": r["name"], "status": r["status"]})
            rrule = (r["rrule"] or "").upper()
            if r["status"] == "ACTIVE" and "FREQ=HOURLY" in rrule and ("BYHOUR" in rrule or "BYMINUTE" in rrule):
                out["rrule_violations"].append({
                    "id": r["id"], "name": r["name"], "rrule": r["rrule"],
                    "note": "铁律: FREQ=HOURLY 忽略 BYHOUR/BYMINUTE（BYMINUTE 精确须 FREQ=WEEKLY）",
                })
        # 最近运行时间（方便看"某任务上次是否跑过"）
        out["runs"] = [
            {"id": r["id"], "name": r["name"], "last": r["last_run_at"], "next": r["next_run_at"]}
            for r in rows if r["last_run_at"] or r["next_run_at"]
        ]
        con.close()
    except Exception as e:
        out["error"] = f"读 automation DB 失败: {e}"
    return out


def check_launchd():
    out = {"registered": [], "running": []}
    la = Path.home() / "Library" / "LaunchAgents"
    plists = sorted(la.glob("com.limao*.plist"))
    out["registered"] = [p.name for p in plists]
    try:
        res = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10)
        loaded = res.stdout.lower()
        for p in plists:
            label = p.stem  # com.limao.pulse-fetcher
            if label.lower() in loaded:
                out["running"].append(label)
            else:
                out["running"].append(f"{label} ⚠️未加载")
    except Exception as e:
        out["error"] = f"launchctl list 失败: {e}"
    return out


def _json_date(path: Path) -> str | None:
    """读 JSON 顶层 date 字段（theme_rotation/yesterday_zt 等）。"""
    try:
        d = json.loads(path.read_text())
        return str(d.get("date") or d.get("date_raw") or "")
    except Exception:
        return None


def check_cache_freshness(now: datetime):
    ltd = last_trading_day(now)
    ltd_close = datetime(ltd.year, ltd.month, ltd.day, 16, 56)  # 盘后沉淀基准时刻
    today_925 = datetime(now.year, now.month, now.day, 9, 25)   # 今日首个盘中任务时刻
    out = {"items": [], "issues": []}
    for name, (cat, note) in CORE_FILES.items():
        p = CACHE / name
        if not p.exists():
            out["issues"].append(f"❌ 缺失: {name}")
            out["items"].append({"file": name, "status": "missing", "note": note})
            continue
        mtime = datetime.fromtimestamp(p.stat().st_mtime)
        h = round((now - mtime).total_seconds() / 3600, 1)
        status, issue = "ok", ""
        if cat == "post_close":
            dstr = _json_date(p)
            fdate = dstr[:10] if dstr else ""
            if fdate == str(ltd):
                pass  # 数据即最近交易日 → ok
            elif fdate == str(prev_trading_day(ltd)) and now < datetime(ltd.year, ltd.month, ltd.day, 17):
                pass  # 今日盘后链(17:00)未跑，持有昨日沉淀属正常
            elif not fdate:
                # 无 date 字段（如 theme_series）→ 退化按 mtime：>= 上一交易日 16:56 即 ok
                prev_close = datetime(prev_trading_day(ltd).year, prev_trading_day(ltd).month,
                                      prev_trading_day(ltd).day, 16, 56)
                if p.stat().st_mtime >= prev_close.timestamp():
                    pass
                else:
                    status = "stale"
                    issue = f"mtime 早于上一交易日 {prev_trading_day(ltd)} 16:56"
            else:
                status = "stale"
                issue = f"数据 date={fdate or '?'} 早于最近交易日 {ltd}（盘后链可能连续停摆）"
        elif cat == "intraday":
            if is_trading_hours(now) and mtime < today_925:
                status = "stale"
                issue = "交易时段内今日尚未刷新（守护未跑？pulse_fetcher 未执行？）"
        out["items"].append({"file": name, "status": status, "mtime": mtime.strftime("%m-%d %H:%M"),
                             "age_h": h, "note": note, "issue": issue})
        if status == "stale":
            out["issues"].append(f"⚠️ 陈旧: {name} ({issue})")
    # feedback_matrix error 标记
    fm = CACHE / "feedback_matrix.json"
    if fm.exists():
        try:
            d = json.loads(fm.read_text())
            err = d.get("error")
            if err:
                out["feedback_error"] = err
                out["issues"].append(f"ℹ️ feedback_matrix 守卫态: {err}（按守卫规则处理，非故障）")
        except Exception:
            pass
    # 当日情绪快照
    today_mem = CACHE / f"market_memory_{now.strftime('%Y-%m-%d')}.json"
    out["today_sentiment_file"] = {
        "exists": today_mem.exists(),
        "note": "今日情绪快照（memory_lib record --sentiment 写回）" if today_mem.exists()
        else "今日尚未写回（当日首卡后才有）",
    }
    return out


def check_upgrade_todo():
    out = {"done": [], "todo": [], "done_count": 0}
    if not UPGRADE.exists():
        out["error"] = f"upgrade_plan.md 不存在: {UPGRADE}"
        return out
    txt = UPGRADE.read_text()
    # 已落地表（第二章·四列，末列含 ✅）
    done_pat = re.compile(r"\|\s*(P\d+-\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*✅\s*\|")
    seen = set()
    for m in done_pat.finditer(txt):
        if m.group(1) in seen:
            continue
        seen.add(m.group(1))
        out["done"].append({"id": m.group(1), "title": m.group(2).strip()})
    # 待办表（第三章·三列：P | 标题 | 要点）
    todo_pat = re.compile(r"\|\s*(P\d+-\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")
    seen2 = set()
    for m in todo_pat.finditer(txt):
        if m.group(1) in seen or m.group(1) in seen2:
            continue
        seen2.add(m.group(1))
        out["todo"].append({"id": m.group(1), "title": m.group(2).strip(),
                            "status": m.group(3).strip()[:60]})
    out["done_count"] = len(out["done"])
    return out


def _rrule_trigger(rrule: str):
    """WEEKLY rrule 取 BYHOUR/BYMINUTE 首值 → (h, m) 或 None。"""
    mh = re.search(r"BYHOUR=([\d,]+)", rrule or "")
    mm = re.search(r"BYMINUTE=([\d,]+)", rrule or "")
    if mh and mm:
        return int(mh.group(1).split(",")[0]), int(mm.group(1).split(",")[0])
    return None


def check_run_health(now: datetime):
    """运行健康度（第 5 维）：ACTIVE 任务最近成功运行 vs 预期频率。
    日频任务（交易日每天应跑）容忍跨周末 3 天；今日未到触发点的任务不判超期；
    超期（今日已过触发点仍 >3 天）或零记录 → 预警。
    """
    out = {"stale": [], "never": []}
    if not DB.exists():
        out["error"] = f"automation DB 不存在: {DB}"
        return out
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id,name,rrule,status,last_run_at,created_at,next_run_at FROM automations "
            "WHERE deleted_at IS NULL AND status='ACTIVE'"
        ).fetchall()
        runs = {
            r["automation_id"]: r["last_ok"]
            for r in con.execute(
                "SELECT automation_id, MAX(created_at) as last_ok FROM automation_runs "
                "WHERE result_success=1 GROUP BY automation_id"
            )
        }
        con.close()
        today_trigger_passed = False  # 今天是否已过触发点
        for r in rows:
            lr = r["last_run_at"]
            ref = runs.get(r["id"]) or lr
            if ref is None:
                # 零记录：已排期首次触发(next_run_at 在未来) → 待首次，非异常；已过排期仍零记录 → 真异常
                nxt = r["next_run_at"]
                if nxt and nxt / 1000 > now.timestamp():
                    out["never"].append({"id": r["id"], "name": r["name"], "rrule": r["rrule"],
                                         "note": f"待首次触发（已排期 {datetime.fromtimestamp(nxt / 1000).strftime('%m-%d %H:%M')}，非异常）"})
                else:
                    out["never"].append({"id": r["id"], "name": r["name"], "rrule": r["rrule"],
                                         "note": "已过排期仍零运行记录（调度可能未生效，需核查）"})
                continue
            age_days = (now - datetime.fromtimestamp(ref / 1000)).total_seconds() / 86400
            trig = _rrule_trigger(r["rrule"])
            not_yet = False
            if trig and now.weekday() < 5:
                # 交易日且未到今日触发点 → 今天还没轮到，不判超期
                if (now.hour, now.minute) < trig:
                    not_yet = True
            if not not_yet and age_days > 3:
                out["stale"].append({
                    "id": r["id"], "name": r["name"], "rrule": r["rrule"],
                    "last_run": datetime.fromtimestamp(ref / 1000).strftime("%m-%d %H:%M"),
                    "age_days": round(age_days, 1),
                    "note": "ACTIVE 但超 3 天未成功运行（可能 rrule 配置漂移 / 调度未生效）",
                })
    except Exception as e:
        out["error"] = f"读运行记录失败: {e}"
    return out


def check_quality_gate(timeout: int = 900) -> dict:
    """质量门（2026-09-02 新增第 5 维）：委托 scripts/quality_gate.py 统一执行。
    quality_gate 会自行挑选装了 PyYAML/pytest 的解释器，避免系统 python3 缺依赖误报。"""
    gate = ROOT / "scripts" / "quality_gate.py"
    if not gate.exists():
        return {"all_pass": False, "error": "scripts/quality_gate.py 缺失", "results": []}
    try:
        r = subprocess.run([sys.executable, str(gate), "--json"], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=timeout)
        raw = (r.stdout or "").strip()
        if not raw:
            return {"all_pass": False, "error": f"质量门无输出(exit={r.returncode})",
                    "stderr": (r.stderr or "")[-500:], "results": []}
        data = json.loads(raw)
        data["exit"] = r.returncode
        return data
    except subprocess.TimeoutExpired:
        return {"all_pass": False, "error": f"质量门超时(>{timeout}s)", "results": []}
    except Exception as e:
        return {"all_pass": False, "error": f"质量门执行异常: {e}", "results": []}


def main():
    now = datetime.now()
    rep = {
        "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()],
        "last_trading_day": str(last_trading_day(now)),
        "automations": check_automations(),
        "run_health": check_run_health(now),
        "launchd": check_launchd(),
        "cache": check_cache_freshness(now),
        "upgrade_todo": check_upgrade_todo(),
    }
    if "--skip-gate" not in sys.argv:
        rep["quality_gate"] = check_quality_gate()
    if "--json" in sys.argv:
        print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
        return
    print(f"== 狸猫猎手系统体检 {rep['ts']}（{rep['weekday']}·最近交易日 {rep['last_trading_day']}）==")
    a = rep["automations"]
    print(f"\n[1] 自动化任务: {a.get('active')}/{a.get('total')} ACTIVE（有效任务）| PAUSED: "
          f"{[x['name'] for x in a.get('paused', [])] or '无'}")
    for v in a.get("rrule_violations", []):
        print(f"  ⚠️ rrule 违规: {v['name']} → {v['note']}")
    if a.get("error"):
        print(f"  ❌ {a['error']}")
    rh = rep["run_health"]
    if rh.get("never") or rh.get("stale"):
        print(f"\n[1.5] 运行健康度: 零记录 {len(rh.get('never', []))} | 超期未跑 {len(rh.get('stale', []))}")
        for x in rh.get("never", []):
            print(f"  ⚠️ 零记录: {x['name']} → {x.get('note','')}")
        for x in rh.get("stale", []):
            print(f"  ⚠️ 超期未跑: {x['name']} 上次成功 {x['last_run']}（{x['age_days']}天前）→ {x['note']}")
    elif rh.get("error"):
        print(f"\n[1.5] 运行健康度: ❌ {rh['error']}")
    else:
        print("\n[1.5] 运行健康度: 全部 ACTIVE 任务按预期运行 ✅")
    l = rep["launchd"]
    print(f"\n[2] launchd 守护: 注册 {l.get('registered')} | 运行: {l.get('running')}")
    if any("未加载" in str(x) for x in l.get("running", [])):
        print("  ⚠️ 守护未加载 → 盘中数据靠兜底扫盘(P0-3) + 11猎手任务刷新")
    c = rep["cache"]
    print(f"\n[3] 数据新鲜度: 今日情绪快照={'✅' if c['today_sentiment_file']['exists'] else '❌ 未写回'}")
    for it in c["items"]:
        flag = "✅" if it["status"] == "ok" else "❌"
        print(f"  {flag} {it['file']} ({it['mtime']} · {it['age_h']}h) {it['issue']}")
    for i in c.get("issues", []):
        print(f"  {i}")
    u = rep["upgrade_todo"]
    print(f"\n[4] 升级待办: 已落地 {u.get('done_count')} | 未落地 {len(u.get('todo', []))}")
    for t in u.get("todo", []):
        print(f"  ⏳ {t['id']} {t['title']}")
    q = rep.get("quality_gate")
    if q is None:
        print("\n[5] 质量门: 已跳过（--skip-gate）")
    elif q.get("error"):
        print(f"\n[5] 质量门: ❌ {q['error']}")
    else:
        print(f"\n[5] 质量门: {'✅ 全部通过' if q.get('all_pass') else '❌ 存在失败项'}（解释器 {q.get('python')}）")
        for r in q.get("results", []):
            flag = "✅" if r["ok"] else "❌"
            print(f"  {flag} {r['name']}（exit={r['exit']}）")
            if not r["ok"]:
                for ln in r.get("detail", [])[:4]:
                    print(f"     {ln}")


if __name__ == "__main__":
    main()
