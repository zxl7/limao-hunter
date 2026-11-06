#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读研究：逐票质量预期 E ×【板块 / 梯队 / 量能三环联动】× 当日收盘 复盘/回测。

v2（2026-11-05 用户「板块、梯队、量能都是环环相扣的」）：
  旧版只用一个单因子 `gap_vs_theme`；本版把竞价接力还原成**有先后的证据链**：

    情绪/环境（今天做不做，生产已有 market_open）
      → 板块（分母：同炒作题材今早整体开得怎样，个股开幅才有意义）
      → 梯队（顺序：同线更高板/同层的票今早有没有一起开，低板越位高开＝接盘）
      → 量能（证伪：排除一字次日巨量开板这类结构失真；⛔ R 高低不打分）
      → 个股预期（E / DEV：这只票自己该开多少）
      → 物理可买（竞价封死）

  ⛔ 只优化**研究判据**：不写生产文件、不改 pool_call / ai_caliber / 卡面 / 账本，
     不新增加减分、不新开链路。所有阈值是探索值，须逐日样本外验证才谈接线。

用法：
  python3 scripts/research_sector_gap_backtest.py --days 20 --end 2026-11-05
  python3 scripts/research_sector_gap_backtest.py --days 10 --end 2026-11-05 --out /tmp/x.json

事实源（全部 09:25 前可得，⛔ 无事后信息、⛔ 不抓行情）：
  T 日 09:25 竞价     wealth_code/cache/auction_quotes-<T>.json（全池同一时点）
  T-1 收盘涨停归档    cache/zt_history/zt_<T-1>.json（E / 板数 / 封板质量 / R 分母）
  T-1 盘后题材快照    cache/theme_snapshot/theme_<T-1>.json（选股宝 plates；缺则降级 sector）
  T 日收盘            cache/kline_qfq/<code>.json + cache/zt_history/zt_<T>.json

收益 = 竞价价 → 同日收盘的**系统决策评估代理**；
⛔ 非 A 股 T+1 可执行往返收益、非真实成交战绩、不含费用滑点。
⛔ 晋级率 ≠ 买入胜率 ≠ 可执行收益；只读，不写任何生产文件、不改冻结件/账本。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str((ROOT / 'wealth_code').resolve()))
sys.path.insert(0, str(ROOT.resolve()))
os.environ['AI_EXPECTED_GAP_MODE'] = 'relative'
from core import expected_gap as eg            # noqa: E402
from core import ai_caliber as ac              # noqa: E402
from common.trading_calendar import prev_a_share_trading_day  # noqa: E402

KL = ROOT / 'cache/kline_qfq'
_BARS_CACHE: dict[str, list] = {}

# ── 探索阈值（⛔ 研究用，非生产参数；改动须重跑前后分段）────────────────────
SECTOR_MIN_PEERS = 2        # 同线至少几家有今早报价
SECTOR_RED_MIN = 0.50       # 同线红开率下限
LADDER_OVERTAKE_MAX = 3.0   # 非线内最高板：自己超出同线上层竞价中位的上限（pct）
VOL_R_MIN, VOL_R_MAX = 1.0, 6.0   # 【absolute 模式】R（竞价额/昨全天额×100）合理承接区
VOL_R_EXTREME = 60.0        # R 超此值或昨一字 → 极端值，不裁决
VOL_R_PCTL = 0.75           # 【relative 模式】当日全池竞价 R 的分位上界
VOLUME_MODE = 'relative'    # relative（默认）/ absolute；--volume-mode 可切

# ⚠️ 20 日实测纠偏：plates 段高开>5% 的 R 中位 10.53%、P75 14.2%，
#    固定 6% 会把 3/4 的高开票一律判「爆量」→ 量能环退化成常数、失去区分度。
#    改用**当日横截面分位**（09:25 全池报价同时可得，⛔ 无未来函数）。
DEV_OVERHEAT = 6.0          # 开幅超出自身质量预期太多 → 不给「预期」背书


def load(p, default=None):
    f = Path(p)
    if not f.exists():
        return default
    try:
        return json.loads(f.read_text(encoding='utf-8'))
    except Exception:
        return default


