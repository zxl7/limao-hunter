#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中实时信号卡推送（方案A·事件驱动）。

由 pulse_fetcher.sh 每轮(120s)抓取后调用：
  1) 读 latest.json / prev.json（与守护进程同目录）
  2) 检测本轮「新信号」：
       - 新实时快讯 newsflash（含涨停/涨超/直线等硬信号词，按 title 当天去重）
       - 新涨停/封板龙头 leaders（按10cm/20cm区分阈值，且上轮未涨停，按 code 当天仅推1次）
       - 涨幅突增 leaders（与上轮同 code 比 Δ>=5%，按 code 每15分钟限频1次）
  3) 去重 + 限频，聚合成本轮 ≤1 张「原始信号卡」推送（不含 AI 研判）
非交易时段自动跳过。

用法:
  python3 live_signal.py            # 真实推送
  python3 live_signal.py --dry-run # 仅打印卡片 JSON，不推送
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# 2026-08-18 用户选方案A：live_signal 恢复写窗口缓冲，窗口聚合卡（盘中脉冲·XX窗口）持续推。
# 分级（shared_rules）：A级（冲板/强势/快讯/板块）+ 未共振涨停/暴拉 → 入 pulse_buffer.json
#   → pulse_fetcher close_window_step 每5min边界闭合 → pulse_aggregator 生成窗口卡；
#   S级共振（题材方向级）仍走 short_spirit 即时推（见 main 内 s_sector 分支）。

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# launchd 以 scripts/live_signal.py 直接启动，后续机构行情等模块需从项目根目录导入 common。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CACHE = ROOT / "cache"
SNAP = CACHE / "pulse_snapshots"
PUSH = HERE / "push_card.py"
# 盘中异动盯盘 → 独立机器人（2026-09-03 用户切流：事件卡/核按钮/退潮/回流/板块加强/资金切换
# /N字二波/苗头共振 全部走 feishu_webhook_intraday，不占正式群）；定时卡(猎手/快照/复盘)不受影响
PY = sys.executable

# 窗口聚合缓冲写入（方案A·2026-08-18恢复）。防御性导入，导入失败降级跳过不影响实时推送。
try:
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import pulse_aggregator as pa
except Exception as _imp_err:
    pa = None
    print(f"[live_signal] 警告: 无法导入 pulse_aggregator ({_imp_err})，窗口缓冲写入降级跳过")

# 板块家族聚合（2026-09-07 优化C·单一权威源=scripts/sector_family.py）。防御性导入。
try:
    from sector_family import family_of_top5  # noqa: E402
except Exception as _fam_imp_err:
    family_of_top5 = None
    print(f"[live_signal] 警告: 无法导入 sector_family ({_fam_imp_err})，家族聚合禁用")

# 交易日历（唯一真源）。2026-10-09：`_load_zt_history` 的 12 日窗必须按交易日计，
# 否则长假盘中空壳（intraday_only·zt_list=[]）占满名额。防御性导入，失败退化为不过滤。
try:
    from common.trading_calendar import is_a_share_trading_day  # noqa: E402
except Exception as _cal_imp_err:  # pragma: no cover
    print(f"[live_signal] 警告: 无法导入交易日历 ({_cal_imp_err})，窗口按文件数退化")
    def is_a_share_trading_day(_value) -> bool:  # type: ignore[misc]
        return True

# 硬信号词：快讯仅含这些词才进信号卡（过滤弱描述，防刷屏）
HARD = ["涨停", "涨超", "直线", "封板", "触及涨停", "拉升", "拉板", "冲板", "大涨", "秒板", "飙升"]

# ==== 资金场内切换检测（2026-08-26 用户实盘教学·盘中分钟级）====
# 五步时序：主线拉升→后排掉队→他线加强→龙头炸板出货→新龙拉起。
# 权威规则：shared_rules【资金场内切换·盘中分钟级】；本块只做机器检测。
FUND_SWITCH_ENABLE = True        # 可逆开关：False 关闭本检测（回退原逻辑）
FUND_DROP_PCT = 2.0              # 旧龙开板回落最小幅度（百分点）
FUND_NEW_ZT_MIN = 3              # 新线板块最小涨停数
CORE_HIGH_MIN_BOARDS = 2         # 核心高位票最小板数（boards≥2 或 pct≥9.5 即追踪）
CORE_HIGH_MAX = 60               # core_high 追踪上限（防膨胀）

# 🔁 N字二波检测（2026-09-01 用户「N字形态重点关注·楚天龙/青山纸业/康盛·金健米业×万向德农」）
N_SHAPE_HIST_DAYS = 12           # 回看涨停池历史交易日数（第一波窗口）
N_SHAPE_MIN_BOARDS = 2           # 第一波最低连板数（用户 2026-09-01 两度定版：先 ≥3 后改 **最少二连**——3连门槛会错过第一波仅2连板就启动的票如万向德农；1板脉冲仍不算）
N_SHAPE_MIN_GAP = 2              # 距上次涨停最小交易日间隔（≥2=中间至少1个完整断板交易日·非连板续板）

# 🆕 题材首板即时卡（2026-09-01 上线 → 当日 14:32 用户「首板太多了·一个板块推一次·取消」停用）
# 可逆开关：False=停用推送（板块当日第1只涨停不再单独推卡）；True=恢复。冷启动种子同开关。
ENABLE_FIRST_ZT_CARD = False

# ==== 涨停阈值（**本文件唯一来源** · 2026-10-14 审计 B6 / §十一-P0③）====
# 背景：本文件原先同时存在两套字面量 —— `_juggernaut_charge` 用 9.8，
#       `_pool_new_zt_signals` 用 9.9/19.9，**同文件不同口径且无任何说明**，
#       改一处漏一处。现收敛为下面 3 个具名常量，语义差异显式化：
#
#   · ZT_JUDGE_* = 「涨停**判定**」严格口径 —— 用于新建涨停信号（`new_zt`）。
#     必须严格：若放宽到 9.8，会把「+9.85% 尚未封板」的票误标涨停
#     （正是 2026-08-18「中石科技 +19.53% 未封板被列进新涨停」那个 bug 的成因）。
#   · ZT_SEAL_MAIN = 「封板**确认**」宽松下限 —— 只与 `seal ∈ {一字,秒封,封死}`
#     **同时**成立才用（见 `_juggernaut_charge`）。seal 字段本身已证明封板，
#     这里的涨幅只作二次防呆；取 9.8 是容差：涨停价 = round(昨收×1.1, 2)，
#     低价股四舍五入会把实际涨停涨幅压到 9.8%（如 昨收 1.02 → 涨停价 1.12 → +9.80%）。
#
# ⚠️ 跨文件仍有 4 套口径（见审计 B6：intraday_timeline/feedback_matrix/reflow_scan/
#    short_spirit 等）。本文件先自洽，**全局单一函数留待后续专项**（§十一-P0③ 的第二步）。
ZT_JUDGE_MAIN = 9.9              # 主板涨停判定（严格）
ZT_JUDGE_20CM = 19.9             # 创业板/科创板涨停判定（严格）
ZT_SEAL_MAIN = 9.8               # 主板封板确认下限（宽松·仅配合 seal 字段使用）
ZT_SEAL_20CM = 19.8              # 20cm 封板确认下限（同义，当前无调用方·留齐口径）


def _track_core_high(sig: dict, prev: dict) -> None:
    """维护当日核心高位票基准（sig.core_high）。

    每轮用 prev.leaders 中 board≥2 或涨幅≥9.5 的票刷新 {code: {name, sector,
    boards, pct, sealed, ts}}——sealed=prev 轮是否封板。供旧龙开板检测对比
    （汉森制药 10:20 炸板即属此列：曾封板→回落≥FUND_DROP_PCT）。
    """
    recs = sig.setdefault("core_high", {})
    now_iso = datetime.now().isoformat()
    for l in (prev.get("leaders") or []):
        code = str(l.get("code") or "")
        if not code:
            continue
        pct = float(l.get("pct") or 0)
        if pct < 9.5 and parse_boards(l.get("boards")) < CORE_HIGH_MIN_BOARDS:
            continue
        recs[code] = {
            "name": l.get("name") or code,
            "sector": l.get("sector") or "",
            "boards": l.get("boards") or "",
            "pct": pct,
            "sealed": l.get("buyable") is False,
            "ts": now_iso,
        }
    if len(recs) > CORE_HIGH_MAX:
        for k in sorted(recs, key=lambda x: recs[x].get("ts", ""))[: len(recs) - CORE_HIGH_MAX]:
            recs.pop(k, None)
    sig["core_high"] = recs


def detect_fund_switch(latest: dict, prev: dict, sig: dict, hhmm: str) -> dict | None:
    """资金场内切换检测（机器版）。

    旧线退潮：core_high 中 prev 封板的票本轮开板回落 ≥FUND_DROP_PCT，且所属板块
    当日已爆发过（sector_pushed=旧主线）→ 记该板块为退潮方向。
    新线承接：top5 板块涨停 ≥FUND_NEW_ZT_MIN 且命中资金净流入榜且龙头封板/近板。
    两者同轮出现（旧线≠新线）→ 返回切换信息；同组合当天仅推 1 次（防刷屏）。
    """
    if not FUND_SWITCH_ENABLE:
        return None
    try:
        # ---- 旧线退潮：核心高位票开板回落 ----
        old_fall = {}  # sector -> [票名...]
        leaders_now = {str(l.get("code")): l for l in (latest.get("leaders") or []) if l.get("code")}
        for code, rec in (sig.get("core_high") or {}).items():
            if not rec.get("sealed"):
                continue
            nl = leaders_now.get(code)
            if not nl:
                continue
            now_pct = float(nl.get("pct") or 0)
            if now_pct < float(rec.get("pct") or 0) - FUND_DROP_PCT:
                sec = rec.get("sector") or ""
                if sec and sec in (sig.get("sector_pushed") or []):
                    old_fall.setdefault(sec, []).append(rec.get("name") or code)
        # ---- 新线承接：涨停潮 + 资金净流入 + 龙头封板/近板 ----
        inflow_names = {str(x.get("name")) for x in
                        ((latest.get("sector_risk") or {}).get("inflow_top") or [])}
        new_up = []
        for s in (latest.get("top5") or []):
            nm = str(s.get("name") or "")
            zt = int(s.get("zt_count") or 0)
            ld = s.get("leader") or {}
            if zt < FUND_NEW_ZT_MIN or nm not in inflow_names:
                continue
            if ld.get("buyable") is False or float(ld.get("pct") or 0) >= ZT_JUDGE_MAIN:
                new_up.append({"name": nm, "zt": zt,
                               "leader": ld.get("name") or "", "lpct": ld.get("pct") or 0})
        # ---- 组合触发 ----
        if old_fall and new_up:
            old_sec = max(old_fall, key=lambda k: len(old_fall[k]))  # 退潮最明显
            new_sec = new_up[0]
            pushed = sig.setdefault("fund_switch_pushed", [])
            key = f"{old_sec}→{new_sec['name']}"
            if key in pushed:
                return None
            pushed.append(key)
            sig["fund_switch_pushed"] = pushed
            return {"old": old_sec, "old_names": old_fall[old_sec],
                    "new": new_sec["name"], "new_zt": new_sec["zt"],
                    "new_leader": new_sec["leader"], "new_lpct": new_sec["lpct"]}
    except Exception as e:
        print(f"[live_signal] fund switch detect ERR:{e}")
    return None


def build_fund_switch_card(sw: dict, hhmm: str) -> dict:
    """💰 资金切换卡（机器检测·非AI判断）。"""
    old_names = "、".join(sw["old_names"][:3])
    lines = [
        f"**💰 资金场内切换**：{sw['old']}退潮 → {sw['new']}承接（{hhmm}）",
        "",
        f"**📉 旧线退潮**：{sw['old']}核心 <font color='green'>{old_names}</font> 开板回落·巨量出货",
        f"**📈 新线承接**：{sw['new']} 涨停 <font color='red'>{sw['new_zt']}只</font>｜"
        f"龙头 <font color='red'>{sw['new_leader']} +{sw['new_lpct']}%</font>",
        "",
        "<font color='turquoise'>切换确认：旧龙开板出货+新方向资金霸榜·跟随新方向承接者，旧线龙头不追</font>",
    ]
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": "💰 资金切换检测（机器检测·非AI判断）"}]}]
    return {"title": f"⚡短线精灵·资金切换·{sw['old']}→{sw['new']} {hhmm}", "color": "red", "elements": elements}


def in_trading() -> bool:
    n = datetime.now()
    hm = n.hour * 60 + n.minute
    in_window = (9 * 60 + 15 <= hm <= 11 * 60 + 30) or (13 * 60 <= hm <= 15 * 60)
    if not in_window:
        return False
    # 节假日门禁：法定节假日与周末（含调休补班日）一律不推送（is_a_share_trading_day 为唯一权威）
    try:
        from common.trading_calendar import is_a_share_trading_day
        return is_a_share_trading_day(n)
    except Exception:
        return n.weekday() < 5  # trading-day-fallback: 唯一真源不可用时的周末兜底


def load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def parse_boards(v) -> int:
    """连板数解析：原上游 boards 字段可能是中文描述串（如 '4天2板'/'3连板'/'首板'）或空串。
    返回板数整数，无法解析时回退 0。"""
    if v is None or v == "":
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    m = re.search(r"(\d+)\s*板", s)
    if m:
        return int(m.group(1))
    if "首板" in s:
        return 1
    if s.isdigit():
        return int(s)
    return 0


def sig_path() -> Path:
    return SNAP.parent / f"pushed_signals_{datetime.now().strftime('%Y%m%d')}.json"


def load_sig() -> dict:
    try:
        return json.loads(sig_path().read_text(encoding="utf-8"))
    except Exception:
        return {"zt_codes": [], "surge_codes": {}, "nf_titles": [], "sector_pushed": [], "strong_codes": [], "charge_codes": []}


def save_sig(s: dict) -> None:
    p = sig_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 普通信号冷却合并（2026-08-18 用户「频率高但不想错过」）────────────────
# 实时模式下每 30s/60s 检测，单轮 1 只冲板/涨停也各推一张 → 盘中刷屏（实测 25 分钟 22 张）。
# 解法：普通信号（快讯/涨停/冲板/强势/突增/板块）先累积到 pending，距上次普通卡 ≥COOLDOWN
# 才推一张合并卡（信号不丢，只是合并呈现）；S级共振/苗头/核按钮/退潮/回流等紧急卡不受
# 冷却限制、仍即时推（在普通卡之前单独推）。
# ⛔ 异动群高频模式（2026-09-03 用户「异动群就是要高频率·不要限制」）：盘中异动推送已切
#    独立机器人（feishu_webhook_intraday），该群定位=高频流水线 → 放开全部时间/次数闸门：
#    COMMON_COOLDOWN 归零（不做合并冷却）/ surge 全时段不限频 / 板块·苗头·回流·核按钮
#    「每天N次」改为状态变化驱动（无变化不重复推，防 30s 轮询纯重复刷屏=幂等底线）。
#    仅保留「同对象同状态幂等去重」。False=回退旧限频（主群模式）。
HIGH_FREQ_INTRADAY = True
COMMON_COOLDOWN_SEC = 0 if HIGH_FREQ_INTRADAY else 120
PENDING_PATH = SNAP / "pulse_pending.json"

# ── 精灵推送最小间隔闸门（2026-09-15 降噪迭代·可逆）──
# 背景：HIGH_FREQ 放开全部闸门后，早盘 09:25-09:55 每轮(30s)都触发 short_spirit --push --force
#   → 单日早盘炸 60+ 张合并卡（信息不丢但刷屏）。新增最小间隔闸门：
#   早盘高频窗口内 SPIRIT_EARLY_MIN_GAP_SEC（保留实时感）/ 窗口外 SPIRIT_MIN_GAP_SEC。
#   间隔未到则本轮跳过触发，待推池持续累积新信号→下个合格轮次一次性批量推（合并呈现·不丢信号）。
# ⛔ 可逆：SPIRIT_MIN_GAP_SEC=0（且 SPIRIT_EARLY_MIN_GAP_SEC=0）即回退"每轮即推"旧行为。
SPIRIT_MIN_GAP_SEC = 90
SPIRIT_EARLY_MIN_GAP_SEC = 45

# ── 板块加强独立卡（2026-09-07 · 优化A/B/C · 2026-09-07 10:46 用户「直接启动」激活）──
# 背景：HIGH_FREQ 高频模式下 A 级板块信号只触发 short_spirit 个股卡，板块级
#   「强势/加强」无独立出口 → 用户看到农业个股刷屏，看不到"农业在整体走强"。
#   A=强势板块资金补查 / B=渐进加强识别 / C=板块家族聚合（映射见 scripts/sector_family.py）。
# ⛔ 可逆：任一开关 False 即回退对应能力；全部 False=与改动前推送行为完全一致。
ENABLE_SECTOR_BOOST_CARD = True     # 总开关：板块强势/加强独立卡出口（异动群）
ENABLE_GRADUAL_BOOST = True         # B：渐进加强（zc>已推峰值 且 距上次渐进报≥8min → 慢涨再报）
GRADUAL_BOOST_MIN_GAP_S = 8 * 60    # B：同板块渐进加强最小间隔（秒·防每轮+1刷屏）
# ── 板块加强卡·全类型最小间隔（2026-09-15 降噪·可逆）──
# 背景：首次/峰值跃迁/渐进三型加强卡无统一板块间隔→单日 76 张。
# 新增：非首次加强（峰值跃迁+2 / 渐进）需满足板块最小间隔才成卡；首次板块强势仍即时（最有价值）。
# ⛔ 可逆：BOOST_MIN_GAP_S=0 即回退"每轮可报"旧行为。
BOOST_MIN_GAP_S = 10 * 60
ENABLE_SECTOR_FAMILY = True         # C：板块家族聚合（家族名去重+合并计数，如 泛农业）
ENABLE_SECTOR_FLOW_LINE = True      # A：板块卡资金行补查（不在前5榜也查，未入榜显灰字）
# ── ⛔ 板块过热预警（2026-09-08 用户「发酵到高位别追高·话术通俗」）──────────
# 语义：主流板块今天已冲很高（涨停家数≥已推峰值80% 且 本轮不再创新高）=情绪最热
# 最易站岗时点 → 提醒别追、等分歧。与 peak 卡互斥（peak=创新高喜报 / 过热=高位滞涨警示）。
ENABLE_CLIMAX_WARN = True           # 总开关：板块过热预警独立卡（异动群）
CLIMAX_MIN_ZT = 10                  # 已推峰值≥10 的板块才够格预警（<10 的小板块/支流不预警——
                                    # 09-08 用户「有点子重复」：医药 6 家过热提示价值低，只给大主流）
CLIMAX_PEAK_PCT = 0.8               # 涨停数 ≥ 已推峰值 80% = 进入过热区
CLIMAX_BROKEN_HINT_N = 8            # 盘中炸板累计≥8 家时附一句「封板在松动」


# ── 早盘前半小时高频实时窗口（2026-08-24 用户「解除早盘前半小时脉冲频率限制·
#    直接实时接口·高频脉冲」→ 2026-08-25 用户「必须根据接口实时推，不需要限制」）────
# 背景：早盘 09:25-09:55 是开盘博弈最激烈时段，用户要求这段时间异动信号
# 直接高频即时推（不等 5min 窗口聚合/120s 冷却/15min surge 限频）。
# 实现：窗口内 A 级信号 → 触发 short_spirit --force 即时推（短线精灵自带当天 code
# 去重·信息不重只提速；待推池无新 code 自动跳过不推空卡）；窗口外维持原 5min 窗口聚合。
# ⛔ 2026-08-25 用户「早盘半小时这个脉冲必须根据接口实时推就行了，不需要限制」：
#    移除 MIN_GAP 45s 冷却——接口有信号就立刻推，不做任何频率节流；
#    surge 突增限频同步移除（原窗口内 60s → 不限频，2026-08-25 用户「取消60秒的限频」）；
#    防刷屏仅靠 short_spirit 当天 code 去重（同票不重推）。
# 关闭方式：EARLY_REALTIME_WINDOW=False（回退 5min 窗口聚合现状）。
EARLY_REALTIME_WINDOW = True
EARLY_REALTIME_START = 9 * 60 + 25   # 09:25（竞价结束·开盘博弈开始）
EARLY_REALTIME_END = 9 * 60 + 55     # 09:55（早盘前半小时结束）

