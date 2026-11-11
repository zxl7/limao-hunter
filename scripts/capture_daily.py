#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按天持久化：题材爆发快照 + 涨停池历史（题材轮动回流预测的地基）。

盘后 17:00 调用（也可手动 `python3 scripts/capture_daily.py`）。
- 爆发快照：抓 getFryTomorrowList(题材名+涨停数+涨幅+催化标题) + 每题材 getStockList(成分股+龙头)
  → cache/eruption_history/eruption_YYYY-MM-DD.json
- 涨停池：复制 cache/yesterday_zt.json（含每票 sector 板块）→ cache/zt_history/zt_{其date}.json
- 板块资金流：抓东财 f62 全量板块（build_sector_flow_snapshot）→ cache/sector_flow_history/sector_flow_YYYY-MM-DD.json
  （回流趋势地基：支撑 theme_rotation「资金边际趋势」判定）
- 情绪数值快照：读 cache/feedback_matrix.json 数值 → cache/short_term/daily/emotion_YYYY-MM-DD.json
  （结构化情绪历史，供 gen_sentiment_summary 做 5 日汇总，2026-08-16 加）
- 新闻归档：读 cache/pulse_snapshots/latest.json 的 newsflash → cache/newsflash_history/daily_YYYY-MM-DD.json
  （外部新闻维度沉淀，供 5 日汇总/复盘引用，2026-08-16 加）
- 自动清理：涨停池（主+镜像）/爆发快照/资金流 保留最近 RETAIN_DAYS(40) 个交易日，短期情绪日志保留最近 5 个交易日（按文件名日期判定，交易日计数天然跳过周末）
- 交易日守卫：main() 先读 feedback_matrix.json 的 error 标记（non_trading_day / zt_pool_unreliable → 非交易日或数据不可靠，跳过归档，防周末假数据污染 5 日窗口）