def bars_of(code):
    if code not in _BARS_CACHE:
        _BARS_CACHE[code] = (load(KL / f'{code}.json', {}) or {}).get('bars') or []
    return _BARS_CACHE[code]


def med(xs):
    return round(st.median(xs), 2) if xs else None


def main_theme(code, yrow, mapping):
    """主题材单一源：选股宝 plates[0] > theme > 归档 sector（兜底并标来源）。"""
    m = mapping.get(code) or {}
    plates = [str(x).strip() for x in (m.get('plates') or []) if str(x).strip()]
    if plates:
        return plates[0], 'plates'
    t = str(m.get('theme') or '').strip()
    if t:
        return t, 'theme'
    return str((yrow or {}).get('sector') or '').strip(), 'sector'


def prev_bar_quality(bars, prev):
    """昨收一字板（严格日K 开=高=低=收）与昨量/前5日均量。缺 → None，不猜。"""
    idx = next((i for i, b in enumerate(bars) if b.get('date') == prev), None)
    if idx is None:
        return None, None
    b = bars[idx]
    o, h, l, c = (b.get(k) for k in ('open', 'high', 'low', 'close'))
    yizi = None
    if all(isinstance(x, (int, float)) for x in (o, h, l, c)):
        yizi = (abs(o - h) < 1e-6 and abs(l - h) < 1e-6 and abs(c - h) < 1e-6)
    hist = [x.get('vol') for x in bars[max(0, idx - 5):idx]
            if isinstance(x.get('vol'), (int, float)) and x['vol'] > 0]
    vr = None
    if len(hist) >= 5 and isinstance(b.get('vol'), (int, float)) and b['vol'] > 0:
        vr = round(b['vol'] / (sum(hist[-5:]) / 5), 2)
    return yizi, vr