# ── P1-5 退潮/冰点聚合预警卡（2026-09-11 落地·可逆开关）──
# 背景：8/14 冰点日盘中退潮卡 0 张——恶化信号散落在核按钮/晋级率/炸板率/亏钱效应各维度，
# 冰点期没有一张把它们聚合起来的防守卡。本卡=机械聚合（不做个股推荐·不发交易指令）。
ENABLE_EBB_ALERT = True          # False=关闭退潮卡（回退现状）
EBB_SENTIMENT_MAX = 2.5          # 情绪分 ≤ 此值 = 冰点/退潮区（进入候选）
EBB_MIN_DIMS = 2                 # 恶化维度命中数下限（≥2 才推·单项恶化不单独成卡）

# ── P2-10 回流前兆判定放宽（2026-09-11 根因修复·可逆开关）──
# 根因：原判定只认 phase=="re-entry-candidate"，而引擎多数候选落在 分歧/cooling/watch
# （冷却期/低置信沉淀）→ 判定永不命中 → reflow_pushed 长期为 0（8/14 起零触发）。
# 语义：回流前兆 = 冷却/分歧中的题材当日盘中翻回资金净流入榜；boom(今日已爆发)/dead(死透)排除。
REFLOW_PHASE_WIDEN = True
REFLOW_PHASES = {"re-entry-candidate", "cooling", "分歧", "watch"}
REENTRY_BOOM_MIN_ZT = 3   # 与 theme_rotation.BOOM_ZT 同源·回流确认卡强度门槛（今日涨停数需达此值）

# ── 回流前兆「资金趋势」主判据（2026-09-15 用户拍板·口径切换）──
# 背景：「电子布」刷屏事故暴露——原判定唯一触发条件是**当日实时资金榜命中**，
#   而实时榜含「电子」等泛行业名且长期常驻 → 名称类匹配天然脆弱（08-15 加长度门只是补丁）。
# 口径（用户拍板）：**主判据改为 theme_rotation 引擎的 flow_trend**（盘后算·5日资金趋势·
#   不受盘中快照噪声影响）→ 硬门 = phase ∧ flow_trend；
#   **当日实时榜命中降级为「盘中佐证」**（不再否决，仅用于卡面标注与去重指纹：
#   盘中新翻入榜 → 指纹变化 → 冷却到期后可重推，即"变化驱动"）。
# ⛔ 可逆开关：加入 "neutral" 即放宽为「无明确趋势也参与」；置空集合 = 关闭资金趋势门
#   （不推荐·会退回"实时榜单判"的旧行为）。
REFLOW_FLOW_TREND_ALLOW = {"inflow_rising"}

# 回流分层：资金趋势只能说明「钱开始回来」，题材确认还必须有盘中扩散与核心票价格响应。
# 不修改候选与资金趋势阈值，仅决定卡片所处阶段及阶段迁移时是否需要即时提示。
REFLOW_CONFIRM_MIN_ZT = 2
REFLOW_CONFIRM_LEADER_PCT = 5.0

# ── P2-17 L2 机构票实时拉升事件层（2026-09-11 落地·可逆开关）──
# 08:50 机构早盘卡点名池（institution_pool_{date}.json·70+ 只中军/趋势票）× 盘中即时响应：
# 早盘高频窗口内池内票「拉升未封板」→ 即时卡（异动群）。意义=机构点名 × 盘面立刻响应（逻辑被认可）。
# ⛔ 双闸门（09-01 首板卡 18 条刷屏教训）：每票每日 1 次 + 全市场每日总量上限 INST_MAX_DAILY。
ENABLE_INSTITUTION_L2 = True
INST_MIN_PCT = 3.0          # 拉升幅度下限（未封板才算"拉升中"）
INST_MAX_DAILY = 6          # 全市场每日总量闸门


def in_early_realtime_window(now: datetime | None = None) -> bool:
    """早盘前半小时高频实时窗口判定（**交易日** + 09:25-09:55）。

    交易日走唯一真源 `is_a_share_trading_day`（本模块顶部导入）——⛔ 不得自判 `weekday()`。
    """
    if not EARLY_REALTIME_WINDOW:
        return False
    now = now or datetime.now()
    if not is_a_share_trading_day(now):
        return False
    mins = now.hour * 60 + now.minute
    return EARLY_REALTIME_START <= mins <= EARLY_REALTIME_END


def spirit_gap_sec(now: datetime | None = None) -> int:
    """当前时段精灵推送最小间隔（秒）：早盘高频窗口更小、其余更大。"""
    return SPIRIT_EARLY_MIN_GAP_SEC if in_early_realtime_window(now) else SPIRIT_MIN_GAP_SEC


def spirit_push_allowed(sig: dict, now: datetime | None = None) -> bool:
    """精灵推送间隔闸门（2026-09-15 降噪）：距上次精灵推送未达最小间隔则拦。"""
    now = now or datetime.now()
    lp = (sig or {}).get("spirit_last_push")
    if not lp:
        return True
    try:
        el = (now - datetime.fromisoformat(lp)).total_seconds()
    except Exception:
        return True
    return el >= spirit_gap_sec(now)


def load_pending() -> dict:
    try:
        return json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"last_push": None, "nf": [], "zt": [], "surge": [], "strong": [], "sector": [], "charge": []}


def save_pending(p: dict) -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")


def pending_count(p: dict) -> int:
    return sum(len(v) for k, v in p.items() if k != "last_push")


# 资金边际趋势 → 语义 emoji（与 shared_rules 配色规范一致，禁彩色圆点）
FLOW_TREND_EMOJI = {
    "inflow_rising": "📈",
    "outflow_shrinking": "🔆",
    "outflow_widening": "⚠️",
    "neutral": "—",
}
TREND_WEIGHT = {"inflow_rising": 0.2, "outflow_shrinking": 0.15,
                "outflow_widening": 0.0, "neutral": 0.0}


def _juggernaut_charge(sec: dict) -> tuple[bool, str]:
    """容量中军拉升信号（2026-08-17 用户「中军可能拉到5%就要提示」）。

    判定：容量中军（cap_yi≥80 亿）**动了才算**——
      - 中军涨幅 5% ~ `ZT_SEAL_MAIN`(9.8)（未涨停但大涨·资金进场）且板块前排封板（zt_count≥2）
      - 或中军已封板（一字/秒封/封死·pct≥`ZT_SEAL_MAIN`）且板块 zt_count≥2
    光有市值不动（pct<5 且未封板）不算——「容量中军拉到5%+板块前排封板」才是启动确认。
    返回 (是否触发, 触发描述)。用于苗头卡/独立提示，不等共振分。

    ⚠️ 阈值取模块常量（2026-10-14 审计 P0③ 收敛）：此处是**封板确认**口径，
    与 `_pool_new_zt_signals` 的**涨停判定**口径（`ZT_JUDGE_*`）语义不同、故意不同值，
    详见文件头常量块的说明。原为裸字面量 9.8，易与 9.9 混淆。
    """
    leader = sec.get("leader") or {}
    cap = float(leader.get("cap_yi") or 0)
    pct = float(leader.get("pct") or 0)
    zt = int(sec.get("zt_count") or 0)
    seal = str(leader.get("seal") or "")
    lname = str(leader.get("name") or "").strip()
    if cap < 80:
        return False, ""
    if 5.0 <= pct < ZT_SEAL_MAIN and zt >= 2:
        return True, f"容量中军{lname}({cap:.0f}亿)拉到+{pct:.1f}%·板块前排{zt}只封板"
    if pct >= ZT_SEAL_MAIN and any(k in seal for k in ("一字", "秒封", "封死")) and zt >= 2:
        return True, f"容量中军{lname}({cap:.0f}亿){seal.strip()}·板块前排{zt}只封板"
    return False, ""


def _locate_core_stock(sec: dict) -> dict | None:
    """板块核心票定位（2026-08-17 用户「对比成交量/换手定位活跃度·活跃度越高辨识度越高」）。

    板块内（leader + followers）按活跃度排序定位核心票：
    - leader 有 cap_yi(流通市值亿)+turn(换手率%) → 成交额代理 = cap_yi × turn / 100（亿）
    - followers 有 turn（换手率）→ 换手率即活跃度
    活跃度 = 成交额代理（leader 有市值）或换手率（followers），涨幅>0 优先。
    返回 {name, pct, turn, cap_yi, amount_yi, role} 或 None。
    """
    leader = sec.get("leader") or {}
    members: list[dict] = []
    if leader.get("name"):
        turn = float(leader.get("turn") or 0)
        cap = float(leader.get("cap_yi") or 0)
        members.append({
            "name": str(leader.get("name") or "").strip(),
            "pct": float(leader.get("pct") or 0),
            "turn": turn,
            "cap_yi": cap,
            "amount_yi": round(cap * turn / 100, 1) if cap and turn else None,
            "role": "容量中军" if cap >= 80 else "板块龙",
        })
    for f in (sec.get("followers") or []):
        fname = str(f.get("name") or "").strip()
        if not fname:
            continue
        members.append({
            "name": fname,
            "pct": float(f.get("pct") or 0),
            "turn": float(f.get("turn") or 0),
            "cap_yi": 0.0,
            "amount_yi": None,
            "role": "跟风",
        })
    if not members:
        return None

    def _activity(m: dict) -> float:
        # 活跃度：成交额代理（亿）优先；无市值则换手率(%)（跟风票）
        return m["amount_yi"] if m["amount_yi"] else m["turn"]

    # 涨幅>0 优先（有资金认可），再按活跃度降序 → 核心票 = 活跃且上涨的辨识度最高票
    members.sort(key=lambda m: (m["pct"] > 0, _activity(m)), reverse=True)
    return members[0]


def _resonance_score(sec: dict, index: list | None = None) -> tuple[int, list[str]]:
    """板块共振评分（2026-08-17 用户「S级题材=板块整体共振，非单股暴拉」）。

    评分要素（每项 1 分，满分 5）：
      1) 板块联动：zt_count >= 3（板块多只涨停·全军出动）
      2) 容量中军：leader.cap_yi >= 80 亿（大市值核心动·容量中军参与）
      3) 板块广度：zt_count / total_stocks >= 0.5（板块内过半涨停·整体性）
      4) 大盘共振：三指数平均涨幅 >= 1.0%（大盘同向配合·甚至共振大盘）
      5) 封死叠加：leader.seal 含「一字」/「秒封」/「封死」（一字涨停等要素叠加）

    返回 (score, 命中要素文案列表)。score >= 4 = S级题材（整体性共振确认）；
    score 3 = 次强（板块动但缺大盘/容量配合）；< 3 = 单股级（不算 S 级）。
    """
    hits = []
    sc = 0
    zt = int(sec.get("zt_count") or 0)
    total = int(sec.get("total_stocks") or 0)
    leader = sec.get("leader") or {}
    cap = float(leader.get("cap_yi") or 0)
    seal = str(leader.get("seal") or "")
    # 1) 板块联动
    if zt >= 3:
        sc += 1
        hits.append(f"板块{zt}只涨停")
    elif zt >= 1:
        hits.append(f"板块{zt}只涨停")
    # 2) 容量中军（2026-08-17 用户「中军可能拉到5%就要提示」：市值大不算，**动了才算**——
    #    cap≥80亿 且 涨幅≥5% 或已封板。光有市值不动（pct<5 未封板）= 中军未参与，不构成共振）
    if cap >= 80:
        jc, jc_desc = _juggernaut_charge(sec)
        if jc:
            sc += 1
            hits.append(jc_desc)
        elif float(leader.get("pct") or 0) >= 5.0 or any(k in seal for k in ("一字", "秒封", "封死")):
            # 中军动了（≥5% 或封板）但板块前排封板不足 2 只——仍算中军参与，弱分
            sc += 1
            hits.append(f"容量中军{leader.get('name','')}({cap:.0f}亿·+{float(leader.get('pct') or 0):.1f}%)")
    # 3) 板块广度（过半涨停）
    if total > 0 and zt / total >= 0.5:
        sc += 1
        hits.append(f"板块过半涨停({zt}/{total})")
    # 4) 大盘共振
    if index:
        pcts = [float(i.get("pct") or 0) for i in index if isinstance(i, dict)]
        if pcts:
            avg = sum(pcts) / len(pcts)
            if avg >= 1.0:
                sc += 1
                hits.append(f"大盘共振(均{avg:+.1f}%)")
    # 5) 封死叠加（2026-08-17：若 seal 已被中军拉升描述引用（jc_desc 含 seal），
    #    不重复加文案，避免「🔥秒封板」出现两次；分仍计）
    if any(k in seal for k in ("一字", "秒封", "封死")):
        sc += 1
        if not any(seal.strip() in h for h in hits):
            hits.append(f"{seal.strip()}")
    return sc, hits


def _resonance_signal(sec: dict, prev_sec: dict | None, index: list | None = None) -> tuple[int, list[str]]:
    """盘中前瞻共振评分（2026-08-17 用户「不能等半个板块涨停才提示·买个得儿」）。

    与 _resonance_score（滞后确认·看当下 zt_count 过半）不同，本函数**前瞻识别 S 级题材苗头**：
    - 板块刚启动（本轮涨停数较上轮上升）时就提示，不等过半涨停
    - 要素（每项 1 分，满分 5）：
      1) 启动增量：本轮 zt_count > 上轮 zt_count（板块正在启动·涨停数上升）
      2) 容量中军：leader.cap_yi >= 80 亿（大市值核心参与·容量中军动）
      3) 启动强度：本轮 zt_count >= 2（至少 2 只涨停·非单股）
      4) 封板节奏：leader.seal 含 一字/秒封/早封（封板快·资金坚决）
      5) 大盘共振：三指数均涨幅 >= 1.0%（大盘配合）
    返回 (score, hits)。score >= 4 = S级题材苗头（前瞻·板块刚启动即可推）；
    score 3 = 关注（有苗头但缺要素）；< 3 = 忽略。
    """
    hits = []
    sc = 0
    zt = int(sec.get("zt_count") or 0)
    prev_zt = int((prev_sec or {}).get("zt_count") or 0)
    leader = sec.get("leader") or {}
    cap = float(leader.get("cap_yi") or 0)
    seal = str(leader.get("seal") or "")
    # 1) 启动增量（本轮 > 上轮·正在启动）
    if zt > prev_zt:
        sc += 1
        hits.append(f"启动({prev_zt}→{zt})")
    # 2) 容量中军（2026-08-17 用户「中军可能拉到5%就要提示」：市值大不算，**动了才算**——
    #    前瞻版同样要求 cap≥80亿 且 涨幅≥5% 或已封板；光有市值不动 = 中军未参与）
    if cap >= 80:
        jc, jc_desc = _juggernaut_charge(sec)
        if jc:
            sc += 1
            hits.append(jc_desc)
        elif float(leader.get("pct") or 0) >= 5.0 or any(k in seal for k in ("一字", "秒封", "早封", "封死")):
            sc += 1
            hits.append(f"容量中军{leader.get('name','')}({cap:.0f}亿·+{float(leader.get('pct') or 0):.1f}%)")
    # 3) 启动强度（较上轮增量 ≥2：板块级扩散，非单股首板）
    if zt - prev_zt >= 2:
        sc += 1
        hits.append(f"扩散({prev_zt}→{zt})")
    elif zt >= 3 and prev_zt == 0:
        # 首现即 3 只+（板块突然整体启动）
        sc += 1
        hits.append(f"首现{zt}只")
    # 4) 封板节奏（一字/秒封/早封 = 资金坚决）
    if any(k in seal for k in ("一字", "秒封", "早封", "封死")):
        sc += 1
        # 防重（2026-08-28 修复·对齐 S 级版 _resonance_score 394-395）：jc_desc 已含 seal 文本时
        # 不重复追加，避免苗头卡「🔥秒封板」出现两次（实证 14:27 大金融苗头卡三段重复）
        if not any(seal.strip() in h for h in hits):
            hits.append(f"{seal.strip()}")
    # 5) 大盘共振
    if index:
        pcts = [float(i.get("pct") or 0) for i in index if isinstance(i, dict)]
        if pcts:
            avg = sum(pcts) / len(pcts)
            if avg >= 1.0:
                sc += 1
                hits.append(f"大盘共振(均{avg:+.1f}%)")
    return sc, hits


def _sector_trend_dir(zt_series: list[int | None]) -> str:
    """板块涨停数趋势方向（2026-08-17 用户「等级可迁移·看板块往上还是往下」）。

    对比最近 2 个非空点（含本轮）：
      - 末 > 前 → "rising"（向上·预判升级）
      - 末 < 前 → "falling"（向下·预判降级）
      - 平 → "flat"（横盘·维持当前等级）
    """
    vals = [v for v in zt_series if v is not None]
    if len(vals) < 2:
        return "flat"
    if vals[-1] > vals[-2]:
        return "rising"
    if vals[-1] < vals[-2]:
        return "falling"
    return "flat"


def _predict_level_shift(
    cur_level: str, trend_dir: str, flow_yi: float | None = None
) -> str:
    """等级迁移预测（2026-08-17 用户核心思路：当前卡等级 → 预测可能到 S 级/B 级）。

    输入当前等级（pre_s / s / a）+ 板块趋势方向（rising/falling/flat）+ 板块资金（亿）：
      - pre_s(苗头) + rising → 预判升级 S 级（扩散中·共振要素在增强）
      - pre_s + falling     → 预判降级（扩散未持续·留意熄火）
      - s(共振) + falling   → 预判降级 A 级（共振瓦解·警惕退潮）
      - s + flat + 资金流出 → 预判降级（横盘但资金撤离）
      - a(窗口) + rising    → 预判升级 pre-S/S（资金回流·扩散重启）
      - 其余 → 维持当前等级（观望）
    返回短句（如「📈 预判升级 S 级」/「📉 预判降级」/「➡️ 维持观望」）。
    """
    if cur_level == "pre_s":
        if trend_dir == "rising":
            return "📈 预判升级 S 级（扩散中·共振要素增强）"
        if trend_dir == "falling":
            return "📉 预判降级（扩散未持续·留意熄火）"
        return "➡️ 维持苗头观望（横盘待变）"
    if cur_level == "s":
        if trend_dir == "falling":
            return "📉 预判降级 A 级（共振瓦解·警惕退潮）"
        if trend_dir == "flat" and flow_yi is not None and flow_yi < 0:
            return "📉 预判降级（横盘但资金撤离）"
        return "➡️ 维持 S 级（共振延续）"
    # a 级
    if trend_dir == "rising":
        return "📈 预判升级（资金回流·扩散重启）"
    return "➡️ 维持 A 级（次级信号聚合）"


# 🆕 首板卡板块名桥接（2026-09-01）：选股宝板块名 vs theme_rotation 题材名 口径不一致
# （数字人民币↔数字货币 / 种植业↔粮食概念）→ 同义词映射 + 前缀匹配兜底，防"老题材误判新题材首日"
_FIRST_ZT_ALIAS = {
    "数字人民币": "数字货币", "数字货币": "数字货币",
    "种植业": "粮食概念", "大农业": "粮食概念", "农业": "粮食概念",
    "光模块": "CPO概念", "光通信": "CPO概念",
}


def _sector_ctx(sec: str) -> str:
    """板块热度上下文（🆕 首板卡·2026-09-01）：
    theme_rotation.sector_zt_trends 近5日是否有涨停活动 → 老题材（回流/续热）vs 新题材首日。
    匹配：精确 → 双向包含 → 同义词别名 → 前2字前缀（防口径差异误判）。"""
    try:
        tr = json.loads((CACHE / "theme_rotation.json").read_text(encoding="utf-8"))
        cand = _FIRST_ZT_ALIAS.get(sec, sec)
        for z in tr.get("sector_zt_trends") or []:
            zt = z.get("theme") or ""
            if not zt:
                continue
            hit = (cand == zt) or (sec == zt) or (cand in zt) or (zt in cand) or (
                len(cand) >= 2 and len(zt) >= 2 and cand[:2] == zt[:2])
            if not hit:
                continue
            s = [v for v in (z.get("zt_series") or []) if v]
            if s:
                d_cn = {"rising": "升温", "falling": "退潮", "new": "首现",
                        "flat": "平稳"}.get(z.get("dir"), "")
                return f"近5日有涨停活动（{len(s)}日{d_cn}）·老题材"
            break
        return "近5日无涨停活动 · 新题材首日"
    except Exception:
        return "热度数据缺失"


