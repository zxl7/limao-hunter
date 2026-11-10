import json

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr('common.participation_quotes.fetch_quotes', lambda codes: {})
from datetime import datetime
from zoneinfo import ZoneInfo

from common.participation_quotes import participation_quotes, quote_time

NOW = datetime(2026, 11, 9, 10, 30, tzinfo=ZoneInfo('Asia/Shanghai'))


def setup(root, snapshot, codes=('000001', '000002')):
    call = root / 'wealth_code/cache/pool_call-2026-11-09.json'
    call.parent.mkdir(parents=True)
    call.write_text(json.dumps({'trade_date': '2026-11-09', 'meta': {'participate': list(codes)}}))
    snap = root / 'cache/pulse_snapshots/latest.json'
    snap.parent.mkdir(parents=True)
    snap.write_text(json.dumps(snapshot))


def test_missing_today_never_uses_yesterday(tmp_path):
    assert participation_quotes(tmp_path, NOW)['call_available'] is False


def test_snapshot_zero_is_real_but_missing_is_not_zero(tmp_path):
    setup(tmp_path, {'ts': NOW.isoformat(), 'hot_spots': [{'stocks': [{'code': '000001.SZ', 'pct': 0}]}]})
    result = participation_quotes(tmp_path, NOW)
    assert result['quotes']['000001']['pct'] == 0
    assert result['quotes']['000001']['ts'] == NOW.isoformat()
    assert result['missing'] == ['000002']


def test_old_snapshot_and_auction_not_current(tmp_path):
    setup(tmp_path, {'ts': '2026-11-06T15:00:00+08:00', 'leaders': [{'code': '000001', 'pct': 9}]})
    assert participation_quotes(tmp_path, NOW)['quotes'] == {}
    f = tmp_path / 'cache/pulse_snapshots/latest.json'
    f.write_text(json.dumps({'ts': '2026-11-09T09:25:00+08:00', 'leaders': [{'code': '000001', 'pct': 3}]}))
    assert participation_quotes(tmp_path, NOW)['quotes'] == {}


def test_missing_snapshot_means_missing_without_external_requests(tmp_path):
    setup(tmp_path, {})
    assert participation_quotes(tmp_path, NOW)['missing'] == ['000001', '000002']


def test_refresh_reads_new_snapshot(tmp_path):
    setup(tmp_path, {'ts': NOW.isoformat(), 'limit_up_stocks': [{'code': '000001.SS', 'pct': 1}]}, ('000001',))
    assert participation_quotes(tmp_path, NOW)['quotes']['000001']['pct'] == 1
    f = tmp_path / 'cache/pulse_snapshots/latest.json'
    f.write_text(json.dumps({'ts': NOW.isoformat(), 'leaders': [{'code': '000001', 'pct': 2}]}))
    assert participation_quotes(tmp_path, NOW)['quotes']['000001']['pct'] == 2


def test_invalid_pct_and_stale_row_rejected(tmp_path):
    setup(tmp_path, {'ts': NOW.isoformat(), 'leaders': [
        {'code': '000001', 'pct': True},
        {'code': '000002', 'pct': 5, 'ts': '2026-11-06T15:00:00+08:00'},
    ]})
    assert participation_quotes(tmp_path, NOW)['quotes'] == {}


def test_existing_quote_fallback_is_read_only_and_scoped(tmp_path, monkeypatch):
    setup(tmp_path, {}, ('000001',))
    call = tmp_path / 'wealth_code/cache/pool_call-2026-11-09.json'
    before = call.read_bytes()
    requested = []

    def fetch(codes):
        requested.extend(codes)
        return {'000001': {'price': 10, 'pct': 2, 'source_ts_iso': NOW.isoformat()}}

    monkeypatch.setattr('common.participation_quotes.fetch_quotes', fetch)
    result = participation_quotes(tmp_path, NOW)
    assert requested == ['000001']
    assert result['quotes']['000001']['source'] == 'em_flow/quotes'
    assert result['missing'] == []
    assert call.read_bytes() == before


@pytest.mark.parametrize('stamp', ['2026-11-06T15:00:00+08:00', '2026-11-09T09:25:00+08:00',
                                 '2026-11-09T10:20:00+08:00', '2026-11-09T10:31:00+08:00'])
def test_fallback_rejects_old_auction_and_future(tmp_path, monkeypatch, stamp):
    setup(tmp_path, {}, ('000001',))
    monkeypatch.setattr('common.participation_quotes.fetch_quotes', lambda codes: {
        '000001': {'price': 10, 'pct': 2, 'source_ts_iso': stamp}})
    assert participation_quotes(tmp_path, NOW)['missing'] == ['000001']


def test_fallback_failure_keeps_frozen_names(tmp_path, monkeypatch):
    setup(tmp_path, {}, ('000001',))
    def fail(codes):
        raise RuntimeError('报价暂不可用')
    monkeypatch.setattr('common.participation_quotes.fetch_quotes', fail)
    assert participation_quotes(tmp_path, NOW)['call_available'] is True
    assert participation_quotes(tmp_path, NOW)['missing'] == ['000001']


def test_quote_time_requires_same_day_and_continuous_session():
    assert quote_time('10:30:00', '2026-11-09') is None
    assert quote_time('2026-11-06 15:00:00', '2026-11-09') is None
    assert quote_time('2026-11-09 09:25:00', '2026-11-09') is None
    assert quote_time(NOW.isoformat(), '2026-11-09')