def build(days):
    rows, meta = [], []
    for d in days:
        prev = prev_a_share_trading_day(d)
        q = load(ROOT / f'wealth_code/cache/auction_quotes-{d}.json', {}) or {}
        yz = load(ROOT / f'cache/zt_history/zt_{prev}.json', {}) or {}
        tz = load(ROOT / f'cache/zt_history/zt_{d}.json', {}) or {}
        th = load(ROOT / f'cache/theme_snapshot/theme_{prev}.json', {}) or {}
        z4 = {r['code']: r for r in (yz.get('zt_list') or [])}
        z5 = {r['code']: r for r in (tz.get('zt_list') or [])}
        mapping = th.get('mapping') or {}
        theme_of, src_of = {}, {}
        for c in q:
            theme_of[c], src_of[c] = main_theme(c, z4.get(c), mapping)
        # 昨日**完整涨停池**归线（⛔ 不是候选池，否则梯队/家数被选股结果高估）
        y_groups = defaultdict(list)
        for c, r in z4.items():
            t, _ = main_theme(c, r, mapping)
            if t and t not in ('其他', '未分类'):
                y_groups[t].append(c)
        gaps = {}
        for c, v in q.items():
            if v.get('prev_close') and v.get('auction_price'):
                gaps[c] = round((v['auction_price'] / v['prev_close'] - 1) * 100, 2)
        market_med = med(list(gaps.values()))
        # 当日全池竞价 R 横截面（⛔ 只用 09:25 同一时点已知量；一字次日等 extreme 不入参照）
        day_Rs = []
        for c, v in q.items():
            y = z4.get(c) or {}
            amt, pc = v.get('auction_amount_yi'), y.get('cje_yi')
            if not (isinstance(amt, (int, float)) and isinstance(pc, (int, float)) and pc > 0):
                continue
            rb = bars_of(c)
            yi, _ = prev_bar_quality(rb, prev)
            rr = amt / pc * 100
            if rr <= VOL_R_EXTREME and yi is not True:
                day_Rs.append(rr)
        day_Rs.sort()
        day_R_p75 = (day_Rs[int((len(day_Rs) - 1) * VOL_R_PCTL)]
                     if len(day_Rs) >= 4 else None)
        market_max_boards = max((int(r.get('boards') or 0) for r in z4.values()), default=0)
        today_groups = defaultdict(list)
        for c, g in gaps.items():
            t = theme_of.get(c)
            if t and t not in ('其他', '未分类'):
                today_groups[t].append(c)
        theme_max_boards, theme_board_set = {}, {}
        for t, codes in y_groups.items():
            bs = [int(z4[c].get('boards') or 0) for c in codes]
            theme_max_boards[t] = max(bs) if bs else 0
            theme_board_set[t] = sorted(set(bs))
        meta.append((d, len(q), len(z4), len(z5),
                     sum(1 for c in q if src_of.get(c) == 'plates'), market_med,
                     bool(th), market_max_boards))
        # 与生产模块同日起算：⛔ bars 显式裁到 T-1，防止影子链路吃 T 日未来数据
        try:
            from core import sector_gap_context as sgc
            prod = sgc.build_context(
                quotes=q, zt_rows=list(z4.values()), plates_map=mapping,
                bars_by_code={c: [b for b in bars_of(c)
                                 if str(b.get('date') or '') <= prev]
                              for c in q},
                prev_date=prev,
                expected_gaps={c: eg.expected_gap_pct(z4.get(c)) for c in q})
        except Exception as e:                       # 研究脚本不得因影子模块中断
            print(f'[WARN] 生产影子上下文 {d} 计算失败：{e}')
            prod = {}
        for c, v in q.items():
            if c not in gaps:
                continue
            gap = gaps[c]
            y = z4.get(c) or {}
            name = (z5.get(c) or y).get('name') or (mapping.get(c) or {}).get('name') or ''
            is20 = bool(y.get('is_20cm'))
            sealed = ac.unbuyable_gap(gap, is20)
            e = eg.expected_gap_pct(y)
            zone = ac.gap_zone(gap, None, expected_gap_pct=e, is_20cm=is20)
            dev = round(gap - e, 2) if e is not None else None
            boards = int(y.get('boards') or 0) or None
            yizi, vol_ma5 = prev_bar_quality(bars_of(c), prev)
            prev_cje = y.get('cje_yi')
            amt = v.get('auction_amount_yi')
            R = (round(amt / prev_cje * 100, 2)
                 if isinstance(amt, (int, float)) and isinstance(prev_cje, (int, float))
                 and prev_cje > 0 else None)
            r_extreme = (R is None or R > VOL_R_EXTREME or yizi is True)
            t = theme_of.get(c) or ''
            valid = bool(t) and t not in ('其他', '未分类')
            peers = [x for x in today_groups.get(t, []) if x != c] if valid else []
            peer_gaps = [gaps[x] for x in peers]
            tmed = med(peer_gaps)
            red_rate = round(sum(1 for g in peer_gaps if g > 0) / len(peer_gaps), 3) if peer_gaps else None
            # 梯队：同线且板数不低于自己的其他票 = 上层/同层；低于自己 = 下层
            upper = [gaps[x] for x in peers
                     if (int((z4.get(x) or {}).get('boards') or 0) or 0) >= (boards or 0)]
            lower = [gaps[x] for x in peers
                     if (int((z4.get(x) or {}).get('boards') or 0) or 0) < (boards or 0)]
            upper_med, lower_med = med(upper), med(lower)
            tmax = theme_max_boards.get(t, 0) if valid else None
            is_line_top = bool(boards and tmax and boards >= tmax)
            bs = theme_board_set.get(t, []) if valid else []
            ladder_complete = bool(bs) and bs == list(range(1, max(bs) + 1))
            b = next((x for x in bars_of(c) if x.get('date') == d), None)
            close = b.get('close') if b else None
            rows.append({
                'd': d, 'code': c, 'name': name, 'gap': gap,
                'E': e, 'DEV': dev, 'zone': zone, 'sealed': sealed, 'is20': is20,
                # 板块环
                'theme': t, 'theme_src': src_of.get(c),
                'theme_zt_count': len(y_groups.get(t, [])) if valid else None,
                'theme_peer_count': len(peers) if valid else None,
                'theme_red_rate': red_rate, 'theme_gap_med': tmed,
                'gap_vs_theme': round(gap - tmed, 2) if tmed is not None else None,
                'gap_vs_market': round(gap - market_med, 2) if market_med is not None else None,
                # 梯队环
                'boards_y': boards, 'theme_max_boards': tmax,
                'market_max_boards': market_max_boards,
                'pos_gap_line': (tmax - boards) if (tmax is not None and boards) else None,
                'pos_gap_mkt': (market_max_boards - boards) if boards else None,
                'is_line_top': is_line_top, 'ladder_complete': ladder_complete,
                'theme_boards': bs, 'upper_gap_med': upper_med, 'lower_gap_med': lower_med,
                'gap_vs_upper': round(gap - upper_med, 2) if upper_med is not None else None,
                # 量能环
                'R': R, 'R_extreme': r_extreme, 'amt_yi': amt, 'prev_cje_yi': prev_cje,
                'day_R_p75': round(day_R_p75, 2) if day_R_p75 is not None else None,
                'yizi_y': yizi, 'vol_ma5_y': vol_ma5,
                # 结果
                'close': close,
                'ret': round((close / v['auction_price'] - 1) * 100, 2) if close else None,
                'zt_today': c in z5,
                'old_ok': 0 < gap <= 5 and not sealed, 'new_ok': zone == 'ok',
                '_prod': {k: (prod.get(c) or {}).get(k)
                          for k in ('credits', 'linked_status', 'linked_would_cut',
                                    'linked_unbuyable', 'linked_theme_src',
                                    'linked_isolated_high_open')},
            })
    return rows, meta