零新增数据源：复用 intraday_pulse 的 http_post/rc/ts/parse_tomorrow/parse_theme_stocks/build_sector_flow_snapshot。
"""
import sys
import os
import json
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                 # import scripts.intraday_pulse
sys.path.insert(0, str(ROOT / "scripts"))     # import intraday_pulse

from common.market_time import iso_bj  # noqa: E402  规范时间戳（ISO8601 +08:00·2026-10-14 审计 P1-⑦）

CACHE = ROOT / "cache"
ERUPT_DIR = CACHE / "eruption_history"
ZT_DIR = CACHE / "zt_history"
# 归档**镜像目录**（双目录同写·2026-09-18）：题材沉淀副本，消费方在 wealth_code 侧。
# ⛔ 写入一律走 `_archive_dirs()` + `zt_archive.write_archives`，不得各自拼路径。
ZT_MIRROR_DIR = ROOT / "wealth_code" / "data" / "zt_history"
FLOW_DIR = CACHE / "sector_flow_history"   # 板块资金流日级时间序列（回流趋势用）
NEWSFLASH_DIR = CACHE / "newsflash_history"  # 外部新闻（快讯）日归档
# 涨停池/爆发快照/资金流保留交易日数（供给）。
# ⚠️【耦合铁律 · 2026-09-12 N1 修复 → 2026-10-14 审计 P1-⑤ 二次扩容】本值必须 ≥
#    全部消费方的回看窗口上限（需求）。当前**最大需求 = 30**（leader_cycle.HISTORY_DAYS）：
#      scripts/live_signal.py            N_SHAPE_HIST_DAYS       = 12（第一波/N字/二波）
#      scripts/theme_rotation.py         REENTRY_LOOKBACK_LONG   = 15（回流确认长回看）
#      scripts/theme_rotation.py         TRADING_LOOKBACK        =  5（5 日轮动窗）
#      wealth_code/core/leader_cycle.py  HISTORY_DAYS            = 30 ← 最大（读 cache/zt_history + 新闻）
#      wealth_code/core/defense_gate.py  POSITION_LOOKBACK_DAYS  = 20（位置闸·零缓冲）
#      wealth_code/core/ambush_pullback.py LOOKBACK_DAYS         =  5
#      wealth_code/core/reflow_scan.py   REFLOW_LOOKBACK_DAYS    =  5
#      wealth_code/core/pool_call.py     LINE_CORE_LOOKBACK_DAYS =  4
#    变更史：原值 5 < 需求 12 → 盘中形态检测长期缺 7 天料（静默降级）；
#            20 → 对 30 仍差 10 天（leader_cycle 读满时**静默少料**，无告警）。
#    ⛔ 改任一回看窗口必须同步本值 + tests/test_history_window.py 的 WINDOW_CONSUMERS 表。
RETAIN_DAYS = 40          # 40 交易日（≈2 个月）·覆盖 30 天最大需求 + 10 天缓冲
SHORT_TERM_RETAIN = 5     # 短期情绪 daily 日志 保留天数
DAILY_DIR = CACHE / "short_term" / "daily"
TOP_THEMES = 10          # 每天最多存前 10 个爆发题材
STOCKS_PER_THEME = 8     # 每题材最多存前 8 只成分股

# ── D2 钩子开关（2026-10-10 批·可逆）─────────────────────────────────
# False = 不挂 sector_official --build（下游 lookup 仍读旧映射表，行为等同改动前）。
# 盘后链末步增量刷新「选股宝板块 → 东财官方板块」映射（只读 top5 + 本地缓存，
# 仅查新 code 才联网，失败不影响主链路）。
ENABLE_SECTOR_OFFICIAL_REFRESH = True

# ── 机制4 钩子开关（2026-10-12 传媒事故「哨兵」·可逆）─────────────────────
# 盘后链末步跑题材归并巡检（只读）：高置信「跨大类错配」→ 推异动群告警 + 落归并快照。
# False = 不跑（行为等同改动前）。
ENABLE_THEME_LINE_AUDIT = True

# ── 题材长期归档 钩子开关（2026-10-13 用户「题材是可以积累和沉淀的毕竟有上限」·可逆）──
# 盘后链末步把「选股宝 top5」逐日落成 cache/top5_history/（原始快照 + 题材库索引）。
# 动机：market_memory 只是当日快照、历史被覆盖（实测只剩 7 天）→「前期的热门题材」
#       这类跨日口径没法长窗验证。False = 不归档（行为等同改动前）。
ENABLE_TOP5_HISTORY = True

# ── 数据源聚合守门 钩子开关（2026-11-04 用户「源必须聚合」·可逆）─────────────
# 盘后链末步跑 `snapshot_build.py --guard`：补全 4 个只有构建器会写的 section
# （market/themes/ladder/lines）→ 一致性校验 → 落 health section。
# ⛔ 只告警不阻断；环境变量 ENABLE_SNAPSHOT_GUARD=0 / 本常量 False = 不挂（行为等同改动前）。
# 与 core.snapshot.GUARD_ENV 同一个环境变量，避免两处开关打架。
ENABLE_SNAPSHOT_GUARD = os.environ.get("ENABLE_SNAPSHOT_GUARD", "1") not in ("0", "false", "False", "")

# ── 归档日集合差对账窗口（2026-10-14 审计 P1-④）────────────────────────────
# 对账「最近 N 个**参考日**」内 flow / eruption 相对 zt 的缺口。
# N 取 10：足够暴露「最近一次断链」，又不把 9 月的远古缺日天天重播成噪声。
ARCHIVE_AUDIT_WINDOW = 10

# ── 脉冲窗口存档保留期（2026-10-14 审计 P2-⑪）────────────────────────────
# 背景：cache/pulse_windows/ 长期「无清理」式增长（实测 232 个 window_*.json +
#   450 个 .closed_* 防重标记 = 682 项），产侧自 2026-09-03 起停用
#   （pulse_fetcher.sh 的 close_window_step 因 HIGH_FREQ_INTRADAY=True 被注释；
#    回滚 = 取消注释那一行）。
# ⚠️ 但该目录**不是无主目录**，故**只加保留期、不删目录**：
#   ① 狸猫终端 `OpsPage` 经 routes.ts `/api/pulse/windows` 读取 window_*.json 存档
#      （该路由自带 limit=40，故保留 60 份足够覆盖 UI 展示 + 回看缓冲）；
#   ② pulse_fetcher.sh 仍会在该目录落 `.closed_*` 防重标记（保留 10 天足够，
#      标记语义是「当日窗口已闭合」，跨日即失效）。
PULSE_WINDOW_DIR = CACHE / "pulse_windows"
PULSE_WINDOW_RETAIN = 60       # window_*.json 保留份数（≥ 前端路由 limit 40）
PULSE_MARK_RETAIN_DAYS = 10    # .closed_* 防重标记保留天数


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _archive_dirs() -> tuple[Path, ...]:
    """涨停池归档目录（主 + 镜像）。调用时求值 → 测试注入 `ZT_DIR`/`ZT_MIRROR_DIR` 生效。"""
    return (ZT_DIR, ZT_MIRROR_DIR)


def _guard_trading_day(today: str) -> bool:
    """交易日守卫：**日历硬门禁** + feedback_matrix 数据可靠性标记（双重）。

    ⛔ 2026-10-09 修正：原实现只读 feedback_matrix 的 error 标记，而 feedback_matrix
    当时的交易日判定仅判 `weekday()` → **工作日休市**（2026-09-25 中秋）漏网：那天数据源
    回吐 09-24 的完整池（非空、且未触发自交守卫）→ 无 error 标记 → 本守卫放行 → 归档出
    与 09-24 **逐字段相同**的幽灵日。故把日历判定**前置**，不再依赖上游是否识别出节假日。
    """
    # ① 日历硬门禁（唯一真源·含法定休市日与周末）
    try:
        from common.trading_calendar import is_a_share_trading_day, uncovered_years
        # ①b 覆盖度告警（2026-10-14 审计 R1）：休市表缺下一年度 ⇒ 元旦/春节会被判成交易日。
        #     ⛔ 只打印、**不阻断**（若按「未覆盖=休市」处理，日历维护滞后会连续拦掉整年真实交易日，
        #     伤害远大于幽灵日）。跨年提前量由 scripts/guard_eod_chain.py 的日历守卫负责推卡。
        try:
            _gaps = uncovered_years([datetime.now().year])
            if _gaps:
                print(f"[capture_daily] ⚠️ 交易日历未覆盖 {_gaps} 年（唯一真源 common/trading_calendar"
                      " 的休市表只到 2026）→ 该年节假日会被判为交易日，需国办公布后补录")
        except Exception:  # pragma: no cover - 覆盖度查询失败不影响主门禁
            pass
        if not is_a_share_trading_day(today):
            print(f"[capture_daily] {today} 非A股交易日（交易日历），跳过归档")
            return False
    except Exception:  # pragma: no cover - 极端环境退回下游守卫
        pass
    # ② 数据可靠性守卫（feedback_matrix 的 non_trading_day / zt_pool_unreliable）
    fb = CACHE / "feedback_matrix.json"
    if fb.exists():
        try:
            err = (json.loads(fb.read_text(encoding="utf-8")) or {}).get("error") or ""
            if "non_trading_day" in err or "zt_pool_unreliable" in err:
                print(f"[capture_daily] {today} 非交易日/数据不可靠（feedback error: {err}），跳过归档")
                return False
        except Exception:
            pass
    if datetime.now().weekday() >= 5:  # trading-day-fallback: 唯一真源不可用时的周末兜底
        print(f"[capture_daily] {today} 周末，跳过归档")
        return False
    return True


def capture_eruption():
    """抓当日题材爆发快照（题材 + 催化标题 + 成分股 + 龙头标记）。"""
    import intraday_pulse as ip

    def _post(path, args, client="wap"):
        return ip.http_post(f"{ip.EM_BASE}/{path}", {
            "args": args,
            "client": client, "clientType": "cfw",
            "clientVersion": "9001" if client == "wap" else "8.3",
            "randomCode": ip.rc(), "timestamp": ip.ts(),
        })

    raw = _post("getFryTomorrowList", {"pageSize": 3, "lastTradeDate": ""})
    themes = ip.parse_tomorrow(raw or {})            # 已按 zt 倒序、去重、isHot==1
    out = []
    for t in themes[:TOP_THEMES]:
        code = t.get("themeCode")
        stocks = []
        if code:
            try:
                sr = _post("getStockList", {
                    "themeCode": code, "pageSize": 1000,
                    "pageNum": 1, "sort": -1, "sortField": "f3",
                }, client="web")
                stocks = ip.parse_theme_stocks(sr)[:STOCKS_PER_THEME]
            except Exception:
                stocks = []
        out.append({
            "name": t.get("name"), "themeCode": code,
            "zt": t.get("zt"), "pct": t.get("pct"),
            "cum_pct": t.get("cum_pct"), "title": t.get("title", ""),
            "stocks": stocks,
        })
    return out


def _ensure_pool_fresh(today: str, now_hour: int | None = None) -> str:
    """确保 cache/yesterday_zt.json 是「今日」口径——盘后链时序缺陷的**脚本级根治**（2026-09-15）。

    ⛔ 事故（09-04 / 09-07 / 09-11 三日实证，09-14 复核）：盘后链里 `capture_daily` 若
    早于 `record_zt_snapshot` 执行，`yesterday_zt.json` 仍是**前一交易日**的 → 双重损坏：
      ① 当日 `zt_history/zt_{今日}.json` 归档缺失 → 形态/N字/回流检测**永久少一天料**；
      ② 反而把**前一交易日**的归档文件用旧数据重写一遍。
    旧处置分别依赖「automation 执行次序写对」（脆弱）与「事后 `_heal_residual_archives`
    自愈」（受 heal_date 匹配 + limit 限制）。本函数把自愈**前置到源头**：
    发现口径日 ≠ 今日即主动跑一次上游 `record_zt_snapshot`（幂等），使本流程与调用次序解耦。

    安全边界（防误伤）：
      · **15:00 前不主动补齐**——盘前/盘中 yesterday_zt 停在前一交易日是**正常态**，
        此时跑上游会抓到不完整的盘中涨停池（数据污染）；
      · 补齐失败不影响主流程（返回描述串，由调用方打印，原逻辑继续走
        `_heal_residual_archives` 兜底）；
      · 顺序正确时（date == today）**零额外开销**，直接返回 "ok"。

    返回：动作描述（"ok" / "filled(...)" / "fill-failed(...)" / "stale-before-close(...)"）。
    """
    src = CACHE / "yesterday_zt.json"

    def _cur_date():
        try:
            return json.loads(src.read_text(encoding="utf-8")).get("date")
        except Exception:
            return None

    cur = _cur_date()
    if cur == today:
        return "ok"
    # ⛔ 收盘前不主动补齐（见上方安全边界）；now_hour 可注入以便回归测试
    hour = datetime.now().hour if now_hour is None else now_hour
    if hour < 15:
        return f"stale-before-close(cur={cur})"
    up = ROOT / "scripts" / "record_zt_snapshot.py"
    if not up.exists():
        return f"skip-no-upstream(cur={cur})"
    try:
        r = subprocess.run([sys.executable, str(up)], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=180)
        new = _cur_date()
        if r.returncode == 0 and new == today:
            return f"filled(cur={cur}→{new})"
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
        return f"fill-failed(cur={cur}→{new} rc={r.returncode} {tail[0][:80]})"
    except Exception as e:
        return f"fill-err(cur={cur}):{e}"


def _heal_residual_archives(heal_date: str, pool: list, limit: int = 5) -> list:
    """残缺日自愈（2026-09-14 立）：过去交易日归档若缺 zt_list，且其日期正是 `heal_date`
    （= 本次 yesterday_zt 的口径日），用该池补写；来源不明的残缺日只告警不编造。

    事故链（09-11 实证）：盘后链里 capture_daily 早于 record_zt_snapshot 跑完 → 当日的
    `yesterday_zt.json` 还是前一交易日的 → 当日文件被 theme_series 的盘中时间轴创建成
    `intraday_only`（无 zt_list），此后永无来源补 → 形态/N字/回流检测永久少一天料。
    """
    if not pool:
        return []
    today = _today()
    healed = []
    for p in sorted(ZT_DIR.glob("zt_*.json"))[-limit:]:
        try:
            z = json.loads(p.read_text())
        except Exception:
            continue
        date = str(z.get("date") or "")
        if not date or date >= today or (z.get("zt_list") or []):
            continue
        if date != heal_date:
            print(f"[capture_daily] ⚠️ 残缺历史日 {p.name}（缺 zt_list）来源不明·未自愈（禁编造）")
            continue
        z = dict(z)
        z.update({"zt_list": pool, "zt_count": len(pool), "tc": len(pool),
                  "healed_ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        import zt_archive
        zt_archive.write_archives(z, dirs=_archive_dirs())   # 自愈同样双目录同写（防两边不一致）
        healed.append(p.name)
        print(f"[capture_daily] 🩹 残缺历史日已自愈 {p.name}｜补 zt_list {len(pool)} 只")
    return healed


def capture_zt():
    """复制昨日涨停池为按天历史副本（用其自带 date 字段命名）。

    若当日盘中已沉淀 intraday_points（题材时间轴），合并保留，不覆盖丢失。
    ⛔ 写盘前先做**前置补齐**（`_ensure_pool_fresh`·根治盘后链时序错：既防当日缺料，
       也防把前一交易日归档用旧数据重写）；落盘后顺带做**残缺日自愈**（`_heal_residual_archives`）。
    """
    _fill = _ensure_pool_fresh(_today())
    if _fill != "ok":
        print(f"[capture_daily] 🔧 涨停池前置补齐：{_fill}")
    src = CACHE / "yesterday_zt.json"
    if not src.exists():
        return None
    try:
        data = json.loads(src.read_text())
    except Exception:
        return None
    date_str = data.get("date") or _today()
    target = ZT_DIR / f"zt_{date_str}.json"
    if not (data.get("zt_list") or []):
        print(f"[capture_daily] ⚠️ 缺 zt_list：{target.name}（yesterday_zt 无涨停池·盘后链时序?）")
    # ⛔ 双目录同写 + 合并保留 intraday_points（单一出口 = scripts/zt_archive.py·2026-09-18）
    #    此前本函数只写 cache/zt_history，而 record_zt_snapshot 只写 wealth_code/data/zt_history
    #    → 「双路径分裂」（15:30 theme_roll 读到的当日归档是无 zt_list 的盘中空壳）。
    import zt_archive
    zt_archive.write_archives(data, dirs=_archive_dirs())
    # ⛔ 盘后仍拿不到今日池 = 上游补齐失败 → 当日归档注定缺料，必须显式告警（勿静默）
    if date_str != _today() and datetime.now().hour >= 15:
        print(f"[capture_daily] ⚠️ 盘后未拿到今日涨停池：本次归档口径={date_str}"
              f"（今日 {_today()} 缺料·见上方前置补齐结果）")
    _heal_residual_archives(date_str, data.get("zt_list") or [])
    return date_str


def prune_history():
    """删除超过保留期的历史快照：**按交易日计数**，保留最近 N 个交易日的文件。

    快照只在交易日生成（盘后归档），故按文件名日期降序取前 N 个即最近 N 个交易日，
    天然跳过周末/节假日（原按自然日 (today-dd).days 计算，周末断档会把交易日提前删掉，
    导致「5 日数据」永远凑不齐——2026-08-12 修正）。
    """
    removed = []
    # —— zt_history(主) + zt_history(镜像) + eruption_history + sector_flow_history：RETAIN_DAYS ——
    # ⚠️ 2026-10-14 审计 P1-⑥：镜像目录原先不在裁剪表内 → 主目录 19 文件 vs 镜像 40 文件
    #    （同一份数据两套保留期，消费方读到哪份取决于它读哪个目录，属**隐式口径分裂**）。
    #    两者同日写入、同一保留期，必须成对裁剪。ZT_DIR/ZT_MIRROR_DIR 在函数体内求值，
    #    故测试 monkeypatch 模块属性即可生效（见 tests/test_history_window.py）。
    pref_map = {ERUPT_DIR: "eruption_", ZT_DIR: "zt_", ZT_MIRROR_DIR: "zt_",
                FLOW_DIR: "sector_flow_"}
    for d, pref in pref_map.items():
        dated = []
        for p in sorted(d.glob("*.json")):
            stem = p.name[:-5]                       # 去 .json
            if not stem.startswith(pref):
                continue
            ds = stem[len(pref):]
            try:
                dd = datetime.strptime(ds, "%Y-%m-%d").date()
            except Exception:
                continue                             # 格式不符跳过
            dated.append((dd, p))
        dated.sort(key=lambda x: x[0], reverse=True)  # 最近交易日在前
        for _, p in dated[RETAIN_DAYS:]:
            p.unlink()
            removed.append(p.name)
    # —— short_term/daily：SHORT_TERM_RETAIN ——
    dated = []
    for p in sorted(DAILY_DIR.glob("daily_*.md")):
        stem = p.name[:-3]                            # 去 .md
        ds = stem[6:]                                 # 去 daily_
        try:
            dd = datetime.strptime(ds, "%Y-%m-%d").date()
        except Exception:
            continue                                  # 格式不符跳过
        dated.append((dd, p))
    dated.sort(key=lambda x: x[0], reverse=True)
    for _, p in dated[SHORT_TERM_RETAIN:]:
        p.unlink()
        removed.append(p.name)
    # —— cache/pulse_windows：window_*.json 按份数 + .closed_* 按天（审计 P2-⑪）——
    # ⛔ 目录本身保留（前端 OpsPage 读取 + pulse_fetcher 落防重标记，见常量注释）。
    if PULSE_WINDOW_DIR.exists():
        wins = sorted(PULSE_WINDOW_DIR.glob("window_*.json"), reverse=True)  # 名内嵌 YYYYMMDD_HHMM → 倒序即最新在前
        for p in wins[PULSE_WINDOW_RETAIN:]:
            p.unlink()
            removed.append(p.name)
        cutoff = (datetime.now() - timedelta(days=PULSE_MARK_RETAIN_DAYS)).strftime("%Y%m%d")
        for p in sorted(PULSE_WINDOW_DIR.glob(".closed_*")):
            # 名形：.closed_YYYYMMDD_HHMM[_s15] → 取第 2 段判日期，解析失败即跳过（不误删）
            parts = p.name.split("_")
            if len(parts) < 3 or not parts[1].isdigit() or len(parts[1]) != 8:
                continue
            if parts[1] < cutoff:
                p.unlink()
                removed.append(p.name)
    return removed


def capture_sector_flow():
    """抓全量板块主力资金流（f62）沉淀为日级快照，供 theme_rotation 算边际趋势。

    2026-08-26 双源升级（用户「就用腾讯，腾讯更稳」+「5日详情、10日总览双重判断」）：
      - 当日全量榜：东财 f62（全板块一次拉全·build_sector_flow_snapshot）
      - 5D/10D 窗口：腾讯自选股 fund flow 批量回填 Top 板块（MainNetFlow5D/10D，多窗口·稳）
    每板块 boards[name] 扩展字段：flow_yi(东财当日) / tx_flow_yi(腾讯当日) /
    tx_5d_yi / tx_10d_yi（腾讯无该板块或查询失败=缺失，下游诚实降级）。
    """
    import intraday_pulse as ip
    try:
        boards = ip.build_sector_flow_snapshot()
        if not boards:
            # ⛔ 2026-10-14 审计 P1-④：原实现此分支**静默 return 0**（全空却一声不响）
            #    —— 这正是「flow 单侧缺口」能长期不被发现的原因之一。改为显式告警。
            print("[capture_sector_flow] ⚠️ 板块资金流快照为空（build_sector_flow_snapshot 返回 {}）"
                  "→ 今日 sector_flow 不落盘，将在 3.5) 日集合差对账中报为单侧缺口")
            return 0
        boards = _tx_backfill_flow(boards)
        FLOW_DIR.mkdir(parents=True, exist_ok=True)
        (FLOW_DIR / f"sector_flow_{_today()}.json").write_text(json.dumps({
            "date": _today(),
            "generated_ts": iso_bj(),          # 规范形态（ISO8601 +08:00）· 2026-10-14 审计 P1-⑦
            "boards": boards,
        }, ensure_ascii=False))
        return len(boards)
    except Exception as e:
        print(f"[capture_sector_flow] ERR:{e}")
        return 0


# 东财板块名 → 腾讯申万板块搜索词（抓不到 pt 代码时的兜底：按名搜索）
_TX_SECTOR_TOP = 40   # 腾讯 5D/10D 回填覆盖：东财榜前 N 个板块（流入+流出各取·2026-08-26 25→40扩覆盖）
_TX_FLOW_MAP = None   # 懒加载：{name: {5d, 10d}} 缓存（单次 capture 内复用）

# 东财板块名 → 腾讯自选股板块代码（pt 开头）。覆盖高频跟踪板块；未映射的跳过腾讯回填（诚实降级）。
# 2026-08-26 建：核心板块经腾讯 search --type sector 探明真实 pt 代码（东财概念名 ≠ 腾讯申万名，需桥接）
_TX_SECTOR_PT = {
    # 申万一级（pt01801xxx 段）
    "有色金属": "pt01801050", "工业金属": "pt01801050", "黄金": "pt01801050",
    "黄金概念": "pt01801050", "贵金属": "pt01801050", "小金属": "pt01801050",
    "小金属概念": "pt01801050", "稀土": "pt01801050",
    "铜": "pt01801050", "白银": "pt01801050", "有色": "pt01801050",
    "煤炭": "pt01801950", "煤炭概念": "pt01801950", "焦炭": "pt01801950",
    "焦煤": "pt01801950", "动力煤": "pt01801950", "石油": "pt01801060",
    "粮食概念": "pt01801310", "农业": "pt01801310",
    "电力": "pt01801030", "公用事业": "pt01801030", "电力设备": "pt01801030",
    "医药": "pt01801150", "医药生物": "pt01801150", "创新药": "pt01801150",
    "化学制药": "pt01801150", "中药": "pt01801150", "医疗器械": "pt01801150",
    "医疗服务": "pt01801150", "生物制品": "pt01801150", "医药商业": "pt01801150",
    "白酒": "pt02B20001", "白酒概念": "pt02B20001", "食品饮料": "pt01801120", "饮料制造": "pt01801120",
    "银行": "pt01801160", "证券": "pt01801170",
    "通信": "pt01801102", "通信设备": "pt01801102", "通信网络设备及器件": "pt01801102",
    "光通信": "pt02GN2211", "光模块": "pt02GN2211", "CPO": "pt02GN2211",
    "共封装光模块": "pt02GN2211", "铜缆高速连接": "pt01801102",
    "半导体": "pt01801180", "电子": "pt01801180", "消费电子": "pt01801180",
    "印制电路板": "pt01801180", "PCB": "pt01801180",
    "计算机": "pt01801200", "软件": "pt01801200", "人工智能": "pt01801200",
    "房地产": "pt01801210", "建筑": "pt01801220", "建材": "pt01801230",
    "汽车": "pt01801250", "新能源车": "pt01801250", "汽车零部件": "pt01801250",
    "光伏": "pt01801260", "风电": "pt01801270", "军工": "pt02003490",
    "机械": "pt01801290", "机器人": "pt01801290", "家电": "pt01801300",
    "农业": "pt01801310", "化工": "pt01801320", "钢铁": "pt01801040",
    "交通运输": "pt01801340", "传媒": "pt01801350", "环保": "pt01801360",
    "商贸零售": "pt01801380", "社服": "pt01801390", "纺织": "pt01801400",
    "轻工": "pt01801410", "游戏": "pt01801350", "影视": "pt01801350",
    # 概念类
    "存储": "pt02GN2124", "存储器": "pt02GN2124", "液冷服务器": "pt02GN2224",
    "液冷概念": "pt02GN2224", "国产芯片": "pt01801180", "5G概念": "pt02GN2190",
    "华为概念": "pt01801200",
}


def _tx_backfill_flow(boards: dict) -> dict:
    """腾讯 fund flow 批量回填 boards 的 5D/10D（Top 板块优先，覆盖资金流向关键板块）。

    腾讯优势（2026-08-26 实测）：稳定（东财 push2 连挂 5 次）+ 多窗口（MainNetFlow5D/10D/20D）。
    东财优势：全板块一次拉全（f62 当日）——所以「当日全量」仍用东财，「5D/10D 双窗口」用腾讯回填。

    回填策略：boards 按当日 |flow_yi| 排序取 Top（流入+流出各半），命中 _TX_SECTOR_PT 映射的
    板块批量查腾讯（fund flow 支持逗号分隔多代码）→ 每板块追加 tx_5d_yi / tx_10d_yi / tx_flow_yi。
    未命中映射或查询失败 → 不硬编（数据即事实，缺失字段下游可见）。
    """
    global _TX_FLOW_MAP
    try:
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "wealth_code" / "scripts"))
        from tx_query import fund_flow
    except Exception as e:
        print(f"[capture_sector_flow] tx_query import ERR:{e}")
        return boards

    # 2026-08-26：命中映射表的板块全部回填（不限 Top）——保证 theme_rotation 候选主题
    # 的资金 5D/10D 都能拿到腾讯多窗口（此前限 Top50 漏掉焦煤/粮食概念等映射板块）
    pt_to_names = {}
    for nm in boards:
        pt = _TX_SECTOR_PT.get(nm)
        if pt:
            pt_to_names.setdefault(pt, []).append(nm)
    if not pt_to_names:
        return boards

    # 批量查腾讯（一次 npx 调用多个 pt 代码）
    try:
        res = fund_flow(list(pt_to_names.keys()))
    except Exception as e:
        print(f"[capture_sector_flow] tx fund_flow ERR:{e}")
        return boards

    filled = 0
    for pt, names in pt_to_names.items():
        r = res.get(pt) or {}
        # 腾讯返回 key 可能是 pt 代码或 symbol 字段，兼容
        if not r:
            r = next((v for k, v in res.items() if str(k).startswith("pt")), None) or {}
        tx5 = r.get("main_flow_5d_yi")
        tx10 = r.get("main_flow_10d_yi")
        tx0 = r.get("main_flow_yi")
        if tx5 is None and tx10 is None:
            continue
        for nm in names:
            if nm not in boards:
                continue
            if tx0 is not None:
                boards[nm]["tx_flow_yi"] = tx0
            if tx5 is not None:
                boards[nm]["tx_5d_yi"] = tx5
            if tx10 is not None:
                boards[nm]["tx_10d_yi"] = tx10
            filled += 1
    if _TX_FLOW_MAP is None:
        _TX_FLOW_MAP = {}
    print(f"[capture_sector_flow] 腾讯5D/10D回填 {filled} 板块（映射 {len(pt_to_names)} 个pt）")
    return boards


def capture_emotion(today: str) -> dict:
    """情绪数值日快照：读 feedback_matrix.json 数值 → short_term/daily/emotion_{today}.json。

    结构化为 5 日情绪汇总（gen_sentiment_summary.py）提供历史数值，替代 AI 手工抄表。
    仅当 feedback 无 error（数据真实）才写。
    """
    src = CACHE / "feedback_matrix.json"
    if not src.exists():
        return {"error": "no_feedback_matrix"}
    try:
        d = json.loads(src.read_text(encoding="utf-8")) or {}
    except Exception:
        return {"error": "bad_feedback_matrix"}
    if d.get("error"):
        return {"error": d["error"]}
    snap = {
        "date": d.get("date") or today,
        "prev_date": d.get("prev_date"),
        "prev_zt_count": d.get("prev_zt_count"),
        "zt_count": d.get("zt_count", d.get("now_zt_count")),
        "promote_rate": d.get("promote_rate"),
        "lb_promote_rate": d.get("lb_promote_rate"),
        "avg_pct": d.get("avg_pct"),
        "up_rate": d.get("up_rate"),
        "nuke_count": d.get("nuke_count"),
        "break_count": d.get("break_count"),
        "max_boards": d.get("max_boards", d.get("now_max_boards")),
        "prev_max_boards": d.get("prev_max_boards"),
        "sentiment_score": d.get("sentiment_score"),
        "sentiment_label": d.get("sentiment_label"),
        "sentiment_model": d.get("sentiment_model") or "legacy_v2",
        "sentiment_quality": d.get("sentiment_quality"),
        "sentiment_inputs_missing": d.get("sentiment_inputs_missing"),
        "source_snapshot_ts": d.get("source_snapshot_ts"),
        # 保留重算所需原始因子，旧历史不回填。
        "broken_rate": d.get("broken_rate"),
        "idx_avg": d.get("idx_avg"),
        "zt_expand": d.get("zt_expand"),
        "max_trend": d.get("max_trend"),
        "market_amount_yi": d.get("market_amount_yi"),
        "breadth": d.get("breadth"),
        "regulatory_pressure": d.get("regulatory_pressure"),
        # ⚠️ 这里**故意**沿用上游值的原名：`feedback_matrix` 的 `generated_ts` 实为
        # **15:00 收盘源时刻**（不是采集时刻）—— 属审计 P1-⑦ 点名的「字段名与语义不符」。
        # 保留原值以免改变下游字符串切片行为；语义已在规范中登记，迁移另行立项。
        "generated_ts": d.get("generated_ts") or iso_bj(),
    }
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    (DAILY_DIR / f"emotion_{today}.json").write_text(json.dumps(snap, ensure_ascii=False, indent=1))
    return snap


def capture_newsflash(today: str) -> int:
    """外部新闻（快讯）日归档：读 latest.json 的 newsflash → newsflash_history/daily_{today}.json。

    盘中快讯只进 latest.json（当日覆盖），盘后归档留存当日全部硬信号快讯，
    供 5 日汇总/复盘做「事件/催化」维度引用（情绪判断的事件驱动源头）。
    A2（2026-10-10）：每条快讯增量标注 `polarity`（利好/利空/中性），语义复用
    newsflash_express._direction（涨→利好/跌→利空/均无→中性），供 5 日汇总按极性聚合。
    """
    src = CACHE / "pulse_snapshots" / "latest.json"
    if not src.exists():
        return 0
    try:
        d = json.loads(src.read_text(encoding="utf-8")) or {}
    except Exception:
        return 0
    nf = d.get("newsflash") or []
    if not nf:
        return 0
    # ── A2 极性标注（仅增字段，不破坏既有消费方契约）─────────────────────
    pol = _newsflash_polarity
    out_items = []
    for it in nf:
        item = dict(it) if isinstance(it, dict) else {"title": str(it)}
        txt = (item.get("title") or item.get("text") or "")
        item["polarity"] = pol(txt)
        out_items.append(item)
    NEWSFLASH_DIR.mkdir(parents=True, exist_ok=True)
    (NEWSFLASH_DIR / f"daily_{today}.json").write_text(json.dumps({
        "date": today,
        "generated_ts": iso_bj(),              # 规范形态（ISO8601 +08:00）· 2026-10-14 审计 P1-⑦
        "newsflash": out_items,
    }, ensure_ascii=False, indent=1))
    return len(out_items)


# ── A2 极性判定（2026-10-10）─────────────────────────────────────────────
# 单一词源 = scripts/newsflash_express.py::_direction（涨类→up / 跌类→down / 均无→neutral）。
# 此处转译为「利好/利空/中性」业务语义（5 日汇总聚合口径），避免两处维护词表。
def _newsflash_polarity(title: str) -> str:
    """返回 利好 / 利空 / 中性。标题缺字段或两词类均无 → 中性（不误导）。"""
    try:
        from newsflash_express import _direction
        d = _direction(title or "")
    except Exception:
        d = "neutral"
    return {"up": "利好", "down": "利空", "neutral": "中性"}.get(d, "中性")


def capture_risk_watch(today: str, limit: int = 8) -> int:
    """步骤 8 · P2-19（2026-09-10 落地）：高标个股「风险事件 / 风险公告 / 两融」盘后归档。

    对象 = 涨停池中 板数降序 Top(limit)（复盘最相关的高标·含龙头），池源 cache/yesterday_zt.json
    （record_zt_snapshot 沉淀·权威·含 boards/code/name/sector）。
    产出 cache/risk_watch_{today}.json：
      {date, generated_ts, scope, items[{code,name,boards,sector,risk_labels,pledge_pct,notices,margin}], summary}
    供盘后复盘卡（骨架 C）风险区引用。⛔ 上游不可用 → 返回 0 空退出（不编造风险标签）；
    单票失败跳过不影响其余（诚实降级）。
    """
    zp = CACHE / "yesterday_zt.json"
    if not zp.exists():
        return 0
    try:
        zd = json.loads(zp.read_text(encoding="utf-8"))
    except Exception:
        return 0
    pool = zd.get("zt_list") or []
    ranked = sorted(pool, key=lambda x: (x.get("boards") or 0), reverse=True)[:limit]
    if not ranked:
        return 0
    # 腾讯查询层（wealth_code/scripts/tx_query.py·P2-19 新增三模块）
    tx_dir = ROOT / "wealth_code" / "scripts"
    if str(tx_dir) not in sys.path:
        sys.path.insert(0, str(tx_dir))
    try:
        import tx_query
    except Exception as e:
        print(f"[capture_risk_watch] tx_query 导入失败: {e}")
        return 0
    codes = [str(x.get("code") or "") for x in ranked if x.get("code")]
    if not codes:
        return 0
    risks = tx_query.risk_events(codes, limit=limit)
    nts = tx_query.notices(codes, limit=limit)          # ntype 默认 6=风险类公告
    mgs = tx_query.margin(codes)
    risk_unavailable = risks.pop("_unavailable", []) if isinstance(risks, dict) else []
    items = []
    labels_dist: dict[str, int] = {}
    for x in ranked:
        code = str(x.get("code") or "")
        if not code:
            continue
        r = risks.get(code) or {}
        nt = nts.get(code) or {}
        for lb in (r.get("labels") or []):
            labels_dist[lb] = labels_dist.get(lb, 0) + 1
        items.append({
            "code": code,
            "name": x.get("name"),
            "boards": x.get("boards"),
            "sector": x.get("sector"),
            "checked": bool(r.get("checked") or r.get("labels")),
            "risk_labels": r.get("labels") or [],
            "pledge_pct": r.get("pledge_pct"),
            "notice_count": nt.get("count"),
            "notices": (nt.get("items") or [])[:3],
            "margin": mgs.get(code) or None,
        })
    out = {
        "date": today,
        "generated_ts": iso_bj(),              # 规范形态（ISO8601 +08:00）· 2026-10-14 审计 P1-⑦
        "scope": f"涨停池板数 Top{len(ranked)}（高标风险面）",
        "items": items,
        "summary": {
            "total": len(items),
            "with_risk": len([i for i in items if i.get("risk_labels")]),
            "risk_unavailable": risk_unavailable,
            "labels_dist": labels_dist,
        },
    }
    (CACHE / f"risk_watch_{today}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return len(items)


# ══════════════════════════════════════════════════════════════════════
# 归档日集合差对账（2026-10-14 数据血缘审计 P1-④）
# ══════════════════════════════════════════════════════════════════════
# 【审计发现】`sector_flow_history/` 在 09-01~10-14 的 26 个交易日里缺 12 天；
#   去掉目录诞生前的 09-01~09-04，**中后段仍缺 8 天**：09-21 / 23 / 24 / 29、
#   10-08 / 10-09 / 10-12 / 10-14。`eruption_history` 缺同一批（少 09-29）。
#
# 【为什么会出现「单侧缺口」——按 mtime 实证】
#   · `sector_flow_history` **只有一条触发路径** = 本文件 step 2.5（盘后复盘 17:18 链内）。
#   · `zt_history` 有**两条及以上**：`record_zt_snapshot.py`（15:30 接力卡任务，
#     实测镜像 `wealth_code/data/zt_history/zt_2026-10-13.json` mtime=17:19:11）
#     以及一条 **~23:5x 的独立写入**（实测 `cache/zt_history` 多数文件 mtime=23:5x，
#     如 zt_2026-10-13.json=10-13 23:50:01）。
#   ⇒ 盘后链只要**没跑到 step 2.5**（整链未触发 / 其中一步失败），zt 仍会被其他路径补齐，
#     而 flow 不会 —— 于是「zt 有、flow 没有」的单侧缺口静默产生。
#   ⚠️ 诚实边界：那条 23:5x 写入方**未能确证**（crontab 在本环境不可读、launchd 无
#     ~23:50 任务、平台 automation 列表无该时刻条目）。本文只登记**现象与 mtime 证据**，
#     ⛔ 不臆造一个「02:00 僵尸任务」。
#
# 【为什么只告警、不回补（重要·勿擅自改成回补）】
#   数据源侧是**可回补**的：`push2his.eastmoney.com/api/qt/stock/fflow/daykline/get`
#   能按板块返回历史日主力净额，且口径已逐字段验证一致（BK0447 互联网服务 10-13
#   历史值 +2.91 亿 / 0.92% 与存档 `flow_yi=2.91, pct=0.92` 完全吻合）。
#   **但必须拒绝部分回补**，理由两条：
#     ① 契约 §1 / §12 明令「缺失一律不裁决、**不以邻近日期代替**、缺失不填零」；
#     ② `cache/em_stock_sectors.json` 只能反推出 **184 / 357（约 52%）** 板块的名称↔BK
#        映射 ⇒ 部分回补会让「5 日累计净流入排行」**静默扭曲**——比缺一天更危险
#        （缺一天下游能看见 NaN，回补一半下游会把错值当全量）。
#   故此处只做「**消灭静默**」：把缺口显式打到盘后链汇总行，由人决定是否全量重跑。
#   真正的兜底另有 `scripts/guard_eod_chain.py`（其 CHECKS 已含 sector_flow_history），
#   本函数是**第二道**、也是唯一会指出「到底缺哪几天」的一道。
def _dated_stems(d: Path, pref: str) -> set[str]:
    """目录下 `{pref}{YYYY-MM-DD}.json` 的日期集合（不合法命名直接忽略）。"""
    out: set[str] = set()
    for p in d.glob(f"{pref}*.json"):
        ds = p.name[len(pref):-5]
        if len(ds) == 10 and ds[4] == "-" and ds[7] == "-":
            out.add(ds)
    return out


def _short_dates(dates: list[str], cap: int = 6) -> str:
    """`2026-09-21,2026-09-23` → `09-21,09-23`；超过 cap 个补 `+N`。"""
    shown = ",".join(d[5:] for d in dates[:cap])
    return f"{shown},+{len(dates) - cap}" if len(dates) > cap else shown


def audit_archive_date_sets(window: int = ARCHIVE_AUDIT_WINDOW) -> str:
    """对账 zt / flow / eruption 三目录的**日集合差**，返回一行人读摘要。

    ⛔ 只对账、不写盘、不回补（理由见上方大段注释）。
    参考日轴取三目录日期的并集（zt 是最全的一侧），窗口取最近 `window` 个。
    """
    zt = _dated_stems(ZT_DIR, "zt_")
    flow = _dated_stems(FLOW_DIR, "sector_flow_")
    erupt = _dated_stems(ERUPT_DIR, "eruption_")

    ref = sorted(zt | flow | erupt)
    if not ref:
        return "archive_audit=空(三目录均无归档)"
    win = ref[-window:]

    msgs: list[str] = []
    for label, have in (("flow", flow), ("eruption", erupt)):
        miss = [d for d in win if d not in have]
        if miss:
            msgs.append(f"{label}缺{len(miss)}日[{_short_dates(miss)}]")
    only_flow = [d for d in win if d in flow and d not in zt]
    if only_flow:
        msgs.append(f"zt缺{len(only_flow)}日[{_short_dates(only_flow)}]")

    if not msgs:
        return f"archive_audit=✓对称({len(win)}日·{win[0]}~{win[-1]})"
    return (f"archive_audit=⚠️单侧缺口 {';'.join(msgs)}"
            f"（窗口{win[0]}~{win[-1]}·{len(win)}日·只告警不回补）")


def _process_stock_technical():
    """盘后数据归档完成后，为当天所有命中系统的股票计算技术派生层。

    失败只留日志/队列错误态，不影响既有盘后沉淀链；不产生任何交易结论。
    """
    try:
        from common import stock_registry as registry
        today = _today()
        registry.enqueue_daily_codes(trade_date=today, trigger="capture_daily:eod")
        result = registry.process_technical_queue(trade_date=today)
        return (f"technical={result.get('queued', 0)}入队/"
                f"{result.get('written', 0)}写入/"
                f"{result.get('failed', 0)}失败")
    except Exception as exc:  # noqa: BLE001 — 派生层不阻断盘后主链
        print(f"[capture_daily] ⚠️ 技术派生失败(不影响主链): {exc}")
        return f"technical=ERR:{exc}"


def main():
    ERUPT_DIR.mkdir(parents=True, exist_ok=True)
    ZT_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    today = _today()

    # 0) 交易日守卫（复用 feedback_matrix P0-1 守卫结论）
    if not _guard_trading_day(today):
        return

    # 1) 爆发快照
    try:
        themes = capture_eruption()
        (ERUPT_DIR / f"eruption_{today}.json").write_text(json.dumps({
            "date": today,
            "generated_ts": iso_bj(),          # 规范形态（ISO8601 +08:00）· 2026-10-14 审计 P1-⑦
            "themes": themes,
        }, ensure_ascii=False))
        erupt_msg = f"themes={len(themes)}"
    except Exception as e:
        erupt_msg = f"ERR:{e}"

    # 2) 涨停池副本
    try:
        zt_date = capture_zt()
        zt_msg = f"zt={zt_date}" if zt_date else "zt=none"
        # 完整性守卫（2026-09-12 N1 修复）：沉淀结果必须含非空 zt_list，
        # 否则下游（live_signal 形态/theme_rotation 题材/弱转强）会静默少算。
        # 历史实证：09-04 / 09-07 / 09-11 三日沉淀出来只有盘中快照、无涨停数据。
        if zt_date:
            _p = ZT_DIR / f"zt_{zt_date}.json"
            try:
                _d = json.loads(_p.read_text(encoding="utf-8"))
                if not (_d.get("zt_list") or []):
                    zt_msg += " ⚠️缺zt_list(沉淀不完整·检查 yesterday_zt.json 是否就绪/是否被盘中快照覆盖)"
            except Exception as _e:
                zt_msg += f" ⚠️校验失败:{_e}"
    except Exception as e:
        zt_msg = f"zt ERR:{e}"

    # 2.5) 板块资金流全量快照（回流趋势地基）
    try:
        n_flow = capture_sector_flow()
        flow_msg = f"sector_flow={n_flow}"
    except Exception as e:
        flow_msg = f"sector_flow ERR:{e}"

    # 2.6) 情绪数值日快照（5 日汇总的结构化地基·2026-08-16）
    try:
        emo = capture_emotion(today)
        emo_msg = emo.get("error", f"score={emo.get('sentiment_score')}/10")
    except Exception as e:
        emo_msg = f"emotion ERR:{e}"

    # 2.7) 外部新闻（快讯）日归档（事件/催化维度·2026-08-16）
    try:
        n_nf = capture_newsflash(today)
        nf_msg = f"newsflash={n_nf}"
    except Exception as e:
        nf_msg = f"newsflash ERR:{e}"

    # 3) 清理
    removed = prune_history()

    # 3.5) 归档日集合差对账（2026-10-14 审计 P1-④·**消灭静默缺口**）
    #      必须排在 1)/2)/2.5) 全部写入之后，才能反映「本次跑完」的真实状态：
    #      若某些步骤静默失败，今日就会以「单侧缺口」形式出现在窗口里。
    #      ⛔ 只告警不回补（契约 §1/§12 + 板块映射仅 52% 覆盖，见函数上方大段说明）。
    try:
        arch_msg = audit_archive_date_sets()
    except Exception as e:
        arch_msg = f"archive_audit ERR:{e}"

    # 4) 当日题材时间线（融合进 daily_YYYY-MM-DD.md，不新建文件·2026-08-16）
    try:
        import subprocess
        py = sys.executable
        tl = subprocess.run([py, str(Path(__file__).resolve().parent / "gen_daily_timeline.py"), today],
                            capture_output=True, text=True, timeout=30)
        tl_msg = tl.stdout.strip() or (tl.stderr.strip()[:80] if tl.returncode else "ok")
    except Exception as e:
        tl_msg = f"timeline ERR:{e}"

    # 5) 5 日情绪汇总（读 5 日 emotion/zt/eruption 快照 → 刷新 5 日情绪演变表·2026-08-16）
    try:
        import subprocess
        py = sys.executable
        ss = subprocess.run([py, str(Path(__file__).resolve().parent / "gen_sentiment_summary.py"), today],
                            capture_output=True, text=True, timeout=30)
        ss_msg = ss.stdout.strip() or (ss.stderr.strip()[:80] if ss.returncode else "ok")
    except Exception as e:
        ss_msg = f"summary ERR:{e}"

    # 6) 监管红线监测（2026-08-19 用户定·P1-8 情绪拐点信号）：扫描涨停池 10日≥100%/30日≥200%
    #    严重异常波动票 → 沉淀 cache/regulatory_events.json（供 feedback_matrix 情绪打分联动）
    try:
        import subprocess
        py = sys.executable
        rw = subprocess.run([py, str(Path(__file__).resolve().parent / "regulatory_watch.py")],
                            capture_output=True, text=True, timeout=180)
        rw_msg = rw.stdout.strip() or (rw.stderr.strip()[:80] if rw.returncode else "ok")
    except Exception as e:
        rw_msg = f"regulatory ERR:{e}"

    # 7) 股性涨停池逐日归档（2026-09-09 用户「股性很重要」）：zt_genes fetch 当日
    #    涨停池原始(lbc/首封/炸板/zttj) → cache/zt_genes/pool_YYYYMMDD.json 逐日积累，
    #    供股性档案(涨停频率/连板/晋级/溢价基因)统计窗口随运行自然变长(60日后满窗)。
    try:
        import subprocess
        py = sys.executable
        zg = subprocess.run([py, str(Path(__file__).resolve().parent / "zt_genes.py"), "fetch", "--days", "1"],
                            capture_output=True, text=True, timeout=120)
        zg_msg = zg.stdout.strip().splitlines()[-1] if zg.returncode == 0 else (zg.stderr.strip()[:80])
    except Exception as e:
        zg_msg = f"zt_genes ERR:{e}"

    # 7.1) 股性档案刷新（**2026-10-14 审计修复 A4**）：`zt_genes.py gene --scope today`
    #      → cache/zt_genes/gene_{code}.json（近期涨停行为：炸板率/晋级率/首日溢价/股性分）。
    #
    #      ⚠️ 缺口成因：`fetch`(盘后归档) 与 `hist`(EOD 候选) 都有人调，**唯独 `gene` 无人调**
    #      → gene_*.json 冻结在 2026-09-09，而 `/api/gene/detail` 仍按 daily 路由在产，
    #        `/gene` 页把 09-09 的「近期股性」与当日 hist 左连接后混展 ⇒ 静默失真 35 天。
    #      `--scope today` = 最近一期 pool 全部代码 ∪ latest.json 的 top5 成分（候选常用票）；
    #      `--scope today` 走 `with_kline=False` 纯本地快路径（不拉日K、不受腾讯限流）。
    #      必须排在 7) fetch 之后 —— gene 依赖刚写入的当日 pool。失败静默降级不阻断归档。
    try:
        import subprocess
        py = sys.executable
        gg = subprocess.run([py, str(Path(__file__).resolve().parent / "zt_genes.py"), "gene", "--scope", "today"],
                            capture_output=True, text=True, timeout=180)
        gg_msg = gg.stdout.strip().splitlines()[-1] if gg.returncode == 0 else (gg.stderr.strip()[:80])
    except Exception as e:
        gg_msg = f"zt_genes gene ERR:{e}"

    # 7.5) 连板晋级事件当日归档（2026-09-09 用户立项 P1·promo_quant）：今日池落盘后，
    #    对(昨日,今日)相邻对生成晋级事件表(1进2..高标·成功率) → cache/promo/promo_{date}.json；
    #    随后 enrich 全量事件次日溢价（晋级日需其后 2 个交易日日K·滞后自动滚动补全）。
    try:
        import subprocess
        py = sys.executable
        pq_run = subprocess.run([py, str(Path(__file__).resolve().parent / "promo_quant.py"), "build"],
                                capture_output=True, text=True, timeout=60)
        pq_msg = pq_run.stdout.strip().splitlines()[-1] if pq_run.returncode == 0 else (pq_run.stderr.strip()[:80])
    except Exception as e:
        pq_msg = f"promo ERR:{e}"
    try:
        import subprocess
        py = sys.executable
        pe_run = subprocess.run([py, str(Path(__file__).resolve().parent / "promo_quant.py"), "enrich", "--all"],
                                capture_output=True, text=True, timeout=300)
        pe_msg = pe_run.stdout.strip().splitlines()[-1] if pe_run.returncode == 0 else (pe_run.stderr.strip()[:80])
    except Exception as e:
        pe_msg = f"promo_enrich ERR:{e}"

    # 8) 高标风险面归档（P2-19·2026-09-10）：涨停池板数 Top8 的 风险事件/风险公告/两融
    #    → cache/risk_watch_{date}.json（供盘后复盘卡风险区引用·上游不可用则空退出）
    try:
        n_rw = capture_risk_watch(today)
        rwm_msg = f"risk_watch={n_rw}"
    except Exception as e:
        rwm_msg = f"risk_watch ERR:{e}"

    # 9) D2（2026-10-10）：选股宝→东财官方板块 映射增量刷新（盘后链末步）。
    #    latest.json top5 盘中已沉淀 → 此刻读得到；仅新 code 才联网 slist，失败降级不阻断。
    if ENABLE_SECTOR_OFFICIAL_REFRESH:
        try:
            import subprocess as _sp
            _so = _sp.run([sys.executable, str(Path(__file__).resolve().parent / "sector_official.py"), "--build"],
                          capture_output=True, text=True, timeout=240)
            _tail = (_so.stdout or "").strip().splitlines()[-1:] or [""]
            so_msg = _tail[0][:80] if _so.returncode == 0 else f"ERR:{_so.stderr.strip()[:80]}"
        except Exception as e:
            so_msg = f"sector_official ERR:{e}"
    else:
        so_msg = "sector_official=skip(开关关闭)"

    # 10) 机制4（2026-10-12）：题材归并巡检「哨兵」（盘后链末步·只读·高置信错配推异动群）
    if ENABLE_THEME_LINE_AUDIT:
        try:
            import subprocess as _sp
            _ta = _sp.run([sys.executable,
                           str(Path(__file__).resolve().parent / "theme_line_audit.py"), "--push"],
                          capture_output=True, text=True, timeout=120)
            _tl = [x for x in (_ta.stdout or "").splitlines() if "theme_line_audit]" in x]
            ta_msg = (_tl[0].split("]", 1)[-1].strip()[:60] if _tl else "ok")
        except Exception as e:
            ta_msg = f"theme_line_audit ERR:{e}"
    else:
        ta_msg = "theme_line_audit=skip(开关关闭)"

    # 11) 题材长期归档（2026-10-13）：选股宝 top5 → cache/top5_history/（逐日快照 + 题材库索引）
    #     只读 market_memory 的 prev_top5；交易日闸 + 幂等（已归档不覆盖）；失败降级不阻断。
    if ENABLE_TOP5_HISTORY:
        try:
            import subprocess as _sp
            _th = _sp.run([sys.executable, str(Path(__file__).resolve().parent / "top5_history.py"),
                           "archive"], capture_output=True, text=True, timeout=60)
            _thl = [x for x in (_th.stdout or "").splitlines() if "top5_history]" in x]
            th_msg = (_thl[0].split("]", 1)[-1].strip()[:60] if _thl else "ok")
        except Exception as e:
            th_msg = f"top5_history ERR:{e}"
    else:
        th_msg = "top5_history=skip(开关关闭)"

    # 12) 全量命中股票技术派生：事实归档、股性、题材库均完成后再算，避免半成品。
    technical_msg = _process_stock_technical()

    # 13) 🐉 连板日观察台账（2026-10-27 用户「我们要做好每一个连板日的观察」）。
    #     依赖两件已就绪的产物：① 当日 `zt_history/zt_<date>.json`（本脚本第 2 步写）
    #     ② 当日题材快照 `theme_snapshot/theme_<date>.json`（15:30 eod 首跑落盘）。
    #     ⛔ 必须排在盘后链而不是 15:30 的 eod：实测同一交易日 15:30 与 17:18 的涨停池
    #       会不一致（10-27：42 只 → 41 只，个别票封单 0.24% → 1.65%），
    #       台账要记**收盘定稿口径**，否则「连板日」会记成盘中的样子。
    #     ⛔ 失败只降级不阻断（与其余末步同一纪律）。
    try:
        import subprocess as _sp
        _ld = _sp.run([sys.executable, str(Path(__file__).resolve().parent / "board_leader_daily.py"),
                       "build"], capture_output=True, text=True, timeout=60)
        _ldl = (_ld.stdout or "").strip().splitlines()[-1:]
        ld_msg = (_ldl[0][:70] if _ld.returncode == 0 else f"ERR:{_ld.stderr.strip()[:80]}")
    except Exception as e:
        ld_msg = f"leader_daily ERR:{e}"

    # 14) 🗂 数据源聚合 + 一致性守门（2026-11-04 用户「源必须聚合」·架构级）。
    #     ⛔ 必须排在盘后链**最末**：此刻当日涨停池已定稿（第 2 步）、题材快照已落盘
    #       （15:30 eod）；此步只补四个市场维度，不覆盖早盘竞价候选和终裁。
    #     `snapshot_build.py --guard` = 补全 market/themes/ladder/lines 四个只有构建器会写的
    #       section → 跑 validate → 落 health section（打开当日快照即知数据健不健康）。
    #     ⛔ 只告警不阻断（与第 13 步及其余末步同一纪律）；异常/超时只降级。
    if ENABLE_SNAPSHOT_GUARD:
        try:
            import subprocess as _sp
            _sg = _sp.run([sys.executable,
                           str(Path(__file__).resolve().parent / "snapshot_build.py"),
                           "--date", today, "--only", "market,themes,ladder,lines", "--guard"],
                          capture_output=True, text=True, timeout=120)
            _sgl = [x for x in (_sg.stdout or "").splitlines() if "守门" in x or "覆盖" in x]
            if _sg.returncode != 0:
                detail = (_sg.stderr or _sg.stdout or "无输出").strip()[-240:]
                sg_msg = f"snapshot_guard ERR:rc={_sg.returncode} {detail}"
            else:
                sg_msg = (_sgl[-1].strip()[:70] if _sgl else "ok")
        except Exception as e:
            sg_msg = f"snapshot_guard ERR:{e}"
    else:
        sg_msg = "snapshot_guard=skip(开关关闭)"

    # 15) 🐲 龙头基因沉淀池（2026-11-10 用户「挂在盘后复盘」·不新建定时任务）。
    #     依赖第 2 步刚写好的 zt_history；`dragon_gene.py` 自身会跳过盘中不完整归档，
    #     所以盘中重跑本脚本也不会凭空判断板。⛔ 失败只降级，不阻断复盘主流程。
    try:
        import subprocess as _sp2
        _dg = _sp2.run([sys.executable, str(Path(__file__).resolve().parent / "dragon_gene.py")],
                       capture_output=True, text=True, timeout=120)
        _dgl = [x for x in (_dg.stdout or "").splitlines() if "入池" in x]
        dg_msg = (_dgl[0].strip()[:80] if _dg.returncode == 0 and _dgl
                  else f"ERR:rc={_dg.returncode} {(_dg.stderr or '').strip()[:80]}")
    except Exception as e:
        dg_msg = f"dragon_gene ERR:{e}"

    # 16) 🧭 板块强度 + 同题材身位快照（2026-11-11 用户方案 P0）。
    #     纯派生数据：只读 zt_history / theme_snapshot / kline_qfq，写两份 cache JSON。
    #     ⛔ 不参与任何买入判据；失败只降级，不阻断复盘主流程。
    #     为什么要每天落：板块强度/身位以前算完就丢，没有历史 → 半年后无法回测。
    try:
        import subprocess as _sp3
        _tl = _sp3.run([sys.executable, str(Path(__file__).resolve().parent / "snapshot_theme_ladder.py"),
                        "--date", today, "--quiet"],
                       capture_output=True, text=True, timeout=120)
        tl2_msg = ("theme_ladder ok" if _tl.returncode == 0
                   else f"ERR:rc={_tl.returncode} {(_tl.stderr or '').strip()[:60]}")
    except Exception as e:
        tl2_msg = f"theme_ladder ERR:{e}"

    print(f"[capture_daily {today}] {erupt_msg} | {zt_msg} | {flow_msg} | {emo_msg} | {nf_msg} | {arch_msg} | {tl_msg} | {ss_msg} | {rw_msg} | {zg_msg} | {gg_msg} | {pq_msg} | {pe_msg} | {rwm_msg} | {so_msg} | {ta_msg} | {th_msg} | {ld_msg} | {technical_msg} | {sg_msg} | {dg_msg} | {tl2_msg} | pruned={removed}")


if __name__ == "__main__":
    main()
