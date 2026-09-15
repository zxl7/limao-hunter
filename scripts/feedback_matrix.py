#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""昨日涨停反馈矩阵 + 10分制情绪评分（短线情绪核心指标）。

读「昨日涨停快照」(cache/yesterday_zt.json，盘后复盘记录) + 当日盘面，
计算短线情绪最关键的一组「隔日反馈」指标：
  - 晋级率：昨日涨停今日仍涨停的比例（>60% 情绪好 / <40% 情绪差）
  - 连板晋级率：昨日>=2板今日仍涨停的比例
  - 昨涨停今均涨幅：>2% 强 / 0~2% 中 / <0% 弱
  - 核按钮：昨日涨停今日跌幅<-5% 的家数（>=3 严重警告）
  - 最高板趋势：昨日 vs 今日
  - 10分制情绪评分（含反馈矩阵因子）

由 pulse_fetcher.sh 每轮抓取后调用（gen_candidates 之后），写 cache/feedback_matrix.json，
供盘中猎手任务直接读取展示，AI 不再自己算。

用法:
  python3 feedback_matrix.py            # 计算并写文件
  python3 feedback_matrix.py --dry-run  # 只打印不写
"""
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 架构重构（2026-08-17 P0-3·复用优先）：网络层统一 common.http_client（含 Referer header），
# 时间统一 common.market_time，JSON 读写统一 common.json_io。
from common.http_client import http_get as _hc_get
from common.json_io import load_json, save_json
from common.market_time import now as _now, BJ_TZ

CACHE = ROOT / "cache"
SNAP = CACHE / "pulse_snapshots"

ULIST_URL = ("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids={secids}&fields=f2,f3,f12,f14")
ZT_POOL_URL = ("https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989"
               "&dpt=wz.ztzt&Pageindex=0&pagesize=2000&sort=fbt%3Aasc&date={date}")
QUOTE_TTL = 120  # 行情缓存秒数（守护每60s唤醒，避免刷爆接口）

# 晋级率动态阀门（2026-09-08 用户定）：晋级率"恶化线"从固定 <25 改为
# 前 PROMO_GATE_WINDOW 个交易日 promote_rate 去极值截尾均值（去最大最小·不含当日），
# 与市场自身近期中枢比较而非绝对 25（冰点期 25 恒绿警=噪声）。样本不足或开关关 → None
# （渲染端回退固定 25 线）。PROMO_GATE_DYNAMIC=False 一键回退旧行为（可逆开关）。
PROMO_GATE_DYNAMIC = True
PROMO_GATE_WINDOW = 10          # 截尾均值窗口（交易日）
PROMO_GATE_MIN_SAMPLES = 6      # 样本守卫：有效样本 <6 不产出（去两端后至少剩 4）

# 东财接口需要 Referer（统一 http_client 默认无此 header，这里封装带 Referer 的变体）
_EM_HEADERS = {"Referer": "https://quote.eastmoney.com/"}


def http_get(url, t=15):
    """东财数据 GET（带 Referer）。统一走 common.http_client，失败返回 None。"""
    return _hc_get(url, t=t, headers=_EM_HEADERS, retries=1)


def secid(code: str) -> str:
    """东财 secid：沪市(6/9/5开头)→1.，其余(深0/3、北8/4/920)→0."""
    return ("1." if code[:1] in ("6", "9", "5") else "0.") + code


def is_20cm(code: str) -> bool:
    return code.startswith(("300", "301", "688", "689", "8", "4", "920"))


def zt_thr(code: str) -> float:
    return 19.9 if is_20cm(code) else 9.9


def _tk_market(code: str) -> str:
    """腾讯市场前缀：沪 6/9→sh，深 0/3→sz，北 8/4/920→bj"""
    if code[0] in ("6", "9"):
        return "sh"
    if code.startswith(("8", "4", "920")):
        return "bj"
    return "sz"


def fetch_ma510(code: str):
    """腾讯日K（前复权）算 MA5/MA10（统一 common.tencent_kline，2026-08-17 架构重构消重复）。"""
    try:
        from common.tencent_kline import fetch_ma510 as _tk_ma510
        return _tk_ma510(code)
    except Exception as e:
        print(f"[feedback_matrix] MA510 拉取失败 {code}: {e}")
        return None


def is_trading_day(now: datetime) -> bool:
    """A股交易日判定：周一到周五（统一 common.market_time）。

    法定节假日（工作日休市）由下游「涨停池空 / 自交」双保险守卫兜底——
    节假日东财 getTopicZTPool?date=今日 返回上一交易日池 → 与昨池自交 → 触发自交守卫拦截。
    """
    return now.weekday() < 5


def fetch_today_zt(date: str) -> set:
    """今日真实涨停池代码集合 + 最高板"""
    try:
        raw = http_get(ZT_POOL_URL.format(date=date))
        pool = (raw.get("data") or {}).get("pool") or []
        codes = {str(it.get("c")) for it in pool if it.get("c")}
        max_b = max((int(it.get("lbc") or 0) for it in pool), default=0)
        return codes, max_b
    except Exception as e:
        print(f"[feedback_matrix] 今日涨停池拉取失败: {e}")
        return set(), 0


def fetch_quotes(codes):
    """批量拉昨日涨停股今日行情：返回 {code: {"name","pct","price"}}
    主源=东财 ulist；失败/空 → 降级腾讯 qt.gtimg.cn（2026-08-20 修复：东财限流致
    quote_fail_count=36 全空 avg_pct=None，溢价因子失分——双源互备）。"""
    out = {}
    codes = [c for c in codes if c]
    if not codes:
        return out
    for i in range(0, len(codes), 80):  # 分批（东财单次上限约100）
        batch = codes[i:i + 80]
        try:
            raw = http_get(ULIST_URL.format(secids=",".join(secid(c) for c in batch)))
            diff = (raw.get("data") or {}).get("diff") or []
            for it in diff:
                c = str(it.get("f12") or "")
                if c:
                    out[c] = {
                        "name": str(it.get("f14") or ""),
                        "pct": round(float(it.get("f3") or 0), 2),
                        "price": round(float(it.get("f2") or 0), 2),
                    }
        except Exception as e:
            print(f"[feedback_matrix] 东财行情批次{i}拉取失败: {e}，降级腾讯")
        # 降级：东财返回空/异常 → 腾讯 qt.gtimg.cn 单票补查（未覆盖部分）
        # ⚠️ 不走 http_client：它强制 utf-8 解码会毁掉 GBK 字节；这里用 urllib 原始字节按 GBK 解
        missing = [c for c in batch if c not in out]
        if missing:
            try:
                import urllib.request as _ur
                qs = "q=" + ",".join("sh" + c if c[:1] in ("6", "9", "5") else "sz" + c for c in missing)
                req = _ur.Request(f"https://qt.gtimg.cn/{qs}", headers={"Referer": "https://gu.qq.com/"})
                with _ur.urlopen(req, timeout=10) as _resp:
                    raw_bytes = _resp.read()
                raw_tx = raw_bytes.decode("gbk", errors="ignore")
                for line in raw_tx.split(";"):
                    if "~" not in line:
                        continue
                    p = line.split("~")
                    if len(p) < 35:
                        continue
                    code = p[2]
                    out[code] = {
                        "name": p[1],
                        "pct": round(float(p[32]), 2),   # 涨跌幅%
                        "price": round(float(p[3]), 2),  # 现价
                    }
            except Exception as e2:
                print(f"[feedback_matrix] 腾讯降级行情失败: {e2}")
    return out


def _trend_signal(prev_date: str = None):
    """晋级率/溢价 5 日趋势信号（2026-08-20 用户「晋级率越来越低、溢价越来越低、越不好做」）。
    读 cache/short_term/daily/emotion_*.json 历史，检测：
      - promote_downtrend: 近3个有效日 promote_rate 连续下滑（或 2 日下滑 + 当前<25%）
      - avg_pct_weak:     近3日 avg_pct 均值 < 1%（涨停溢价兑现乏力）
      - defense_level:    0 正常 / 1 谨慎（单指标走弱）/ 2 防守（双指标走弱或晋级率<15%）
    输出供卡片/门控引用（regime 判定仍以当日值为主，本信号只做叠加警示）。"""
    try:
        import glob
        daily_dir = Path(__file__).resolve().parent.parent / "cache" / "short_term" / "daily"
        files = sorted(glob.glob(str(daily_dir / "emotion_*.json")))
        rows = []
        for f in files[-14:]:  # 窗口拓宽：趋势取近4有效日，阀门 gate 取前10交易日（均从 rows 取）
            try:
                d = json.loads(Path(f).read_text(encoding="utf-8"))
                pr = d.get("promote_rate")
                ap = d.get("avg_pct")
                if pr is not None:
                    rows.append({"date": d.get("date") or Path(f).stem[-10:], "pr": pr, "ap": ap})
            except Exception:
                continue
        if not rows:
            return {"promote_downtrend": False, "avg_pct_weak": False, "defense_level": 0, "history": [],
                    "promotion_gate_10d": None}
        # 晋级率动态阀门（2026-09-08 用户定）：前10交易日 promote_rate 去极值截尾均值
        # （去最大/最小·不含当日=prev_date）。样本<MIN_SAMPLES 或开关关 → None（渲染端回退固定 25）。
        gate = None
        if PROMO_GATE_DYNAMIC:
            gw = [r["pr"] for r in rows if r.get("pr") is not None and r["date"] != (prev_date or "")]
            gw = gw[-PROMO_GATE_WINDOW:]
            if len(gw) >= PROMO_GATE_MIN_SAMPLES:
                gw_sorted = sorted(gw)
                core = gw_sorted[1:-1]
                gate = round(sum(core) / len(core), 1)
        # 晋级率趋势：取最近连续有效日
        prs = [r["pr"] for r in rows if r.get("pr") is not None][-4:]
        down = len(prs) >= 3 and all(prs[i] <= prs[i - 1] for i in range(1, len(prs)))
        down2 = len(prs) >= 2 and prs[-1] <= prs[-2] and prs[-1] < 25
        # 溢价走弱：近3日 avg_pct 均值（None 跳过）
        aps = [r["ap"] for r in rows if r.get("ap") is not None][-3:]
        ap_weak = len(aps) >= 2 and (sum(aps) / len(aps)) < 1.0
        cur_pr = prs[-1] if prs else None
        lvl = 0
        if (down or down2) and ap_weak:
            lvl = 2
        elif down or down2 or ap_weak or (cur_pr is not None and cur_pr < 15):
            lvl = 1
        return {"promote_downtrend": bool(down or down2), "avg_pct_weak": ap_weak,
                "defense_level": lvl, "history": rows[-5:], "promotion_gate_10d": gate}
    except Exception as e:
        print(f"[feedback_matrix] 趋势信号计算失败: {e}")
        return {"promote_downtrend": False, "avg_pct_weak": False, "defense_level": 0, "history": []}


def sentiment_score(promote, lb_promote, avg_pct, nuke, broken_rate, max_trend,
                    zt_expand, up_rate, idx_avg, break_count=0,
                    market_amount_yi=0.0, breadth_up=0, breadth_down=0,
                    regulatory_pressure=0.0):
    """10分制「行情强度」打分（2026-08-17 用户定·重构：好行情必须打得出高分）。

    设计原则：
    - 语义 = 赚钱效应/行情强度，不是"退潮温度计"——强普涨(涨停翻倍+广谱红盘+放量)必须 ≥9 分，
      冰点退潮才 1-3 分（曾踩坑：08-17 涨停63→110翻倍、三指数+1.4~3.1%、核按钮仅2，旧算法
      因 promote<25% 一票-2 只打 4~5 分，严重失真）。
    - 废除「晋级率<25% 一票扣2」：涨停家数爆发会稀释晋级率数值（63→110 首板激增），
      晋级率仅作接力质量参考，不再当退潮裁决。
    - 12 因子（2026-08-17 晚 P0-量能广度升级）：原 10 因子 + ⑪量能(放量/缩量) + ⑫全市场涨跌广度。
      量能/广度此前为"AI 定性引用"脱节点——用户「盘面是整体情绪场」审计判定，现量化进打分。

    参数：
    - promote 晋级率(0-1) / lb_promote 连板晋级(0-1) / avg_pct 昨涨停今均涨幅%
    - nuke 核按钮家数 / broken_rate 炸板率(0-1) / max_trend 最高板趋势(+1升/0平/-1降)
    - zt_expand 涨停家数环比(now/prev-1，如 0.75=涨停增75%)
    - up_rate 昨涨停今上涨占比% (0-100，赚钱广度代理)
    - idx_avg 三大指数平均涨幅%（上证/深证/创业板 pct 均值）
    - break_count 核按钮中真破位（跌破MA5+MA10）家数：仅 >=3 才算高位破位杀跌恶化
    - market_amount_yi 两市成交额（亿，latest.json market 字段）：≥2.5万亿放量 / ≤1.2万亿缩量
    - breadth_up/breadth_down 全市场涨跌家数（latest.json breadth 字段）：涨跌比 ≥3:1 普涨 / ≤1:2 普跌
    """
    s = 5.0
    # ① 涨停扩张（核心·行情爆发度）
    if zt_expand >= 0.60: s += 2.0      # 涨停近翻倍级爆发
    elif zt_expand >= 0.30: s += 1.5
    elif zt_expand >= 0.10: s += 1.0
    elif zt_expand >= 0: s += 0.5
    elif zt_expand <= -0.30: s -= 1.5
    elif zt_expand <= -0.10: s -= 0.5
    # ② 上涨广度（昨涨停今红盘占比·赚钱效应广度）
    if up_rate >= 70: s += 1.5
    elif up_rate >= 60: s += 1.0
    elif up_rate >= 50: s += 0.5
    elif up_rate < 35: s -= 1.0
    elif up_rate < 45: s -= 0.5
    # ③ 赚钱效应质量（昨涨停今均涨幅）
    if avg_pct > 3: s += 1.0
    elif avg_pct > 1: s += 0.5
    elif avg_pct > -1: s += 0
    elif avg_pct > -3: s -= 0.5
    else: s -= 1.0
    # ④ 晋级率（接力质量·基数膨胀稀释保护：不因涨停激增而误判退潮）
    if promote >= 0.50: s += 1.0
    elif promote >= 0.35: s += 0.5
    elif promote >= 0.25: s += 0
    elif promote < 0.15: s -= 1.0
    # ⑤ 连板晋级
    if lb_promote >= 0.50: s += 0.5
    elif lb_promote < 0.30: s -= 0.5
    # ⑥ 核按钮
    if nuke >= 5: s -= 2.0
    elif nuke >= 3: s -= 1.0
    elif nuke >= 1: s -= 0.5
    else: s += 0.5
    # ⑦ 真破位杀跌（核按钮里跌破 MA5+MA10 家数，>=3 才算高位杀跌恶化）
    if break_count >= 3: s -= 1.0
    elif break_count >= 1: s -= 0.5
    # ⑧ 炸板率
    if broken_rate > 0.40: s -= 1.0
    elif broken_rate > 0.25: s -= 0.5
    elif broken_rate < 0.15: s += 0.5
    # ⑨ 指数强度（三指数均涨幅）
    if idx_avg >= 2.5: s += 1.0
    elif idx_avg >= 1.5: s += 0.5
    elif idx_avg <= -2.5: s -= 1.0
    elif idx_avg <= -1.0: s -= 0.5
    # ⑩ 最高板趋势
    s += max_trend * 0.5
    # ⑪ 量能（P0·2026-08-17）：放量=承接确认（强加分），缩量=无承接（降分）
    # ⚠️ 修正系数设计：强普涨时原10因子已近满分，叠加分会被上限吞掉（实测放量/缩量
    # 都顶到10）→ 改为「先算原分→乘量能/广度修正系数→再封顶」，让量能与广度真正影响结果。
    s_orig = s
    mkt_mult = 1.0
    brd_mult = 1.0
    if market_amount_yi and market_amount_yi > 0:
        if market_amount_yi >= 25000: mkt_mult = 1.04    # ≥2.5万亿 显著放量
        elif market_amount_yi >= 20000: mkt_mult = 1.02
        elif market_amount_yi < 12000: mkt_mult = 0.92   # <1.2万亿 缩量
        elif market_amount_yi < 15000: mkt_mult = 0.96
    # ⑫ 全市场涨跌广度（P0·2026-08-17）：直接度量盘面扩散，与涨停数/情绪互相印证
    if breadth_up and breadth_down and (breadth_up + breadth_down) > 0:
        ratio = breadth_up / max(1, breadth_down)
        if ratio >= 3.0: brd_mult = 1.04      # 普涨（≥3:1）
        elif ratio >= 2.0: brd_mult = 1.02
        elif ratio <= 0.5: brd_mult = 0.88    # 普跌（≤1:2）
        elif ratio <= 0.8: brd_mult = 0.94
    s = s_orig * mkt_mult * brd_mult
    s = max(1, min(10, s))
    # ⑬ 监管打击强度（2026-08-19 用户定·P1-8 情绪拐点信号）：监管红线触发（10日≥100%/
    # 30日≥200% 的严重异常波动票被点名/停牌）→ 短线资金恐慌出逃 → 情绪压制。
    # ⚠️ 采用「clamp 后减法」（2026-08-19 定·区别于量能/广度的乘法）：乘法在 s_orig 极高时
    # 会被封顶吞掉（实测 12.5×0.90 仍>10 修正失效），减法在任何样本下必然生效。
    # penalty：pressure≥6 → -1.0；≥4 → -0.6；≥2 → -0.3；≥1 → -0.2（只扣不奖）。
    # 数据源 cache/regulatory_events.json 当日 pressure（regulatory_watch.py 沉淀）。
    if regulatory_pressure and regulatory_pressure > 0:
        if regulatory_pressure >= 6: s -= 1.0
        elif regulatory_pressure >= 4: s -= 0.6
        elif regulatory_pressure >= 2: s -= 0.3
        else: s -= 0.2
        s = max(1, s)
    # 四舍五入到 0.1 前先判断 label（避免 9.95→10 与 9.94→9.9 的边界抖动影响 label）
    if s >= 9: label = "亢奋"
    elif s >= 7: label = "偏强"
    elif s >= 4.5: label = "修复"
    elif s >= 2.5: label = "分歧"
    else: label = "退潮"
    return round(s, 1), label


def main():
    dry = "--dry-run" in sys.argv[1:]
    force = "--force" in sys.argv[1:]  # 2026-08-20 加：绕过120s缓存强制重算（修代码后立即生效）
    # 竞价守卫：9:26:00 前集合竞价未定价/未开盘，行情与涨停池接口返回昨收或昨日数据
    # → 会产出"晋级率100%"失真假象（2026-08-04 实测 08:57 产生），9:25 盘中猎手会误读
    # → 9:26 前整段跳过，并写 error 标记让 AI 任务按 shared_rules 跳过反馈块；9:26 起首轮=竞价/开盘高开率
    now = _now()
    hm = now.hour * 60 + now.minute
    if hm < 566:  # 00:00 - 09:25:59 全部跳过
        out = CACHE / "feedback_matrix.json"
        if not dry:
            save_json(out, {"generated_ts": now.strftime("%Y-%m-%d %H:%M:%S"),
                                       "error": "pre_auction",
                                       "msg": "9:26 竞价定价后首轮生效（当前时段不计算，防昨日数据假象）"})
        print("[feedback_matrix] ⏭ 9:26前跳过（防未开盘假象），已写 pre_auction 标记")
        return 0
    snap = load_json(CACHE / "yesterday_zt.json")
    latest = load_json(SNAP / "latest.json")

    empty = {"generated_ts": _now().strftime("%Y-%m-%d %H:%M:%S"),
             "error": "no_prev_snapshot", "msg": "昨日涨停快照缺失（盘后复盘未记录），反馈矩阵不可用"}
    if not snap.get("zt_list"):
        print("[feedback_matrix] ⚠️ yesterday_zt.json 缺失或为空，跳过")
        out = CACHE / "feedback_matrix.json"
        # ⛔ 2026-08-17 修复（当日踩坑2次）：已有有效缓存（无 error 且 promote_rate 存在）
        # 时**不得覆盖**——非交易时段手动跑/守护触发会清掉盘中真实数据（如 15:00 打分 v2 结果）。
        # 仅在无有效缓存时才写守卫标记。
        old = load_json(out)
        has_valid = (isinstance(old, dict) and not old.get("error")
                     and old.get("promote_rate") is not None)
        if has_valid:
            print(f"[feedback_matrix] ⏭ 快照缺失但已有有效缓存({old.get('generated_ts')}·情绪{old.get('sentiment_score')})，保留不覆盖")
            return 0
        if not dry:
            save_json(out, empty)
        return 0

    # 守卫0（2026-08-16 新增·修复周末假数据事故）：非交易日（周末/节假日）不计算隔日反馈。
    # 根因复盘：8/16(周日)13:23 落在 09:00-15:00 "交易时段"内被放行，东财涨停池接口
    # 对非交易日返回上一交易日(8/14)数据 → 与昨日快照自交 → 晋级率100%、情绪10分亢奋的假数据，
    # 污染 8/18(周一)盘前/盘中情绪判断。此前守卫只判「时段」与「快照日期==今天」，均未判"是否交易日"。
    # 保留上一交易日真实缓存（prev_date=最近交易日 且 无 error 且 promote_rate 存在）→ 不覆盖；
    # 否则写 non_trading_day error 标记，AI 任务按 shared_rules 跳过反馈块并诚实标注。
    out = CACHE / "feedback_matrix.json"
    if not is_trading_day(_now()):
        old = load_json(out)
        snap_date_norm0 = str(snap.get("date_raw") or snap.get("date", "")).replace("-", "").strip()
        old_prev_norm0 = str(old.get("prev_date", "")).replace("-", "")
        if (old_prev_norm0 and old_prev_norm0 != snap_date_norm0 and not old.get("error")
                and old.get("promote_rate") is not None):
            print(f"[feedback_matrix] ⏭ 非交易日(周末/节假日)，保留上一交易日缓存({old.get('prev_date')})")
            return 0
        err = {"generated_ts": _now().strftime("%Y-%m-%d %H:%M:%S"), "error": "non_trading_day",
               "msg": "非交易日(周末/节假日)，不计算隔日反馈，防止涨停池接口返回上一交易日数据自交假象"}
        if not dry:
            save_json(out, err)
        print("[feedback_matrix] ⏭ 非交易日，写 non_trading_day 标记（防自交100%假象）")
        return 0

    # 缓存短路：同日期快照且 <120s 前算过 → 静默跳过（守护每60s唤醒不重复刷行情）
    out = CACHE / "feedback_matrix.json"
    old = load_json(out)
    if not dry and not force and old.get("prev_date") == snap.get("date") and old.get("generated_ts"):
        try:
            old_ts = datetime.strptime(old["generated_ts"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJ_TZ)
            if (_now() - old_ts).total_seconds() < QUOTE_TTL:
                print(f"[feedback_matrix] ⏭ 缓存未过期(<{QUOTE_TTL}s)，跳过重算")
                return 0
        except ValueError:
            pass

    prev_list = snap["zt_list"]
    prev_codes = [x["code"] for x in prev_list]
    prev_map = {x["code"]: x for x in prev_list}
    today_str = _now().strftime("%Y%m%d")
    snap_date_norm = str(snap.get("date_raw") or snap.get("date", "")).replace("-", "").strip()
    # 守卫1：快照日期==今天（盘后复盘已把 yesterday_zt 更新为今日池）→ 隔日反馈尚未产生
    # （今日涨停要等明日才有隔日表现），禁止自交算 100% 假象。保留上一交易日缓存或标记 error。
    if snap_date_norm == today_str:
        old = load_json(out)
        old_prev = str(old.get("prev_date", "")).replace("-", "")
        if old_prev and old_prev != today_str and not old.get("error") and old.get("promote_rate") is not None:
            print(f"[feedback_matrix] ⏭ 快照({snap_date_norm})即今日，隔日反馈未产生，保留上一交易日缓存({old.get('prev_date')})")
            return 0
        err = {"generated_ts": _now().strftime("%Y-%m-%d %H:%M:%S"), "error": "snapshot_is_today",
               "msg": "昨涨停快照为今日，隔日反馈待明日产生，反馈矩阵暂不可用"}
        if not dry:
            save_json(out, err)
        print("[feedback_matrix] ⏭ 快照即今日，标记 error（避免自交100%假象）")
        return 0
    # 守卫2：非交易时段（盘前/盘后）涨停池接口不可靠（盘后仅返2只异常数据），保护缓存避免垃圾值
    mins = _now().hour * 60 + _now().minute
    if not (540 <= mins <= 900):
        old = load_json(out)
        if str(old.get("prev_date", "")).replace("-", "") == snap_date_norm and not old.get("error") and old.get("promote_rate") is not None:
            print(f"[feedback_matrix] ⏭ 非交易时段，保留缓存({old.get('prev_date')})")
            return 0
        err = {"generated_ts": _now().strftime("%Y-%m-%d %H:%M:%S"), "error": "non_trading_no_cache",
               "msg": "非交易时段涨停池接口不可靠，反馈矩阵暂用缓存/不可用"}
        if not dry:
            save_json(out, err)
        print("[feedback_matrix] ⏭ 非交易时段且无匹配缓存，标记 error")
        return 0
    # 隔日反馈核心：昨涨停(快照) ∩ 今涨停(今日实时)。必须用【今天日期】拉今日涨停池，
    # 绝不可用快照日期(date_raw)，否则会变成「昨池∩昨池」自交 → 晋级率恒为100%假象（2026-08-04 实测）
    today_codes, today_max_b = fetch_today_zt(today_str)
    # 守卫3（2026-08-16 新增·节假日兜底）：今日涨停池为空 或 与昨池完全重合 = 接口返回上一交易日数据
    # （法定节假日/接口异常特征）。此时禁止自交计算，保留上一交易日缓存或标记 error。
    prev_code_set = set(prev_codes)
    if not today_codes or today_codes == prev_code_set:
        old = load_json(out)
        old_prev_norm3 = str(old.get("prev_date", "")).replace("-", "")
        if old_prev_norm3 and not old.get("error") and old.get("promote_rate") is not None:
            print(f"[feedback_matrix] ⏭ 今日涨停池空/自交({len(today_codes)}只)，判非交易日或接口异常，保留缓存({old.get('prev_date')})")
            return 0
        err3 = {"generated_ts": _now().strftime("%Y-%m-%d %H:%M:%S"), "error": "zt_pool_unreliable",
                "msg": "今日涨停池为空或与昨池重合，判非交易日/接口异常，反馈矩阵不可用"}
        if not dry:
            save_json(out, err3)
        print("[feedback_matrix] ⏭ 今日涨停池空/自交，写 zt_pool_unreliable 标记")
        return 0
    quotes = fetch_quotes(prev_codes)
    quote_fail = len(prev_codes) - len(quotes)

    # 晋级：昨日涨停 ∩ 今日真实涨停池（独立于行情接口，最可靠）
    promote_codes = [c for c in prev_codes if c in today_codes]
    promote = len(promote_codes) / max(1, len(prev_codes))
    # 连板晋级：昨日>=2板 且 今日仍涨停
    lb_prev = [x["code"] for x in prev_list if x.get("boards", 0) >= 2]
    lb_promote = len([c for c in lb_prev if c in today_codes]) / max(1, len(lb_prev)) if lb_prev else 0.0
    # 昨涨停今均涨幅 / 核按钮 / 大面：只统计行情接口成功返回的（缺失不编造、不计入）
    pcts = [q["pct"] for q in quotes.values() if q.get("pct") is not None]
    avg_pct = round(sum(pcts) / len(pcts), 2) if pcts else None
    # 高开率（早盘首轮计算时=竞价/开盘表现，隔日反馈最早信号）：上涨占比 + 强势(>=5%)家数
    up_rate = round(sum(1 for q in quotes.values() if (q.get("pct") or 0) > 0) / max(1, len(quotes)) * 100, 1)
    strong_count = sum(1 for q in quotes.values() if (q.get("pct") or 0) >= 5)
    nukes = []
    for c, q in quotes.items():
        if q["pct"] < -5:
            b = prev_map.get(c, {}).get("boards", 0)
            nukes.append({"name": q["name"], "code": c, "pct": q["pct"], "price": q.get("price"),
                          "prev_boards": b, "tag": "昨连板" if b >= 2 else "昨涨停"})
    nukes.sort(key=lambda x: x["pct"])
    # 破位判定：对每只核按钮股拉腾讯 MA5/MA10，标 break_ma5/break_ma10
    # 规则（2026-08-07 用户标准）：仅「跌破 MA5+MA10」=真破位杀跌；
    #       仅破 MA5 或仍在 MA5 上方 = 连板分歧回踩（正常，可接受）
    for n in nukes:
        ma = fetch_ma510(n["code"])
        price = n.get("price")
        if ma and price is not None:
            ma5, ma10 = ma
            n["ma5"] = ma5
            n["ma10"] = ma10
            n["break_ma5"] = price < ma5
            n["break_ma10"] = price < ma10
            # 真破位 = 同时跌破 MA5 与 MA10（高位破位杀跌）
            n["breakdown"] = (price < ma5) and (price < ma10)
        else:
            n["break_ma5"] = None
            n["break_ma10"] = None
            n["breakdown"] = None
    break_count = sum(1 for n in nukes if n.get("breakdown") is True)
    nuke_count = len(nukes)
    big_loss = sum(1 for q in quotes.values() if -5 <= q["pct"] < -3)
    # 炸板率：最新盘面 broken/(broken+total_zt)
    total_zt = int(latest.get("total_zt") or 0)
    broken_count = int((latest.get("broken") or {}).get("count") or 0)
    broken_rate = broken_count / max(1, broken_count + total_zt)
    # 最高板趋势
    prev_max_b = int(snap.get("max_boards") or 0)
    max_trend = (1 if today_max_b > prev_max_b else -1 if today_max_b < prev_max_b else 0)
    # 涨停扩张（行情爆发度）：今日涨停 vs 昨日涨停 环比
    zt_expand = round(total_zt / max(1, len(prev_codes)) - 1, 2)
    # 指数强度：三大指数平均涨幅（latest.json.index 已有真实 pct，复用零新增抓取）
    idx_avg = 0.0
    try:
        idx_list = (latest.get("index") or [])
        pcts = [float(i.get("pct") or 0) for i in idx_list if isinstance(i, dict)]
        idx_avg = round(sum(pcts) / len(pcts), 2) if pcts else 0.0
    except Exception:
        idx_avg = 0.0
    # P0-量能广度（2026-08-17）：两市成交额 + 全市场涨跌家数（latest.json market/breadth，
    # intraday_pulse 已抓 push2 落盘）——量能/广度两因子数据底座，缺失时 0 不干扰打分。
    # 2026-08-17 晚口径修正：breadth = 沪深全市场A股（上证+深证相加·创业板含于深证不重复计，
    # 合计约5280家；旧实现仅上证≈2351只漏深证）；market = 沪深两市成交额合计（3.02万亿口径不变）
    market_amount_yi = 0.0
    breadth_up = breadth_down = 0
    try:
        market_amount_yi = float(latest.get("market") or 0.0) if latest.get("market") else 0.0
        _b = latest.get("breadth") or {}
        breadth_up = int(_b.get("up") or 0)
        breadth_down = int(_b.get("down") or 0)
    except Exception:
        pass
    # ⚠️ 量能/广度缺失告警（2026-08-25 自查发现·字段级完整性盲区）：latest.json market/breadth
    # 为空时情绪分「量能/广度修正系数」静默失效（mkt_mult/brd_mult 恒 1.0），体检只看文件
    # mtime 新鲜度不查字段 → 全绿但量能丢了一天（08-25 东财 push2 全天 73 次请求全失败实证）。
    # 缺失时：①feedback_matrix 落盘标记 market_missing=True ②notify_alert 推蓝卡告警（30min去重）。
    # 修复方向：intraday_pulse 已接腾讯 qt.gtimg.cn 量能兜底（fetch_market 双源·2026-08-25）。
    market_missing = market_amount_yi <= 0 or (breadth_up + breadth_down) <= 0
    if market_missing:
        try:
            _fb_missing = json.loads(
                (Path(__file__).resolve().parent.parent / "cache" / "feedback_matrix.json")
                .read_text(encoding="utf-8"))
            _prev_missing = bool(_fb_missing.get("market_missing"))
        except Exception:
            _prev_missing = False
        if not _prev_missing:  # 状态翻转才告警（防每轮刷屏；30min 去重由 notify_alert 内建）
            try:
                from logging_setup import notify_alert
                _what = "量能缺失" if market_amount_yi <= 0 else "广度缺失"
                notify_alert(
                    f"⚠️ 情绪分量能/广度数据缺失（{_what}）：latest.json market/breadth 为空，"
                    f"情绪分未含量能/广度修正（market_amount_yi={market_amount_yi}）。"
                    f"东财 push2 不可用时已切腾讯量能兜底，广度暂无替代源降级 1.0。详见 cache/logs/")
            except Exception:
                pass
    # 晋级股名单（连板晋级重点）
    promote_names = []
    for c in promote_codes:
        q = quotes.get(c)
        promote_names.append({"name": q["name"] if q else prev_map[c].get("name", ""),
                              "code": c, "prev_boards": prev_map[c].get("boards", 0),
                              "pct": q["pct"] if q else prev_map[c].get("pct", 0)})
    promote_names.sort(key=lambda x: (-x["prev_boards"], -x["pct"]))

    # 监管打击强度（2026-08-19·P1-8）：读 regulatory_watch 沉淀的当日压力分
    regulatory_pressure = 0.0
    try:
        _rp = json.loads((Path(__file__).resolve().parent.parent / "cache" / "regulatory_events.json")
                         .read_text(encoding="utf-8"))
        for _day in _rp.get("days", []):
            if _day.get("date") == _now().strftime("%Y-%m-%d"):
                regulatory_pressure = float(_day.get("pressure") or 0.0)
                break
    except Exception:
        regulatory_pressure = 0.0

    # 评分：行情全失败时 avg_pct 用 None → 该因子取0分
    score, label = sentiment_score(promote, lb_promote, avg_pct or 0.0, nuke_count,
                                    broken_rate, max_trend,
                                    zt_expand, up_rate, idx_avg, break_count=break_count,
                                    market_amount_yi=market_amount_yi,
                                    breadth_up=breadth_up, breadth_down=breadth_down,
                                    regulatory_pressure=regulatory_pressure)

    m = {
        "generated_ts": _now().strftime("%Y-%m-%d %H:%M:%S"),
        "date": _now().strftime("%Y-%m-%d"),   # 2026-08-19 补：date 缺失致 pulse_aggregator._money_effect 守卫拦截
        "prev_date": snap.get("date"),
        "prev_zt_count": len(prev_codes),
        "now_zt_count": total_zt,
        "promote_count": len(promote_codes),
        "promote_rate": round(promote * 100, 1),
        "lb_prev_count": len(lb_prev),
        "lb_promote_count": len([c for c in lb_prev if c in today_codes]),
        "lb_promote_rate": round(lb_promote * 100, 1),
        "avg_pct": avg_pct,
        "quote_fail_count": quote_fail,
        "up_rate": up_rate,            # 昨涨停今上涨占比（早盘首轮=高开率）
        "strong_count": strong_count,  # 昨涨停今涨幅>=5% 家数（早盘首轮=大幅高开数）
        "nuke_count": nuke_count,
        "break_count": break_count,   # 真破位（跌破MA5+MA10）家数
        "market_missing": market_missing,  # 2026-08-25：量能/广度缺失标记（字段级完整性·自查盲区修复）
        "nuke_list": nukes[:8],
        "big_loss_count": big_loss,
        "broken_rate": round(broken_rate * 100, 1),
        "prev_max_boards": prev_max_b,
        "now_max_boards": today_max_b,
        "max_trend": max_trend,
        "zt_expand": zt_expand,
        "idx_avg": idx_avg,
        "market_amount_yi": market_amount_yi,   # P0-量能广度：两市成交额(亿)
        "breadth": {"up": breadth_up, "down": breadth_down},  # 全市场涨跌家数
        "promote_list": promote_names[:8],
        "sentiment_score": score,
        "sentiment_label": label,
        "regulatory_pressure": regulatory_pressure,  # 监管打击强度 0-10（P1-8）
    }

    if dry:
        print(json.dumps(m, ensure_ascii=False, indent=2)[:2000])
        return 0
    # 趋势信号（2026-08-20 用户「晋级率越来越低、溢价越来越低、越不好做」）：
    # 读历史5日 emotion_*.json，检测 promote_rate 连续下滑 / 溢价走弱 → 防守警示字段
    m["trend"] = _trend_signal(prev_date=_now().strftime("%Y-%m-%d"))
    save_json(out, m)
    print(f"[feedback_matrix] ✅ 昨涨停{len(prev_codes)}只→今晋级{promote*100:.0f}% "
          f"均涨幅{avg_pct}% 核按钮{nuke_count} 情绪{score}/10({label}) → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