# ── 三环背书（全部只用 09:25 前事实；缺数据 → None「不裁决」，⛔ 不当通过）──────
def c_sector(r):
    if not r['theme_peer_count'] or r['theme_peer_count'] < SECTOR_MIN_PEERS:
        return None
    if r['theme_gap_med'] is None or r['theme_red_rate'] is None:
        return None
    return r['theme_gap_med'] > 0 and r['theme_red_rate'] >= SECTOR_RED_MIN


def c_ladder(r):
    """梯队背书只认「上层先开、自己跟上」的传导证据。

    ⚠️ 20 日实测纠偏：线内最高板自己高开 n=74 均 −2.68%/胜率 44.6%，
       比非最高板更差（「自己是锚」不等于「有资格高开」）。
       故线内最高板 **不额外背书也不否决 → None 不裁决**，
       它的高开合不合理交给板块环与预期环去判，不由梯队环白送一分。
    """
    if r['is_line_top']:
        return None
    if r['upper_gap_med'] is None or r['gap_vs_upper'] is None:
        return None
    return r['upper_gap_med'] > 0 and r['gap_vs_upper'] <= LADDER_OVERTAKE_MAX


def c_volume(r):
    """量能环 = **证伪层**，⛔ 不给「好」背书。

    20 日实测两次纠偏：
      · 固定 6% 判爆量 → 高开票 R 中位 10.53%，3/4 被一刀切，环退化成常数；
      · 改当日横截面 P75 → 方向反转（R≤P75 −2.26%/胜47.2% vs R>P75 −1.53%/胜53.5%）。
    ⇒ 「R 高还是低」在高开票里没有稳定方向（一字板/换手板成交额含义相反，
       见 skill §三·G）。唯一跨口径稳定的信号是 `R_extreme`
       （一字次日巨量开板 / 分母失真）：n=13 均 −4.45%、胜率 38.5%，三档口径下都最差。
    故：非极端 → None 不裁决；极端 → False 否决。⛔ 不拿 R 高低打分。
    `--volume-mode absolute` 可回退到旧的固定阈值打分口径（仅作对照，不推荐）。
    """
    if VOLUME_MODE == 'absolute':
        if r['R'] is None or r['R_extreme']:
            return None
        return VOL_R_MIN <= r['R'] <= VOL_R_MAX
    return False if (r['R'] is None or r['R_extreme']) else None


def c_expected(r):
    if r['DEV'] is None:
        return None
    return r['DEV'] <= DEV_OVERHEAT