def build_first_zt_card(fz: dict, sec: str, hhmm: str) -> dict:
    """🆕 题材首板即时卡（2026-09-01 用户「首板即提示·不等3只确认爆发」）：
    板块当日第一只涨停 → 30s 内即时推（每天每板块限1次·由调用方 sig.first_zt_map 去重），
    附板块热度上下文。直接解决「晚于题材爆发节点」——首板时刻即爆发节点第一时间。"""
    name = fz.get("name") or ""
    code = fz.get("code") or ""
    pct = fz.get("pct") or 0
    is20 = "🔥20cm" if fz.get("is_20cm") else "📈"
    boards = fz.get("boards") or ""
    lines = [
        f"**🆕 题材首板（当日该板块第1只涨停）**：",
        f"  <font color='red'>▸ {sec}</font>｜{is20} {name}({code}) +{pct:.2f}% {boards}",
        f"　📜 上下文：{_sector_ctx(sec)}",
        f"　💡 关注：板块扩散≥2只或龙头封死 → 升级 S 级；单枪未扩散则观察",
    ]
    return {"title": f"⚡短线精灵·题材首板 {hhmm}", "color": "orange",
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
                         {"tag": "note", "elements": [
                             {"tag": "plain_text", "content": f"🆕 首板提示（机器检测·非AI判断）{hhmm}"}]}]}


_ZT_HIST_CACHE: dict = {"sig": None, "res": None}   # mtime 缓存：文件未变不重读磁盘


def _load_zt_history(ndays: int = N_SHAPE_HIST_DAYS) -> tuple[dict, list]:
    """加载近 N 个交易日涨停池历史（zt_history/zt_*.json·排除今日文件）→ (hist, trade_days)。
    hist[code] = {"name","dates":[涨停日升序],"last_date","last_boards","max_boards","last_sector"}。
    🔁 N 字检测数据源：第一波涨停记录（第一波存在 + 断板回调 + 今日再涨停 = 二波启动）。

    性能/数据完整性（2026-09-01 用户「不会每次都重新拿数据吧」）：
    ① mtime 签名缓存——守护链 30s/60s 一轮，zt_history 文件未变直接复用内存结果（6.4ms→~0ms）；
    ② yesterday_zt.json 补齐——zt_history 某日若为盘中快照（intraday_only·zt_list 空，如 08-31 盘后链
       未沉淀），用 yesterday_zt.json 当日 88 票补全，恢复「昨日涨停=连板排除」判定（防误报 N 字）。"""
    import glob
    _all = sorted(glob.glob(str(CACHE / "zt_history" / "zt_*.json")))
    # ⛔ 2026-10-09 修正：窗口按**交易日**取（唯一真源 = common.trading_calendar）。
    # 长假期间盘中写入的空壳（intraday_only·zt_list=[]，如 10-01/02/05）此前占满 12 日窗的
    # 3 个名额 → 形态/N字/二波检测实际只有 9 个交易日的料，且休市日被混进 `trade_days`。
    files = [f for f in _all if is_a_share_trading_day(os.path.basename(f)[3:13])][-ndays:]
    yz_path = CACHE / "yesterday_zt.json"
    # 文件签名（mtime 元组）——任一文件变化即失效
    try:
        sig = tuple(os.path.getmtime(f) for f in files) + (os.path.getmtime(yz_path),)
    except OSError:
        sig = None
    if _ZT_HIST_CACHE["sig"] == sig and _ZT_HIST_CACHE["res"] is not None:
        return _ZT_HIST_CACHE["res"]
    today = datetime.now().strftime("%Y-%m-%d")
    trade_days: list[str] = []
    hist: dict = {}
    seen_days = set()
    day_has_zt: set = set()          # 该日是否有实际涨停记录（盘中快照 zt_list 空则无）
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        date = str(d.get("date") or "")
        if not date:
            continue
        # 排除今日文件（盘中快照 intraday_only 无 zt_list 全量；今日涨停用实时 new_zt）
        if date >= today or date in seen_days:
            continue
        seen_days.add(date)
        trade_days.append(date)
        for x in (d.get("zt_list") or []):
            code = str(x.get("code") or "")
            if not code:
                continue
            day_has_zt.add(date)
            h = hist.setdefault(code, {"name": x.get("name"), "dates": [],
                                       "last_date": "", "last_boards": 0,
                                       "max_boards": 0, "last_sector": "",
                                       "board_by_date": {}})
            b = parse_boards(x.get("boards"))
            h["dates"].append(date)
            # 逐日板数留痕（2026-09-14）：卡面「首板日 → N板」链条需要，不能只留 max_boards
            (h.setdefault("board_by_date", {}))[date] = b
            h["last_date"] = date
            h["last_boards"] = b
            h["max_boards"] = max(h["max_boards"], b)
            h["last_sector"] = str(x.get("sector") or "")
    # 补齐：zt_history 某日有文件但无涨停数据（盘中快照/盘后未沉淀，如 08-31 zt_list 空）
    # → 用 yesterday_zt.json 当日 zt_list 补，恢复「昨日涨停=连板排除」判定。
    try:
        yz = json.load(open(yz_path, encoding="utf-8"))
        ydate = str(yz.get("date") or "")
        if ydate and ydate < today and ydate not in day_has_zt:
            seen_days.add(ydate)
            if ydate not in trade_days:
                trade_days.append(ydate)
            for x in (yz.get("zt_list") or []):
                code = str(x.get("code") or "")
                if not code:
                    continue
                h = hist.setdefault(code, {"name": x.get("name"), "dates": [],
                                           "last_date": "", "last_boards": 0,
                                           "max_boards": 0, "last_sector": "",
                                           "board_by_date": {}})
                b = parse_boards(x.get("boards") or x.get("lbc"))
                h["dates"].append(ydate)
                (h.setdefault("board_by_date", {}))[ydate] = b
                h["last_date"] = ydate
                h["last_boards"] = b
                h["max_boards"] = max(h["max_boards"], b)
                h["last_sector"] = str(x.get("sector") or "")
    except Exception:
        pass
    trade_days.sort()
    res = (hist, trade_days)
    _ZT_HIST_CACHE["sig"] = sig
    _ZT_HIST_CACHE["res"] = res
    return res


def _n_shape_gap(code: str, hist: dict, trade_days: list, today: str):
    """距上次涨停的交易日间隔（含今日）：None=无历史涨停；1=昨日涨停（连板·非N字）；≥2=N字二波。"""
    h = hist.get(code)
    if not h or not h.get("dates") or h.get("last_date") >= today:
        return None
    last = h["last_date"]
    if last not in trade_days:
        return None
    idx = trade_days.index(last)
    return len(trade_days) - idx


def _resolve_hist(hist, code: str) -> dict:
    """取单票涨停档案——**兼容两种入参形态**（2026-09-14 修「?首板 → ? 0板连板」事故）。

    ① 全量表 `{code: {...}}`（`_load_zt_history` 原始返回）；
    ② 单票档案 `{name, dates, max_boards, ...}`（调用方已解引用）。
    ⛔ 旧实现只认①、而唯一调用方传的是② → `hist.get(code)` 恒为 None → 卡面 4 个字段全部渲染成
    「?首板 → ? 0板连板」，数据其实一直在 zt_history 里（属纯渲染层 bug，非数据缺失）。
    """
    if not isinstance(hist, dict) or not hist:
        return {}
    if code and isinstance(hist.get(code), dict):
        return hist[code]
    if "dates" in hist or "max_boards" in hist:
        return hist
    return {}


def _first_wave_chain(h: dict, trade_days: list) -> dict:
    """第一波（最近一段**连续**涨停）链条 → {start,end,start_boards,boards,span,gap}。

    - start  = 连板起点（首板日）· end = 第一波最后涨停日 · boards = 该段最高板数
    - span   = 该段交易日跨度 · gap = 距 end 的**完整断板交易日数**（≥1 才算断板回调）
    只认 `trade_days` 内相邻的交易日；中间缺料（归档空日）不硬连，避免把两段合并成假连板。
    """
    dates = [d for d in (h.get("dates") or []) if d in trade_days]
    if not dates:
        return {}
    pos = [trade_days.index(d) for d in dates]
    s = len(pos) - 1
    while s > 0 and pos[s - 1] == pos[s] - 1:
        s -= 1
    seq = dates[s:]
    bbd = h.get("board_by_date") or {}
    bbs = [int(bbd.get(d) or 0) for d in seq]
    return {"start": dates[s], "end": dates[-1], "span": len(seq),
            "start_boards": bbs[0] if bbs else 0,
            "boards": max(bbs) if bbs else int(h.get("max_boards") or 0),
            "gap": max(0, len(trade_days) - 1 - pos[-1]),
            "window_edge": bool(trade_days) and dates[0] == trade_days[0]}


_HITHINK_LADDER_CACHE: dict = {"sig": None, "res": None}


def _hithink_ladder_map() -> dict:
    """同花顺「连板天梯」30日缓存（`cache/hithink_ladder.json`·仅 2 板以上）→ {ticker: {...}}。

    ⛔ 只作**校验/兜底**：天梯不含首板，无法给出「首板日」，故不作为主数据源；
    用于 ①连板高度交叉校验 ②zt_history 窗口内缺料时的板数兜底。
    mtime 签名缓存（守护链 30s/60s 一轮，文件未变不重读）。
    """
    p = CACHE / "hithink_ladder.json"
    try:
        sig = os.path.getmtime(p)
    except OSError:
        return {}
    if _HITHINK_LADDER_CACHE["sig"] == sig and _HITHINK_LADDER_CACHE["res"] is not None:
        return _HITHINK_LADDER_CACHE["res"]
    res: dict = {}
    try:
        d = json.load(open(p, encoding="utf-8"))
        for it in (d.get("items") or []):
            date = str(it.get("date") or "")
            for lvl, arr in (it.get("boards") or {}).items():
                for x in (arr or []):
                    t = str(x.get("ticker") or "")
                    if not t:
                        continue
                    e = res.setdefault(t, {"name": x.get("name"), "max_boards": 0, "dates": []})
                    bn = int(x.get("board_num") or 0)
                    e["max_boards"] = max(e["max_boards"], bn)
                    e["dates"].append(date)
    except Exception:
        res = {}
    _HITHINK_LADDER_CACHE["sig"] = sig
    _HITHINK_LADDER_CACHE["res"] = res
    return res


def _mmdd(d: str) -> str:
    """'2026-09-08' → '09-08'（卡面紧凑；空值回退原串）。"""
    s = str(d or "")
    return s[5:] if len(s) == 10 and s[4] == "-" else (s or "—")


def build_n_shape_card(stock: dict, hist, sec_info: dict, hhmm: str,
                       trade_days: list | None = None) -> dict:
    """🔁 N字二波启动即时卡（2026-09-01 用户「N字形态重点关注」）：
    个股历史涨停（第一波·近12交易日）→ 断板回调（≥1完整交易日）→ 今日再涨停 = 二波启动。
    与首板卡互补：首板=新题材第1枪；N字=老龙回调后再启动（板块二波·新龙反推旧龙同族形态）。
    调用方负责 sig.n_shape_map 去重（每天每票1次）；`hist` 可传全量表或单票档案（见 _resolve_hist）。

    ⛔ 2026-09-14 修复：旧版把调用方传入的**单票档案**当全量表再解一次 → 四字段恒为「?/0」。
    现改为真实链条渲染（首板日 → 连板高度 → 断板回调天数），并用同花顺天梯做校验/兜底。
    """
    code = str(stock.get("code") or "")
    h = _resolve_hist(hist, code)
    name = stock.get("name") or h.get("name") or ""
    pct = float(stock.get("pct") or 0)
    boards = stock.get("boards") or ""
    is20 = "🔥20cm" if stock.get("is_20cm") else "📈"
    sec = str(stock.get("sector") or h.get("last_sector") or "")
    zt_today = sec_info.get("zt_today") or 0
    top = sec_info.get("top") or ""

    # ── 第一波链条（主）+ 同花顺天梯（校验/兜底）──
    chain = _first_wave_chain(h, trade_days or []) if trade_days else {}
    boards_chain = int(chain.get("boards") or 0)
    ladder = (_hithink_ladder_map().get(code) or {}) if code else {}
    ladder_b = int(ladder.get("max_boards") or 0)
    ladder_last = max([d for d in (ladder.get("dates") or []) if trade_days and d in trade_days], default="")
    if chain:
        sd, ed = _mmdd(chain.get("start")), _mmdd(chain.get("end"))
        gap = int(chain.get("gap") or 0)
        use_b = max(boards_chain, ladder_b)
        # 首板日标注：该段起点板数 ≤1 才算「首板」；>1 说明窗口内已是连板中段，如实标板数
        head = f"{sd}首板" if int(chain.get("start_boards") or 0) <= 1 else f"{sd} {chain.get('start_boards')}板"
        extra = ""
        if ladder_b > boards_chain:
            extra = f" ·天梯见{ladder_b}板（同花顺30日）"
            print(f"[live_signal] 📊 N字天梯校验 {name}({code})：zt_history {boards_chain}板 "
                  f"< 同花顺 {ladder_b}板 → 取高值")
        if chain.get("window_edge"):
            extra += " ｜首波起点在窗口首日·可能更早"
        line = f"　📐 N字：{head} → {ed} {use_b}板连板 → 断板回调{gap}天 → 今日再启动{is20}{extra}"
    elif ladder_b:
        # 兜底：zt_history 窗口（12 日）里没有这只票，但同花顺天梯（30 日）见到过连板
        if ladder_last:
            gap = len(trade_days) - 1 - trade_days.index(ladder_last)
            wave = f"同花顺天梯 {_mmdd(ladder_last)} 止于 {ladder_b}板"
            gtxt = f"断板回调{gap}天"
        else:
            wave = f"同花顺天梯 30 日内最高 {ladder_b}板"
            gtxt = "断板回调"
        line = f"　📐 N字：首波在12日窗口外（{wave}） → {gtxt} → 今日再启动{is20}"
    else:
        print(f"[live_signal] ⚠️ N字卡缺首波数据 {name}({code})｜zt_history 窗口={len(trade_days or [])}天"
              f"·天梯无记录 → 降级渲染")
        line = "　📐 N字：历史窗口内无首波记录（今日首次启动）"
    lines = [
        "**🔁 N字二波启动（回调后再启动）**：",
        f"  <font color='red'>▸ {name}({code}) +{pct:.2f}% {boards}</font>",
        line,
        f"　🏷 板块：{sec}（今日涨停{zt_today}只{top}）",
        "　💡 关注：二波需放量过前高确认，量能/情绪双升才成立",
        "　⛔ 破前波起点或 5日线 失效即离场，勿在缩量反抽中追高",
    ]
    return {"title": f"⚡短线精灵·N字二波 {hhmm}", "color": "red",
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
                         {"tag": "note", "elements": [
                             {"tag": "plain_text", "content": f"🔁 N字形态监测（机器检测·非AI判断）{hhmm}"}]}]}


def _reflow_name_hit(theme: str, board: str) -> bool:
    """题材名 ↔ 资金榜板块名 的命中判定（2026-09-15 修·防「短名吞长名」误命中）。

    ⛔ 原实现（1816 行）为裸双向子串 `nm in n or n in nm`，无长度门 →
       榜内「电子」（2 字）是题材「电子布」的子串 → 把"电子布"误判成在资金净流入榜，
       而它根本不在榜（09-15 实证：latest 榜 10 个板块无一项含"电子布"）。
       叠加 30min 时间冷却 → 只要「电子」在榜就每半小时弹一张回流卡，今日 6 次刷屏。

    规则：① 精确相等恒命中；
          ② 「题材名 ⊂ 榜内名」恒命中（芯片→国产芯片、电子布→电子布概念）；
          ③ 「榜内名 ⊂ 题材名」**仅当榜内名 ≥3 字**才成立。
    ③ 的长度门是关键：榜内泛行业名（电子/芯片/电网，多为 1-2 字）不得因是
    具体题材名的子串就判为"该题材在榜"——否则「电子」常驻榜 → 「电子布」假命中。
    """
    t = (theme or "").strip()
    b = (board or "").strip()
    if not t or not b:
        return False
    if t == b:
        return True
    if len(t) >= 2 and t in b:      # 题材名 ⊂ 榜内名
        return True
    if len(b) >= 3 and b in t:      # 榜内名 ⊂ 题材名（须 ≥3 字，防短名误吞）
        return True
    return False


def _reflow_flow_ok(c: dict) -> bool:
    """回流前兆**主判据**（2026-09-15 用户拍板·口径切换）：引擎资金趋势是否「流入上升中」。

    取 `flow_trend`（theme_rotation 引擎盘后算·5 日资金趋势，纯值）为主；
    缺失时回退 `confidence_factors.capital`——⚠️ 该字段可能带中文后缀
    （如 "neutral(无趋势)" / "inflow_fading(流入转弱)"）→ 取括号前主值，防比较永不相等。

    语义：回流前兆 = 该题材**本身**资金趋势向上（不再依赖当日实时榜命中）。
    """
    ft = str(c.get("flow_trend") or "").strip()
    if not ft:
        ft = str((c.get("confidence_factors") or {}).get("capital") or "").split("(")[0].strip()
    return ft in REFLOW_FLOW_TREND_ALLOW


def _reflow_price_evidence(theme: str, top5: list) -> dict:
    """题材回流的盘中价格确认：板块新晋/升温 + 至少两只涨停 + 核心票明显拉升。

    未匹配到当前 top5 只表示尚无实时价格证据，不能据此编造成「回流失败」。
    """
    for sector in top5 or []:
        if not _reflow_name_hit(theme, str(sector.get("name") or "")):
            continue
        zt = int(sector.get("zt_count") or 0)
        leader = sector.get("leader") or {}
        leader_pct = float(leader.get("pct") or 0)
        active = str(sector.get("delta") or "") in {"new", "surging"}
        confirmed = active and zt >= REFLOW_CONFIRM_MIN_ZT and leader_pct >= REFLOW_CONFIRM_LEADER_PCT
        return {"matched": True, "confirmed": confirmed, "sector": sector.get("name") or theme,
                "zt_count": zt, "leader": leader, "leader_pct": leader_pct, "active": active}
    return {"matched": False, "confirmed": False}


def _reflow_fail_reason(c: dict) -> str:
    """只在已提示的回流阶段转弱时出风险状态，避免把数据缺失误写成失败。"""
    trend = str(c.get("flow_trend") or "")
    return {"inflow_fading": "资金转弱", "outflow_shrinking": "资金尚未转入",
            "outflow_widening": "资金流出放大", "neutral": "资金未形成回流"}.get(trend, "资金趋势失效")


def build_reflow_card(cands, hhmm: str) -> dict:
    """回流监控卡：区分资金试探、题材确认和回流失效（阶段变化驱动，避免重复刷屏）。

    资金趋势仍是入池前提；只有板块扩散与核心票拉升同时成立，才显示为「题材确认」。
    """
    grouped = {"题材确认": [], "资金试探": [], "回流失效": []}
    for c in cands:
        grouped.setdefault(c.get("_reflow_stage") or "资金试探", []).append(c)
    lines = ["**🔔 回流监控**（资金试探需等题材价格确认）："]
    for stage, icon, color in (("题材确认", "✅", "red"), ("资金试探", "🟣", "purple"), ("回流失效", "⚠️", "orange")):
        rows = grouped.get(stage) or []
        if not rows:
            continue
        lines.append(f"**{icon} {stage} {len(rows)}个**：")
        for c in rows[:6]:
            theme = c.get("theme", "")
            pool = "、".join(f"{s.get('name')}({s.get('code')})"
                             for s in (c.get("watch_pool") or [])[:4] if s.get("name"))
            live = c.get("_reflow_price") or {}
            if stage == "题材确认":
                leader = live.get("leader") or {}
                evidence = (f"{live.get('sector')} 涨停{live.get('zt_count')}只"
                            f"｜{leader.get('name') or '核心票'} +{float(live.get('leader_pct') or 0):.2f}%")
            elif stage == "回流失效":
                evidence = c.get("_reflow_fail_reason") or "资金趋势失效"
            else:
                _lh = c.get("_reflow_live_hits") or []
                evidence = f"盘中入榜 {_lh[0]}" if _lh else "等待板块扩散"
            lines.append(f"　<font color='{color}'>▸ {theme}｜{evidence}｜跟踪池 {pool}</font>")
    lines.append("")
    lines.append("<font color='purple'>资金试探不等于题材启动；仅扩散与核心票同步时升级为题材确认</font>")
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}]
    elements.append({"tag": "note", "elements": [
        {"tag": "plain_text", "content": "🔔 资金与题材回流监控（机器检测·非AI判断）"}]})
    return {"title": f"⚡短线精灵·回流监控 {hhmm}", "color": "purple", "elements": elements}


