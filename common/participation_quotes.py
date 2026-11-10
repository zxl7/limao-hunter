"""只读展示今日冻结参与票行情；不写冻结件、账本或调度。"""
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from common.em_flow import fetch_quotes


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def quote_time(value, date):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo('Asia/Shanghai'))
        dt = dt.astimezone(ZoneInfo('Asia/Shanghai'))
        return dt.isoformat() if dt.date().isoformat() == date and dt.strftime('%H:%M') >= '09:30' else None
    except ValueError:
        return None


def participation_quotes(root, now=None):
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    date = now.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    root = Path(root)
    result = {'date': date, 'ts': now.isoformat(), 'quotes': {}, 'missing': [], 'call_available': False}
    file = root / 'wealth_code/cache' / f'pool_call-{date}.json'
    if not file.exists():
        return result
    call = json.loads(file.read_text())
    if call.get('trade_date') != date:
        return result
    result['call_available'] = True
    codes = list(dict.fromkeys(c for c in call.get('meta', {}).get('participate', []) if isinstance(c, str) and len(c) == 6 and c.isdigit()))
    snapshot_file = root / 'cache/pulse_snapshots/latest.json'
    snapshot = json.loads(snapshot_file.read_text()) if snapshot_file.exists() else {}
    ts = quote_time(snapshot.get('ts'), date)

    def walk(value):
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            code = str(value.get('code', '')).split('.')[0]
            pct = number(value.get('pct'))
            stamp = quote_time(value.get('ts', snapshot.get('ts')), date)
            if code in codes and pct is not None and ts and stamp:
                result['quotes'][code] = {'pct': pct, 'ts': stamp, 'source': 'market/latest'}
            for item in value.values():
                if isinstance(item, (list, dict)):
                    walk(item)
    for key in ('leaders', 'hot_spots', 'limit_up_stocks'):
        walk(snapshot.get(key))
    missing = [c for c in codes if c not in result['quotes']]
    try:
        supplemental = fetch_quotes(missing) if missing else {}
    except Exception:
        supplemental = {}
    for code in missing:
        row = supplemental.get(code, {})
        pct = number(row.get('pct'))
        price = number(row.get('price'))
        stamp = quote_time(row.get('source_ts_iso'), date)
        if pct is not None and price is not None and price > 0 and stamp:
            age = (now - datetime.fromisoformat(stamp)).total_seconds()
            if 0 <= age <= 300:
                result['quotes'][code] = {'pct': pct, 'ts': stamp, 'source': 'em_flow/quotes'}
    result['missing'] = [c for c in codes if c not in result['quotes']]
    result['names'] = {c: call.get('views', {}).get(c, {}).get('name', '') for c in codes}
    return result