def credits(r):
    """三环（板块 / 梯队 / 预期）计背书；量能只作证伪否决，不进分子分母。"""
    cs = {'sector': c_sector(r), 'ladder': c_ladder(r),
          'volume': c_volume(r), 'expected': c_expected(r)}
    rings = ('sector', 'ladder', 'expected')
    cs['n'] = sum(1 for k in rings if cs[k] is True)
    cs['known'] = sum(1 for k in rings if cs[k] is not None)
    cs['veto'] = cs['volume'] is False      # 量能结构失真 → 一票否决
    cs['max'] = 3
    return cs


def isolated(r):
    """旧版单因子：高开>5% 但脱离同题材 09:25 竞价中位 >6pct。"""
    return (r['gap'] > 5 and r['gap_vs_theme'] is not None
            and r['gap_vs_theme'] > 6)


def stat(rs, label, w=42):
    v = [x['ret'] for x in rs]
    z = sum(1 for x in rs if x['zt_today'])
    if not v:
        return f'{label:<{w}} n=0'
    return (f'{label:<{w}} n={len(rs):>4}  均值={st.mean(v):+6.2f}%  中位={st.median(v):+6.2f}%  '
            f'胜率={sum(1 for x in v if x > 0)/len(v)*100:5.1f}%  晋级={z:>3}({z/len(rs)*100:4.1f}%)')


ABSTAIN_MISSING = True   # 缺任一环 → 不裁决、保留现判据（项目铁律）；--missing-policy cut 可对比


def credit_ok(r, min_n):
    cr = r['_cr']
    if cr['veto']:
        return False
    if ABSTAIN_MISSING and cr['known'] < 3:
        return True
    return cr['n'] >= min_n


def hi_pass(r, min_n):
    return r['gap'] <= 5 or credit_ok(r, min_n)