def build_reflow_confirm_card(cands, hhmm: str) -> dict:
    """回流确认卡：题材冷却后今天重新爆发（系统原盲区信号）。

    与回流前兆卡互补——前兆卡抓"冷却中预判回流"，本卡抓"今天已经爆发的回流"。
    大白话呈现，不堆机器黑话；不是买点信号，只做事实提醒。
    """
    lines = ["**⚡ 回流确认**（之前冷却过、今天重新爆发，不是新题材）："]
    for c in cands[:6]:
        theme = c.get("theme", "")
        _zt = c.get("boom_zt") or 0
        zt = int(_zt) if isinstance(_zt, (int, float)) and float(_zt) == int(_zt) else _zt
        gap = c.get("reentry_gap_days") or 0
        pool = "、".join(f"{s.get('name')}" for s in (c.get("watch_pool") or [])[:4] if s.get("name"))
        lines.append(f"　<font color='red'>▸ {theme}｜今日涨停{zt}只｜冷却{gap}天后回流｜核心 {pool}</font>")
    lines.append("")
    lines.append("<font color='purple'>回流确认是事实提醒，不是买点信号；下手看龙头封单、五日线与资金是否真跟上</font>")
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}]
    elements.append({"tag": "note", "elements": [
        {"tag": "plain_text", "content": "⚡ 题材回流确认（机器检测·非AI判断）"}]})
    return {"title": f"⚡短线精灵·回流确认 {hhmm}", "color": "red", "elements": elements}


def _detect_reflow_confirm(candidates, pushed_map, sig_map, now, cooldown_sec):
    """回流确认候选筛选（2026-09-30，单一源·可单测）。

    输入候选（已带 is_reentry 标记），做：
    ① is_reentry 过滤 ② 板块强度二次过滤(今日涨停数≥阈值) ③ 限频+内容指纹去重防刷屏。
    返回 (待推候选列表, 跳过数, 冷却去重数)。
    """
    out, skip, cool = [], 0, 0
    for c in candidates or []:
        if not c.get("is_reentry"):
            skip += 1
            continue
        nm = c.get("theme", "")
        if not nm:
            continue
        _zt = c.get("boom_zt") or 0
        # 板块强度二次过滤：今日涨停数需达爆发阈值（is_reentry 已隐含今日 boom，此处双保险）。
        if _zt < REENTRY_BOOM_MIN_ZT:
            continue
        _sig_now = f"{int(_zt)}#" + "|".join(
            sorted(str(s.get("code") or "") for s in (c.get("watch_pool") or [])[:4]))
        _last = pushed_map.get(nm, "")
        _ok = not _last
        if _last:
            try:
                _cd_ready = (now - datetime.fromisoformat(_last)).total_seconds() > cooldown_sec
            except Exception:
                _cd_ready = False
            # 冷却到期只代表"允许再推"，还须指纹变化（涨停数/跟踪池变了）才算真变化，防刷屏。
            if _cd_ready and sig_map.get(nm, "") != _sig_now:
                _ok = True
            else:
                _ok = False
                cool += 1
        if _ok:
            out.append(c)
            pushed_map[nm] = now.isoformat()
            sig_map[nm] = _sig_now
    return out, skip, cool


def build_card(new_nf, new_zt, new_surge, new_strong, hhmm: str, new_sector=None, new_charge=None) -> dict:
    # 2026-08-18 用户「用这逻辑替换群里的盘中异动信号」：本卡已被短线精灵引擎接管，
    # 生产推送走 short_spirit.py --push（见三处触发点），本函数保留仅作回滚/参考。
    elements = []
    # 🚀 冲板预警 = 接近涨停未封板（先于涨停播报·2026-08-14 提速·机会行紫·置顶）
    if new_charge:
        lines = ["**🚀 冲板预警**（接近涨停·未封板·先于涨停播报）："]
        for l in new_charge[:6]:
            tag = "🔥20cm" if l.get("is_20cm") else "🚀"
            row = f"{l.get('name')}({l.get('code')}) +{l.get('pct')}% 〔{l.get('sector', '')}〕"
            lines.append(f"  {tag} <font color='purple'>{row}</font>")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
    if new_nf:
        lines = []
        for nf in new_nf[:3]:
            lines.append(f"📡 {nf.get('title', '')}")
            st = nf.get("stocks") or []
            if st:
                lines.append("　涉及：" + "、".join(st[:6]))
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
    # 已封板/涨停 = 整行红（shared_rules 统一配色：涨停标杆行整行 red，不分项染色）
    if new_zt:
        lines = ["**⚡ 新涨停**："]
        for l in new_zt[:6]:
            tag = "🔥20cm" if l.get("is_20cm") else "📈"
            row = f"{l.get('name')}({l.get('code')}) +{l.get('pct')}% 〔{l.get('sector', '')}〕"
            lines.append(f"  {tag} <font color='red'>{row}</font>")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
    # 未封板机会 = 整行紫（shared_rules 统一配色：推荐/机会行整行 purple，不分项染色）
    if new_surge:
        lines = ["**🚀 涨幅突增**（较上轮·未封板）："]
        for it in new_surge[:6]:
            l = it["l"]
            row = f"{l.get('name')}({l.get('code')}) +{l.get('pct')}% (Δ{it['delta']:.1f}) 〔{l.get('sector', '')}〕"
            lines.append(f"  <font color='purple'>{row}</font>")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
    if new_strong:
        lines = ["**🔆 强势领涨**（未封板·埋伏观察）："]
        for l in new_strong[:6]:
            row = f"{l.get('name')}({l.get('code')}) +{l.get('pct')}% 〔{l.get('sector', '')}〕"
            lines.append(f"  <font color='purple'>{row}</font>")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
    # 板块级异动（TOP5 涨停>=阈值·今日首次推送，去重限频防刷屏）
    # S级题材共振（2026-08-17）：resonance_score>=4 时展示「🔥 S级题材·共振要素」
    if new_sector:
        lines = []
        for s in new_sector[:6]:
            rs = s.get("resonance_score")
            if rs is not None and rs >= 4:
                lines.append(f"**🔥 S级题材·板块共振 {rs}/5**：")
            else:
                lines.append("**📊 板块强势**（今日首次达涨停阈值·机器检测）：")
            leader = s.get("leader", {})
            zt = s.get("zt_count", 0)
            name = s.get("name", "")
            boards = leader.get("boards", "")
            lname = leader.get("name", "")
            lcode = leader.get("code", "")
            lpct = leader.get("pct", 0)
            followers = "、".join(f"{f.get('name')}+{f.get('pct')}%" for f in (s.get("followers") or [])[:4])
            lines.append(f"  <font color='red'>▸ {name}｜{zt}只涨停</font>")
            lines.append(f"　龙头: {lname}({lcode}) +{lpct}% {boards}")
            if followers:
                lines.append(f"　跟风: {followers}")
            if s.get("desc"):
                lines.append(f"　催化: {s.get('desc')}")
            # 共振要素（用户核心关切：板块动+容量中军+大盘共振+一字封死）
            hits = s.get("resonance_hits") or []
            if hits:
                lines.append(f"　⚡共振: {' + '.join(hits)}")
            # 板块核心票（2026-08-17 用户「对比成交量/换手定位活跃度·辨识度越高越核心」）
            core = s.get("core_stock")
            if core:
                amt = core.get("amount_yi")
                amt_txt = f"·成交{amt}亿" if amt else ""
                lines.append(
                    f"　🔑核心票: <font color='red'>{core.get('name')}</font>"
                    f" +{core.get('pct')}% 换手{core.get('turn')}%{amt_txt}"
                    f"（{core.get('role')}·活跃度最高辨识度最高）"
                )
            # 等级迁移预测（2026-08-17 用户「等级可迁移·看板块往上还是往下」）
            ls = s.get("level_shift")
            if ls:
                lines.append(f"　{ls}")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
    elements.append({"tag": "note", "elements": [
        {"tag": "plain_text", "content": f"⚡ 实时信号播报 {hhmm}（机器检测·非AI判断）"}]})
    return {"title": f"⚡短线精灵·盘中异动 {hhmm}", "color": "red", "elements": elements}


FUND_POOL = SNAP / "sector_fund_pool.json"   # 2026-09-08：盘中全量板块资金池（intraday_pulse 每轮刷新≈360板块）
FUND_POOL_MAX_AGE = 600                       # >10min 视为过期 → 回退 sector_risk top10（守护未刷新的兼容态）


def _load_fund_pool(max_age: float = FUND_POOL_MAX_AGE) -> dict | None:
    """读本地全量板块资金池（cache/pulse_snapshots/sector_fund_pool.json）。

    返回 {"ts","date","boards":{name:{pct,flow_yi}}}；缺失/过期/损坏 → None（调用方走旧兜底）。
    """
    try:
        if not FUND_POOL.exists():
            return None
        d = json.loads(FUND_POOL.read_text(encoding="utf-8"))
        ts = float(d.get("ts") or 0)
        if datetime.now().timestamp() - ts > max_age:
            return None
        if not isinstance(d.get("boards"), dict) or not d["boards"]:
            return None
        return d
    except Exception:
        return None


def _alias_members(board_name: str) -> list:
    """题材聚合名 → 东财资金板块成员（SECTOR_ALIAS 单一源=scripts/sector_family.py）。"""
    if not board_name:
        return []
    try:
        from sector_family import SECTOR_ALIAS
        return list(SECTOR_ALIAS.get(board_name) or [])
    except Exception:
        return []


def _resolve_flows(board_names, pool: dict) -> list:
    """板块名集合 → 池内真实板块资金（去重·2026-09-08 家族资金解析）。

    board_names：板块加强卡板块名（1 个：大消费/机器人…）或家族本次聚合成员名
    （多个 top5 聚合名：泛农业 的 family_members=[大农业15]）。每个名字解析链：
      池内匹配 → SECTOR_ALIAS 成员池内匹配；
    同一真实东财板块只计一次（防家族成员/别名展开重复）。返回 [(real_nm, flow_yi)]。
    """
    if not pool or not board_names:
        return []
    out: dict[str, float] = {}
    for bnm in board_names:
        bnm = str(bnm or "").strip()
        if not bnm:
            continue
        f, hit = _pool_flow(bnm, pool)
        if hit is not None:
            out.setdefault(hit, f)
            continue
        for m in _alias_members(bnm):
            mf, mhit = _pool_flow(m, pool)
            if mhit is not None:
                out.setdefault(mhit, mf)
    return sorted(out.items(), key=lambda kv: -abs(kv[1]))


def _norm_board(nm: str) -> str:
    """剥泛尾缀（概念/板块/指数），两套板块命名的归一化。"""
    return str(nm or "").replace("概念", "").replace("板块", "").replace("指数", "").strip()


def _pool_flow(board_name: str, pool: dict) -> tuple:
    """资金池板块资金解析（纯函数·2026-09-08·供单测锁定）。

    匹配链：池内精确 → 剥泛尾缀精确 → 双向子串(≥2字·多命中取板块名与题材名长度差
    最小者=粒度最接近，防"通信"吞掉"光通信模块"之类的泛匹配污染)。
    返回 (flow_yi, hit_board_name|None)——flow_yi=命中板块主力净额(亿·可为负)；未命中 → (None, None)。
    """
    if not pool or not board_name:
        return None, None
    boards = pool.get("boards") or {}
    if not boards:
        return None, None
    base = _norm_board(board_name)
    if not base:
        return None, None

    def _f(nm: str):
        v = boards.get(nm)
        if v is None:
            return None
        try:
            return float(v.get("flow_yi") or 0.0)
        except (TypeError, ValueError):
            return None

    # 1) 精确
    f = _f(board_name)
    if f is not None:
        return f, board_name
    # 2) 剥泛尾缀精确
    for nm in boards:
        if _norm_board(nm) == base:
            f = _f(nm)
            if f is not None:
                return f, nm
    # 3) 双向子串：候选按「前后缀亲缘优先 + 名字更长者优先」排序——
    #    防"通信"(泛板块)吞掉"光通信模块"(具体板块)：base=光通信 时两候选都含
    #    子串，但"光通信模块"与 base 是前后缀关系、粒度更接近，应优先命中。
    best, best_key, best_f = None, None, None
    for nm in boards:
        nn = _norm_board(nm)
        if len(base) >= 2 and len(nn) >= 2 and (base in nn or nn in base):
            # 前后缀亲缘(0) > 一般包含(1)；亲缘族内名字更长者粒度更细更优
            affin = (nn.startswith(base) or nn.endswith(base)
                     or base.startswith(nn) or base.endswith(nn))
            key = (0 if affin else 1, -len(nn))
            if best_key is None or key < best_key:
                best_key, best, best_f = key, nm, _f(nm)
    if best is not None and best_f is not None:
        return best_f, best
    return None, None


def _sector_money_txt(latest: dict, board_name: str, *, enabled: bool = True,
                      family_members: list | None = None) -> str:
    """资金行补查（2026-09-08 升级·本地板块资金池版）：强势板块资金必须可查、不消失。

    原实现只查 latest.sector_risk.inflow_top/outflow_top（Top10）——涨停多但资金排不进
    前10的板块一律「资金未入榜」（实证 09-08 大消费 zt=6 资金榜却被通信/有色占满）；
    且 top5 板块名(东财涨停聚合口径)与 clist 资金板块名(细粒度)两套命名系统对不上。
    解析链（诚实·逐级降级）：
      1) 单板块：全量资金池 sector_fund_pool.json（≈360 板块·盘中每轮刷新）精确/尾缀/子串命中；
      2) 聚合题材别名 SECTOR_ALIAS（sector_family.py 单一源）→ 成员板块净额**加总**，
         口径=该题材「板块合计主力净额」——成员缺失自动跳过，不编造；
      3) 家族板块（family_members 传入·如 泛农业〔大农业15〕）：对**本次实际聚合成员**
         逐个走 1/2 解析再合计（⛔ 不用静态 FAMILIES 全表——避免把未涨停的
         泛标签成员如乡村振兴也摊进资金，实证口径=卡面成员）；
      4) 池过期/未建 → sector_risk top10 旧逻辑兜底；
      5) 全 miss → 诚实标「无对应板块资金口径」，不消失不编造。
    enabled=False → 返回空（不加行）。
    """
    if not enabled or not latest:
        return ""
    pool = _load_fund_pool()
    # 1-3) 资金池解析（家族=成员集合；单板块=自身）
    if pool:
        if family_members:
            names = [str(m.get("name") or "") if isinstance(m, dict) else str(m)
                     for m in family_members]
            names = [n for n in names if n]
        else:
            names = [board_name]
        hits = _resolve_flows(names, pool)
        if hits:
            tot = sum(f for _, f in hits)
            if len(hits) == 1 and not family_members:
                # 池内直连单板块 → 直接净流入/背离（无合计字眼）
                nm0, f0 = hits[0]
                if f0 >= 0:
                    return f"💰主力净流入 <text_tag color='red'>+{f0:.1f}亿</text_tag>"
                return (f"⚠️资金背离·主力净流出 <text_tag color='green'>-{abs(f0):.1f}亿</text_tag>"
                        f"（{nm0}）")
            # 别名/家族聚合 → 板块口径合计 + 成员明细前3
            clr = "red" if tot >= 0 else "green"
            sign = "+" if tot >= 0 else "-"
            det = "丨".join(f"{n}{v:+.1f}" for n, v in hits[:3])
            head = board_name if not family_members else str(board_name or "家族")
            return (f"💰{head}板块合计净额 <text_tag color='{clr}'>{sign}{abs(tot):.1f}亿</text_tag>"
                    f"（{det}…）")
    # 4) 兜底：sector_risk top10（池过期/未建时）
    sr = latest.get("sector_risk") or {}
    base = _norm_board(board_name)

    def _hit(name: str) -> bool:
        n = _norm_board(name)
        if not n or not base:
            return False
        return n == base or (len(base) >= 2 and (base in n or n in base))

    for f in sr.get("inflow_top") or []:
        if _hit(f.get("name")):
            v = float(f.get("flow_yi") or 0)
            return f"💰主力净流入 <text_tag color='red'>+{v:.1f}亿</text_tag>"
    for f in sr.get("outflow_top") or []:
        if _hit(f.get("name")):
            v = abs(float(f.get("flow_yi") or 0))
            return f"⚠️资金背离·主力净流出 <text_tag color='green'>-{v:.1f}亿</text_tag>"
    if family_members:
        # 家族全 miss → 点名实际成员，避免"泛农业"给人无实体的错觉
        mem = [str(m.get("name") or "") if isinstance(m, dict) else str(m)
               for m in family_members][:3]
        return "💤 资金未入榜（家族成员均无东财独立板块资金：{}）".format("丨".join(mem))
    return "💤 资金未入榜（东财板块口径无此板块·题材聚合无独立资金）"


def _decide_sector_stage(zc: int, pzc: int, *, first: bool, peak: int,
                         high_freq: bool = True, gradual_ok: bool = False) -> str | None:
    """板块触发分级（纯函数·2026-09-07 时效门修复·供 main 板块检测调用与单测锁定）。

    时效铁律（用户 2026-09-07 11:30「推送必须当下·别推早上发生很久的事」）：
      所有触发（first/peak/gradual）必须 本轮 zc > 上轮同口径 pzc（本轮涨停数真实
      上升=当下增量）；存量高度（早达到、本轮无增量）即使当天没报过也**不补报**。
    事故实证：10:46 开家族开关，泛农业 09:25 就 9 只但家族名不在 sector_pushed →
      被误判 first 补报旧闻。此函数统一收口时效门，杜绝再犯。
    返回：stage（first/peak/gradual）或 None（不推）。
    """
    if zc < 3 or zc <= pzc:          # zc 达阈值(3) 且 较上轮真实上升
        return None
    if first:
        return "first"
    if high_freq and zc >= peak + 2:
        return "peak"
    if gradual_ok and zc > peak:
        return "gradual"
    return None


def _climax_hit(zc: int, peak: int) -> bool:
    """过热判定（纯函数·2026-09-08·供单测锁定）。

    触发三条件（全部满足）：已推峰值 ≥ CLIMAX_MIN_ZT（够格的大板块）；
    当前涨停数已进入峰值 80% 高位区（zc*10 >= peak*8）；本轮未再创新高
    （zc<=peak——创新高继续由 peak 卡报喜，过热=高位滞涨才警示）。
    """
    if peak < CLIMAX_MIN_ZT or zc < CLIMAX_MIN_ZT:
        return False
    return (zc * 10 >= peak * int(CLIMAX_PEAK_PCT * 10)) and zc <= peak


def build_climax_card(sectors: list, hhmm: str, broken_cnt: int = 0) -> dict:
    """⛔ 板块过热预警卡（2026-09-08 用户「发酵到高位别追高」·话术通俗）。

    提醒：主流板块涨停家数已冲到今日高点（≥已推峰值 80%）且不再往上创新高——
    最热闹的时候追进去最容易站岗（T+1 今天买明天才能卖）；炸板多时补一句
    「封板在松动」。每天每板块最多 1 次（sig.climax_warned），机器检测非 AI 判断。
    """
    def md(*lines):
        return {"tag": "lark_md", "content": "\n".join(lines)}

    lines = []
    for s in (sectors or [])[:3]:
        nm = str(s.get("name") or "")
        zc = int(s.get("zt_count") or 0)
        lines.append(f"**⛔ {nm} 涨停 {zc} 家·已经冲到今天的最高点了**")
        # 一句话把「位置+后果」讲清即可（09-08 用户「有点子重复」：删独立💡行/去同义复述）
        lines.append(f"　{nm} 从早盘一路加到 {zc} 家，热度到顶——这个位置追进去容易站岗"
                     "（今天买、明天才能卖）")
    if len(sectors or []) > 3:
        lines.append(f"　（另有 {len(sectors) - 3} 个板块同样过热，别追）")
    if broken_cnt >= CLIMAX_BROKEN_HINT_N:
        lines.append(f"　今天盘中已有 {broken_cnt} 家涨停被砸开，封板在松动，追高更要小心")
    elements = [{"tag": "div", "text": md(*lines)}, {"tag": "hr"},
                {"tag": "note", "elements": [md(f"⛔ 板块过热机器检测 | {hhmm} | 非AI判断·防站岗提示")]}]
    return {"title": f"⛔ 短线精灵·板块过热 {hhmm}", "color": "orange", "elements": elements}


