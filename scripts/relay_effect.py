"""连板量价观察：固定昨日样本、分钟快照、15分钟窗口及原子发布。"""
from datetime import datetime, timedelta
from statistics import mean, median
import math
from common.json_io import load_json, save_json
from common.market_time import BJ_TZ


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def stamp(value):
    try:
        return datetime.fromisoformat(value).replace(tzinfo=BJ_TZ)
    except (TypeError, ValueError):
        return None


def session(value):
    dt = stamp(value)
    if not dt:
        return None
    t = dt.hour * 60 + dt.minute
    return 'am' if 570 <= t <= 690 else 'pm' if 780 <= t <= 900 else None


def fresh(value, now):
    dt = stamp(value)
    if not dt or dt.date() != now.date():
        return False
    # 午休/收盘按最后交易时刻判断，不能让过期上午行情冒充收盘行情。
    end = now
    if now.hour >= 15:
        end = now.replace(hour=15, minute=0, second=0, microsecond=0)
    elif now.hour == 12 or (now.hour == 11 and now.minute > 30):
        end = now.replace(hour=11, minute=30, second=0, microsecond=0)
    return -60 <= (end - dt).total_seconds() <= 180


def window(points, current):
    at = stamp(current['at'])
    period = session(current['at'])
    if not at or not period:
        return None
    start = at.replace(hour=9 if period == 'am' else 13, minute=30 if period == 'am' else 0, second=0)
    if (at - start).total_seconds() < 900:
        return None
    target = at - timedelta(minutes=15)
    candidates = [p for p in points if session(p['at']) == period and p['at'][:10] == current['at'][:10]
                  and abs((stamp(p['at']) - target).total_seconds()) <= 60]
    if not candidates:
        return None
    base = min(candidates, key=lambda p: (abs((stamp(p['at']) - target).total_seconds()), p['at']))
    segment = [p for p in points if base['at'] <= p['at'] <= current['at']]
    amounts = [p['amount_yi'] for p in segment]
    if any(v is None for v in amounts) or any(b < a for a, b in zip(amounts, amounts[1:])):
        return None
    if base['pct'] is None or current['pct'] is None or current['amount_yi'] is None:
        return None
    return {'start': base['at'], 'end': current['at'],
            'amount_yi': round(current['amount_yi'] - base['amount_yi'], 6),
            'pct_change': round(current['pct'] - base['pct'], 4)}


def summarize(key, stocks):
    valid = [s for s in stocks if not s['stale'] and s['pct'] is not None]
    pcts = [s['pct'] for s in valid]
    amounts = [s['amount_yi'] for s in stocks if not s['stale'] and s['amount_yi'] is not None]
    states = [s['sealed'] for s in stocks if not s['stale'] and s['sealed'] is not None]
    windows = [s['window'] for s in stocks if s['window'] is not None and not s['stale']]
    return {'key': key, 'count': len(stocks), 'quote_count': len(valid), 'amount_count': len(amounts),
            'state_count': len(states), 'window_count': len(windows),
            'avg_pct': mean(pcts) if pcts else None, 'median_pct': median(pcts) if pcts else None,
            'up_rate': sum(p > 0 for p in pcts) / len(pcts) * 100 if pcts else None,
            'seal_rate': sum(states) / len(states) * 100 if states else None,
            'amount_yi': sum(amounts) if amounts else None,
            'window_amount_yi': sum(w['amount_yi'] for w in windows) if windows else None,
            'window_pct_change': median(w['pct_change'] for w in windows) if windows else None}


def build(state, quotes, now):
    stocks = []
    for sample in state['samples']:
        code = sample['code']
        points = state['series'].setdefault(code, [])
        q = quotes.get(code, {})
        at = q.get('quote_at')
        if fresh(at, now) and session(at) and number(q.get('relay_pct', q.get('pct'))) is not None:
            point = {'at': at, 'pct': number(q.get('relay_pct', q.get('pct'))), 'amount_yi': number(q.get('amount_yi')),
                     'sealed': q.get('sealed'), 'seal_yi': q.get('seal_yi') if q.get('sealed') is True else None, 'today_fbt': q.get('today_fbt')}
            # 同分钟仅保留最新行情，拒绝乱序；重启后可继续使用原记录。
            if not points or at >= points[-1]['at']:
                if points and points[-1]['at'][:16] == at[:16]:
                    points[-1] = point
                else:
                    points.append(point)
        last = points[-1] if points else {'at': None, 'pct': None, 'amount_yi': None, 'sealed': None,
                                         'seal_yi': None, 'today_fbt': None}
        stale = not fresh(last['at'], now)
        stocks.append({**sample, **last, 'stale': stale, 'window': window(points, last) if not stale else None,
                       'series': list(points)})
    groups = [summarize(key, [s for s in stocks if (s['prev_boards'] == b if b < 4 else s['prev_boards'] >= 4)])
              for key, b in [('2', 2), ('3', 3), ('4+', 4), ('1', 1)]]
    return {'date': state['date'], 'prev_date': state['prev_date'], 'generated_ts': now.isoformat(timespec='seconds'),
            'phase': 'closed' if now.hour >= 15 else 'live', 'stocks': stocks, 'groups': groups,
            'overview': summarize('relay', [s for s in stocks if s['prev_boards'] >= 2])}


def publish(cache, snapshot, quotes, now):
    date = now.strftime('%Y-%m-%d')
    path = cache / 'relay_effect' / f'{date}.json'
    state = load_json(path) or {}
    if not state:
        # 仅接受早于当日的真实基线，盘后更新的今日池不能用于回填昨日样本。
        if not snapshot.get('date') or snapshot['date'] >= date or not snapshot.get('zt_list'):
            return False
        state = {'date': date, 'prev_date': snapshot['date'], 'samples': [
            {'code': s['code'], 'name': s['name'], 'prev_boards': s['boards']}
            for s in snapshot['zt_list']], 'series': {}}
    if state['prev_date'] != snapshot.get('date'):
        return False
    result = build(state, quotes, now)
    # 先持久化日内记录，再发布可被终端一次性读取的完整快照。
    if not save_json(path, state):
        return False
    return save_json(cache / 'relay_effect.json', result)
