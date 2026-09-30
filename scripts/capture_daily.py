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
- 自动清理：涨停池/爆发快照/资金流 保留最近 5 个交易日，短期情绪日志保留最近 5 个交易日（按文件名日期判定，交易日计数天然跳过周末）
- 交易日守卫：main() 先读 feedback_matrix.json 的 error 标记（non_trading_day / zt_pool_unreliable → 非交易日或数据不可靠，跳过归档，防周末假数据污染 5 日窗口）

零新增数据源：复用 intraday_pulse 的 http_post/rc/ts/parse_tomorrow/parse_theme_stocks/build_sector_flow_snapshot。
"""
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                 # import scripts.intraday_pulse
sys.path.insert(0, str(ROOT / "scripts"))     # import intraday_pulse

CACHE = ROOT / "cache"
ERUPT_DIR = CACHE / "eruption_history"
ZT_DIR = CACHE / "zt_history"
# 归档**镜像目录**（双目录同写·2026-09-18）：题材沉淀副本，消费方在 wealth_code 侧。
# ⛔ 写入一律走 `_archive_dirs()` + `zt_archive.write_archives`，不得各自拼路径。
ZT_MIRROR_DIR = ROOT / "wealth_code" / "data" / "zt_history"
FLOW_DIR = CACHE / "sector_flow_history"   # 板块资金流日级时间序列（回流趋势用）
NEWSFLASH_DIR = CACHE / "newsflash_history"  # 外部新闻（快讯）日归档
# 涨停池/爆发快照/资金流保留天数。
# ⚠️【耦合铁律 2026-09-12 N1 修复】此值必须 ≥ 全部消费方的回看窗口上限：
#   scripts/live_signal.py  N_SHAPE_HIST_DAYS = 12（第一波/N字/二波窗口）
#   scripts/theme_rotation.py 的 zt_series 窗口
# 原值 5 < 需求 12 → 盘中形态检测长期缺 7 天料（静默降级）。
# 改任一回看窗口必须同步本值；tests/test_history_window.py 已加守卫断言。
RETAIN_DAYS = 20          # 20 交易日（≈1 个月）·覆盖 12 天需求 + 8 天缓冲
SHORT_TERM_RETAIN = 5     # 短期情绪 daily 日志 保留天数
DAILY_DIR = CACHE / "short_term" / "daily"
TOP_THEMES = 10          # 每天最多存前 10 个爆发题材
STOCKS_PER_THEME = 8     # 每题材最多存前 8 只成分股


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _archive_dirs() -> tuple[Path, ...]:
    """涨停池归档目录（主 + 镜像）。调用时求值 → 测试注入 `ZT_DIR`/`ZT_MIRROR_DIR` 生效。"""
    return (ZT_DIR, ZT_MIRROR_DIR)


def _guard_trading_day(today: str) -> bool:
    """交易日守卫：读 feedback_matrix.json 的 error 标记判断今日是否为可归档交易日。

    feedback_matrix 已有双保险守卫（P0-1：weekday + 涨停池空/自交拦截节假日假数据），
    capture_daily 复用它作为信号源——error 含 non_trading_day / zt_pool_unreliable
    → 非交易日或数据不可靠，跳过归档（防周末/节假日假数据污染 5 日窗口）。
    若 feedback_matrix.json 缺失（首次运行），回退 weekday 检查。
    """
    fb = CACHE / "feedback_matrix.json"
    if fb.exists():
        try:
            err = (json.loads(fb.read_text(encoding="utf-8")) or {}).get("error") or ""
            if "non_trading_day" in err or "zt_pool_unreliable" in err:
                print(f"[capture_daily] {today} 非交易日/数据不可靠（feedback error: {err}），跳过归档")
                return False
        except Exception:
            pass
    if datetime.now().weekday() >= 5:
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
    # —— zt_history + eruption_history + sector_flow_history：RETAIN_DAYS ——
    pref_map = {ERUPT_DIR: "eruption_", ZT_DIR: "zt_", FLOW_DIR: "sector_flow_"}
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
            return 0
        boards = _tx_backfill_flow(boards)
        FLOW_DIR.mkdir(parents=True, exist_ok=True)
        (FLOW_DIR / f"sector_flow_{_today()}.json").write_text(json.dumps({
            "date": _today(),
            "generated_ts": datetime.now().isoformat(),
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
        "zt_count": d.get("zt_count"),
        "promote_rate": d.get("promote_rate"),
        "lb_promote_rate": d.get("lb_promote_rate"),
        "avg_pct": d.get("avg_pct"),
        "up_rate": d.get("up_rate"),
        "nuke_count": d.get("nuke_count"),
        "break_count": d.get("break_count"),
        "max_boards": d.get("max_boards"),
        "prev_max_boards": d.get("prev_max_boards"),
        "sentiment_score": d.get("sentiment_score"),
        "sentiment_label": d.get("sentiment_label"),
        "generated_ts": d.get("generated_ts") or datetime.now().isoformat(),
    }
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    (DAILY_DIR / f"emotion_{today}.json").write_text(json.dumps(snap, ensure_ascii=False, indent=1))
    return snap


def capture_newsflash(today: str) -> int:
    """外部新闻（快讯）日归档：读 latest.json 的 newsflash → newsflash_history/daily_{today}.json。

    盘中快讯只进 latest.json（当日覆盖），盘后归档留存当日全部硬信号快讯，
    供 5 日汇总/复盘做「事件/催化」维度引用（情绪判断的事件驱动源头）。
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
    NEWSFLASH_DIR.mkdir(parents=True, exist_ok=True)
    (NEWSFLASH_DIR / f"daily_{today}.json").write_text(json.dumps({
        "date": today,
        "generated_ts": datetime.now().isoformat(),
        "newsflash": nf,
    }, ensure_ascii=False, indent=1))
    return len(nf)


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
        "generated_ts": datetime.now().isoformat(),
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
            "generated_ts": datetime.now().isoformat(),
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

    print(f"[capture_daily {today}] {erupt_msg} | {zt_msg} | {flow_msg} | {emo_msg} | {nf_msg} | {tl_msg} | {ss_msg} | {rw_msg} | {zg_msg} | {pq_msg} | {pe_msg} | {rwm_msg} | pruned={removed}")


if __name__ == "__main__":
    main()
