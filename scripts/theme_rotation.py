#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材轮动回流预测引擎 v1。

读近 N 天的「题材爆发快照」(cache/eruption_history/) 为主时间序列，
交叉「涨停池历史」(cache/zt_history/) 广度、「催化标题」(eruption.title) 新鲜度、
「反馈矩阵」(cache/feedback_matrix.json) 大盘环境、「资金流出榜」(latest.json.outflow_top) 资金撤离，
为每个题材打周期相位标签并产出「回流候选 + 跟踪股票池」。

输出 cache/theme_rotation.json，供盘前卡「🔮题材回流预测」、盘后卡「📊轮动复盘」读取。

设计取舍（v1 诚实边界）：
- 主时间序列用 eruption_history（同接口、题材名跨日一致，无需模糊匹配涨停池板块名）。
- 涨停池仅取市场广度(zt_count 趋势)，不做 per-theme 板块名模糊匹配（v1 避免误匹配噪声）。
- 龙头破五日线需逐票拉日K，v1 不做（成本高），改为盘前卡「触发条件」由盘中实时确认。
- 第一周数据不足时只产 boom 跟踪基线，不强行预测回流；攒满 5 天后模式才生效。
"""
import json
import os
import sys
import urllib.request
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
ERUPT_DIR = CACHE / "eruption_history"
ZT_DIR = CACHE / "zt_history"
OUT_PATH = CACHE / "theme_rotation.json"

# 交易日历（唯一真源·2026-10-09）。窗口必须按交易日计：长假期间盘中写入的空壳
# （intraday_only·zt_list=[]，如 10-01/02/05）会占满 `files[-ndays:]` 的名额。
# 防御性导入，失败退化为不过滤（保旧行为），绝不因日历不可用而中断本模块。
try:
    from common.trading_calendar import is_a_share_trading_day
except ImportError:
    sys.path.insert(0, str(ROOT))
    try:
        from common.trading_calendar import is_a_share_trading_day
    except Exception:  # pragma: no cover
        def is_a_share_trading_day(_value) -> bool:  # type: ignore[misc]
            return True

# 规范时间戳（2026-10-14 审计 P1-⑦）：generated_ts 统一 ISO8601 带 +08:00，
# 旧 naive `.isoformat()`（无时区）会让 `link_guard` 的 aware-naive 相减抛错而静默失效。
try:
    from common.market_time import iso_bj
except ImportError:  # pragma: no cover - 极端环境兜底（脚本从任意 cwd 调用）
    import sys as _sys
    _sys.path.insert(0, str(ROOT))
    try:
        from common.market_time import iso_bj
    except Exception:
        def iso_bj(_dt=None) -> str:  # type: ignore[misc]
            return (_dt or datetime.now()).astimezone().isoformat(timespec="seconds")

# 题材周期相位判定器（2026-11-03）。防御性导入：模块缺失 → phase_v2 恒 None，
# ⛔ 绝不因相位模块不可用而中断轮动主链。
try:
    from core import theme_phase as _tp
except ImportError:
    sys.path.insert(0, str(ROOT / "wealth_code"))
    try:
        from core import theme_phase as _tp
    except Exception:  # pragma: no cover
        _tp = None

# 题材基座（选股宝 plates 原值）。2026-11-03 用户拍板「先按选股宝走」：
# 家数 = **当日归属到该题材的票 ∩ 当日涨停池**，⛔ 不是「该题材的历史成员今天有几只涨停」。
# 分类与交集算法只从公共基座导入，不在消费方复制兜底算法。
from common.theme_base import (
    is_attr_plate as _is_attr_plate,
    snapshot_theme_members,
    count_member_limitups,
)

TRADING_LOOKBACK = 5     # 5 日窗口：最近 5 个**交易日**文件（2026-08-16 由日历日回看 6 天改交易日计数）
BOOM_ZT = 3                # 爆发阈值：题材当日涨停数 >= 3 视为 boom
REENTRY_GAP_MIN_DAYS = 2   # 回流确认：两次爆发间隔≥2个冷却交易日（间隔≥3交易日）才认回流，过滤电风扇
REENTRY_LOOKBACK_LONG = 15  # 回流确认长历史回看（交易日）：覆盖冷却期超过5日窗口的回流（如09-30医药冷却约一周）
BOOM_PCT = 4.0             # 或涨幅 >= 4% 也算 boom
COOL_DAYS_FOR_REENTRY = 2  # 退潮满 2 天后列为回流候选
COOL_STALE_DAYS = 4         # 2026-08-27 B档：re-entry 悬挂超时——冷却满4天且资金未现回流 → 移出回流候选
SURFACE_CONFIDENCE = 0.25   # 置信度阈值（2026-08-18 放宽 0.4→0.25 以沉淀更多概念；re-entry-candidate 不受此限）
MAX_CANDIDATES = 12         # 候选沉淀上限（2026-09-07 用户「板块数量放大至少10个」→ 原8改12，保底≥10有冗余）

# ── 题材周期相位（2026-11-03 落地·用户在调研后拍板「和题材回流一起做」）──
# 病根：本文件的 `phase` 只有一个值 re-entry-candidate，回答的是**跨周期回流**
# （旧题材冷却后复活）；它回答不了「**这波主线走到第几天了**」。
# 实证：传媒 11-02 是 27 家、11-03 是 9 家，而 BOOM_ZT=3 让两天都是 boom
# → 高潮与首日启动在旧口径里长得一模一样，但操作完全相反。
# 故加算 `phase_v2`（独立字段，⛔ 不覆盖旧 phase，避免打断既有消费方与测试）。
PHASE_LOOKBACK = 10         # 家数序列窗口（交易日）。旧 zt_series 只有 5 天 → 算不出「第几天」
ENABLE_THEME_PHASE = os.environ.get("ENABLE_THEME_PHASE", "1") != "0"

# —— 主板倾斜铁律（对齐 shared_rules：科创板688无权限不推 / 北交所8·4·920不推 / 主板优先）——
EXCLUDE_PREFIX = ("688", "8", "4", "920")  # 直接剔除：科创板 + 北交所
MAIN_POOL_SIZE = 8          # 单题材跟踪池容量上限
MAIN_FILL_MIN = 5           # 主板票期望下限，不足则从涨停池补位
MAX_CYB_SEATS = 1           # 创业板(300/301)最多保留席位，且须标注风险
SUPPLEMENT_FROM_ZT = True   # 主板不足时从涨停池同板块补主板涨停票

# ── P2-14 避险反向抽取观察档（2026-09-11 落地·宪法【避险反向抽取 vs 温和回流】）──
# 引擎盲区：candidates 只认"资金 5D/10D 双正 + 涨停≥3"，退潮日被避险资金单日暴动的
# **深度超跌板块**（5 日资金深负）完全不可见（实证 2026-09-03 白银有色：有色 5 日
# -207.6 亿·全场流出第 1·引擎 candidates 无有色）。本档独立输出、⛔ 不进买点池。
ENABLE_AVOIDANCE_SWEEP = True   # 可逆开关：False=不产出 avoidance_sweep（回退现状）
AVOID_SENTIMENT_MAX = 2.0       # ① 大盘退潮：情绪分 ≤ 此值
AVOID_NUKE_MIN = 3              #    或 核按钮 ≥ 此值（满足其一）
AVOID_FLOW_DEEP_YI = -50.0      # ② 板块 5 日累计资金 ≤ 此值（亿元·深负）
AVOID_TOP_N = 5                 # 观察档最多输出条数


def _load(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


# 资金流向榜噪声过滤词表统一从 scripts/flow_noise.py 引入（单一权威来源，禁止在此维护副本）
try:
    from flow_noise import is_flow_noise
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from flow_noise import is_flow_noise


def _is_flow_noise(nm):
    return is_flow_noise(nm)


def _market_context():
    """大盘环境 + 资金流出榜，供置信度判定。"""
    fb = _load(CACHE / "feedback_matrix.json") or {}
    latest = _load(CACHE / "pulse_snapshots" / "latest.json") or {}
    sr = latest.get("sector_risk") or {}
    return {
        **{k: sr.get(k) for k in ("status", "asof", "error", "reference_only", "age_seconds", "sources")},
        "sentiment_score": fb.get("sentiment_score"),
        "nuke_count": fb.get("nuke_count"),
        "promote_rate": fb.get("promote_rate"),
        "outflow_top": [o for o in (sr.get("outflow_top") or []) if not _is_flow_noise(o.get("name"))],
        "inflow_top": [i for i in (sr.get("inflow_top") or []) if not _is_flow_noise(i.get("name"))],
        "zt_count_today": (fb.get("zt_count_today") or _load(CACHE / "yesterday_zt.json", ) or {}).get("zt_count"),
    }


def _recent_files(glob_pattern: str, prefix: str, n: int):
    """取最近 N 个**交易日**文件（按文件日期降序计数，非日历日回看）。

    2026-08-16 修复：原按日历日 (today - timedelta) 回看，长假后（如国庆 8 天）
    6 个日历日覆盖不到 5 个交易日文件 → 5 日回流模式失效。改为按实际存在的
    归档文件日期排序取前 N 个——文件只在交易日生成（capture_daily 守卫），
    天然跳过周末/节假日。返回升序 [(date, path)]。
    """
    from pathlib import Path as _P
    dated = []
    for p in sorted(_P(glob_pattern).parent.glob(_P(glob_pattern).name)):
        stem = p.name[len(prefix):-5] if p.name.endswith(".json") else p.name[len(prefix):-3]
        try:
            dd = datetime.strptime(stem, "%Y-%m-%d").date()
        except Exception:
            continue
        dated.append((dd, p))
    dated.sort(key=lambda x: x[0], reverse=True)
    dated = dated[:n]
    dated.sort(key=lambda x: x[0])
    return dated


def _load_series(ndays: int = None):
    """加载最近 ndays 个**交易日**的 eruption 快照，按日期升序。返回 [(date_str, {theme_name: theme_obj})]。"""
    series = []
    if not ERUPT_DIR.exists():
        return series
    for dd, p in _recent_files(str(ERUPT_DIR / "eruption_*.json"), "eruption_", ndays or TRADING_LOOKBACK):
        data = _load(p)
        if not data:
            continue
        mp = {}
        for t in (data.get("themes") or []):
            nm = t.get("name")
            if nm:
                mp[nm] = t
        series.append((dd.strftime("%Y-%m-%d"), mp))
    series.sort(key=lambda x: x[0])
    return series


def _is_boom(t):
    """当日是否算爆发。"""
    if not t:
        return False
    return (t.get("zt") or 0) >= BOOM_ZT or (t.get("pct") or 0) >= BOOM_PCT


def _classify_reentry(per_day: list, boom_idx: int, gap_min: int = REENTRY_GAP_MIN_DAYS):
    """判定题材是否「冷却后回流确认」（2026-09-30）。

    条件：① 今日真实爆发(per_day[-1] 是 boom) ② 窗口内更早也曾爆发过
    ③ 两次爆发之间间隔 ≥ gap_min 个冷却交易日。
    电风扇板块每天 boom、间隔 0 → 返回 False（不认回流，避免假回流刷屏）。
    返回 (is_reentry, reentry_gap_days)。
    """
    _prev = None
    for i in range(boom_idx - 1, -1, -1):
        if _is_boom(per_day[i]):
            _prev = i
            break
    _today_boom = per_day[-1] is not None and _is_boom(per_day[-1])
    is_reentry = (_prev is not None and (boom_idx - _prev - 1) >= gap_min and _today_boom)
    gap_days = (boom_idx - _prev - 1) if _prev is not None else 0
    return is_reentry, gap_days


def _heat(t):
    """单日热度分：涨停数*2 + 涨幅。"""
    if not t:
        return 0.0
    return (t.get("zt") or 0) * 2 + (t.get("pct") or 0)



def _family_pick(day_map: dict, name: str):
    """同族改名兜底：当天没有 `name` 时，取同族里当天有数据的那个题材（只取一个）。"""
    try:
        from common.theme_base import THEME_FAMILIES
    except Exception:  # noqa: BLE001
        return None
    for members in THEME_FAMILIES.values():
        if name not in members:
            continue
        for alt in members:
            if alt != name and alt in day_map:
                return day_map[alt]
    return None


def _zt_series_of(per_day):
    """近 N 交易日该题材涨停数序列（None=当日缺席），与轮动相位同源。"""
    return [(t.get("zt") if t else None) for t in per_day]


def _zt_dir(zt_series):
    """板数趋势方向（对比最近两个非空点）：rising 升温 / falling 退潮 / flat 平稳 / new 仅一日现身。"""
    vals = [v for v in zt_series if v is not None]
    if not vals:
        return "flat"
    if len(vals) == 1:
        return "new"
    if vals[-1] > vals[-2]:
        return "rising"
    if vals[-1] < vals[-2]:
        return "falling"
    return "flat"


def _zt_delta(zt_series):
    """近期动量：末非空点 - 次非空点（板数环比增减）。"""
    vals = [v for v in zt_series if v is not None]
    if len(vals) < 2:
        return 0
    return vals[-1] - vals[-2]


_SNAP_CACHE: dict = {"sig": None, "res": None}
SNAPSHOT_DIR = CACHE / "theme_snapshot"
SNAPSHOT_MAX_DAYS = 20       # 往回调多少个题材冻结快照（现存 12 天，20 有冗余）
PHASE_MIN_THEME_CODES = 5    # 该题材在快照里少于这个票数 → 家数会被低估，不判相位

_TOP5_CODES_CACHE: dict = {"sig": None, "res": None}
TOP5_DIR = CACHE / "top5_history"
TOP5_CODE_DAYS = 30          # 往回调多少个 top5 快照来聚合成份股
PHASE_MIN_CODES = 5          # 成分股少于这个数 → 家数会系统性低估，不判相位


def _snapshot_plates_by_date():
    """扫描 `cache/theme_snapshot/`，返回 `{date: {选股宝基座名: set(code)}}`。

    这是**相位家数的唯一主口径**（2026-11-03 用户拍板「先按选股宝走」）。

    为什么不用 `top5_history` 的跨日并集（上一版做法）：
      · 它会把「历史上某天挂过这个题材名」的票算成「今天在炒这条线」——
        11-02「大消费 19 家」的名单里有阳光照明（照明）、松炀资源（造纸）、
        大千生态（园林），凭空造出 4 家。
      · 「人形机器人」在 11-02 快照里根本没有这个 key（只有「机器人」），
        子串匹配硬把它并过去，算出一条**当天不存在的线**。
    而快照 `mapping` 覆盖 125/125 涨停，实测 8/9 个题材与选股宝官方 `zt_count`
    完全一致（仅短剧 29 vs 27），绝对误差合计 2。

    归属规则 = `theme_of_plates(plates)`（第一个**非属性标签**项）：
      · 属性标签（业绩增长/ST股/重组…）不是题材，⛔ 不进聚合；
      · 全部是属性标签 → 该票不算任何题材（⛔ 不编「其他」）。
    """
    if not SNAPSHOT_DIR.exists():
        return {}
    import os
    files = sorted(SNAPSHOT_DIR.glob("theme_*.json"))[-SNAPSHOT_MAX_DAYS:]
    try:
        sig = tuple(os.path.getmtime(f) for f in files)
    except OSError:
        sig = None
    if _SNAP_CACHE["sig"] == sig and _SNAP_CACHE["res"] is not None:
        return _SNAP_CACHE["res"]
    out: dict = {}
    for f in files:
        d = _load(f) or {}
        date = d.get("date") or f.stem.replace("theme_", "")
        mp = d.get("mapping") or {}
        if not isinstance(date, str) or not mp:
            continue
        out[date] = snapshot_theme_members(mp)
    _SNAP_CACHE["sig"] = sig
    _SNAP_CACHE["res"] = out
    return out


def _plates_match(nm, plate_names):
    """eruption 题材名 → 快照里匹配的选股宝板块名集合。

    规则与 `_match_top5_codes` 同一条：exact 命中**也要继续并子串**
    （快照里可能同时有「机器人」和「人形机器人」，只取 exact 会漏掉当天真有
    13 家涨停的那条），短名必须是长名的真子串且 ≥2 字（挡「电子⊂消费电子」）。
    """
    out = set()
    if not nm:
        return out
    for k in (plate_names or ()):
        if not k:
            continue
        if k == nm:
            out.add(k)
        elif (len(nm) >= 2 and nm in k) or (len(k) >= 2 and k in nm):
            out.add(k)
    return out


def _top5_codes_by_theme():
    """扫描 `cache/top5_history/`，聚合 {题材名: set(成分股code)}。

    为什么必须合并这一份：eruption 记录只带**热门爆发题材的少量成分股**（实测
    「人形机器人」只有 6 只），用它 ∩ 涨停池算家数会系统性低估——11-02 机器人
    真实涨停 13 家，按 eruption 名单只算出 0 家，相位被误判成「退潮」。
    选股宝 top5 快照里 `codes`/`stocks` 是**当日该题材全部涨停票**，跨日并集后
    才够覆盖。

    返回 {题材名: set(codes)}；目录不存在返回 {}。
    """
    if not TOP5_DIR.exists():
        return {}
    import os
    files = sorted(TOP5_DIR.glob("top5_*.json"))[-TOP5_CODE_DAYS:]
    try:
        sig = tuple(os.path.getmtime(f) for f in files)
    except OSError:
        sig = None
    if _TOP5_CODES_CACHE["sig"] == sig and _TOP5_CODES_CACHE["res"] is not None:
        return _TOP5_CODES_CACHE["res"]
    out: dict = {}
    for f in files:
        d = _load(f) or {}
        for t in d.get("themes") or []:
            if not isinstance(t, dict):
                continue
            nm = t.get("name")
            if not isinstance(nm, str) or not nm:
                continue
            codes = set()
            for c in (t.get("codes") or []):
                codes.add(str(c).split(".")[0])
            for s in (t.get("stocks") or []):
                c = str((s or {}).get("code") or "").split(".")[0]
                if c:
                    codes.add(c)
            if codes:
                out.setdefault(nm, set()).update(codes)
    _TOP5_CODES_CACHE["sig"] = sig
    _TOP5_CODES_CACHE["res"] = out
    return out


def _match_top5_codes(nm, top5_codes):
    """把 eruption 题材名对到 top5 题材名，取并集成分股。

    ⚠️ **exact 命中也要继续并**（2026-11-03 踩到的坑）：top5 里可能同时存在
    「人形机器人」和「机器人」两个名字，若 exact 命中就 return，会只拿到前者那几只，
    而后者才是当天真有 13 家涨停的那条线 —— 实测机器人峰值家数因此从 16 掉到 7、
    今日家数掉到 1，相位被误判成「退潮」。

    匹配规则：短名必须是长名的真子串且 ≥2 字（挡「电子⊂消费电子」这类泛词靠长度闸）。
    """
    if not top5_codes:
        return set()
    out = set(top5_codes.get(nm) or ())
    for k, v in top5_codes.items():
        if not k or k == nm:
            continue
        if (len(nm) >= 2 and nm in k) or (len(k) >= 2 and k in nm):
            out |= v
    return out


def _plates_seq(nm, series_date, *, today_codes=None, ndays: int = PHASE_LOOKBACK):
    """**主口径**：选股宝基座逐日归属 ∩ 当日涨停池 → 家数序列。

    返回 dict（含 `seq`/`hits`）或 None（判不出）。

    ⛔ 三条硬约束：
      ① 某天快照缺失 → 那天写 `None`（不知道），**绝不当 0**
         （当 0 会把「没记账」判成「线没了」，正是我们要避开的假退潮）；
      ② 历史与今日必须同一口径（都用 snapshot_plates），不混代码并集；
      ③ 序列只到 `series_date`，⛔ 不吃未来数据。
    """
    snaps = _snapshot_plates_by_date()
    if not snaps:
        return None
    all_names = set()
    for b in snaps.values():
        all_names |= set(b.keys())
    hits = _plates_match(nm, all_names)
    if not hits:
        return None
    universe = set()
    for b in snaps.values():
        for h in hits:
            universe |= (b.get(h) or set())
    if len(universe) < PHASE_MIN_THEME_CODES:
        return None
    try:
        days, by_date = _load_zt_pool_series(ndays)
    except Exception:                       # pragma: no cover - 防御
        return None
    if not days:
        return None

    def _codes_on(date):
        b = snaps.get(date)
        if b is None:
            return None
        cs = set()
        for h in hits:
            cs |= (b.get(h) or set())
        return cs

    seq = []
    for d in days:
        if d > series_date:
            break
        pool = by_date.get(d) or {}
        cs = _codes_on(d)
        if not pool or cs is None:
            seq.append(None)
        else:
            seq.append(count_member_limitups(cs, pool.keys()))
    # 主序列末日：优先涨停池已归档；否则用当日快照 + 当日实时涨停池补一天
    if series_date not in by_date:
        cs = _codes_on(series_date)
        if cs is not None and today_codes and series_date == datetime.now().strftime("%Y-%m-%d"):
            seq.append(count_member_limitups(cs, today_codes))
        else:
            return None
    return {"seq": seq, "hits": sorted(hits), "universe": len(universe)}


def _codes_seq(nm, theme_codes, series_date, *, today_codes=None, ndays: int = PHASE_LOOKBACK):
    """**兜底口径**：题材历史成员代码并集 ∩ 当日涨停池。

    仅当选股宝快照判不出时使用（快照缺失 / 题材名对不上）。会系统性高估
    （历史成员今天因别的原因涨停也算进来），故标记 `src=pool_codes` 以示区别。
    """
    codes = set(theme_codes or ())
    codes |= _match_top5_codes(nm, _top5_codes_by_theme())
    if len(codes) < PHASE_MIN_CODES:
        return None
    try:
        days, by_date = _load_zt_pool_series(ndays)
    except Exception:                       # pragma: no cover - 防御
        return None
    if not days:
        return None
    seq = []
    for d in days:
        if d > series_date:                 # ⛔ 不许吃未来数据：序列只到主序列末日
            break
        pool = by_date.get(d) or {}
        seq.append(len(codes & set(pool.keys())) if pool else None)
    if series_date not in by_date:
        # 该日涨停池尚未归档：仅在「有当日实时涨停池」时补一天，否则不判
        if today_codes and series_date == datetime.now().strftime("%Y-%m-%d"):
            seq.append(len(codes & set(today_codes)))
        else:
            return None
    return {"seq": seq, "hits": [], "universe": len(codes)}


def _phase_of(nm, theme_codes, series_date, *, today_codes=None, ndays: int = PHASE_LOOKBACK):
    """算出该题材的周期相位（theme_phase_v1）。

    **口径（2026-11-03 用户拍板「先按选股宝走」）**：
      家数 = 当日归属到该题材的票 ∩ 当日涨停池，
      归属取 `theme_snapshot` 里选股宝 `plates` 的**第一个非属性标签项**。
      ⛔ 不是「该题材的历史成员今天有几只涨停」——那会凭空造线（实证见
      `_snapshot_plates_by_date` 的 docstring）。

    兜底顺序：snapshot_plates（主）→ pool_codes（兜底，落 `src` 可区分）。
    两者不混进同一条序列：任一口径判不出就整体降级，⛔ 不按天切换。
    ⚠️ **例外**：快照机制在工作、但 `series_date` 那天没落盘 → 直接返回 None，**不降级**
    （否则末端那天的「今日家数」来自另一套源，序列会跳变；相位本就是盘后指标）。

    `series_date` = 主序列（eruption）的末日：
      · 涨停池已含该日 → 直接判（盘后跑的常态）
      · 涨停池不含（今日 zt_list 未落盘）→ 用 `today_codes`（盘中实时涨停池）补一天；
        没有实时池就返回 None，⛔ 不拿昨天冒充今天

    ⚠️ 题材在快照里票数 < `PHASE_MIN_THEME_CODES` → 返回 None：
       家数会被系统性低估，宁可空着也不给一个「假退潮」。
    ⚠️ 属性标签（业绩增长/ST股/重组…）不是线，直接返回 None。
    """
    if _tp is None or not series_date:
        return None
    if _is_attr_plate(nm):
        return None
    # ⛔ 快照机制在工作、但**这一天**没落盘 → 今天家数就是「不知道」，不许拿兜底冒充。
    #   为什么这条必须硬拦（2026-11-03 实跑踩到）：兜底的「今日家数」= 历史成员今天
    #   有几只涨停，而历史各天是「当日归属」——同一序列两种源，末端必然跳变。
    #   实测 11-03 无快照时降级，锂电池算出 7 家（主口径下只有 3 家），
    #   等 eod 落盘后又变回 3 家，相位从「启动」跳成别的，用户会以为线变了。
    #   代价是盘中看不到相位（相位本就是盘后指标），换来的是**数字可信**。
    snaps_now = _snapshot_plates_by_date()
    if snaps_now and series_date not in snaps_now:
        return None
    src = "snapshot_plates"
    got = _plates_seq(nm, series_date, today_codes=today_codes, ndays=ndays)
    if not got:
        src = "pool_codes"
        got = _codes_seq(nm, theme_codes, series_date, today_codes=today_codes, ndays=ndays)
    if not got:
        return None
    seq = got["seq"]
    try:
        out = _tp.judge(seq)
    except Exception:                       # pragma: no cover - 防御
        return None
    if isinstance(out, dict):
        # 可追溯：把判定依据的家数序列一起落盘。⛔ 只读展示，不参与排序/评分。
        # 2026-11-03 补：此前只存 peak/cur/prev，看不出「凭什么说第 8 天」，
        # 也无法和 zt_series（选股宝口径）对照 —— 两套家数打架时无从查起。
        out["series"] = list(seq)
        out["src"] = src
        out["count_evidence"] = {
            "date": series_date,
            "source_at": None,  # 日频重建未保留逐点源时刻，不以运行时刻冒充
            "basis": "daily_plates_intersection" if src == "snapshot_plates" else "historical_members_estimate",
            "scope": "theme_snapshot.mapping ∩ zt_pool" if src == "snapshot_plates" else "historical_codes ∩ zt_pool",
        }
        if got.get("hits"):
            out["plates"] = got["hits"]      # 匹配到的选股宝板块名，便于人工复核
    return out


def _code_board(code):
    """板块归类：main 主板 / cyb 创业板 / star 科创板 / bse 北交所 / other。"""
    c = (code or "").split(".")[0]
    if c.startswith("688"):
        return "star"
    if c.startswith(("300", "301")):
        return "cyb"
    if c.startswith(("60", "000", "001", "002", "003", "605")):
        return "main"
    if c.startswith(("8", "4", "920")):
        return "bse"
    return "other"


def _is_excluded(code):
    """按铁律剔除：科创板(688)与北交所(8/4/920)无权限/流动性差不推。"""
    return (code or "").split(".")[0].startswith(EXCLUDE_PREFIX)


def _rank_main_first(stocks):
    """主板优先、其次创业、最后其他；同板块内龙头优先、涨幅高优先。"""
    order = {"main": 0, "cyb": 1, "star": 2, "bse": 3, "other": 4}
    return sorted(stocks, key=lambda s: (
        order.get(_code_board(s.get("code")), 4),
        0 if s.get("dragon") else 1,
        -(s.get("pct") or 0),
    ))


def _supplement_main(theme_name, need, exclude):
    """主板不足时，从最近一天涨停池(zt_history)按 sector 模糊匹配题材名，
    捞同板块主板涨停票补位（真实涨停的主板票，逻辑更扎实）。"""
    if not SUPPLEMENT_FROM_ZT or need <= 0:
        return []
    files = sorted(ZT_DIR.glob("zt_*.json"))
    if not files:
        return []
    try:
        data = json.loads(files[-1].read_text())
    except Exception:
        return []
    zt_list = data.get("zt_list") or []
    name = theme_name or ""
    out = []
    for it in zt_list:
        sec = it.get("sector") or ""
        if not (name in sec or sec in name):      # 题材名 ↔ 板块名双向包含
            continue
        code = it.get("code")
        if _code_board(code) != "main" or _is_excluded(code):
            continue
        if code in exclude:
            continue
        out.append({"code": code, "name": it.get("name"),
                    "dragon": False, "pct": it.get("pct"),
                    "cap_yi": None, "board": "main",
                    "from_zt": True, "risk": ""})
    return out[:need]


def _pool_note(pool, supp):
    """跟踪池构成说明，供卡片端直接展示。"""
    mains = [s for s in pool if s["board"] == "main"]
    cybs = [s for s in pool if s["board"] == "cyb"]
    if not pool:
        return "该题材当前无主板核心票，仅作题材观察"
    parts = []
    if supp:
        parts.append(f"自涨停池补{len(supp)}只主板")
    if cybs:
        parts.append(f"含创业板{len(cybs)}只·已标注风险")
    if not mains:
        parts.append("主板不足·观察为主")
    return "；".join(parts) if parts else "主板为主"


# —— 🔧 题材量数据补全（2026-09-02 用户「优化题材量数据」）——
# eruption_history 只记**热门爆发题材**（getFryTomorrowList isHot 过滤·每日仅 3-5 个），大量当日
# 实际有涨停的题材缺席 → zt_series 稀疏（实测候选多数 1-2/5）。zt_history 是**全量涨停池**
# （每日 50-90 票·含当日盘后），用「成分股 code ∩ 涨停池 code」补 eruption 缺席日的真实涨停数
# （code 唯一不受题材/行业命名体系差异影响；eruption 成分股=该题材近期涨停票的可靠代理）。
_ZT_POOL_CACHE: dict = {"sig": None, "res": None}


def _load_zt_pool_series(ndays: int = TRADING_LOOKBACK):
    """加载近 N 交易日**全量涨停池**（zt_history 各日 zt_list + yesterday_zt 补当日缺失日）→
    (days升序, {date: {code: {"name","sector"}}})。mtime 缓存同 _load_zt_history。
    排除今日文件（今日涨停用 build 内 today_zt_codes 从 yesterday_zt 直接取·口径一致）。"""
    import glob
    import os
    # ⛔ 2026-10-09：**先按交易日过滤，再切片**。原实现先 `files[-ndays:]` 后靠
    # `if not zl: continue` 跳空壳 → 长假空壳照样占名额，5 日窗实际只剩 2 个真实交易日。
    files = [f for f in sorted(glob.glob(str(ZT_DIR / "zt_*.json")))
             if is_a_share_trading_day(Path(f).stem[3:])][-ndays:]
    yz_path = CACHE / "yesterday_zt.json"
    try:
        sig = tuple(os.path.getmtime(f) for f in files) + (os.path.getmtime(yz_path),)
    except OSError:
        sig = None
    if _ZT_POOL_CACHE["sig"] == sig and _ZT_POOL_CACHE["res"] is not None:
        return _ZT_POOL_CACHE["res"]
    today = datetime.now().strftime("%Y-%m-%d")
    by_date: dict = {}
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        date = str(d.get("date") or "")
        if not date or date >= today:
            continue
        zl = d.get("zt_list") or []
        if not zl:
            continue                      # 盘中快照 zt_list 空 → 跳过
        by_date[date] = {str(x.get("code") or "").split(".")[0]: {"name": x.get("name"), "sector": x.get("sector")}
                         for x in zl if x.get("code")}
    # yesterday_zt 补当日缺失（如 zt_history 最新完整日之前某日 capture_zt 未沉淀）
    yz = _load(yz_path) or {}
    ydate = str(yz.get("date") or "")
    if ydate and ydate < today and ydate not in by_date:
        zl = yz.get("zt_list") or []
        if zl:
            by_date[ydate] = {str(x.get("code") or "").split(".")[0]: {"name": x.get("name"), "sector": x.get("sector")}
                              for x in zl if x.get("code")}
    days = sorted(by_date.keys())
    res = (days, by_date)
    _ZT_POOL_CACHE["sig"] = sig
    _ZT_POOL_CACHE["res"] = res
    return res


FLOW_DIR = CACHE / "sector_flow_history"
FLOW_LOOKBACK = 5        # 资金趋势窗口：看 D-5 ~ D-1（2026-08-26 用户「扩大为5日流入流出板块监管」）
FLOW_MIN_VALID = 3       # 空洞容忍：5日窗口内至少 3 个有效点才判定资金趋势（东财板块名单每日漂移）
FLOW_TOP_N = 15          # 五日累计资金榜：净流入/流出各取前 N 个板块（监管总览）


def _load_flow_series():
    """加载最近 FLOW_LOOKBACK 个**交易日**的板块资金流快照（全量），按日期升序。
    返回 [(date_str, {board_name: {"pct":, "flow_yi":, "is_focus":}})].
    今日未开盘时盘前读取用昨日沉淀（今天文件可能不存在）。
    2026-08-16 修复：改为交易日计数（同 _load_series），长假/周末不缩窗。"""
    series = []
    if not FLOW_DIR.exists():
        return series
    for dd, p in _recent_files(str(FLOW_DIR / "sector_flow_*.json"), "sector_flow_", FLOW_LOOKBACK + 1):
        data = _load(p)
        if not data:
            continue
        boards = data.get("boards") or {}
        series.append((dd.strftime("%Y-%m-%d"), boards))
    series.sort(key=lambda x: x[0])
    # 取最近 FLOW_LOOKBACK 个历史交易日点（含今日则剔除今日，留纯历史窗口）
    if len(series) > FLOW_LOOKBACK:
        series = series[-FLOW_LOOKBACK:]
    return series


# 题材名 ↔ 板块资金流名 同义词表（eruption 题材名常与申万板块名不一致，需桥接）
SYNONYMS = {
    "兵装重组": "国防军工", "兵装": "国防军工", "军工": "国防军工",
    "地面兵装": "国防军工", "中兵系": "国防军工",
    "PCB": "印制电路板", "半导体": "半导体概念", "存储": "存储芯片",
    # 2026-08-26 补：题材名 ↔ 东财板块名桥接
    "焦炭": "焦煤", "黄金概念": "黄金",
    # 2026-09-01 补（用户「回流板块匹配不对·很多没有数据」）：候选概念板块 → 东财实际板块名
    # ⛔ 粮食概念不桥接"农业"（boards 无此板块·死桥反致数据全丢）→ 走 FLOW_GROUPS 聚合农林牧渔
    "液冷概念": "液冷服务器", "AI漫剧": "短剧互动游戏", "城市更新概念": "房地产开发",
    "电子布": "玻纤制造", "算力租赁": "算力概念", "CPO概念": "CPO概念",
}

# 题材族 → 资金流板块族（2026-09-01 用户「大金融回流了数字货币还在等待回流·不靠谱」修复）：
# 概念板块（数字货币/区块链/金融科技）在东财资金流名单里**每日漂移、经常查不到**（实测数字货币 5 天仅 1 天有数据，
# 而互联网金融/银行/非银金融等父级行业板块 4~5 天齐全）→ 自身数据空洞时聚合族内板块 flow_yi，
# 让「数字货币」的资金判定反映真实的大金融回流（银行末点转流入/非银流入即回流信号）。
# 粮食概念同因（自身 1/5，农林牧渔 5/5）。
FLOW_GROUPS = {
    "数字货币": ["互联网金融", "非银金融", "多元金融", "银行", "证券Ⅱ", "金融控股"],
    "区块链": ["互联网金融", "非银金融", "多元金融", "银行"],
    "金融科技": ["互联网金融", "非银金融", "多元金融", "证券Ⅱ"],
    "大金融": ["银行", "证券Ⅱ", "保险Ⅱ", "非银金融", "多元金融", "互联网金融"],
    "互联网保险": ["保险Ⅱ", "保险Ⅲ", "互联网金融"],
    "粮食概念": ["农林牧渔", "种植业", "农业种植"],
    "大农业": ["农林牧渔", "种植业", "农业种植", "粮食概念"],
    "AI漫剧": ["短剧互动游戏", "游戏Ⅱ", "网络游戏"],
    "旅游酒店": ["旅游酒店", "酒店餐饮", "旅游及景区"],
}

def _match_flow(theme_name, boards):
    """题材名 ↔ 板块名对齐（复用含糊包含匹配），返回该板块资金流 dict 或 None。"""
    name = (theme_name or "").strip()
    name = SYNONYMS.get(name, name)   # 先过同义词桥接
    if not name or not boards:
        return None
    # 精确 / 包含匹配（双向）
    for bn, bv in boards.items():
        if name == bn or name in bn or bn in name:
            return bv
    return None


def _match_flow_group(theme_name, boards):
    """题材资金流匹配（2026-09-01 修复：自身板块 + 题材族聚合双轨）。

    自身板块能匹配到当日数据 → 直接用；查不到或当日空洞 → 若题材在 FLOW_GROUPS
    定义了资金流板块族，聚合族内各板块当日 flow_yi 之和（概念板块如数字货币/区块链
    在东财名单每日漂移，实测 5 天仅 1 天有数据，而父级金融行业板块 4~5 天齐全——
    聚合后「数字货币」的资金判定才反映真实大金融回流）。"""
    bv = _match_flow(theme_name, boards)
    if bv is not None and bv.get("flow_yi") is not None:
        return bv
    group = FLOW_GROUPS.get((theme_name or "").strip())
    if group:
        total = 0.0
        hit = False
        for bn in group:
            gv = boards.get(bn)
            if gv is not None and gv.get("flow_yi") is not None:
                total += gv.get("flow_yi")
                hit = True
        if hit:
            return {"pct": None, "flow_yi": total, "is_focus": False}
    return bv


def _flow_trend(theme_name, flow_series):
    """近 5 日资金边际趋势判定（2026-08-26 升级：3日→5日·累计+连续性双轨·双源）。

    取该题材在最近 5 个历史点的 flow_yi（f1<D-5> → ... → f5<D-1>·东财当日序列·历史一致），
    空洞容忍：至少 FLOW_MIN_VALID(3) 个有效点才判定，否则 neutral（东财板块名单每日漂移，
    单日空洞不吞掉趋势——实测六氟化钨 5 点 4 有效，若用 3 日窗口会因空洞丢 21 日 +10.6 流入信号）。

    双源（2026-08-26 用户「就用腾讯，腾讯更稳」+「5日详情、10日总览双重判断」）：
      - 5日详情 = 东财每日序列（历史一致，回填 tx 前各文件无腾讯字段）
      - 10日总览 = 腾讯最新 tx_10d_yi 单值（当前10日主力净流入·交叉验证）
      - 腾讯 tx_5d_yi 单值同步输出（当前5日主力净流入·与东财序列互证）

    双轨判定（基于东财 5 日序列）：
      🔴 五日转流入 : 末点流入 且 前段曾流出 且 Σ5日>0 → inflow_rising（资金刚掉头·回流最强信号）
      📈 持续流入   : 流入天数 ≥3 且 Σ5日>0 → inflow_rising
      🔆 流出收敛   : 全部有效点<0 且末点比首点绝对值小（收窄）→ outflow_shrinking
      ⚠️ 流出放大   : 全部有效点<0 且末点比首点绝对值大（恶化）→ outflow_widening
      其他/数据不足 → neutral

    返回 (trend, [f1..f5], tx10_yi, tx5_yi)，f 为 None 表示当日无数据，
    tx10_yi=腾讯10日总览单值（None=无腾讯数据），tx5_yi=腾讯5日详情单值（None=无腾讯数据）。
    """
    vals = []
    tx10_yi = None
    tx5_yi = None
    for _, boards in flow_series:
        bv = _match_flow_group(theme_name, boards)
        vals.append(bv.get("flow_yi") if bv else None)
        if bv is not None and bv.get("tx_10d_yi") is not None:
            tx10_yi = bv.get("tx_10d_yi")
        if bv is not None and bv.get("tx_5d_yi") is not None:
            tx5_yi = bv.get("tx_5d_yi")
    while len(vals) < FLOW_LOOKBACK:
        vals.insert(0, None)
    vals = (vals + [None] * FLOW_LOOKBACK)[:FLOW_LOOKBACK]

    valid = [v for v in vals if v is not None]
    if len(valid) < FLOW_MIN_VALID:
        return ("neutral", vals, tx10_yi, tx5_yi)

    # ⛔ 2026-08-27 C档：窗口内有净流入但最近2日连续净流出 = 流入转弱，不得判 inflow_rising。
    #   贵金属实证：5日 [33.8, 34.2, 5.4, -7.8, -13.9] 3天流入+累计为正，
    #   旧规则误判 inflow_rising，实际资金连撤2日——末日趋势优先于累计口径。
    #   ⚠️ 仅拦截"流入转弱"；全负收敛(-10→-1)仍走 outflow_shrinking，不得吞掉回流迹象。
    if not all(v < 0 for v in valid) and len(valid) >= 2 and all(v < 0 for v in valid[-2:]):
        return ("inflow_fading", vals, tx10_yi, tx5_yi)

    total = sum(valid)
    inflow_days = sum(1 for v in valid if v > 0)
    first_valid, last_valid = valid[0], valid[-1]

    # 🔴 五日转流入：末点净流入 且 前段(有效点中最后一个负值之前的窗口)曾流出 且 Σ5日>0
    #   资金刚掉头=回流最强信号（2026-08-26 实测：六氟化钨 [-20,-8,+10.6,空洞,+5.4] 3日版
    #   因空洞丢 21 日流入信号判 neutral，5日版应识别转流入）
    turned = last_valid > 0 and any(v < 0 for v in valid[:-1]) and total > 0
    if turned:
        return ("inflow_rising", vals, tx10_yi, tx5_yi)
    # 📈 持续流入
    if inflow_days >= 3 and total > 0:
        return ("inflow_rising", vals, tx10_yi, tx5_yi)
    # 全负：收敛 / 放大
    if all(v < 0 for v in valid):
        if abs(last_valid) < abs(first_valid):
            return ("outflow_shrinking", vals, tx10_yi, tx5_yi)
        return ("outflow_widening", vals, tx10_yi, tx5_yi)
    return ("neutral", vals, tx10_yi, tx5_yi)


def _capital_state(theme_name, mctx):
    """资金是否撤离：该题材是否出现在净流出榜（模糊包含匹配）。"""
    from common.em_flow import sector_risk_for_decision
    mctx = sector_risk_for_decision(mctx)
    name = (theme_name or "").strip()
    for o in mctx.get("outflow_top", []):
        on = (o.get("name") or "")
        if name and (name in on or on in name):
            amt = o.get("flow_yi") or 0
            return ("outflow", amt)
    return ("neutral", 0.0)


def _flow_5d_board():
    """五日累计资金监管榜（2026-08-26 用户「扩大为5日流入流出板块监管」）。

    全量扫描 sector_flow_history 近 5 个交易日所有板块，按累计净流入(Σ flow_yi)
    排序产出 Top 流入/流出榜（供盘前卡/看板「资金总览」监管；空洞容忍=至少3个有效点，
    板块名单每日漂移但多数板块 5 点齐全）。返回 {"inflow_top": [...], "outflow_top": [...],
    "data_days": [...]}，每项 {name, total_yi(累计亿), days(有效天数), inflow_days(流入天数),
    last_yi(末日流向), trend(五日趋势)}。
    """
    series = _load_flow_series()
    if not series:
        return {"inflow_top": [], "outflow_top": [], "data_days": []}
    agg = {}   # board_name -> {"tot": 0, "days": 0, "in": 0, "last": None, "first": None}
    for _, boards in series:
        for bn, bv in boards.items():
            if _is_flow_noise(bn):
                continue
            fl = bv.get("flow_yi")
            if fl is None:
                continue
            a = agg.setdefault(bn, {"tot": 0.0, "days": 0, "in": 0, "last": None, "first": None, "streak": 0})
            a["tot"] += fl
            a["days"] += 1
            if fl > 0:
                a["in"] += 1
                a["streak"] += 1
            else:
                # 连续流入：series 按日期升序，遇到流出即清零 → 得到「截至最后一天的连涨天数」
                a["streak"] = 0
            a["last"] = fl
            if a["first"] is None:
                a["first"] = fl
    rows = []
    for bn, a in agg.items():
        if a["days"] < FLOW_MIN_VALID:
            continue
        trend = "neutral"
        if a["days"] >= FLOW_MIN_VALID:
            if a["in"] >= 3 and a["tot"] > 0:
                trend = "inflow"
            elif a["in"] == 0 and a["tot"] < 0:
                trend = "outflow"
        rows.append({"name": bn, "total_yi": round(a["tot"], 1), "days": a["days"],
                     "inflow_days": a["in"], "last_yi": a["last"], "trend": trend,
                     "streak": a["streak"]})
    rows.sort(key=lambda x: -x["total_yi"])
    return {
        "inflow_top": rows[:FLOW_TOP_N],
        "outflow_top": rows[-FLOW_TOP_N:][::-1],
        "data_days": [d for d, _ in series],
    }


def _avoidance_sweep(mctx, flow_board, today_hot_names, today_theme_names):
    """P2-14 避险反向抽取观察档（2026-09-11 落地·宪法【避险反向抽取 vs 温和回流】）。

    补齐引擎盲区：`candidates` 只认「资金 5D/10D 双正 + 涨停≥3」，而退潮日被避险资金
    单日暴动的**深度超跌板块**（5 日资金深负）不可见（实证 2026-09-03 白银有色：有色
    5 日 -207.6 亿·全场流出第 1·引擎 candidates 无有色 = 引擎外事件）。

    三项同时满足才入档：
      ① 大盘退潮：情绪分 ≤ AVOID_SENTIMENT_MAX 或 核按钮 ≥ AVOID_NUKE_MIN
      ② 板块 5 日累计资金 ≤ AVOID_FLOW_DEEP_YI（深负·取流出最深的 Top N）
      ③ 当日出现异动：板块在今日盘中活跃榜（sector_delta）或进 eruption 今日题材

    ⛔ 输出与 candidates **分开**、不进买点池（AI 按避险档措辞「⚡ 超跌避险反弹·按一日游对待」）；
    升级路径=资金转正连续 2 日 + 涨停≥3 才回交 re-entry 判定（本档不自动升级，只给 upgrade_hint）。
    返回 [] 表示未触发（退潮门未开或无深负异动板块）——诚实空档，不硬凑。
    """
    if not ENABLE_AVOIDANCE_SWEEP:
        return []
    sent = mctx.get("sentiment_score")
    nuke = mctx.get("nuke_count") or 0
    # ① 大盘退潮门（情绪低 或 核按钮多·满足其一）
    if not ((sent is not None and sent <= AVOID_SENTIMENT_MAX) or nuke >= AVOID_NUKE_MIN):
        return []
    out = []
    for row in (flow_board.get("outflow_top") or []):
        tot = row.get("total_yi")
        if tot is None or tot > AVOID_FLOW_DEEP_YI:      # ② 需 5 日累计 ≤ 阈值（深负）
            continue
        nm = str(row.get("name") or "")
        if not nm:
            continue
        last = row.get("last_yi")
        turn = last is not None and last > 0             # 当日资金转正
        hot = nm in today_hot_names or nm in today_theme_names
        if not (turn or hot):                            # ③ 当日出现异动
            continue
        trig = []
        if turn:
            trig.append(f"当日资金转正 {last:+.1f}亿")
        if hot:
            trig.append("当日题材活跃")
        out.append({
            "name": nm,
            "total_5d_yi": round(float(tot), 1),
            "last_yi": last,
            "inflow_days": row.get("inflow_days"),
            "trigger": "＋".join(trig),
            "tier": "避险反向抽取",                      # 定性档（非回流确认）
            "stance": "⚡ 超跌避险反弹（孤军·未成梯队·按一日游对待）",
            "upgrade_hint": "资金转正连续2日 + 板块涨停≥3 + 晋级率≥30% 才可升级 re-entry",
        })
    out.sort(key=lambda x: x["total_5d_yi"])             # 流出最深优先
    return out[:AVOID_TOP_N]


def _attach_live_pool_quotes(candidates, quotes=None):
    """给回流池附加当日实时涨幅；历史爆发日涨幅保留为建池证据，不作展示替代。

    东财批量行情已有 60 秒缓存，盘中每轮只请求一次。上游没有返回某只股票时，
    显式写 None，让终端显示「—」而不是回退到历史涨幅。
    """
    codes = list(dict.fromkeys(
        str(stock.get("code") or "").split(".")[0].strip()
        for candidate in candidates
        for stock in (candidate.get("watch_pool") or [])
        if str(stock.get("code") or "").strip()
    ))
    fetch_missing = quotes is None
    if fetch_missing:
        try:
            from common.em_flow import fetch_quotes
            quotes = fetch_quotes(codes)
        except Exception:  # noqa: BLE001 — 实时行情缺失必须保留空值语义
            quotes = {}
    quotes = quotes or {}
    # 东财盘后偶发主动断连；复用反馈矩阵已经验证过的腾讯回退，补齐未覆盖代码。
    # 两源都失败才向终端交付 None，绝不回填历史爆发日涨幅。
    missing = [code for code in codes if code not in quotes]
    if fetch_missing and missing:
        try:
            from feedback_matrix import fetch_quotes as fetch_feedback_quotes
            quotes.update(fetch_feedback_quotes(missing) or {})
        except Exception:  # noqa: BLE001 — 回退失败仍维持缺失语义
            pass
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds") if quotes else None
    for candidate in candidates:
        covered = 0
        for stock in candidate.get("watch_pool") or []:
            code = str(stock.get("code") or "").split(".")[0].strip()
            quote = quotes.get(code) or {}
            value = quote.get("pct")
            stock["live_pct"] = value if isinstance(value, (int, float)) else None
            stock["live_price"] = quote.get("price") if isinstance(quote.get("price"), (int, float)) else None
            if stock["live_pct"] is not None:
                covered += 1
        candidate["live_quote_ts"] = captured_at
        candidate["live_quote_coverage"] = {"available": covered, "total": len(candidate.get("watch_pool") or [])}


# ── K线止跌（2026-09-22 用户定：「监听板块消息、资金流和K线是否止跌，确定是否回流」）──
# 本文件 v1 曾明确跳过这一维（见文件头「龙头破五日线需逐票拉日K，v1 不做（成本高）」），
# 结果回流判定只有「催化(消息) + 资金」两维 —— 用户点出缺的正是**价格**这一维。
# 口径：以**题材龙头个股**的日K代表该题材（eruption 的 `stocks[].dragon`，缺失则取首只）。
ENABLE_KLINE_STOPFALL = True   # 可逆开关：False = 不拉K线、kline 因子恒 no_data（回退 v1 行为）
KLINE_LMT = 30                 # 日K根数（够算 MA5 / 近7日低点 / 5日均量）
KLINE_WEIGHT = 0.15            # kline 因子权重（从 catalyst 0.30→0.20、market 0.15→0.10 匀出）
KLINE_MIN_BARS = 8             # 少于 8 根不算（MA5 + 近2日 + 前5日最低 + 前5日均量）
_KLINE_CACHE: dict = {}        # code → (state, hits)：同题材共享龙头时不重复拉


def _secid(code: str) -> str:
    """`600000` / `600000.SH` → 东财 secid（`1.` 沪 / `0.` 深）。空则返回 ''。"""
    c = (code or "").split(".")[0].strip()
    if not c or not c[0].isdigit():
        return ""
    return ("1." if c[0] in "69" else "0.") + c


def _fetch_kline(code: str, lmt: int = KLINE_LMT) -> list:
    """东财日K（klt=101·前复权）→ `[{date, close, low, vol}]`。任何失败返回 []（⛔ 不抛）。"""
    sid = _secid(code)
    if not sid:
        return []
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={sid}&klt=101&fqt=1&lmt={lmt}&end=20500101"
           "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = json.loads(urllib.request.urlopen(req, timeout=8).read().decode("utf-8"))
    except Exception:
        return []
    out = []
    for row in ((raw.get("data") or {}).get("klines") or []):
        p = str(row).split(",")
        if len(p) < 6:
            continue
        try:
            out.append({"date": p[0], "close": float(p[2]), "low": float(p[4]), "vol": float(p[5])})
        except (TypeError, ValueError):
            continue
    return out


def _kline_stop_fall(code: str):
    """题材**龙头个股**日K的「止跌」判定。返回 `(state, hits)`。

    四条判据（**命中 ≥2 视为止跌成立**）：

    | 判据 | 含义 |
    |---|---|
    | ① 收盘站上 MA5 | 短期均线压制解除 |
    | ② 近 2 日不创新低 | `min(low 近2) > min(low 前5)` —— 止跌的最直接形态 |
    | ③ 跌幅收窄 | 今日涨跌幅 > 昨日涨跌幅（还在跌但跌得少了） |
    | ④ 缩量 | 今日量 < 前 5 日均量 × 0.8（恐慌盘衰竭） |

    ⛔ **无数据一律 `no_data`，绝不当「未止跌」** —— 缺数据 ≠ 价格没止跌
    （与「无数据不编结论」铁律一致）。
    """
    if not ENABLE_KLINE_STOPFALL:
        return "off(开关关闭)", 0
    c = (code or "").split(".")[0]
    if not c:
        return "no_data(无K线)", 0
    if c in _KLINE_CACHE:
        return _KLINE_CACHE[c]
    bars = _fetch_kline(code)
    if len(bars) < KLINE_MIN_BARS:
        _KLINE_CACHE[c] = ("no_data(无K线)", 0)
        return _KLINE_CACHE[c]
    closes = [b["close"] for b in bars]
    lows = [b["low"] for b in bars]
    vols = [b["vol"] for b in bars]
    c1 = closes[-1] > (sum(closes[-5:]) / 5)
    c2 = min(lows[-2:]) > min(lows[-7:-2])
    r_today = (closes[-1] / closes[-2] - 1) if closes[-2] else 0.0
    r_prev = (closes[-2] / closes[-3] - 1) if closes[-3] else 0.0
    c3 = r_today > r_prev
    avg5 = (sum(vols[-6:-1]) / 5) if any(vols[-6:-1]) else 0.0
    c4 = bool(avg5) and vols[-1] < avg5 * 0.8
    hits = sum(1 for x in (c1, c2, c3, c4) if x)
    state = f"stop_fall(止跌{hits}/4)" if hits >= 2 else f"not_yet(未止跌{hits}/4)"
    _KLINE_CACHE[c] = (state, hits)
    return _KLINE_CACHE[c]


def _leader_code(stocks) -> str:
    """从 eruption 的 `stocks` 里挑「龙头」代码：`dragon=True` 优先，其次首只。无则 ''。"""
    for s in (stocks or []):
        if s.get("dragon"):
            return s.get("code") or ""
    return ((stocks or [{}])[0] or {}).get("code") or ""


def _kline_state_of(nm, t_boom, series=None) -> str:
    """取该题材**代表龙头**的 K 线止跌状态。

    龙头优先级（⛔ 逐级回落，全都没有才 `no_data`）：

    1. 当日 boom 的 `stocks[].dragon`
    2. 当日 boom 的 `stocks[0]`
    3. **历史 eruption（近 5 交易日）里该题材最近一次出现的** dragon / 首只
       —— `watch` / `cooling` 类候选**没有当日 boom 数据**（实测 5/8 条），
       不回落这一层它们会全部落到 `no_data`，而「待回流候选的K线有没有止跌」
       恰恰是最该看的部分（2026-09-22 用户口径）。
    """
    leader = _leader_code((t_boom or {}).get("stocks"))
    if not leader and series:
        for _d, mp in reversed(series):
            t = mp.get(nm)
            if t:
                leader = _leader_code(t.get("stocks"))
                if leader:
                    break
    state, _ = _kline_stop_fall(leader)
    return state


def _confidence(phase, cooled_days, t_boom, mctx, flow_trend="neutral", flow_vals=None,
                kline_state=None):
    """0-1 置信度。**五因子**加权：催化0.20 + 大盘0.10 + 资金0.35 + 相位0.20 + **K线止跌0.15**。

    ⚠️ 2026-09-22 新增第 5 维「K线止跌」（用户：「监听板块消息、资金流和**K线是否止跌**，
    确定是否回流」）—— `catalyst` 0.30→0.20、`market` 0.15→0.10 让出 0.15 给 `kline`。
    ⛔ 资金 0.35 / 相位 0.20 **不动**（P2-8 已实证校准的核心项）。效果：消息与资金都 OK、
    但**价格没止跌**的候选，置信度直接少 0.15 —— 对齐「没止跌就不算真回流」。

    资金因子 0.2→0.35（用户思路：资金是回流判定的核心证据）：
    8/14 实证——PCB 单靠催化(0.3)+大盘(0.3) 撑到 conf0.8 但近3日资金未回流被证伪。
    与 shared_rules「资金持续净流出不构成回流」铁律对齐：资金趋势决定回流真伪，
    催化/大盘只决定"热度"，不决定"是否真回流"。"""
    factors = {}
    # 催化新鲜度
    title = (t_boom or {}).get("title") or ""
    factors["catalyst"] = bool(title)
    cat = 0.2 if title else 0.0   # 2026-09-22 0.30→0.20（让 0.10 给 kline：催化只决定「热度」）
    # 大盘非退潮（P2-8 降权 0.3→0.15；2026-09-22 再 0.15→0.10 让 0.05 给 kline）
    sent = mctx.get("sentiment_score")
    nuke = mctx.get("nuke_count") or 0
    if sent is not None and sent >= 5 and nuke < 3:
        factors["market"] = True
        mkt = 0.10
    elif sent is not None and sent < 5:
        factors["market"] = False
        mkt = 0.03
    else:
        factors["market"] = None
        mkt = 0.07
    # 资金边际趋势（主，权重 0.35）/ outflow 榜二值（回退）
    if flow_trend == "inflow_rising":
        factors["capital"] = "inflow_rising"
        cap = 0.35
    elif flow_trend == "inflow_fading":
        factors["capital"] = "inflow_fading(流入转弱)"
        cap = 0.25
    elif flow_trend == "outflow_shrinking":
        factors["capital"] = "outflow_shrinking"
        cap = 0.25
    elif flow_trend == "outflow_widening":
        factors["capital"] = "outflow_widening"
        cap = 0.0
    else:
        # 回退：当日 outflow 榜二值判定
        cstate, amt = _capital_state((t_boom or {}).get("name"), mctx)
        if cstate == "neutral":
            if flow_vals and all(v is None for v in flow_vals):
                factors["capital"] = "no_data(无资金流)"
                cap = 0.0
            else:
                factors["capital"] = "neutral(无趋势)"
                cap = 0.3
        elif cstate == "outflow" and abs(amt) < 20:
            factors["capital"] = "weak_outflow(无趋势)"
            cap = 0.15
        else:
            factors["capital"] = "outflow(无趋势)"
            cap = 0.0
    # K线止跌（2026-09-22 用户口径·第 5 维）—— 0.15 全额 = 止跌成立；否则 0
    _ks = kline_state or "no_data(无K线)"
    factors["kline"] = _ks
    kln = KLINE_WEIGHT if str(_ks).startswith("stop_fall") else 0.0
    # 相位
    if phase == "re-entry-candidate":
        ph = 0.2
    elif phase == "boom":
        ph = 0.1
    elif phase == "cooling":
        ph = 0.05
    else:
        ph = 0.0
    # ⛔ 资金无数据时相位分打折（2026-08-20 修）：re-entry-candidate 相位固定 0.2 会把
    # 无资金数据(no_data·cap=0)的题材撑到 conf 0.55，触发盘前卡自查「无数据却高置信」拦截；
    # 资金缺失=回流证据不足，相位分压到 0.05，置信在任何大盘态下都回落 <0.5
    # （与「无数据不编置信度」铁律对齐；2026-08-20 首修 0.1 在 mkt 退化 0.1 时仍到 0.5，收紧到 0.05）。
    if factors.get("capital") == "no_data(无资金流)" and phase == "re-entry-candidate":
        ph = 0.05
    factors["phase"] = phase
    # ⛔ 2026-08-27 C档：资金 neutral/转弱 时 conf 封顶 0.6——催化不再单点拉满。
    #   六氟化钨实证：资金 neutral(无趋势) 靠催化0.3+大盘+相位撑到 0.85，回流证据不足。
    raw = cat + mkt + cap + ph + kln
    if factors.get("capital") in ("neutral(无趋势)", "inflow_fading(流入转弱)"):
        raw = min(raw, 0.6)
    return round(raw, 2), factors


def build(*, live_day=None, live_themes=None, live_codes=None):
    series = _load_series()
    # 盘中题材仅注入本次计算窗口，同日替换，绝不覆写盘后历史档。
    if live_day is not None:
        series = [(day, themes) for day, themes in series if day < live_day]
        series.append((live_day, {item["name"]: item for item in (live_themes or []) if item.get("name")}))
        series = series[-TRADING_LOOKBACK:]
    # 🆕 回流确认长历史回看序列（2026-09-30）：与 series 同源(eruption)，回看更长窗口，
    #    用于识别"冷却期超过5日窗口"的回流（如医药冷却约一周）。不做 phase 判定，仅供 is_reentry。
    series_long = _load_series(ndays=REENTRY_LOOKBACK_LONG)
    if live_day is not None:
        series_long = [(day, themes) for day, themes in series_long if day < live_day]
        series_long.append((live_day, {item["name"]: item for item in (live_themes or []) if item.get("name")}))
        series_long = series_long[-REENTRY_LOOKBACK_LONG:]
    mctx = _market_context()
    # P0-孤岛打通（2026-08-17）：sector_delta 板块增量进轮动判定。
    # 此前 latest.json sector_delta 产出零消费；现读取今日盘中 top5 活跃板块名，
    # 供回流候选回填 today_hot 标记（今日仍在活跃的板块，回流概率更高，AI 点评可引用）。
    _latest = _load(CACHE / "pulse_snapshots" / "latest.json") or {}
    _sd = _latest.get("sector_delta") or {}
    today_hot_names = set()
    for _k in ("new", "surging", "ongoing"):
        for _s in _sd.get(_k) or []:
            # 格式如 "国产芯片(11→11)" 或板块名——取括号前名称
            today_hot_names.add(str(_s).split("(")[0].strip())
    # P1 (2026-08-28)：当日涨停池 code 集合——成分股今日已涨停 → 今日已爆发，非潜伏。
    # 实证：和远气体今日涨停（国产芯片题材·智谱消息），theme_rotation 08-27 盘后仍把
    # 六氟化钨标 re-entry-candidate 且 watch_pool 含和远气体 → 潜伏卡推「次日潜伏」，
    # 用户质疑「都涨停了还潜伏」。引擎侧须用当日涨停池把「成分股今日已涨停」的题材
    # 相位改为 boom（已爆发），不再进回流候选。
    today_zt_codes = set()
    _ztf = _load(CACHE / "yesterday_zt.json") or {}
    for _it in (_ztf.get("zt_list") or []):
        _c = str(_it.get("code") or "").strip()
        if _c:
            today_zt_codes.add(_c.split(".")[0])
    if live_codes is not None:
        today_zt_codes = set(live_codes)
    if not series:
        return {"date": datetime.now().strftime("%Y-%m-%d"),
                "generated_ts": iso_bj(),
                "data_days": [], "candidates": [],
                "market_context": mctx,
                "note": "无 eruption_history 数据，等待 capture_daily 攒数据"}

    flow_series = _load_flow_series()
    # 🔧 金额序列右对齐（2026-09-02 用户「口径统一」）：flow 与 series 须同交易日窗口——
    # capture_daily 单侧失败（某日只跑了 eruption 没跑 sector_flow 或反之）会让两序列错位，
    # 导致金额趋势与题材量不在同一时间轴。按 series 日期过滤 flow_series。
    _sdates = {d for d, _ in series}
    flow_series = [(d, b) for d, b in flow_series if d in _sdates]
    _zt_days, _zt_by_date = _load_zt_pool_series()   # 题材量补全源（全量涨停池）
    today_date, today_map = series[-1]
    # 收集所有出现过的题材名
    all_names = set()
    for _, mp in series:
        all_names.update(mp.keys())

    candidates = []
    zt_trends = []   # 板块涨停数趋势汇总（所有曾现涨停的题材，供看板"增长趋势"卡）
    for nm in sorted(all_names):
        # 该题材在序列中每日的快照（None=当天没出现）
        # 🔁 名字漂移兜底（2026-11-16）：板块榜叫「人形机器人」、快照叫「机器人」，
        #    原来是「查不到就记 None」→ 序列断成 [15, null, null…]。
        #    现在缺名字时去**同族**里找当天有数据的那个（只取一个，⛔ 不求和，避免重复计数）。
        per_day = [mp.get(nm) or _family_pick(mp, nm) for _, mp in series]
        zt_series = _zt_series_of(per_day)
        # 🔧 题材量补全（2026-09-02 用户「优化题材量数据」）：eruption 只记热门爆发题材
        # （每日 3-5 个·isHot 过滤），成分股常以他题涨停/缺席日实际有涨停 → eruption 缺席日
        # 用「成分股 code ∩ 当日全量涨停池 code」补真实涨停数。zt_filled 记录补全日（可追溯）。
        # 实证：数字货币 09-01 eruption 缺席但成分股楚天龙/金一文化涨停（原 zt_series 空洞）。
        theme_codes: set = set()
        for _t in per_day:
            if _t:
                theme_codes.update(str(s.get("code") or "").split(".")[0]
                                   for s in (_t.get("stocks") or []))
        zt_filled: dict = {}
        if theme_codes:
            for _di, (_sdate, _mp) in enumerate(series):
                if per_day[_di] is not None:
                    continue                        # eruption 有记录不覆盖
                _pool = _zt_by_date.get(_sdate, {})
                if not _pool:
                    continue
                _hit = len(theme_codes & set(_pool.keys()))
                if _hit:
                    zt_series[_di] = _hit
                    zt_filled[_sdate] = _hit
        zt_valid = sum(1 for v in zt_series if v is not None)
        zt_dir = _zt_dir(zt_series)                 # 补全后重算方向（量轴更完整）
        # 曾出现至少 1 板才进趋势汇总（过滤纯噪声题材）
        if max([v for v in zt_series if v is not None] + [0]) >= 1:
            zt_trends.append({
                "theme": nm,
                "zt_series": zt_series,
                "latest": next((v for v in reversed(zt_series) if v is not None), 0),
                "delta": _zt_delta(zt_series),
                "dir": zt_dir,
                "zt_filled": zt_filled,              # 补全日映射（展示层虚化标注）
            })
        # 找最近一次 boom
        boom_idx = None
        for i in range(len(per_day) - 1, -1, -1):
            if _is_boom(per_day[i]):
                boom_idx = i
                break
        if boom_idx is None:
            # 未爆发但有涨停活动 → 观察沉淀（2026-08-18 用户：堆叠沉淀7个左右题材板块）
            active_vals = [v for v in zt_series if v is not None]
            if not active_vals or max(active_vals) < 1:
                continue                   # 窗口内无任何涨停活动，不沉淀
            flow_trend, flow_vals, tx10_yi, tx5_yi = _flow_trend(nm, flow_series)
            # 「观察沉淀」类（无当日 boom）→ K线止跌回落到**历史 eruption 的龙头**
            conf, factors = _confidence("watch", None, None, mctx,
                                        flow_trend=flow_trend, flow_vals=flow_vals,
                                        kline_state=_kline_state_of(nm, None, series))
            if conf < SURFACE_CONFIDENCE:
                continue
            # 跟踪池：取最近一次现身日的成分股（主板优先·创限席·不补位）
            last_idx = max(i for i, v in enumerate(per_day) if v is not None)
            last_t = per_day[last_idx]
            raw = []
            for s in (last_t.get("stocks") or []):
                code = s.get("code")
                if _is_excluded(code):
                    continue
                raw.append({"code": code, "name": s.get("name"),
                            "dragon": s.get("dragon"), "pct": s.get("pct"),
                            "cap_yi": s.get("cap_yi"), "board": _code_board(code),
                            "from_zt": False, "risk": ""})
            ranked = _rank_main_first(raw)
            cyb_all = [s for s in ranked if s["board"] == "cyb"]
            if len(cyb_all) > MAX_CYB_SEATS:
                cyb_keep = set(id(x) for x in cyb_all[:MAX_CYB_SEATS])
                ranked = [s for s in ranked
                          if not (s["board"] == "cyb" and id(s) not in cyb_keep)]
            mains = [s for s in ranked if s["board"] == "main"]
            pool = (mains + [s for s in ranked if s["board"] == "cyb"])[:5]
            for s in pool:
                if s["board"] == "cyb":
                    s["risk"] = "⚠️20cm波动大·紧止损"
            cooled_days = (len(per_day) - 1) - last_idx
            candidates.append({
                "theme": nm,
                "phase": "watch",
                "boom_day": None,
                "cooled_days": cooled_days,
                "catalyst": "",
                "catalyst_fresh": False,
                "confidence": conf,
                "confidence_factors": factors,
                "trigger": "资金转流入 + 板块涨停复现即升级跟踪；持续无资金则移出观察",
                "watch_pool": pool,
                "main_board_pool": [s for s in pool if s["board"] == "main"],
                "board_mix": {"main": sum(1 for s in pool if s["board"] == "main"),
                              "cyb": sum(1 for s in pool if s["board"] == "cyb")},
                "pool_note": _pool_note(pool, []),
                "flow_trend": flow_trend,
                "flow_vals": flow_vals,
                "tx_10d_yi": tx10_yi,   # 10日总览（腾讯·2026-08-26 双窗口）
                "tx_5d_yi": tx5_yi,      # 5日详情（腾讯·与东财序列互证）
                "boom_zt": None,
                "boom_pct": None,
                "zt_series": zt_series,
                "zt_dir": zt_dir,
                "zt_filled": zt_filled,   # 题材量补全日映射（eruption缺席·zt池成分股补）
                "zt_valid_days": zt_valid,  # 补全后有效天数（数据质量可追溯）
                # 🆕 周期相位（2026-11-03）：无 boom 的沉淀类也要有「第几天」，
                #    否则前端只能显示「观察」两个字的空壳。None = 数据不足。
                "phase_v2": (_phase_of(nm, theme_codes, today_date, today_codes=today_zt_codes)
                             if ENABLE_THEME_PHASE else None),
            })
            continue
        t_boom = per_day[boom_idx]
        boom_date = series[boom_idx][0]
        cooled_days = (len(per_day) - 1) - boom_idx

        # 🆕 回流确认标记（2026-09-30）：区分「新题材首次爆发」与「冷却后重新爆发」。
        #    先用 5 日窗口判；若窗口内无更早爆发（候选像新题材），再用更长历史回看
        #    （series_long，覆盖冷却期超过5日的回流，如 09-30 医药冷却约一周）。
        is_reentry, reentry_gap_days = _classify_reentry(per_day, boom_idx)
        if not is_reentry:
            _pl = [mp.get(nm) for _, mp in series_long]
            _bi = None
            for _i in range(len(_pl) - 1, -1, -1):
                if _is_boom(_pl[_i]):
                    _bi = _i
                    break
            if _bi is not None and _bi != len(_pl) - 1:
                _ok_l, _gap_l = _classify_reentry(_pl, _bi)
                if _ok_l:
                    is_reentry, reentry_gap_days = True, _gap_l

        # 相位判定
        if boom_idx == len(per_day) - 1:
            phase = "boom"                 # 今天刚爆发
        elif cooled_days >= COOL_DAYS_FOR_REENTRY:
            # 退潮满2天且今天未爆发 → 回流候选
            today_present = per_day[-1] is not None
            phase = "cooling" if today_present and (per_day[-1].get("zt") or 0) > 0 else "re-entry-candidate"
            if phase == "cooling" and cooled_days >= 3 and per_day[-1] is None:
                phase = "dead"
        else:
            phase = "分歧" if cooled_days == 1 else "cooling"

        # ⛔ P1 相位修正 (2026-08-28)：成分股今日已涨停 → 今日已爆发，非潜伏。
        # 回流引擎主序列只有"题材名→涨停数"，若某成分股以**其他题材**涨停（跨题材联动），
        # 该题材名在今日 eruption 缺席 → 仍判 re-entry → 潜伏卡推「次日潜伏」，语义反向。
        # 实证：和远气体 08-28 涨停归因"国产芯片"（智谱消息），六氟化钨 08-27 盘后仍标
        # re-entry-candidate 且 watch_pool 含和远气体 → 潜伏卡推六氟化钨，用户质疑。
        # 修复：用当日涨停池 code 命中 boom 成分股 → 相位强制置 boom（已爆发·非潜伏）。
        if phase != "boom" and today_zt_codes:
            _boom_codes = {str(s.get("code") or "").split(".")[0]
                           for s in (t_boom.get("stocks") or [])}
            if _boom_codes & today_zt_codes:
                phase = "boom"

        # 资金边际趋势（回流前瞻核心信号）：近3日流向三态（须先于 _confidence 计算）
        flow_trend, flow_vals, tx10_yi, tx5_yi = _flow_trend(nm, flow_series)

        # ⛔ 资金未回流不推（用户铁律 2026-08-14）：近5日资金**持续净流出**（flow_vals 全负、
        # 无任何净流入迹象）的板块，即使退潮满 N 天也**不构成回流候选**——回流的前提是资金
        # 出现净流入/回流迹象（逐步流入或流出收敛），不是"跌多了就回流"。
        # 曾踩坑：兵装重组 flow=[-38,-4.6,-10.5] 三日全负，却因催化+大盘因子被抬到 conf 0.85 当回流推。
        if phase == "re-entry-candidate" and flow_vals and all(
                v is not None and v < 0 for v in flow_vals):
            phase = "no-reentry"

        # ⛔ 2026-08-27 B档：悬挂超时——re-entry 连续冷却≥4天且资金未现回流迹象
        #   （flow_trend != inflow_rising）→ 移出回流候选（持续未回流，不再天天推）。
        #   六氟化钨实证：boom 08-21 后挂 re-entry 4天，zt=0/资金neutral 仍 conf0.85
        #   连续多天出现在盘前/潜伏卡，用户明确质疑；资金一旦转流入自动回归候选。
        if phase == "re-entry-candidate" and cooled_days >= COOL_STALE_DAYS \
                and flow_trend != "inflow_rising":
            continue

        # K线止跌（2026-09-22 用户口径）：用该题材 **代表龙头** 的日K
        # （当日 boom 的 dragon → boom 首只 → **历史 eruption 最近一次**的龙头；
        #   同一龙头跨题材命中 `_KLINE_CACHE` 不重复拉）
        _kl_state = _kline_state_of(nm, t_boom, series)
        conf, factors = _confidence(phase, cooled_days, t_boom, mctx,
                                     flow_trend=flow_trend, flow_vals=flow_vals,
                                     kline_state=_kl_state)

        # 跟踪股票池：取 boom 当日成分股，按铁律剔除科创/北交所，主板优先重排
        raw = []
        for s in (t_boom.get("stocks") or []):
            code = s.get("code")
            if _is_excluded(code):
                continue                       # 688 / 北交所 无权限不推
            raw.append({"code": code, "name": s.get("name"),
                        "dragon": s.get("dragon"), "pct": s.get("pct"),
                        "cap_yi": s.get("cap_yi"), "board": _code_board(code),
                        "from_zt": False, "risk": ""})
        ranked = _rank_main_first(raw)
        # 创业板限席：最多保留 MAX_CYB_SEATS 只，超出剔除
        cyb_all = [s for s in ranked if s["board"] == "cyb"]
        if len(cyb_all) > MAX_CYB_SEATS:
            cyb_keep = set(id(x) for x in cyb_all[:MAX_CYB_SEATS])
            ranked = [s for s in ranked
                      if not (s["board"] == "cyb" and id(s) not in cyb_keep)]
        mains = [s for s in ranked if s["board"] == "main"]
        # 主板不足 → 从涨停池同板块补主板涨停票
        supp = []
        need = max(0, MAIN_FILL_MIN - len(mains))
        if need:
            supp = _supplement_main(nm, need, {x["code"] for x in mains})
        for s in supp:
            s["risk"] = ""
        # 组装：主板(原) + 主板(补) + 创业板(限席)，截断到容量
        pool = (mains + supp
                + [s for s in ranked if s["board"] == "cyb"])[:MAIN_POOL_SIZE]
        for s in pool:
            if s["board"] == "cyb":
                s["risk"] = "⚠️20cm波动大·紧止损"

        # 过滤：re-entry-candidate 必出；其余需达置信度阈值
        if phase != "re-entry-candidate" and conf < SURFACE_CONFIDENCE:
            continue

        trigger = ("明日竞价高开 / 板块资金转流入即确认回流；"
                   "龙头破五日线则放弃（盘中实时确认）")
        candidates.append({
            "theme": nm,
            "phase": phase,
            "boom_day": boom_date,
            "cooled_days": cooled_days,
            "catalyst": (t_boom.get("title") or "")[:60],
            "catalyst_fresh": factors.get("catalyst", False),
            "confidence": conf,
            "confidence_factors": factors,
            "trigger": trigger,
            "watch_pool": pool,
            "main_board_pool": [s for s in pool if s["board"] == "main"],
            "board_mix": {
                "main": sum(1 for s in pool if s["board"] == "main"),
                "cyb": sum(1 for s in pool if s["board"] == "cyb"),
            },
            "pool_note": _pool_note(pool, supp),
            "flow_trend": flow_trend,
            "flow_vals": flow_vals,        # [f1(D-5)..f5(D-1)] 单位亿·5日详情（2026-08-26 5日窗口）
            "tx_10d_yi": tx10_yi,          # 10日总览（腾讯·2026-08-26 双窗口交叉验证）
            "tx_5d_yi": tx5_yi,             # 5日详情（腾讯·与东财序列互证）
            "boom_zt": t_boom.get("zt"),
            "boom_pct": t_boom.get("pct"),
            "zt_series": zt_series,        # 近5交易日涨停数序列（None=缺席·含zt池补全日）
            "zt_dir": zt_dir,              # rising/falling/flat/new
            "zt_filled": zt_filled,        # 题材量补全日映射（eruption缺席·zt池成分股补·可追溯）
            "zt_valid_days": zt_valid,     # 补全后有效天数（数据质量）
            # P0-孤岛打通（2026-08-17）：今日盘中 top5 活跃板块（sector_delta）交叉验证
            "today_hot": nm in today_hot_names,   # 今日仍在活跃 → 回流概率高
            # 🆕 回流确认标记（2026-09-30）：is_reentry 由引擎判定，供 live_signal 实时弹回流确认卡
            "is_reentry": is_reentry,
            "reentry_gap_days": reentry_gap_days,
            # 🆕 周期相位（2026-11-03·theme_phase_v1）：回答「这波线走到第几天了」，
            #    与上面 `phase`（跨周期回流候选）是**两个不同问题**，不互相覆盖。
            #    None = 数据不足，⛔ 前端不得把 None 渲染成任一相位。
            "phase_v2": (_phase_of(nm, theme_codes, today_date, today_codes=today_zt_codes)
                         if ENABLE_THEME_PHASE else None),
        })

    # 候选排序：re-entry-candidate 优先，其次置信度；总量封顶（沉淀密度 7 个左右）
    candidates.sort(key=lambda c: (
        0 if c["phase"] == "re-entry-candidate" else 1,
        -c["confidence"]))
    candidates = candidates[:MAX_CANDIDATES]
    _attach_live_pool_quotes(candidates)

    # 板块涨停数趋势卡排序：升温/新晋优先(按增长/热度)，其次平稳，最后退潮
    def _zsort(x):
        order = {"rising": 0, "new": 0, "flat": 1, "falling": 2}.get(x["dir"], 1)
        return (order, -x["latest"], -x["delta"])
    zt_trends.sort(key=_zsort)
    sector_zt_trends = zt_trends[:14]

    # ── P2-14 避险反向抽取观察档（2026-09-11 落地）──
    # 复用同一份 5 日资金榜（避免重复解析）；与 candidates 分开输出、不进买点池。
    _fb = _flow_5d_board()
    avoidance_sweep = _avoidance_sweep(mctx, _fb, today_hot_names, set(today_map.keys()))

    return {
        "date": today_date,
        "generated_ts": iso_bj(),
        "data_days": [d for d, _ in series],
        "candidates": candidates,
        "sector_zt_trends": sector_zt_trends,   # 看板「板块涨停数增长趋势」卡数据源
        "market_context": mctx,
        "flow_5d": _fb,                         # 五日累计资金监管榜（2026-08-26 新增）
        # 避险反向抽取观察档（P2-14·2026-09-11）：引擎外事件的独立档位（不进买点池）
        "avoidance_sweep": avoidance_sweep,
        # 🔧 数据血缘可追溯（2026-09-02 用户「口径统一·逻辑可追溯」）：
        # 题材量 = eruption(热门爆发) + zt_history 全量池成分股补全（zt_filled 记录补全日）；
        # 金额 = sector_flow 东财 f62 当日 + 腾讯 5D/10D 双窗口回填（FLOW_GROUPS 族聚合补空洞）；
        # 两序列已按 data_days 右对齐（同交易日窗口）。
        "data_meta": {
            "zt_source": "eruption_history(热门爆发·isHot) + zt_history(全量涨停池·成分股code补全)",
            "flow_source": "sector_flow_history(东财f62当日全量 + 腾讯fund_flow 5D/10D回填 + FLOW_GROUPS族聚合)",
            "window": f"{len(series)}交易日",
            "aligned_days": len(flow_series),
            "zt_filled_themes": sum(1 for c in candidates if c.get("zt_filled")),
        },
        "note": ("第1周攒数据期：仅产boom跟踪基线，回流模式待5日数据完整后生效"
                 if len(series) < 4 else "5日数据已齐，回流模式识别生效（资金窗口5日·2026-08-26升级）"),
    }


def main():
    res = build()
    OUT_PATH.write_text(json.dumps(res, ensure_ascii=False, indent=2))
    cands = res.get("candidates", [])
    print(f"[theme_rotation {res.get('date')}] data_days={res.get('data_days')} "
          f"candidates={len(cands)}")
    for c in cands[:6]:
        print(f"  [{c['phase']}] {c['theme']} conf={c['confidence']} "
              f"cooled={c['cooled_days']} flow={c['flow_trend']} pool={len(c['watch_pool'])} "
              f"| {c['catalyst'][:30]}")


if __name__ == "__main__":
    main()