GUARDS = {
    'V0 现判据（不动）': lambda r: r['new_ok'],
    'V4 旧单因子·砍孤立>6': lambda r: r['new_ok'] and not isolated(r),
    'E1 只砍透支高开 DEV>6': lambda r: r['new_ok'] and not (
        r['gap'] > 5 and r['DEV'] is not None and r['DEV'] > DEV_OVERHEAT),
    'LV 只排量能结构失真': lambda r: r['new_ok'] and not (
        r['gap'] > 5 and r['_cr']['veto']),
    'L2 高开须≥1/3环': lambda r: r['new_ok'] and hi_pass(r, 1),
    'L3 高开须≥2/3环': lambda r: r['new_ok'] and hi_pass(r, 2),
    'L1 高开须3/3环全': lambda r: r['new_ok'] and hi_pass(r, 3),
    'LC ≥2环且不孤立': lambda r: r['new_ok'] and (
        r['gap'] <= 5 or (credit_ok(r, 2) and not isolated(r))),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=20)
    ap.add_argument('--end', default='2026-11-05')
    ap.add_argument('--drop-thin', type=int, default=0,
                    help='额外剔除开头 N 个交易日（兼容旧用法）')
    ap.add_argument('--min-quotes', type=int, default=10,
                    help='竞价报价少于此数的交易日视为覆盖不全，自动剔除（默认 10）')
    ap.add_argument('--min-coverage', type=float, default=0.6,
                    help='竞价数/昨日涨停池数下限；早期快照只覆盖候选子集，板块分母会失真（默认 0.6）')
    ap.add_argument('--production', action='store_true',
                    help='用已落地的 core/sector_gap_context 生产判据覆盖研究 credits（一致性校验）')
    ap.add_argument('--missing-policy', choices=('abstain', 'cut'), default='abstain',
                    help='任一环缺数据时：abstain=不裁决保留现判据（默认）／cut=当不背书处理')
    ap.add_argument('--volume-mode', choices=('relative', 'absolute'), default='relative',
                    help='量能环：relative=当日横截面 R 分位（默认）／absolute=固定 1~6%%')
    ap.add_argument('--out', default='/tmp/limao_sector_gap_backtest.json')
    a = ap.parse_args()
    global ABSTAIN_MISSING, VOLUME_MODE
    ABSTAIN_MISSING = (a.missing_policy == 'abstain')
    VOLUME_MODE = a.volume_mode
    days = [a.end]
    for _ in range(a.days - 1):
        days.append(prev_a_share_trading_day(days[-1]))
    days = sorted(days)[a.drop_thin:]
    rows, meta = build(days)
    # ⛔ 早期竞价快照只覆盖候选子集（如 9-21 竞价 11 / 昨涨停 78），板块/梯队分母失真，
    #    必须按「竞价数 / 昨日涨停池数」剔除，⛔ 不能只按日历天数取窗口。
    thin = [d for d, nq, ny, *_ in meta
            if nq < a.min_quotes or not ny or nq / ny < a.min_coverage]
    active = [d for d in days if d not in thin]
    T = [r for r in rows if not r['sealed'] and not r['code'].startswith('688')
         and 'ST' not in (r['name'] or '').upper() and r['ret'] is not None
         and r['d'] in set(active)]
    same = 0
    for r in T:
        r['_cr'] = credits(r)
        p = (r.get('_prod') or {}).get('credits')
        if p == r['_cr']:
            same += 1
        if a.production and p is not None:
            r['_cr'] = p
    print(f'窗口 {days[0]} ~ {days[-1]}（{len(days)} 日，剔除覆盖不全 {len(thin)} 日：{",".join(thin) or "无"}）· '
          f'覆盖率闸 nq/昨涨停 ≥ {a.min_coverage} · '
          f'全部报价 {len(rows)} 票次 → 剔除封死/688/ST/缺收盘后 {len(T)} 票次')
    print(f'生产判据一致性：core/sector_gap_context.credits 与研究脚本逐项相同 {same}/{len(T)} 票次'
          f' · 本次判据来源：{"【生产模块】" if a.production else "研究脚本（默认）"}')
    print('日期        竞价 昨涨停 今涨停 plates覆盖 题材源 竞价中位 昨最高板')
    for d, nq, ny, nt, npl, gm, has_th, mmb in meta:
        print(f'  {d} {nq:>5} {ny:>6} {nt:>6} {npl:>5}/{nq:<5} '
              f'{("plates" if has_th else "sector"):>6} {str(gm):>7} {mmb:>6}'
              f'{"  ⛔薄日" if d in thin else ""}')
    pl = [r for r in T if r['theme_src'] == 'plates']
    print(f'\n题材源：plates {len(pl)} 票次 / sector 兜底 {len(T)-len(pl)} 票次'
          f'（无 plates 的日子板块分组粒度较粗，下面分层复核）')
    print(f'缺数据策略：{a.missing_policy}（{"任一环缺失→不裁决" if ABSTAIN_MISSING else "缺环当不背书"}）'
          f' · 量能口径：{a.volume_mode}'
          f'（{"当日全池 R 的 P75" if VOLUME_MODE == "relative" else "固定 1~6%"}）')

    print('\n【基线】')
    for lb, pred in [('全可买母体', lambda r: 1), ('旧 0<gap≤5', lambda r: r['old_ok']),
                     ('现 DEV≥-2', lambda r: r['new_ok']),
                     ('现 below（放弃）', lambda r: r['zone'] == 'below'),
                     ('现 ∧ 高开>5', lambda r: r['new_ok'] and r['gap'] > 5),
                     ('现 ∧ 低开≤0', lambda r: r['new_ok'] and r['gap'] <= 0)]:
        print(stat([r for r in T if pred(r)], lb))

    hi = [r for r in T if r['new_ok'] and r['gap'] > 5]
    print('\n【高开>5%：板块单因子（旧版口径）】')
    for lb, pred in [
        ('≤0 跟着板块', lambda r: r['gap_vs_theme'] is not None and r['gap_vs_theme'] <= 0),
        ('0~3 同步', lambda r: r['gap_vs_theme'] is not None and 0 < r['gap_vs_theme'] <= 3),
        ('3~6 略独立', lambda r: r['gap_vs_theme'] is not None and 3 < r['gap_vs_theme'] <= 6),
        ('>6 孤立高开', lambda r: r['gap_vs_theme'] is not None and r['gap_vs_theme'] > 6),
        ('无可比同题材', lambda r: r['gap_vs_theme'] is None),
    ]:
        print(stat([r for r in hi if pred(r)], lb))

    print('\n【高开>5%：三环各自单独背书】')
    for k, lb in [('sector', '板块活'), ('ladder', '梯队不越位'),
                  ('volume', '量能不异常'), ('expected', '未透支质量预期')]:
        print(stat([r for r in hi if r['_cr'][k] is True], f'  ✔ {lb}'))
        print(stat([r for r in hi if r['_cr'][k] is False], f'  ✘ {lb}不成立'))
        print(stat([r for r in hi if r['_cr'][k] is None], f'  ？{lb}缺数据不裁决'))

    print('\n【高开>5%：三环联动 · 背书数（板块/梯队/预期；量能只证伪）】')
    for n in range(4):
        print(stat([r for r in hi if r['_cr']['n'] == n], f'  背书 {n}/3'))
    print(stat([r for r in hi if r['_cr']['n'] >= 2], '  ≥2 环'))
    print(stat([r for r in hi if r['_cr']['n'] <= 1], '  ≤1 环'))
    print(stat([r for r in hi if r['_cr']['known'] < 3], '  有环缺数据（守卫不裁决）'))
    print(stat([r for r in hi if r['_cr']['known'] == 3], '  三环全有结论'))
    print(stat([r for r in hi if r['_cr']['veto']], '  量能结构失真（证伪否决）'))

    print('\n【高开>5%：环与环的交集（只看 plates 段，板块分组可信）】')
    hpl = [r for r in hi if r['theme_src'] == 'plates']
    for lb, pred in [
        ('板块 ∧ 梯队', lambda r: r['_cr']['sector'] is True and r['_cr']['ladder'] is True),
        ('板块 ∧ 预期', lambda r: r['_cr']['sector'] is True and r['_cr']['expected'] is True),
        ('梯队 ∧ 预期', lambda r: r['_cr']['ladder'] is True and r['_cr']['expected'] is True),
        ('三环全 True', lambda r: r['_cr']['n'] == 3),
        ('三环全 False', lambda r: r['_cr']['n'] == 0 and r['_cr']['known'] == 3),
        ('量能结构失真', lambda r: r['_cr']['veto']),
    ]:
        print(stat([r for r in hpl if pred(r)], f'  {lb}'))

    print('\n【高开>5%：梯队越位（低板高开、上层不跟）】')
    for lb, pred in [
        ('线内最高板（自己是锚）', lambda r: r['is_line_top']),
        ('非最高板 ∧ 上层绿开', lambda r: not r['is_line_top'] and r['upper_gap_med'] is not None and r['upper_gap_med'] <= 0),
        ('非最高板 ∧ 上层红开 ∧ 不越位', lambda r: not r['is_line_top'] and r['_cr']['ladder'] is True),
        ('非最高板 ∧ 越位>3pct', lambda r: not r['is_line_top'] and r['gap_vs_upper'] is not None and r['gap_vs_upper'] > 3),
        ('梯队完整(1..max连续)', lambda r: r['ladder_complete']),
        ('全市场身位差≥2板', lambda r: (r['pos_gap_mkt'] or 0) >= 2),
    ]:
        print(stat([r for r in hi if pred(r)], lb))

    print('\n【高开>5%：量能 R 分档（extreme 已单列）】')
    for lb, pred in [
        ('R 极端/缺失（一字次日等）', lambda r: r['R_extreme']),
        ('R<1 无量', lambda r: not r['R_extreme'] and r['R'] is not None and r['R'] < 1),
        ('1≤R<3', lambda r: not r['R_extreme'] and r['R'] is not None and 1 <= r['R'] < 3),
        ('3≤R≤6', lambda r: not r['R_extreme'] and r['R'] is not None and 3 <= r['R'] <= 6),
        ('R>6 爆量', lambda r: not r['R_extreme'] and r['R'] is not None and r['R'] > 6),
        ('R ≤ 当日全池P75', lambda r: not r['R_extreme'] and r['R'] is not None
         and r['day_R_p75'] is not None and r['R'] <= r['day_R_p75']),
        ('R > 当日全池P75', lambda r: not r['R_extreme'] and r['R'] is not None
         and r['day_R_p75'] is not None and r['R'] > r['day_R_p75']),
    ]:
        print(stat([r for r in hi if pred(r)], lb))

    print('\n【探索版守卫（⛔ 研究用，非生产参数；只对高开>5%生效）】')
    for lb, pred in GUARDS.items():
        print(stat([r for r in T if pred(r)], lb))
    half = len(active) // 2
    early, late = set(active[:half]), set(active[half:])
    print('\n【前后分段】')
    for lb, pred in GUARDS.items():
        print(stat([r for r in T if pred(r) and r['d'] in early], f'{lb} 前半'), ' | ',
              stat([r for r in T if pred(r) and r['d'] in late], f'{lb} 后半', 24))
    print('\n【题材源分层（plates vs sector 兜底）】')
    for lb, pred in GUARDS.items():
        print(stat([r for r in T if pred(r) and r['theme_src'] == 'plates'], f'{lb} plates'), ' | ',
              stat([r for r in T if pred(r) and r['theme_src'] != 'plates'], f'{lb} 兜底', 24))

    print('\n【生产影子字段 · 留痕质量与守卫对照（A 档已落地，⛔ 未裁决）】')
    def _p(r, k):
        return (r.get('_prod') or {}).get(k)
    for lb, pred in [
        ('status=ok（三环齐全）', lambda r: _p(r, 'linked_status') == 'ok'),
        ('status=abstain_missing_ring', lambda r: _p(r, 'linked_status') == 'abstain_missing_ring'),
        ('status=abstain_no_plates', lambda r: _p(r, 'linked_status') == 'abstain_no_plates'),
        ('status=volume_veto', lambda r: _p(r, 'linked_status') == 'volume_veto'),
        ('linked_would_cut=True', lambda r: _p(r, 'linked_would_cut') is True),
        ('linked_would_cut=False', lambda r: _p(r, 'linked_would_cut') is False),
    ]:
        print(stat([r for r in T if pred(r)], lb))
    print(stat([r for r in T if _p(r, 'linked_would_cut') is True
                and r['theme_src'] == 'plates'], '  would_cut ∧ plates 段'))
    hi_cut = [r for r in hi if _p(r, 'linked_would_cut') is True]
    hi_keep = [r for r in hi if _p(r, 'linked_would_cut') is False]
    print(stat(hi_cut, '  高开>5 ∧ 影子会砍'))
    print(stat(hi_keep, '  高开>5 ∧ 影子不砍'))

    print('\n【逐日】日期        母体 现判据n    均值    胜率   高开>5n   均值   ≥2环高开n')
    for d in active:
        base = [r for r in T if r['d'] == d]
        nk = [r for r in base if r['new_ok']]
        hk = [r for r in nk if r['gap'] > 5]
        hc = [r for r in hk if r['_cr']['n'] >= 2]
        f = lambda rs: f'{st.mean([x["ret"] for x in rs]):+7.2f}%' if rs else '      -'
        w = lambda rs: f'{sum(1 for x in rs if x["ret"] > 0)/len(rs)*100:5.1f}%' if rs else '    -'
        print(f'  {d} {len(base):>5} {len(nk):>7} {f(nk):>9} {w(nk):>7} {len(hk):>7} {f(hk):>9} {len(hc):>8}')

    out = {'days': days, 'active_days': active, 'thin_days': thin, 'meta': meta,
           'params': {'SECTOR_MIN_PEERS': SECTOR_MIN_PEERS, 'SECTOR_RED_MIN': SECTOR_RED_MIN,
                      'LADDER_OVERTAKE_MAX': LADDER_OVERTAKE_MAX, 'VOL_R_MIN': VOL_R_MIN,
                      'VOL_R_MAX': VOL_R_MAX, 'VOL_R_EXTREME': VOL_R_EXTREME,
                      'DEV_OVERHEAT': DEV_OVERHEAT, 'VOL_R_PCTL': VOL_R_PCTL,
                      'missing_policy': a.missing_policy,
                      'volume_mode': a.volume_mode,
                      'min_quotes': a.min_quotes, 'min_coverage': a.min_coverage,
                      'production': bool(a.production)},
           'rows': [{k: v for k, v in r.items() if k != '_cr'} | {'credits': r['_cr']}
                    for r in T]}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n明细 → {a.out}')


if __name__ == '__main__':
    main()