def build_sector_boost_card(sectors: list, hhmm: str, latest: dict) -> dict:
    """📊 板块加强独立卡（2026-09-07 优化A/B/C·总开关 ENABLE_SECTOR_BOOST_CARD 默认关）。

    解决 HIGH_FREQ 高频模式下 A 级板块信号无独立出口的断层：new_sector 只塞 a_events
    触发 short_spirit 个股卡 → 用户看不到「XX板块在整体走强」。本卡把 new_sector
    （首次/峰值跃迁/渐进加强）独立成卡，直推异动群（仿核按钮/N字 --send 通道）。
    标题按 _sector_stage 区分：first=板块强势 / peak=强度新高 / gradual=持续加强。
    家族聚合板块（_family=True）额外渲染构成：泛农业〔大农业7+养猪2〕9只涨停。
    ⛔ 渲染用 <text_tag> 胶囊（<font> 飞书不渲染·2026-08-25 用户抓包后全卡统一）；
       A 开关开时每板块附资金行（命中流入红/流出绿/未入榜灰），关时不加行。
    """
    def md(*lines):
        return {"tag": "lark_md", "content": "\n".join(lines)}

    def block(*lines):
        return {"tag": "div", "text": md(*lines)}

    lines = []
    for s in sectors[:5]:
        stage = s.get("_sector_stage") or "first"
        nm = str(s.get("name") or "")
        zt = int(s.get("zt_count") or 0)
        ldr = s.get("leader") or {}
        lname = ldr.get("name") or ""
        lcode = str(ldr.get("code") or "").replace(".SZ", "").replace(".SS", "")
        lpct = ldr.get("pct")
        boards = ldr.get("boards") or ""
        sub = str(s.get("sub_text") or "")
        if stage == "first":
            lines.append(f"**📊 板块强势**（涨停 {zt} 只·机器检测）：")
        elif stage == "peak":
            lines.append(f"**📈 板块加强·强度新高**（涨停 {zt} 只·超已报峰值）：")
        else:
            lines.append(f"**📈 板块持续加强**（涨停升至 {zt} 只·较上次+变化）：")
        nm_txt = f"{nm}〔{sub}〕" if (sub and s.get("_family")) else nm
        lines.append(f"　<text_tag color='red'>▸ {nm_txt}｜{zt}只涨停</text_tag>")
        # 涨停成分清单（2026-09-08 用户「这种涨停的话，把涨停的个股列出来」）：
        # ⛔ 09-07 删的是「龙头:xxx」行（防存量连板高标被当旧闻）；zt_stocks=该板块
        # **当前涨停成分**（live 当下状态），板块加强卡应列出=让用户看到"到底是哪几只"。
        # 格式对齐 🔥突发题材榜 跟涨行风格：全红·名(代码)+涨幅··分隔·单行自动折行。
        _zs = s.get("zt_stocks") or []
        if _zs:
            # 分段展示（2026-09-21 用户「个股太多堆了一堆·布局分割一下」）：
            # 原 12 只平铺一行 → 视觉堆叠；现 20cm 分档 + 每行 5 只（信息不砍·超量记「等N只」）
            lines.extend(_fmt_zt_stocks(_zs))
        # 板块卡纯板块级：不列个股龙头（防存量连板龙头被当个股旧闻推·2026-09-07 用户铁律
        # 「异动群推送必须当下增量·禁推存量」）；个股级即时推送归短线精灵（A 激活后连板龙头即时卡）
        # A：资金行补查（默认关；关时不加行，与改动前一致）
        # 2026-09-08 11:0x：家族板块(泛农业)传入本次聚合成员 → 资金行对成员逐个解析
        #   加总（家族名本身无同名资金板块；成员=大农业15 → alias 农业种植等加总）
        _fm = s.get("family_members") if s.get("_family") else None
        money = _sector_money_txt(latest, nm, enabled=ENABLE_SECTOR_FLOW_LINE,
                                  family_members=_fm)
        if money:
            lines.append(f"　{money}")
    lines.append("")
    lines.append("📈 板块级强度机器检测：涨停家数达阈值/创新高/持续爬升即报，非个股推荐")
    elements = [block(*lines), {"tag": "hr"},
                {"tag": "note", "elements": [md(f"📡 板块强势/加强 | {hhmm} | 机器检测·非AI判断")]}]
    return {"title": f"📊 短线精灵·板块加强 {hhmm}", "color": "red", "elements": elements}


def _fmt_zt_stocks(zs: list, max_show: int = 12, per_line: int = 5) -> list:
    """板块涨停成分清单 → **分段文本行**（2026-09-21 用户「个股太多堆了一堆·布局分割一下」）。

    背景：涨停成分原平铺一行（12 只 × 约 20 字符 = 单行 240+ 字符）→ 飞书上视觉堆成一坨
    （实证 13:31 医药板块 20 只涨停）。现按 **20cm 优先分档 + 每行 per_line 只**输出多行；
    ⛔ 信息不砍（超出 max_show 记「等 N 只」），仅换行分割。

    ⛔ 防御性过滤（2026-09-21 实证卡面出现 688 票）：剔除 **科创板 688**（用户无权限·铁律）
    与 **ST**（风险标的）——上游若漏滤，卡面也不显示；家数口径与卡面展示一致（有效标的）。

    返回 list[str]（每项一行·含全角缩进与红色标签）；[] = 无有效成分。
    """
    if not zs:
        return []
    # 防御性过滤：688 科创板（无权限）+ ST（风险）
    zs = [z for z in zs
          if not str(z.get("code") or "").strip().startswith("688")
          and "ST" not in str(z.get("name") or "").upper()]
    if not zs:
        return []

    def _one(z: dict) -> str:
        return (f"{z.get('name')}({str(z.get('code') or '')[:6]})"
                f"+{float(z.get('pct') or 0):.1f}%")

    total = len(zs)
    # 头部统计用**全量**分档计数（⛔ 非截断后数量——避免「主板7只」式误导，2026-09-21 修正）
    n20 = len([z for z in zs if float(z.get("pct") or 0) >= 19.0])
    n10 = total - n20
    head = f"🔒 涨停{total}家"
    if n20 and n10:
        head += f"（20cm {n20}只 丨 主板 {n10}只）"
    out = [f"　<font color='red'>{head}</font>"]
    shown = zs[:max_show]
    z20 = [z for z in shown if float(z.get("pct") or 0) >= 19.0]
    z10 = [z for z in shown if float(z.get("pct") or 0) < 19.0]
    groups = ([("20cm", z20), ("主板", z10)] if z20 and z10
              else ([("20cm", z20)] if z20 else [("", z10)]))
    for label, group in groups:
        if not group:
            continue
        for i in range(0, len(group), per_line):
            chunk = group[i:i + per_line]
            prefix = f"{label}：" if (label and i == 0) else "　　"
            out.append(f"　<font color='red'>{prefix}{'丨'.join(_one(z) for z in chunk)}</font>")
    if total > max_show:
        out.append(f"　<font color='red'>　　等{total - max_show}只…</font>")
    return out


def build_nuke_card(fb: dict, hhmm: str) -> dict:
    """核按钮预警卡（分级·2026-08-07 用户标准）。

    破位分级：
      - break_count>=3（核按钮中真破位=跌破5日线+10日线家数）→ 高位破位杀跌·亏钱效应恶化
      - 否则（连板分歧回踩5日线/仍线上）→ 正常分歧，不报「回避高位接力」
    """
    nuke_count = fb.get("nuke_count") or 0
    break_count = fb.get("break_count") or 0
    is_breakdown = break_count >= 3

    if is_breakdown:
        title = f"⚡短线精灵·高位破位预警 {hhmm}"
        lines = [f"**⚠️ 高位破位杀跌**·亏钱效应恶化（{break_count}只跌破5日线+10日线）："]
    else:
        title = f"⚡短线精灵·连板分歧提示 {hhmm}"
        lines = ["**⚠️ 连板分歧回踩**：昨日涨停今日大跌，但多数为连板分歧回踩5日线（仍在均线上）："]

    for n in (fb.get("nuke_list") or [])[:6]:
        tag = "昨连板" if n.get("prev_boards", 0) >= 2 else "昨涨停"
        # 破位标记：真破位（跌破5日线+10日线）走红，否则绿（跌绿）
        if n.get("breakdown") is True:
            color = "red"
        else:
            color = "green"
        pos = "破位" if n.get("breakdown") is True else (
            "破5日线" if n.get("break_ma5") is True else "仍线上")
        lines.append(f"<font color='{color}'>{n.get('name')}({n.get('code')}) {n.get('pct')}%</font>　[{tag}·{pos}]")
    if not (fb.get("nuke_list") or []):
        lines.append("（名单缺失）")
    lines.append("")
    # 语义色板（2026-08-16 用户定）：核按钮多=恶化=绿；晋级率低于阀门=恶化=绿
    # 阀门=feedback_matrix 产 promotion_gate_10d（前10交易日去极值截尾均值·09-08 用户定），缺失回退固定25
    _pgate = (fb.get("trend") or {}).get("promotion_gate_10d") or 25
    nuke_clr = "green" if nuke_count >= 3 else ""
    prom_clr = "green" if (fb.get("promote_rate") is not None and fb.get("promote_rate") < _pgate) else ""
    lines.append(f"昨涨停 {fb.get('prev_zt_count')}只｜核按钮 "
                 f"<font color='{nuke_clr}'>{nuke_count}只</font>｜"
                 f"真破位 {break_count}只｜均涨幅 {fb.get('avg_pct')}%｜晋级率 "
                 f"<font color='{prom_clr}'>{fb.get('promote_rate')}%</font>")
    if is_breakdown:
        lines.append("<font color='red'>建议：回避高位接力，防守优先，等待亏钱效应收敛</font>")
    else:
        lines.append("<font color='turquoise'>解读：连板分歧回踩5日线属正常调整，未见真破位杀跌；高位股仍看5日线得失，破5日线+10日线再降仓</font>")
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}]
    elements.append({"tag": "note", "elements": [
        {"tag": "plain_text", "content": "⚠️ 核按钮预警（机器检测·非AI判断）"}]})
    return {"title": title, "color": "turquoise", "elements": elements}


def _ebb_dims(fb: dict, latest: dict) -> list:
    """P1-5 退潮/冰点恶化维度命中表（机械判定·只列真实命中的维度，缺失数据不编造）。"""
    dims = []
    if (fb.get("nuke_count") or 0) >= 3:
        dims.append(f"核按钮 {fb.get('nuke_count')} 只（亏钱效应显形）")
    _pgate = (fb.get("trend") or {}).get("promotion_gate_10d") or 25
    _pr = fb.get("promote_rate")
    if _pr is not None and _pr < _pgate:
        dims.append(f"晋级率 {_pr}% < 动态阀门 {_pgate}%（接力质量差）")
    if (fb.get("broken_rate") or 0) >= 35:
        dims.append(f"炸板率 {fb.get('broken_rate')}%（封板不稳）")
    if fb.get("avg_pct") is not None and fb["avg_pct"] < 0:
        dims.append(f"昨涨停今均 {fb.get('avg_pct')}%（亏钱效应）")
    _tr = fb.get("trend") or {}
    if _tr.get("promote_downtrend") is True:
        dims.append("晋级率下行趋势")
    if _tr.get("avg_pct_weak") is True:
        dims.append("赚钱效应走弱")
    _ld = latest.get("limit_down") or {}
    if (_ld.get("count") or 0) >= 5:
        dims.append(f"跌停 {_ld.get('count')} 只")
    return dims


def build_ebb_card(fb: dict, latest: dict, hhmm: str, dims: list) -> dict:
    """P1-5 退潮/冰点聚合预警卡（2026-09-11 落地）。

    机械聚合恶化维度（恶化=绿·语义色板 2026-08-16）；结论只给"以观察为主"级纪律提示，
    ⛔ 不发个股买卖指令（数据/AI 边界铁律）。
    """
    score = fb.get("sentiment_score")
    label = fb.get("sentiment_label") or "—"
    ld = latest.get("limit_down") or {}
    bk = latest.get("broken") or {}
    lines = [f"**🌊 冰点/退潮环境**：情绪 <font color='green'>{score}/10·{label}</font>", ""]
    lines.append(f"**恶化的方面**（命中 {len(dims)} 项）：")
    for d in dims:
        lines.append(f"<font color='green'>▸ {d}</font>")
    lines.append("")
    lines.append(f"昨涨停 {fb.get('prev_zt_count')}只｜跌停 {ld.get('count') or 0}只"
                 f"｜炸板 {bk.get('count') or 0}只｜均涨幅 {fb.get('avg_pct')}%")
    if ld.get("names"):
        lines.append("跌停：" + "、".join(str(n) for n in (ld.get("names") or [])[:5]))
    lines.append("")
    lines.append("<font color='turquoise'>解读：退潮/冰点期亏钱效应主导，以观察为主；"
                 "等情绪分回升 + 晋级率站回阀门后再评估接力</font>")
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
                {"tag": "hr"},
                {"tag": "note", "elements": [{"tag": "plain_text",
                 "content": "🌊 退潮/冰点聚合预警（机器检测·非AI判断）"}]}]
    return {"title": f"🌊 短线精灵·退潮冰点预警 {hhmm}", "color": "turquoise", "elements": elements}


def build_institution_card(hits: list, hhmm: str) -> dict:
    """🏛️ 机构票拉升卡（P2-17 L2·2026-09-11 落地）。

    hits = [(code, name, pct, theme)]——机构早盘卡点名池内、早盘窗口内拉升未封板的票。
    机器检测·非AI判断；⛔ 不构成个股推荐（机会色=红·语义色板）。
    """
    lines = ["**🏛️ 机构票拉升**（今晨机构早盘卡点名 × 盘中即时响应）：", ""]
    for c, n, p, th in hits:
        lines.append(f"<font color='red'>{n}({c}) +{p:.2f}%</font>"
                     + (f"　<font color='grey'>{th[:30]}</font>" if th else ""))
    lines.append("")
    lines.append("<font color='turquoise'>解读：机构点名方向出现盘中资金响应（逻辑被认可）；"
                 "未封板=尚在博弈，有没有资金接需自行验证</font>")
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
                {"tag": "hr"},
                {"tag": "note", "elements": [{"tag": "plain_text",
                 "content": "🏛️ 机构票拉升（机器检测·非AI判断）"}]}]
    return {"title": f"🏛️ 短线精灵·机构票拉升 {hhmm}", "color": "red", "elements": elements}


