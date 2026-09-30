"""从现有涨停归档与同日反馈恢复历史截面，不生成虚假的分钟序列。"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from common.json_io import load_json, save_json
from common.market_time import now
from scripts.relay_effect import number, summarize


def restore(cache):
    fb = load_json(cache / 'feedback_matrix.json')
    if not fb or fb.get('error') or not fb.get('date') or not fb.get('prev_date'):
        raise ValueError('缺少有效历史反馈')
    date, prev_date = fb['date'], fb['prev_date']
    previous = load_json(cache / 'zt_history' / f'zt_{prev_date}.json')
    closing = load_json(cache / 'zt_history' / f'zt_{date}.json')
    if not previous or previous.get('date') != prev_date or not previous.get('zt_list'):
        raise ValueError('昨日样本归档缺失')
    if not closing or closing.get('date') != date or len(closing.get('zt_list', [])) != closing.get('zt_count'):
        raise ValueError('当日收盘归档不完整')
    by_code = {s['code']: s for s in closing['zt_list']}
    stocks = []
    for base in previous['zt_list']:
        code = base['code']
        end = by_code.get(code)
        quote = fb.get('quotes', {}).get(code, {})
        # 成交额仅来源于同日归档；不从昨日成交额推算，也不将未知填零。
        pct = number(end.get('pct')) if end else number(quote.get('pct'))
        stocks.append({
            'code': code, 'name': base['name'], 'prev_boards': base['boards'],
            'pct': pct, 'amount_yi': number(end.get('cje_yi')) if end else None,
            'at': f'{date}T15:00:00+08:00' if end else fb['generated_ts'].replace(' ', 'T') + '+08:00',
            'sealed': end is not None, 'seal_yi': number(end.get('seal')) if end else None,
            'today_fbt': end.get('fbt') if end else None,
            'stale': False, 'window': None, 'series': [],
        })
    result = {
        'historical': True,
        'source_note': f'历史回放：{date}。涨停股取收盘归档；其余股票涨幅取 {fb["generated_ts"]} 反馈。成交额仅覆盖同日归档股票，15分钟记录未归档。',
        'date': date, 'prev_date': prev_date, 'generated_ts': now().isoformat(timespec='seconds'),
        'phase': 'closed', 'stocks': stocks,
        'overview': summarize('relay', [s for s in stocks if s['prev_boards'] >= 2]),
        'groups': [summarize(key, [s for s in stocks if (s['prev_boards'] == b if b < 4 else s['prev_boards'] >= 4)])
                   for key, b in [('2', 2), ('3', 3), ('4+', 4), ('1', 1)]],
    }
    current = load_json(cache / 'relay_effect.json')
    if current and not current.get('historical') and current.get('date', '') >= date:
        raise ValueError('已有同日或更新的真实采集快照，拒绝覆盖')
    if not save_json(cache / 'relay_effect.json', result):
        raise OSError('历史截面发布失败')
    print({'date': date, 'prev_date': prev_date, 'samples': len(stocks), 'relay': result['overview']})

if __name__ == '__main__':
    restore(ROOT / 'cache')