def main():
    dry = "--dry-run" in sys.argv[1:]
    if not in_trading():
        print("[live_signal] 非交易时段，跳过")
        return
    latest = load(SNAP / "latest.json")
    prev = load(SNAP / "prev.json")
    sig = load_sig()
    now = datetime.now()
    hhmm = now.strftime("%H:%M")
    import tempfile
    new_nf, new_zt, new_surge, new_strong = [], [], [], []
    new_charge = []
    new_sector = []

    # 1) 新实时快讯（当天 title 去重 + ⛔时效门 2026-09-07：只推最近 600s 的当下快讯，
    #    数据层 intraday_pulse 已滤，此处双保险——实证 09-07 express 推了 55min 前旧快讯）
    prev_titles = set(sig.get("nf_titles", []))
    for nf in (latest.get("newsflash") or []):
        t = (nf.get("title") or "").strip()
        if not t or t in prev_titles:
            continue
        ca = nf.get("ctime") or nf.get("created_at")   # parse 后字段=ctime；原始消息=created_at
        if ca is not None:
            try:
                if isinstance(ca, (int, float)):
                    _nt = float(ca if ca < 1e12 else ca / 1000)
                else:
                    _s = str(ca).strip()
                    _nt = (datetime.fromisoformat(_s.replace("Z", "+00:00")).timestamp()
                           if "T" in _s else datetime.strptime(_s, "%Y-%m-%d %H:%M:%S").timestamp())
                if datetime.now().timestamp() - _nt > 600:
                    continue
            except Exception:
                pass
        if any(w in t for w in HARD):
            new_nf.append(nf)
            sig.setdefault("nf_titles", []).append(t)

    # 2) 新涨停龙头 + 3) 涨幅突增
    # prev 检测池同样 = leaders + rising（rising 票的 surge 突增对比也需上轮基准，2026-08-18）
    prev_leaders = {l.get("code"): l for l in (prev.get("leaders") or []) if l.get("code")}
    for _pr in (prev.get("rising") or []):
        _pc = str(_pr.get("code") or "")
        if _pc and _pc not in prev_leaders:
            prev_leaders[_pc] = {
                "code": _pc, "name": _pr.get("name") or _pc,
                "pct": float(_pr.get("pct") or 0), "price": _pr.get("price") or 0,
                "buyable": not bool(_pr.get("up")),
                "is_20cm": _pc[:3] in ("300", "301"),
                "boards": "", "sector": _pr.get("industry") or "",
            }
    zt_codes = set(sig.get("zt_codes", []))
    surge_codes = sig.get("surge_codes", {})
    strong_codes = set(sig.get("strong_codes", []))
    charge_codes = set(sig.get("charge_codes", []))
    # 冲板动态阈值：随情绪/赚钱效应浮动（2026-08-14 用户定·灵活不僵化）
    try:
        _fb = json.loads((CACHE / "feedback_matrix.json").read_text(encoding="utf-8"))
        _senti = int(_fb.get("sentiment_score") or 3)
    except Exception:
        _senti = 3
    if _senti >= 3:
        charge_main, charge_20cm = 7.5, 15.0   # 情绪好·赚钱效应佳 → 更积极
    else:
        charge_main, charge_20cm = 8.5, 17.0   # 退潮/分歧·亏钱效应大 → 更保守
    # 检测池 = 板块领涨 leaders + 东财涨幅榜兜底 rising（2026-08-18：选股宝异动池只收精选
    # ~9 只未封板票，涨幅≥5% 的非精选票如中大力德会被漏；rising 字段由 intraday_pulse 抓
    # push2 涨幅榜前 200 生成，本循环统一对两池做 zt/charge/strong/surge 检测）
    _pool = list(latest.get("leaders") or [])
    _seen_pool = {str(l.get("code")) for l in _pool if l.get("code")}
    for _r in (latest.get("rising") or []):
        _c = str(_r.get("code") or "")
        if not _c or _c in _seen_pool:
            continue
        _seen_pool.add(_c)
        _is20 = _c[:3] in ("300", "301")
        _pool.append({
            "code": _c, "name": _r.get("name") or _c,
            "pct": float(_r.get("pct") or 0), "price": _r.get("price") or 0,
            "buyable": not bool(_r.get("up")), "is_20cm": _is20,
            "boards": "", "sector": _r.get("industry") or "",
        })
    for l in _pool:
        code = l.get("code")
        if not code:
            continue
        pct = float(l.get("pct") or 0)
        is_20 = bool(l.get("is_20cm"))
        zt_thr = ZT_JUDGE_20CM if is_20 else ZT_JUDGE_MAIN
        # 涨停判定（2026-08-18 修）：单有板数不足为凭——未封板拉升票也带历史板数，
        # 会被误标"涨停"（如中石科技 09:31 +19.53% 未封板被列进新涨停）。
        # 正确标准 = 涨幅达标 或（有板数 且 当前已封板 buyable=False）。
        _sealed = l.get("buyable") is False
        is_zt = (pct >= zt_thr) or (parse_boards(l.get("boards")) > 0 and _sealed)
        pl = prev_leaders.get(code)
        # 上轮是否已涨停：避免「开盘即板」被当成新信号一锅端
        prev_zt = False
        if pl is not None:
            pzt_thr = ZT_JUDGE_20CM if bool(pl.get("is_20cm")) else ZT_JUDGE_MAIN
            _psealed = pl.get("buyable") is False
            prev_zt = (float(pl.get("pct") or 0) >= pzt_thr) or (parse_boards(pl.get("boards")) > 0 and _psealed)
        if is_zt and not prev_zt and code not in zt_codes:
            new_zt.append(l)
            zt_codes.add(code)
        # 强势领涨（20cm票涨超10%但未封板）：提前播报埋伏窗口
        if (not is_zt) and is_20 and pct >= 10.0 and code not in strong_codes:
            new_strong.append(l)
            strong_codes.add(code)
        # 🚀 冲板预警（2026-08-14 提速·接近涨停未封板先报，避免"推送来已涨停"）：
        # 阈值随市场情绪浮动（2026-08-14 用户定：逻辑要灵活、注重情绪与赚钱效应）：
        #   情绪分≥3（修复/偏强/亢奋·赚钱效应好）→ 主板7.5%/20cm15%（积极捕捉机会）
        #   情绪分<3（分歧/退潮·亏钱效应大）→ 主板8.5%/20cm17%（保守，只报真强，减少退潮期噪音）
        if (not is_zt) and code not in charge_codes:
            charge_thr = charge_20cm if is_20 else charge_main
            if pct >= charge_thr:
                new_charge.append(l)
                charge_codes.add(code)
        if pl is not None:
            ppct = float(pl.get("pct") or 0)
            d = pct - ppct
            last = surge_codes.get(code)
            # 涨幅突增限频：默认 15min 1 次；早盘前半小时高频实时窗口内不限频
            # （2026-08-24 用户「直接实时接口·高频脉冲」→ 2026-08-25 用户「9点半到10点的高频窗口取消60秒限频」：
            #   窗口内去掉 60s 节流——接口有突增就推，防刷屏交给 short_spirit 当天 code 去重兜底）
            # ⛔ 异动群高频（2026-09-03）：HIGH_FREQ_INTRADAY 时全时段不限频
            _surge_gap = 0.0 if (HIGH_FREQ_INTRADAY or in_early_realtime_window(now)) else 900.0
            ok_time = (last is None) or ((now - datetime.fromisoformat(last)).total_seconds() > _surge_gap)
            if d >= 5 and ok_time and code not in zt_codes:
                new_surge.append({"l": l, "delta": d})
                surge_codes[code] = now.isoformat()

    # 4) 板块级异动：TOP5 涨停 >= SECTOR_ZT_MIN 且今日尚未推送过 → 记一次（去重防刷屏）    #    ⛔ 异动群高频（2026-09-03）：首次达标必报 + 涨停再上台阶(≥已推峰值+2)再报（变化驱动）；
    #    False=每天每板块仅首次。
    #    2026-09-07 优化（开关见头部常量区）：
    #      C ENABLE_SECTOR_FAMILY  家族成员合并为家族对象检测（大农业7+养猪2=泛农业9）
    #      B ENABLE_GRADUAL_BOOST  渐进加强：zc>已推峰值 且 距上次渐进报≥8min → 慢涨也报
    #      每次命中打 _sector_stage 标（first/peak/gradual），供独立板块卡渲染区分
    #    ⛔⛔ 时效门（2026-09-07 11:30 用户「推送必须当下·别推早上发生很久的事」修复）：
    #       live_signal 30-60s 轮询，推送必须代表「本轮刚发生的变化」。所有触发
    #       （first/peak/gradual）一律要求 本轮 zc > 上轮同口径 zc（涨停数真实上升）；
    #       存量高度（早达到、本轮无增量）即使当天没报过/开关晚开也**不补报**。
    #       实证事故：10:46 开家族开关，泛农业 09:25 就 9 只 → 判 first 补报旧闻被用户抓包。
    SECTOR_ZT_MIN = 3
    _prev_top5_map: dict[str, int] = {}
    for _ps in (prev.get("top5") or []):
        _pn = str(_ps.get("name") or "")
        if _pn:
            _prev_top5_map[_pn] = int(_ps.get("zt_count") or 0)

    def _prev_zc(s0: dict) -> int:
        """上轮同口径 zt：家族对象=成员 prev 合计；单板块=同名板块 prev。"""
        if s0.get("_family"):
            return sum(_prev_top5_map.get(str(m.get("name") or ""), 0)
                       for m in (s0.get("family_members") or []))
        return _prev_top5_map.get(str(s0.get("name") or ""), 0)

    sector_pushed = set(sig.get("sector_pushed", []))
    sector_peak = sig.get("sector_zt_peak") or {}   # 板块→已推时涨停峰值
    _src_top5 = latest.get("top5") or []
    _iter_sectors = list(_src_top5)
    if ENABLE_SECTOR_FAMILY and family_of_top5 is not None:
        try:
            _fam_aggs = family_of_top5(_src_top5)
            if _fam_aggs:
                _covered_fam: set[str] = set()
                for _fam, _agg in _fam_aggs.items():
                    _iter_sectors.append(_agg)
                    for _m in (_agg.get("family_members") or []):
                        _covered_fam.add(str(_m.get("name") or ""))
                # 家族成员被家族对象覆盖，不再单板块重复检测（防大农业报完养猪再报）
                _iter_sectors = [s0 for s0 in _iter_sectors
                                 if str(s0.get("name") or "") not in _covered_fam]
        except Exception as _fam_err2:
            print(f"[live_signal] 家族聚合计算失败，回退单板块: {_fam_err2}")
    _gradual_ts = sig.get("gradual_boost_ts") or {}   # B：板块→最近一次渐进报 iso
    _boost_last_ts = sig.get("boost_last_ts") or {}    # 2026-09-15 降噪：板块→最近一次任意加强卡 iso
    for s in _iter_sectors:
        nm = s.get("name")
        if not nm:
            continue
        zc = s.get("zt_count", 0) or 0
        pzc = _prev_zc(s)
        # ⛔ 时效门在下方 _decide_sector_stage 内统一收口：zc<=pzc（无本轮增量）一律不推
        # 家族基线：成员已报过则继承其峰值（防「成员早报、家族又补报一次」旧闻）
        _fam_base = 0
        if s.get("_family"):
            for _m in (s.get("family_members") or []):
                _mb = sector_peak.get(str(_m.get("name") or "")) or 0
                if _mb > _fam_base:
                    _fam_base = _mb
            if _fam_base:
                sector_peak[nm] = max(sector_peak.get(nm) or 0, _fam_base)
        # 家族成员已覆盖板块强势 → 家族名吸收去重但不补报 first（存量旧闻不推）
        if s.get("_family") and nm not in sector_pushed and _fam_base:
            sector_pushed.add(nm)
        _first = (nm not in sector_pushed) and not (s.get("_family") and _fam_base)
        _boosted = HIGH_FREQ_INTRADAY and not _first and (zc >= (sector_peak.get(nm) or 0) + 2)
        # B：渐进加强（每次+1爬升被峰值+2 漏掉的场景；限 8min 一报防刷屏）
        _gradual = False
        if ENABLE_GRADUAL_BOOST and not _first and not _boosted and zc > (sector_peak.get(nm) or 0):
            _lastg = _gradual_ts.get(nm)
            try:
                _okg = (not _lastg) or (
                    (now - datetime.fromisoformat(_lastg)).total_seconds() >= GRADUAL_BOOST_MIN_GAP_S)
            except Exception:
                _okg = True
            if _okg:
                _gradual = True
                _gradual_ts[nm] = now.isoformat()
        # ⛔ 时效门统一收口（_decide_sector_stage 纯函数）：无本轮增量一律不推
        _stage = _decide_sector_stage(
            zc, pzc,
            first=_first, peak=int(sector_peak.get(nm) or 0),
            high_freq=HIGH_FREQ_INTRADAY, gradual_ok=_gradual)
        # 2026-09-15 降噪：非首次加强（峰值跃迁/渐进）受板块最小间隔约束，防每轮+1/峰值跳变刷屏；
        # 首次板块强势（最有价值）不受限，即时成卡。满足间隔才落 boost_last_ts。
        if _stage:
            if not _first:
                _lb = _boost_last_ts.get(nm)
                if _lb and (now - datetime.fromisoformat(_lb)).total_seconds() < BOOST_MIN_GAP_S:
                    _stage = None
                else:
                    _boost_last_ts[nm] = now.isoformat()
            else:
                _boost_last_ts[nm] = now.isoformat()
        if _stage:
            s2 = dict(s)
            s2["_sector_stage"] = _stage
            new_sector.append(s2)
            sector_pushed.add(nm)
            sector_peak[nm] = max(zc, sector_peak.get(nm) or 0)
    sig["sector_pushed"] = list(sector_pushed)
    sig["sector_zt_peak"] = sector_peak
    if _gradual_ts:
        sig["gradual_boost_ts"] = _gradual_ts
    if _boost_last_ts:
        sig["boost_last_ts"] = _boost_last_ts

    # ── ⛔ 板块过热预警（2026-09-08 用户「发酵到高位别追高」·独立出口）──
    # 只对「今天已报过的强势板块」（in sector_pushed）判：涨停数进入已推峰值 80%
    # 高位区且本轮不再创新高 = 情绪最热/易站岗时点。与 peak 卡互斥（peak 要求
    # zc>=peak+2 创新高；过热要求 zc<=peak 滞涨），当天每板块 1 次（sig.climax_warned）。
    if ENABLE_CLIMAX_WARN:
        _warned = set(sig.get("climax_warned") or [])
        # 本轮刚推过「强势/加强」的板块跳过过热（09-08 用户「重复」：首报即过热=语义矛盾
        # 「刚启动」又「到顶」；过热只留给非首报轮的高位滞涨，两卡天然错开不齐发）
        _fresh_boost = {str(x.get("name") or "") for x in new_sector}
        _climax_sectors = []
        for _cs in _iter_sectors:
            _cn = str(_cs.get("name") or "")
            _cz = int(_cs.get("zt_count") or 0)
            _pk = int(sector_peak.get(_cn) or 0)
            if not _cn or _cn in _warned or _cn not in sector_pushed or _cn in _fresh_boost:
                continue
            if _climax_hit(_cz, _pk):
                _climax_sectors.append(_cs)
                _warned.add(_cn)
        if _climax_sectors:
            _broken_cnt = int((latest.get("broken") or {}).get("count") or 0)
            _cc = build_climax_card(_climax_sectors[:3], hhmm, _broken_cnt)
            if dry:
                print("[live_signal][dry-run] ⛔板块过热预警卡：")
                print(json.dumps(_cc, ensure_ascii=False, indent=2))
            else:
                try:
                    _cf_c = Path(tempfile.gettempdir()) / "limao_sector_climax_card.json"
                    _cf_c.write_text(json.dumps(_cc, ensure_ascii=False), encoding="utf-8")
                    _rc_c = os.system(f'{PY} "{HERE / "short_spirit.py"}" --send "{_cf_c}"')
                    # 推送成功后记当天去重（sig_path 按日期轮换，次日自动重置）；
                    # dry-run 不写记录（防 dry 污染把当天真推送吞掉）
                    sig["climax_warned"] = list(_warned)
                    save_sig(sig)
                    print(f"[live_signal] ⛔板块过热预警推送{'成功' if _rc_c == 0 else '失败'} "
                          f"n={len(_climax_sectors)} peak={[str(x.get('name')) for x in _climax_sectors]}")
                except Exception as _e_c:
                    print(f"[live_signal] ⛔板块过热预警 ERR:{_e_c}")

    # 4.2) 板块首见时间戳（2026-08-26 用户「今日题材轮动顺序不对」根治）：
    # 每轮扫 top5 时，给「当日首次进入 top5 的板块」记 first_seen 时间（HH:MM:SS）。
    # 修复 theme_sequence 缺陷：早盘高频窗口(09:25-09:55)跳过窗口聚合 → 板块级事件
    # 不进 buffer → 轮动序列缺板块/顺序失真。first_seen 由 live_signal 每轮维护，
    # 不受窗口聚合影响；看板 render_rotation_tab 读它做真实时序排序。
    # 注：本字段为纯增量记录（只写 sig.first_seen_<date>），不影响任何推送逻辑。
    fs_key = "first_seen_" + now.strftime("%Y%m%d")
    fs_map = sig.get(fs_key) or {}
    for s in (latest.get("top5") or []):
        nm = s.get("name")
        if nm and nm not in fs_map:
            fs_map[nm] = now.strftime("%H:%M:%S")
    sig[fs_key] = fs_map

    # 4.5) 板块趋势序列记录（2026-08-17 用户「等级可迁移·看板块往上还是往下」）：
    # 每轮把 top5 板块 zt_count 追加进 sig.sector_zt_series（保留最近 10 轮），
    # 供苗头卡/S级卡做趋势方向判定（rising→预判升级 / falling→预判降级）。
    # ⚠️ 必须在苗头检测之前（苗头/S级卡都要用本轮趋势判定）。
    sig.setdefault("sector_zt_series", {})
    for s in (latest.get("top5") or []):
        nm = s.get("name")
        if not nm:
            continue
        seq = sig["sector_zt_series"].setdefault(nm, [])
        seq.append(int(s.get("zt_count") or 0))
        if len(seq) > 10:
            del seq[: len(seq) - 10]

    # 4.6) 盘中前瞻共振（2026-08-17 用户「不能等半个板块涨停才提示·买个得儿」）：
    # 对比 prev.json 上轮 top5，识别「S级题材苗头」——板块刚启动（涨停数上升 + 容量中军 +
    # 封板节奏 + 大盘共振）就提示，不等过半涨停（滞后确认由 4) 的 new_sector 兜底）。
    # 苗头卡独立推送（每天每板块限1次，防刷屏）；成熟确认（resonance_score>=4）走 S 级卡。
    prev_top5 = {s.get("name"): s for s in (prev.get("top5") or []) if s.get("name")}
    _idx_now = latest.get("index") or []
    sig.setdefault("pre_resonance_pushed", [])
    pre_resonance_pushed = set(sig.get("pre_resonance_pushed", []))
    pre_res_map = sig.get("pre_resonance_score_map") or {}   # 板块→已推前瞻分（高频变化驱动）
    pre_resonance = []
    for s in (latest.get("top5") or []):
        nm = s.get("name")
        if not nm:
            continue
        # 高频放开：前瞻分升级(≥+1)再报；False=每天每板块 1 次
        if nm in pre_resonance_pushed and not HIGH_FREQ_INTRADAY:
            continue
        # 已被 S 级确认的板块不再发苗头
        if nm in sector_pushed:
            continue
        psc, phits = _resonance_signal(s, prev_top5.get(nm), _idx_now)
        # 中军拉升独立触发（2026-08-17 用户「中军可能拉到5%就要提示」）：
        # 容量中军 cap≥80 拉到 +5%~9.9% 且板块前排封板 ≥2 → 即使前瞻分不足也提示（不等共振）
        jc, jc_desc = _juggernaut_charge(s)
        if (psc >= 4 or jc) and (
                nm not in pre_resonance_pushed
                or (HIGH_FREQ_INTRADAY and psc >= (pre_res_map.get(nm) or 0) + 1)):
            s2 = dict(s)
            s2["pre_resonance_score"] = psc
            s2["pre_resonance_hits"] = phits
            if jc and psc < 4:
                s2["pre_resonance_score"] = 4  # 中军拉升=强启动确认，按苗头处理
                # 2026-08-28 修复：phits 里已含 jc_desc 时不再重复追加（实证 14:27 三段重复）
                s2["pre_resonance_hits"] = phits + ([jc_desc] if jc_desc not in phits else [])
                s2["juggernaut_charge"] = True
            # 板块核心票定位（2026-08-17 用户「对比成交量/换手定位活跃度·辨识度越高越核心」）
            core = _locate_core_stock(s)
            if core:
                s2["core_stock"] = core
            # 等级迁移预测（pre_s 苗头 → 看板块趋势预判升级/降级）
            zt_series = (sig.get("sector_zt_series") or {}).get(nm) or []
            s2["trend_dir"] = _sector_trend_dir(zt_series)
            s2["level_shift"] = _predict_level_shift("pre_s", s2["trend_dir"])
            # 2026-08-28 修复：中军拉升=启动确认，覆盖 flat 趋势误给的「横盘待变」自相矛盾
            # （实证 14:27：一字板启动确认 + 「维持苗头观望（横盘待变）」同卡并存）
            if s2.get("juggernaut_charge") and "观望" in s2["level_shift"]:
                s2["level_shift"] = "📈 启动确认（容量中军封板·扩散待续）"
            pre_resonance.append(s2)
            pre_resonance_pushed.add(nm)
            pre_res_map[nm] = max(psc, pre_res_map.get(nm) or 0)
    sig["pre_resonance_pushed"] = list(pre_resonance_pushed)
    sig["pre_resonance_score_map"] = pre_res_map

    # 5) 回流前兆监测：re-entry 候选板块盘中翻入资金净流入榜 → 弹🔔
    #    ⛔ 异动群高频（2026-09-03）：翻出后再翻入（冷却 30min）可再弹=变化驱动；
    #    False=每天每板块 1 次。
    _reflow_pushed_raw = sig.get("reflow_pushed", {})
    if isinstance(_reflow_pushed_raw, dict):
        reflow_pushed = dict(_reflow_pushed_raw)
    else:  # 旧 list 数据迁移为 dict
        reflow_pushed = {n: "" for n in _reflow_pushed_raw}
    # ⛔ 变化驱动签名（2026-09-15）：记录上次推送时的「命中板块 + 跟踪池」指纹，
    #    冷却到期但指纹未变 → 不重推（无变化不重复推=幂等底线）。
    _reflow_sig_raw = sig.get("reflow_sig_map", {})
    reflow_sig_map = dict(_reflow_sig_raw) if isinstance(_reflow_sig_raw, dict) else {}
    # 阶段图只记录已告警对象：资金试探→题材确认可立即升级；转弱仅报一次，防高频轮询刷屏。
    _reflow_stage_raw = sig.get("reflow_stage_map", {})
    reflow_stage_map = dict(_reflow_stage_raw) if isinstance(_reflow_stage_raw, dict) else {}
    _reflow_cd = 1800 if HIGH_FREQ_INTRADAY else 10**12
    reflow_cands = []
    tr = None
    live_inflow: set = set()
    _st_phase = _st_pool = _st_cool = _st_nodup = _st_flow = 0   # P2-10 漏斗计数（初始化在此，防 try 早期异常未定义）
    _st_probe = _st_confirm = _st_failed = 0
    # 🆕 回流确认监测（2026-09-30）：独立冷却 map，与回流前兆互不干扰
    reflow_confirm_cands = []
    _reflow_confirm_pushed_raw = sig.get("reflow_confirm_pushed", {})
    reflow_confirm_pushed = (dict(_reflow_confirm_pushed_raw)
                             if isinstance(_reflow_confirm_pushed_raw, dict) else {})
    _reflow_confirm_sig_raw = sig.get("reflow_confirm_sig_map", {})
    reflow_confirm_sig_map = (dict(_reflow_confirm_sig_raw)
                              if isinstance(_reflow_confirm_sig_raw, dict) else {})
    _st_confirm_skip = _st_confirm_cool = 0
    try:
        tr = load(CACHE / "theme_rotation.json")
        inflow_names = {x.get("name") for x in (tr.get("market_context") or {}).get("inflow_top", [])}
        # 盘中实时净流入榜（latest.json.sector_risk.inflow_top），优先；无则用盘前快照
        live_inflow = {(x.get("name")) for x in
                       ((latest.get("sector_risk") or {}).get("inflow_top") or [])}
        if not live_inflow:
            live_inflow = inflow_names
        # P2-10（2026-09-11 根因修复）：原判定只认 phase=="re-entry-candidate"，
        # 而引擎候选多为 分歧/cooling/watch（冷却期沉淀）→ 判定永不命中=长期零触发。
        # 回流前兆语义 = 冷却/分歧中的题材当日翻回资金净流入榜（boom 已爆发/dead 死透排除）。
        _reflow_phases = REFLOW_PHASES if REFLOW_PHASE_WIDEN else {"re-entry-candidate"}
        # P2-10 漏斗计数：逐层过滤原因可追溯（phase 不匹配 / 资金榜未命中 / 冷却中）
        for c in (tr.get("candidates") or []):
            if c.get("phase") not in _reflow_phases:
                _st_phase += 1
                continue
            nm = c.get("theme", "")
            if not nm:
                continue
            # ⛔ 主判据（2026-09-15 用户拍板·口径切换）：引擎 flow_trend 必须为「流入上升中」。
            #    原判定唯一条件是下方实时榜命中 → 实时榜的泛行业名（电子/PCB）常驻
            #    → 名称类匹配脆弱、易造"永久假命中"。现以引擎 5 日资金趋势为主判
            #    （见 `_reflow_flow_ok`）；实时榜降级为**盘中佐证**（不否决·见下方）。
            if not _reflow_flow_ok(c):
                _st_flow += 1
                # 已发过试探/确认、但资金趋势转弱：输出一次失效状态；未曾提示的候选不制造失败卡。
                if reflow_stage_map.get(nm) in {"资金试探", "题材确认"}:
                    failed = dict(c)
                    failed["_reflow_stage"] = "回流失效"
                    failed["_reflow_fail_reason"] = _reflow_fail_reason(c)
                    reflow_cands.append(failed)
                    reflow_stage_map[nm] = "回流失效"
                    _st_failed += 1
                continue
            # 实时榜命中 = **盘中佐证**（2026-09-15 降级·不再是否决门）：
            #   主判据已由引擎 flow_trend 承担；实测该榜为东财泛行业名（PCB/半导体/电子…），
            #   与概念题材名（MLCC/电子布/CPO概念）命名体系不同 → 若用作硬门会使本卡近乎恒不触发。
            #   故仅记录：① 卡面标注"盘中已入榜" ② 并入去重指纹（新翻入榜=指纹变化=可重推）。
            _hits = sorted(n for n in live_inflow if _reflow_name_hit(nm, n))
            if not _hits:
                _st_pool += 1          # 无盘中佐证计数（保留可观测性·不阻断）
            c["_reflow_live_hits"] = _hits
            price = _reflow_price_evidence(nm, latest.get("top5") or [])
            stage = "题材确认" if price.get("confirmed") else "资金试探"
            c["_reflow_stage"] = stage
            c["_reflow_price"] = price
            _sig_now = stage + "#" + "|".join(_hits) + "#" + "|".join(
                sorted(str(s.get("code") or "") for s in (c.get("watch_pool") or [])[:4]))
            _last = reflow_pushed.get(nm, "")
            # 阶段升级（试探→确认）是新的盘面事实，不能被 30 分钟冷却延迟；
            # 旧状态文件没有 stage 时只补状态、不补发历史卡，避免部署后集中重推。
            _previous_stage = reflow_stage_map.get(nm)
            _stage_changed = bool(_previous_stage) and _previous_stage != stage
            _ok = not _last or _stage_changed
            if _last:
                try:
                    _cool_ready = (datetime.now() - datetime.fromisoformat(_last)).total_seconds() > _reflow_cd
                except Exception:
                    _cool_ready = False
                # ⛔ 变化驱动（2026-09-15 修）：冷却到期只代表"允许再推"，还须**指纹变化**
                #    （命中的资金榜板块 或 跟踪池成员变了）才算真变化。
                #    反例：09-15「电子布」因短名误命中而持续"在榜"，指纹恒同，
                #    冷却每到期就重弹一张 → 全天 6 次刷屏。
                if not _stage_changed and (_cool_ready and reflow_sig_map.get(nm, "") != _sig_now):
                    _ok = True
                elif not _stage_changed:
                    _ok = False
                    _st_nodup += 1
            if _ok:
                reflow_cands.append(c)
                reflow_pushed[nm] = datetime.now().isoformat()
                reflow_sig_map[nm] = _sig_now
                reflow_stage_map[nm] = stage
                if stage == "题材确认":
                    _st_confirm += 1
                else:
                    _st_probe += 1
            else:
                _st_cool += 1
                reflow_stage_map[nm] = stage

        # ── 🆕 回流确认监测（2026-09-30）：识别「冷却后重新爆发」的题材（is_reentry）。
        #    与回流前兆（冷却中预判）互补：前兆抓"冷却中预判回流"，本段抓"今天已爆发的回流"
        #    ——正是 09-30 医药这类系统原盲区。限频防刷屏：独立冷却 map + 内容指纹。
        #    ⛔ 必须在 for 循环【之外】只调一次：本函数会改写 reflow_confirm_pushed 冷却时间戳，
        #       若放在循环体内每轮对全部候选重跑，首轮标记后次轮即被冷却/去重过滤 → 最终恒为空
        #       （实测回流确认卡永不触发）。移到循环外一次性判定，才是正确语义。
        reflow_confirm_cands, _st_confirm_skip, _st_confirm_cool = _detect_reflow_confirm(
            tr.get("candidates"), reflow_confirm_pushed, reflow_confirm_sig_map,
            datetime.now(), _reflow_cd)
    except Exception as e:
        print(f"[live_signal] reflow detect ERR:{e}")
    # ⛔ 2026-09-14 修复（刷屏级·与上方 1713 同源故障的第二处）：原下一行
    #    `sig["reflow_pushed"] = list(reflow_pushed)` 把刚写入冷却时间戳的 dict
    #    立即覆盖成「只有名字的 list」→ 下轮 load 走 list 迁移分支得到 {名称: ""}
    #    → `_ok = not _last` 恒为 True → **30min 冷却常数被彻底绕过**，候选只要还在
    #    资金净流入榜就 **每轮(30s)重推一张回流前兆卡**（实测 10:24:09 / 10:25:04 重复）。
    #    本块 09-11 上线后被 1713 的崩溃屏蔽（崩在推送出口之前）故未显形，今日修 1713
    #    后才暴露。现只保留带时间戳的 dict；load 侧 list 迁移分支保留（兼容历史文件）。
    sig["reflow_pushed"] = reflow_pushed
    sig["reflow_sig_map"] = reflow_sig_map   # ⛔ 必须与 reflow_pushed 同写入，否则指纹丢失=去重失效
    sig["reflow_stage_map"] = reflow_stage_map
    # 🆕 回流确认冷却状态写回（2026-09-30）：与回流前兆独立，避免互相污染去重
    sig["reflow_confirm_pushed"] = reflow_confirm_pushed
    sig["reflow_confirm_sig_map"] = reflow_confirm_sig_map
    # P2-10 审计（2026-09-11）：回流前兆判定留痕——每日首次记录**漏斗分解**
    # （phase 不匹配 / 资金榜未命中 / 冷却中 / 最终命中），零触发时可直接定位卡在哪一层。
    # ⛔ dry-run 不写（防污染真实诊断）。
    # ⛔ 2026-09-14 修复（生产故障·本块 09-11 上线即崩）：此处原引用 `today`，但 main() 内
    #    `today` 直到下方（核按钮段）才赋值 → Python 视其为 main 局部变量 → 每次运行
    #    UnboundLocalError，整条盘中异动事件链（核按钮/退潮/回流/板块加强/N字/苗头/S级）
    #    自 09-14 首个交易日全停（signal.log 80 次崩溃·fetch.log 79 次 rc=1）。
    #    改用独立名 `_reflow_today`，与下方 `today` 解耦，杜绝再被后置赋值遮蔽。
    _reflow_today = now.strftime("%Y-%m-%d")
    if not dry and sig.get("reflow_eval_date") != _reflow_today:
        try:
            from common.audit_log import audit
            audit("reflow_eval", phase_widen=REFLOW_PHASE_WIDEN,
                  flow_allow=sorted(REFLOW_FLOW_TREND_ALLOW),
                  cands_total=len((tr or {}).get("candidates") or []),
                  phases=sorted({str(x.get("phase")) for x in ((tr or {}).get("candidates") or [])}),
                  skip_phase=_st_phase, skip_flow=_st_flow, skip_pool=_st_pool,
                  skip_cool=_st_cool,
                  skip_nodup=_st_nodup, hits=len(reflow_cands), inflow_pool=len(live_inflow or set()),
                  probe=_st_probe, confirmed=_st_confirm, failed=_st_failed)
            sig["reflow_eval_date"] = _reflow_today
            save_sig(sig)
        except Exception:
            pass

    # ── 🆕 题材首板·冷启动种子（2026-09-01 首版上线/跨日重启首轮）──
    # 必须在"无信号早退"判断之前：first_map 为空时用当前已涨停票的板块做种子，
    # 只对"之后新封板的板块"推首板——防把当天第 2 只涨停误报"首板"。
    # ⛔ dry-run 不写（防污染）；种子板块本身不推卡（仅去重）。
    # 2026-09-01 14:32 用户取消首板卡 → 冷启动种子随 ENABLE_FIRST_ZT_CARD 停用。
    if ENABLE_FIRST_ZT_CARD and not (sig.get("first_zt_map")) and not dry:
        _seed_map = {}
        for _l in _pool:
            _pct0 = float(_l.get("pct") or 0)
            _z200 = bool(_l.get("is_20cm"))
            _zt0 = (_pct0 >= (ZT_JUDGE_20CM if _z200 else ZT_JUDGE_MAIN)) or (
                parse_boards(_l.get("boards")) > 0 and _l.get("buyable") is False)
            if _zt0:
                _s0 = str(_l.get("sector") or "").strip()
                if _s0:
                    _seed_map.setdefault(_s0, {"seed": True, "time": hhmm})
        if _seed_map:
            sig["first_zt_map"] = _seed_map
            save_sig(sig)
            print(f"[live_signal] 🆕首板冷启动种子 {len(_seed_map)} 板块（当日已涨停，防误报）")

    # 核按钮预警（亏钱效应显形）：feedback_matrix nuke>=3。
    # ⛔ 2026-09-04 修正（用户「怎么一直在提示」）：昨日高频放开把 nuke 卡改「名单变化即推」，
    #   但核按钮名单盘中每轮抖动（-5% 边缘票随时进出）→ 实测 20 分钟推 11 张同款温和档=刷屏。
    #   防守卡不应高频重复。现规则=每日每档 1 次 + 恶化升级再推：
    #   档位 grade = breakdown(高位破位杀跌·break_count>=3) / mild(连板分歧回踩)；
    #   同档当日只推 1 次（名单微抖不刷屏），mild→breakdown 恶化升级当日可再推 1 次。
    fb = load(CACHE / "feedback_matrix.json")
    today = now.strftime("%Y-%m-%d")
    _nuke_grade = "breakdown" if (fb.get("break_count") or 0) >= 3 else "mild"
    _grade_seen = sig.get("nuke_warn_grades") or {}
    _nuke_new = (_grade_seen.get(_nuke_grade) != today)
    if fb.get("nuke_count", 0) >= 3 and _nuke_new:
        nuke_card = build_nuke_card(fb, hhmm)
        _grade_seen = dict(_grade_seen)
        _grade_seen[_nuke_grade] = today
        sig["nuke_warn_grades"] = _grade_seen
        if dry:
            print("[live_signal][dry-run] 核按钮预警卡：")
            print(json.dumps(nuke_card, ensure_ascii=False, indent=2))
        else:
            cf2 = Path(tempfile.gettempdir()) / "limao_nuke_card.json"
            cf2.write_text(json.dumps(nuke_card, ensure_ascii=False), encoding="utf-8")
            rc2 = os.system(f'{PY} "{HERE / "short_spirit.py"}" --send "{cf2}"')
            print(f"[live_signal] 核按钮预警推送{'成功' if rc2 == 0 else '失败'} nuke={fb.get('nuke_count')}")

    # ── P1-5 退潮/冰点聚合预警卡（2026-09-11 落地）──
    # 触发：情绪分 ≤ EBB_SENTIMENT_MAX ∧ 恶化维度 ≥ EBB_MIN_DIMS ∧ 当日未推（每天 1 次防刷屏）。
    # 未达条件时每天写一次 ebb_eval 审计（P2-10 同款·供事后排查"为何没推"）。
    if ENABLE_EBB_ALERT:
        _score = fb.get("sentiment_score")
        _dims = _ebb_dims(fb, latest) if _score is not None else []
        _ebb_seen = sig.get("ebb_alert_date")
        if (_score is not None and _score <= EBB_SENTIMENT_MAX
                and len(_dims) >= EBB_MIN_DIMS and _ebb_seen != today):
            _ebb_card = build_ebb_card(fb, latest, hhmm, _dims)
            if dry:
                print("[live_signal][dry-run] 退潮/冰点预警卡：")
                print(json.dumps(_ebb_card, ensure_ascii=False, indent=2))
            else:
                sig["ebb_alert_date"] = today
                save_sig(sig)
                cf_e = Path(tempfile.gettempdir()) / "limao_ebb_card.json"
                cf_e.write_text(json.dumps(_ebb_card, ensure_ascii=False), encoding="utf-8")
                rc_e = os.system(f'{PY} "{HERE / "short_spirit.py"}" --send "{cf_e}"')
                print(f"[live_signal] 退潮/冰点预警推送{'成功' if rc_e == 0 else '失败'} "
                      f"score={_score} dims={len(_dims)}")
                try:  # P2-10 审计：触发留痕（延迟导入，缺失不影响推送）
                    from common.audit_log import audit
                    audit("ebb_push", score=_score, dims=_dims, ok=(rc_e == 0))
                except Exception:
                    pass
        elif _score is not None and _ebb_seen != today and sig.get("ebb_eval_date") != today:
            # 当天首次评估未达推送条件 → 记一次诊断（不刷屏·每天 1 条）
            try:
                from common.audit_log import audit
                audit("ebb_eval", score=_score, dims=_dims,
                      reason=("情绪分未到冰点区" if _score > EBB_SENTIMENT_MAX
                              else f"恶化维度不足({len(_dims)}<{EBB_MIN_DIMS})"))
                sig["ebb_eval_date"] = today
                save_sig(sig)
            except Exception:
                pass

    # ── P2-17 L2 机构票实时拉升事件层（2026-09-11 落地）──
    # 早盘窗口内：机构早盘点名池（70+ 只）批量查实时涨幅（em_flow.fetch_quotes·一次请求+
    # 60s 缓存）→ 拉升未封板（≥INST_MIN_PCT 且 < 涨停线）即推；双闸门=每票每日1次 + 当日总量。
    if ENABLE_INSTITUTION_L2 and in_early_realtime_window():
        _inst_p = CACHE / f"institution_pool_{today}.json"
        if _inst_p.exists():
            try:
                _inst = load(_inst_p) or {}
                _items = {str(x.get("code") or ""): x for x in (_inst.get("items") or [])}
                _ip = sig.get("institution_pushed") or {}
                _used = sum(1 for v in _ip.values() if v == today)
                _quota = max(0, INST_MAX_DAILY - _used)
                if _items and _quota > 0:
                    from common.em_flow import fetch_quotes   # 复用 P2-18 数据层（延迟导入）
                    _q = fetch_quotes(list(_items.keys()))
                    _hits = []
                    for _c, _x in _items.items():
                        if _ip.get(_c) == today or not _q.get(_c):
                            continue
                        _p = float((_q.get(_c) or {}).get("pct") or 0)
                        _lim = ZT_JUDGE_20CM if str(_c).startswith(("300", "301")) else ZT_JUDGE_MAIN
                        if INST_MIN_PCT <= _p < _lim:        # 拉升中·未封板
                            _hits.append((_c, (_q.get(_c) or {}).get("name") or _x.get("name") or _c,
                                          _p, str(_x.get("theme") or "")))
                    _hits.sort(key=lambda t: -t[2])
                    _hits = _hits[:_quota]
                    if _hits:
                        _ic = build_institution_card(_hits, hhmm)
                        if dry:
                            print("[live_signal][dry-run] 机构票拉升卡：")
                            print(json.dumps(_ic, ensure_ascii=False, indent=2))
                        else:
                            for _c, _n, _p, _th in _hits:
                                _ip[_c] = today
                            sig["institution_pushed"] = _ip
                            save_sig(sig)
                            _cf = Path(tempfile.gettempdir()) / "limao_inst_card.json"
                            _cf.write_text(json.dumps(_ic, ensure_ascii=False), encoding="utf-8")
                            _rc = os.system(f'{PY} "{HERE / "short_spirit.py"}" --send "{_cf}"')
                            print(f"[live_signal] 机构票拉升推送{'成功' if _rc == 0 else '失败'} "
                                  f"n={len(_hits)} 剩余额度={_quota - len(_hits)}")
            except Exception as _e:
                print(f"[live_signal] institution L2 ERR:{_e}")

    # 资金场内切换检测（2026-08-26 用户实盘教学·盘中分钟级）：旧龙开板出货+新方向涨停潮。
    # 独立即时推（同组合当天1次防刷屏），失败不影响主流程。
    # ⛔ dry 模式用副本检测，禁止污染真实 sig（core_high/fund_switch_pushed 只随真实推送落盘）
    _sig_probe = None
    if dry:
        _sig_probe = json.loads(json.dumps(sig))
        _track_core_high(_sig_probe, prev)
        try:
            sw = detect_fund_switch(latest, prev, _sig_probe, hhmm)
            if sw:
                sw_card = build_fund_switch_card(sw, hhmm)
                print("[live_signal][dry-run] 资金切换卡：")
                print(json.dumps(sw_card, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"[live_signal] fund switch detect ERR:{e}")
    else:
        _track_core_high(sig, prev)
        try:
            sw = detect_fund_switch(latest, prev, sig, hhmm)
            if sw:
                sw_card = build_fund_switch_card(sw, hhmm)
                cf_s = Path(tempfile.gettempdir()) / "limao_fund_switch_card.json"
                cf_s.write_text(json.dumps(sw_card, ensure_ascii=False), encoding="utf-8")
                rc_s = os.system(f'{PY} "{HERE / "short_spirit.py"}" --send "{cf_s}"')
                print(f"[live_signal] 资金切换推送{'成功' if rc_s == 0 else '失败'} "
                      f"{sw['old']}→{sw['new']}")
        except Exception as e:
            print(f"[live_signal] fund switch push ERR:{e}")

    # 常规信号入窗口缓冲（档位A·2026-08-15）：聚合推送，不再逐轮各推一张；
    # 紧急信号（核按钮/回流前兆）仍即时推，不进缓冲；--push-now 可退回逐轮即时推
    # 分级降噪（2026-08-17 用户「暴拉/涨停决定题材方向爆发」·盘中快照优化二迭代）：
    #   实时模式（2026-08-18 用户「脉冲卡改为实时」）：S/A 级全部即时推（去重/限频压刷屏），
    #   不再区分窗口缓冲；分级注释仅保留历史语义参考。

    if not (new_nf or new_zt or new_surge or new_strong or new_sector or new_charge):
        # 无常规信号但有回流前兆，仍可独立推送（每天每板块已限频1次）
        if reflow_cands:
            reflow_card = build_reflow_card(reflow_cands, hhmm)
            if dry:
                print("[live_signal][dry-run] 回流前兆卡：")
                print(json.dumps(reflow_card, ensure_ascii=False, indent=2))
            else:
                cf_r = Path(tempfile.gettempdir()) / "limao_reflow_card.json"
                cf_r.write_text(json.dumps(reflow_card, ensure_ascii=False), encoding="utf-8")
                rc_r = os.system(f'{PY} "{HERE / "short_spirit.py"}" --send "{cf_r}"')
                print(f"[live_signal] 回流前兆推送{'成功' if rc_r == 0 else '失败'} n={len(reflow_cands)}")
        # 🆕 回流确认卡（2026-09-30）：独立即时推，与回流前兆互不干扰
        if reflow_confirm_cands:
            cf_c = build_reflow_confirm_card(reflow_confirm_cands, hhmm)
            if dry:
                print("[live_signal][dry-run] 回流确认卡：")
                print(json.dumps(cf_c, ensure_ascii=False, indent=2))
            else:
                cf_rc = Path(tempfile.gettempdir()) / "limao_reflow_confirm_card.json"
                cf_rc.write_text(json.dumps(cf_c, ensure_ascii=False), encoding="utf-8")
                rc_rc = os.system(f'{PY} "{HERE / "short_spirit.py"}" --send "{cf_rc}"')
                print(f"[live_signal] 回流确认推送{'成功' if rc_rc == 0 else '失败'} n={len(reflow_confirm_cands)}")
        else:
            # 冷却到期且 pending 有积压 → 补推合并卡（2026-08-18：避免行情平静期信号滞留）
            _pend = load_pending()
            _el = 1e9
            if _pend.get("last_push"):
                try:
                    _el = (datetime.now() - datetime.fromisoformat(_pend["last_push"])).total_seconds()
                except Exception:
                    _el = 1e9
            if pending_count(_pend) > 0 and spirit_push_allowed(sig) and not dry:
                # 盘中异动信号卡 → 短线精灵引擎（自带无信号跳过/当天code去重/幂等）
                # ⛔ 2026-09-15 降噪：间隔闸门（spirit_push_allowed）约束，避免无新信号时也每轮补推刷屏。
                _ss_force = " --force" if HIGH_FREQ_INTRADAY else ""
                rc = os.system(f'{PY} "{HERE / "short_spirit.py"}" --push{_ss_force}')
                print(f"[live_signal] 盘中异动信号→短线精灵触发 {'成功' if rc == 0 else '失败'} n={pending_count(_pend)}")
                if rc == 0:
                    sig["spirit_last_push"] = datetime.now().isoformat()
                _pend = {"last_push": datetime.now().isoformat(), "nf": [], "zt": [], "surge": [],
                         "strong": [], "sector": [], "charge": []}
            save_pending(_pend)
            print("[live_signal] 本轮无新信号，跳过")
        save_sig(sig)
        return

    # 去重记录照旧（防下一轮重复检测）
    sig["zt_codes"] = list(zt_codes)
    sig["surge_codes"] = surge_codes
    sig["strong_codes"] = list(strong_codes)
    sig["charge_codes"] = list(charge_codes)
    save_sig(sig)

    # ── 🆕 题材首板即时卡（2026-09-01 用户「首板即提示·不等3只确认爆发」）──
    # 板块当日第1只涨停 → 即时推（sig.first_zt_map 当天去重·每天每板块1次防刷屏）。
    # 直接消灭"首板→题材爆发确认"的 30min 滞后：首板时刻即爆发节点第一时间。
    # 冷启动种子见上方（无信号早退前执行）；⛔ dry-run 只打印不写 sig.first_zt_map。
    # ⛔ 2026-09-01 14:32 用户「首板太多了·一个板块推一次·取消」→ 开关停用，恢复置 True。
    first_map = sig.get("first_zt_map") or {}
    new_first = []
    for _l in new_zt:
        _sec = str(_l.get("sector") or "").strip()
        if not _sec or _sec in first_map:
            continue
        new_first.append(_l)
    if ENABLE_FIRST_ZT_CARD and new_first:
        if dry:
            for _l in new_first:
                _sec = str(_l.get("sector") or "").strip()
                print(f"[live_signal][dry-run] 🆕题材首板：{_sec} {_l.get('name')}({_l.get('code')}) "
                      f"+{_l.get('pct')}% | {_sector_ctx(_sec)}")
        else:
            for _l in new_first:
                _sec = str(_l.get("sector") or "").strip()
                if _sec in first_map:
                    continue
                first_map[_sec] = {"name": _l.get("name"), "code": _l.get("code"),
                                   "pct": _l.get("pct"), "time": hhmm,
                                   "is_20cm": bool(_l.get("is_20cm")),
                                   "boards": _l.get("boards") or ""}
                sig["first_zt_map"] = first_map
                save_sig(sig)
                try:
                    _fzc = build_first_zt_card(first_map[_sec], _sec, hhmm)
                    cf_f = Path(tempfile.gettempdir()) / "limao_first_zt_card.json"
                    cf_f.write_text(json.dumps(_fzc, ensure_ascii=False), encoding="utf-8")
                    rc_f = os.system(f'{PY} "{HERE / "short_spirit.py"}" --send "{cf_f}"')
                    print(f"[live_signal] 🆕题材首板推送{'成功' if rc_f == 0 else '失败'} "
                          f"{_sec} {first_map[_sec]['name']}({first_map[_sec]['code']}) "
                          f"+{first_map[_sec]['pct']}%")
                except Exception as _e:
                    print(f"[live_signal] 🆕题材首板推卡 ERR:{_e}")

    # ── 🔁 N字二波启动即时卡（2026-09-01 用户「N字形态重点关注·楚天龙/青山纸业/康盛」）──
    # 个股第一波（近N_SHAPE_HIST_DAYS交易日）**≥N_SHAPE_MIN_BOARDS连板（最少二连·用户09-01定）** → 断板回调
    # （间隔≥N_SHAPE_MIN_GAP）→ 今日再涨停 = N字二波启动 → 即时推。
    # ⛔ 用户 2026-09-01 定版：1 板脉冲（如金健米业 1板→断→1板）不算 N 字；先 ≥3 后降为最少二连
    #   （3连门槛会错过第一波仅2连板就启动的票，如万向德农首板+次日连板即构成板块强度验证）。
    # 去重：sig.n_shape_map {code: hhmm} 每天每票1次；⛔ dry-run 只打印不写 sig。
    _hist, _trade_days = _load_zt_history()
    _today_s = now.strftime("%Y-%m-%d")
    _n_shape = []
    for _l in new_zt:
        _c = str(_l.get("code") or "").strip()
        if not _c:
            continue
        _h = _hist.get(_c)
        _gap = _n_shape_gap(_c, _hist, _trade_days, _today_s)
        # 第一波连板高度 ≥3（用户 N 字定义）且断板回调后今日再涨停
        if (_gap is not None and _gap >= N_SHAPE_MIN_GAP
                and (_h or {}).get("max_boards", 0) >= N_SHAPE_MIN_BOARDS):
            _n_shape.append((_l, _h))
    if _n_shape:
        # 板块上下文：今日同板块新涨停数 + 板块高标（新龙反推旧龙语境）
        _sec_zt: dict = {}
        for _l in new_zt:
            _s = str(_l.get("sector") or "").strip()
            if _s:
                _sec_zt.setdefault(_s, []).append(_l)
        n_map = sig.get("n_shape_map") or {}
        for _l, _h in _n_shape:
            _c = str(_l.get("code") or "")
            if not dry and _c in n_map:
                continue
            _sec = str(_l.get("sector") or (_h or {}).get("last_sector") or "")
            _ztn = len(_sec_zt.get(_sec, []))
            _top, _topb = "", 0
            for _s2 in _sec_zt.get(_sec, []):
                _b2 = parse_boards(_s2.get("boards"))
                if _b2 > _topb:
                    _topb, _top = _b2, f"{_s2.get('name')}{_b2}板"
            for _ld in (latest.get("leaders") or []):
                if str(_ld.get("sector") or "") == _sec:
                    _b3 = parse_boards(_ld.get("boards"))
                    if _b3 > _topb:
                        _topb, _top = _b3, f"{_ld.get('name')}{_b3}板"
            _info = {"zt_today": _ztn, "top": (f"·高标{_top}" if _top else "")}
            if dry:
                _ch = _first_wave_chain(_h or {}, _trade_days)
                print(f"[live_signal][dry-run] 🔁N字二波：{_l.get('name')}({_c}) +{_l.get('pct')}% "
                      f"| {_sec} | 首波 {_mmdd(_ch.get('start'))}首板→{_mmdd(_ch.get('end'))} "
                      f"{_ch.get('boards')}板·断板回调{_ch.get('gap')}天"
                      f" | 板块今日涨停{_ztn}只{_info['top']}")
            else:
                n_map[_c] = hhmm
                sig["n_shape_map"] = n_map
                save_sig(sig)
                try:
                    _nc = build_n_shape_card(_l, _hist, _info, hhmm, _trade_days)
                    cf_n = Path(tempfile.gettempdir()) / "limao_n_shape_card.json"
                    cf_n.write_text(json.dumps(_nc, ensure_ascii=False), encoding="utf-8")
                    rc_n = os.system(f'{PY} "{HERE / "short_spirit.py"}" --send "{cf_n}"')
                    print(f"[live_signal] 🔁N字二波推送{'成功' if rc_n == 0 else '失败'} "
                          f"{_l.get('name')}({_c}) +{_l.get('pct')}%")
                except Exception as _e:
                    print(f"[live_signal] 🔁N字二波推卡 ERR:{_e}")

    # 回流前兆卡：独立即时推（低频，每天每板块1次，保留）
    if reflow_cands:
        reflow_card = build_reflow_card(reflow_cands, hhmm)
        if dry:
            print("[live_signal][dry-run] 回流前兆卡：")
            print(json.dumps(reflow_card, ensure_ascii=False, indent=2))
        else:
            cf_r = Path(tempfile.gettempdir()) / "limao_reflow_card.json"
            cf_r.write_text(json.dumps(reflow_card, ensure_ascii=False), encoding="utf-8")
            rc_r = os.system(f'{PY} "{HERE / "short_spirit.py"}" --send "{cf_r}"')
            print(f"[live_signal] 回流前兆推送{'成功' if rc_r == 0 else '失败'} n={len(reflow_cands)}")
    # 🆕 回流确认卡（2026-09-30）：独立即时推，与回流前兆互不干扰
    if reflow_confirm_cands:
        cf_c = build_reflow_confirm_card(reflow_confirm_cands, hhmm)
        if dry:
            print("[live_signal][dry-run] 回流确认卡：")
            print(json.dumps(cf_c, ensure_ascii=False, indent=2))
        else:
            cf_rc = Path(tempfile.gettempdir()) / "limao_reflow_confirm_card.json"
            cf_rc.write_text(json.dumps(cf_c, ensure_ascii=False), encoding="utf-8")
            rc_rc = os.system(f'{PY} "{HERE / "short_spirit.py"}" --send "{cf_rc}"')
            print(f"[live_signal] 回流确认推送{'成功' if rc_rc == 0 else '失败'} n={len(reflow_confirm_cands)}")

    # 📊 板块加强独立卡（2026-09-07 优化A/B/C·总开关默认关）：
    # HIGH_FREQ 高频模式下 A 级板块信号只触发 short_spirit 个股卡 → 板块级"强势/加强"
    # 无独立出口（实证 09-07：sector_pushed/sector_zt_peak 有记录但用户看不到板块卡）。
    # 本出口把 new_sector（首次/峰值跃迁/渐进）独立成卡直推异动群（仿核按钮/N字 --send）。
    # 放置于 S级/A级 分级之前：紧急变化驱动卡（板块创新高）优先于个股流水线。
    if ENABLE_SECTOR_BOOST_CARD and new_sector:
        _bc = build_sector_boost_card(new_sector[:5], hhmm, latest)
        if dry:
            print("[live_signal][dry-run] 📊板块加强卡：")
            print(json.dumps(_bc, ensure_ascii=False, indent=2))
        else:
            try:
                _cf_b = Path(tempfile.gettempdir()) / "limao_sector_boost_card.json"
                _cf_b.write_text(json.dumps(_bc, ensure_ascii=False), encoding="utf-8")
                _rc_b = os.system(f'{PY} "{HERE / "short_spirit.py"}" --send "{_cf_b}"')
                print(f"[live_signal] 📊板块加强卡推送{'成功' if _rc_b == 0 else '失败'} "
                      f"n={len(new_sector)} stage={[s.get('_sector_stage') for s in new_sector]}")
            except Exception as _e_b:
                print(f"[live_signal] 📊板块加强卡 ERR:{_e_b}")

    # 分级：S级（暴拉/涨停·题材方向级）即时推；A级（冲板/强势/快讯/板块）入窗口缓冲
    s_zt = []
    s_surge = []
    a_events = []
    for l in new_zt[:8]:
        s_zt.append(l)
    for it in new_surge[:8]:
        s_surge.append(it)
    for l in new_charge[:8]:
        a_events.append({"type": "charge", "code": l.get("code"), "name": l.get("name"),
                         "sector": l.get("sector", ""), "pct": l.get("pct")})
    for n in new_nf[:4]:
        a_events.append({"type": "nf", "title": n.get("title", "")})
    for l in new_strong[:8]:
        a_events.append({"type": "strong", "code": l.get("code"), "name": l.get("name"),
                         "sector": l.get("sector", ""), "pct": l.get("pct"),
                         "is_20cm": bool(l.get("is_20cm"))})
    for s in new_sector[:6]:
        ld = s.get("leader") or {}
        a_events.append({"type": "sector", "name": s.get("name", ""),
                         "zt_count": s.get("zt_count"), "leader_name": ld.get("name", "")})

    if dry:
        print(f"[live_signal][dry-run] 实时推：S级 zt={len(s_zt)} surge={len(s_surge)} | "
              f"A级同轮即时推 n={len(a_events)}（冲板/强势/快讯/板块）")
        return

    # S级判定（2026-08-17 用户「S级题材=板块整体共振，非单股暴拉」·二迭代）：
    # 单股暴拉/涨停不再单独即时推——先与 top5 板块匹配，算板块共振评分：
    #   score >= 4（板块联动+容量中军+大盘共振+一字封死叠加等 ≥4 要素）→ S级题材爆发卡（即时推）
    #   score < 4 → 单股级/板块弱共振，降为 A 级入窗口聚合（不刷屏）
    # 大盘 index 用于共振判定（latest.json.index 已有真实 pct）
    _idx = latest.get("index") or []
    s_sector = []   # 共振确认的板块（S级题材爆发）
    s_zt_remain = list(s_zt)   # 未匹配到共振板块的涨停（降 A 级）
    s_surge_remain = list(s_surge)
    top5 = latest.get("top5") or []
    for sec in top5:
        nm = sec.get("name")
        if not nm:
            continue
        sec_codes = set()
        for f in (sec.get("followers") or []):
            c = str(f.get("code") or "")
            if c:
                sec_codes.add(c)
        lc = str((sec.get("leader") or {}).get("code") or "").replace(".SZ", "").replace(".SS", "")
        if lc:
            sec_codes.add(lc)
        # 板块内是否有本轮的涨停/暴拉
        in_sec_zt = [z for z in s_zt if str(z.get("code") or "").replace(".SZ", "").replace(".SS", "") in sec_codes]
        in_sec_surge = [it for it in s_surge if str(it["l"].get("code") or "").replace(".SZ", "").replace(".SS", "") in sec_codes]
        if not (in_sec_zt or in_sec_surge):
            continue
        # 共振评分
        sc, hits = _resonance_score(sec, _idx)
        sec_dup = dict(sec)
        sec_dup["resonance_score"] = sc
        sec_dup["resonance_hits"] = hits
        # 等级迁移预测（2026-08-17 用户「等级可迁移·看板块往上还是往下」）
        zt_series = (sig.get("sector_zt_series") or {}).get(nm) or []
        tdir = _sector_trend_dir(zt_series)
        # 板块资金（sector_risk inflow/outflow 匹配）用于横盘判别
        _flow = None
        for _f in ((latest.get("sector_risk") or {}).get("inflow_top") or []):
            if str(_f.get("name") or "") == nm:
                _flow = float(_f.get("flow_yi") or 0)
                break
        if _flow is None:
            for _f in ((latest.get("sector_risk") or {}).get("outflow_top") or []):
                if str(_f.get("name") or "") == nm:
                    _flow = -abs(float(_f.get("flow_yi") or 0))
                    break
        cur_level = "s" if sc >= 4 else "a"
        sec_dup["trend_dir"] = tdir
        sec_dup["level_shift"] = _predict_level_shift(cur_level, tdir, _flow)
        # 板块核心票定位（2026-08-17 用户「对比成交量/换手定位活跃度·辨识度越高越核心」）
        core = _locate_core_stock(sec)
        if core:
            sec_dup["core_stock"] = core
        if sc >= 4:
            s_sector.append(sec_dup)
            for z in in_sec_zt:
                if z in s_zt_remain:
                    s_zt_remain.remove(z)
            for it in in_sec_surge:
                if it in s_surge_remain:
                    s_surge_remain.remove(it)
        # 未达 S 级的板块内涨停/暴拉保留在 remain（降 A 级）

    if dry:
        print(f"[live_signal][dry-run] S级共振题材 n={len(s_sector)}（score≥4）| "
              f"苗头前瞻 n={len(pre_resonance)} | "
              f"未共振 zt={len(s_zt_remain)} surge={len(s_surge_remain)} | "
              f"A级同轮即时推 n={len(a_events)}")
        return

    # S级题材爆发 → 触发短线精灵（2026-08-18 用户「用这逻辑替换群里的盘中异动信号」）
    if s_sector:
        rc_s = os.system(f'{PY} "{HERE / "short_spirit.py"}" --push') if not dry else 0
        print(f"[live_signal] S级题材→短线精灵触发 {'成功' if rc_s == 0 else '失败'} n={len(s_sector)}")

    # 苗头卡（前瞻·2026-08-17 用户「不能等半个板块涨停才提示」）：板块刚启动（涨停数上升+
    # 容量中军+封板节奏+大盘共振 ≥4 要素）即提示，独立 purple 卡，每天每板块限1次
    if pre_resonance and not dry:
        lines = ["**🌱 S级题材苗头（板块刚启动·前瞻）**："]
        for s in pre_resonance[:5]:
            leader = s.get("leader") or {}
            zt = s.get("zt_count", 0)
            name = s.get("name", "")
            psc = s.get("pre_resonance_score")
            lname = leader.get("name", "")
            lpct = leader.get("pct", 0)
            lines.append(f"  <font color='purple'>▸ {name}｜{zt}只涨停（苗头{psc}/5）</font>")
            lines.append(f"　龙头: {lname} +{lpct}%")
            # 同板块票（2026-09-01 用户「命中的同板块多个票都要显示」）：
            # leader 已单独一行，此处列 followers（跟风/涨停票·前6），覆盖扩散后的板块全貌
            fws = s.get("followers") or []
            if fws:
                fw_s = "、".join(
                    f"{f.get('name')} +{float(f.get('pct') or 0):.2f}%" for f in fws[:6])
                lines.append(f"　📋 同板块: {fw_s}")
            hits = s.get("pre_resonance_hits") or []
            if hits:
                lines.append(f"　⚡苗头: {' + '.join(hits)}")
            # 中军拉升标记（2026-08-17 用户「中军可能拉到5%就要提示」）
            if s.get("juggernaut_charge"):
                lines.append(f"　⚡中军拉升: 容量中军已拉到+{lpct}%·前排封板·启动确认")
            # 板块核心票（活跃度定位·辨识度最高）
            core = s.get("core_stock")
            if core:
                amt = core.get("amount_yi")
                amt_txt = f"·成交{amt}亿" if amt else ""
                lines.append(
                    f"　🔑核心票: <font color='purple'>{core.get('name')}</font>"
                    f" +{core.get('pct')}% 换手{core.get('turn')}%{amt_txt}"
                    f"（{core.get('role')}·活跃度最高辨识度最高）"
                )
            ls = s.get("level_shift")
            if ls:
                lines.append(f"　{ls}")
        lines.append("")
        lines.append("<font color='purple'>板块刚启动·提前布局窗口（非确认信号，待共振成熟升级 S 级）</font>")
        pe_card = {"title": f"⚡短线精灵·板块苗头 {hhmm}", "color": "purple",
                   "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
                                {"tag": "note", "elements": [{"tag": "plain_text", "content": "🌱 前瞻提示（机器检测·非AI判断）"}]}]}
        cf_p = Path(tempfile.gettempdir()) / "limao_pre_resonance_card.json"
        cf_p.write_text(json.dumps(pe_card, ensure_ascii=False), encoding="utf-8")
        rc_p = os.system(f'{PY} "{HERE / "short_spirit.py"}" --send "{cf_p}"')
        print(f"[live_signal] S级苗头前瞻推送 {'成功' if rc_p == 0 else '失败'} n={len(pre_resonance)}")

    # ⛔ 2026-09-04 11:00 撤销：zt_flash 新涨停速报与 short_spirit 重复（实证 10:46 平潭发展
    # +9.99% 涨停 1 秒内被 zt_flash(10:46:01)+精灵(10:46:02) 双推——精灵数据源含「涨停信号」档，
    # 非仅拉升）。分工=涨停/拉升统一由 short_spirit 单通道（自带当天 code 去重）；HIGH_FREQ
    # 全天 A 级唤精灵 --force 已覆盖。合富中国 10:38 漏推=当时精灵通道断链窗口(09:55-10:37)所致，
    # 非池覆盖问题（精灵 10:46 已验证能推涨停票）。如需再保秒板兜底：精灵去重状态与 live_signal
    # 共享后再考虑开回（避免双通道重叠）。
    a_events.extend(
        {"type": "surge", "code": it["l"].get("code"), "name": it["l"].get("name"),
         "sector": it["l"].get("sector", ""), "pct": it["l"].get("pct"),
         "delta": round(it.get("delta", 0), 1)} for it in s_surge_remain)

    if pa is not None:
        # 早盘前半小时高频实时窗口（2026-08-24 用户「解除频率限制·直接实时接口·
        # 高频脉冲」→ 2026-08-25 用户「早盘半小时必须根据接口实时推·不需要限制」→
        # 同日「高频窗口取消60秒限频」）：
        # A级信号不再攒 5min 窗口、不再 45s 冷却、surge 不再 60s 限频——直接触发短线精灵
        # --force 即时推；短线精灵自带当天 code 去重（同票不重推）+ 待推池空自动跳过
        # （无新信号不推空卡），防刷屏仅靠这两层。
        # 2026-09-04 修复（用户「异动没数据推」·断链根因）：09:55 高频窗口外 A 级信号
        # 原走 pa.append_events 入窗口缓冲，但窗口聚合卡 close_window_step 已于 09-03 停用
        # （单通道直推）→ 缓冲无人消费 → 09:55 后普通个股异动（新涨停/冲板/强势）全丢。
        # 修复=HIGH_FREQ_INTRADAY 模式下全天 A 级直触发 short_spirit --force 即时推
        # （自带当天 code 去重 + 空池自动跳过 = 幂等，不刷屏）；False（主群限频）仍按窗口缓冲。
        if in_early_realtime_window(datetime.now()) or HIGH_FREQ_INTRADAY:
            if not dry:
                # ⛔ 2026-09-15 降噪：间隔闸门——早盘高频窗口/全天 HIGH_FREQ 原每轮(30s)即推，
                # 单日早盘炸 60+ 张。间隔未到则跳过本轮，待推池累积→下个合格轮次批量推（不丢信号）。
                if spirit_push_allowed(sig):
                    rc_e = os.system(f'{PY} "{HERE / "short_spirit.py"}" --push --force')
                    if rc_e == 0:
                        sig["spirit_last_push"] = datetime.now().isoformat()
                        save_sig(sig)
                    print(f"[live_signal] 早盘高频窗口→短线精灵即时推 {'成功' if rc_e == 0 else '失败'} "
                          f"A级信号 n={len(a_events)}")
                else:
                    print(f"[live_signal] 精灵间隔闸门（{spirit_gap_sec()}s 未到）·跳过本轮·待推池累积合并")
            else:
                print("[live_signal] 早盘高频窗口→短线精灵即时推（dry 跳过）")
        else:
            added = pa.append_events(a_events)
            print(f"[live_signal] A级信号入窗口缓冲 n={added}（窗口聚合卡由 pulse_fetcher "
                  f"close_window_step 每5min边界推送）")
    else:
        print("[live_signal] 窗口缓冲不可用，A级信号未写入（不影响S级实时推送）")


if __name__ == "__main__":
    main()
