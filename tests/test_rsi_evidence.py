"""RSI研究链隔离验证；全部数据库与候选使用tmp_path。"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from common import market_daily_store as store
from common.rsi_evidence import annotate_candidates, build_evidence, rsi_wilder


def test_reference_and_gap():
    assert rsi_wilder([1, 2, 3, 2, 2, 4, 3], 3)[3:] == pytest.approx([
        66.6666666667, 66.6666666667, 86.6666666667, 59.7701149425])
    for gap in [None, float('nan'), float('inf')]:
        assert rsi_wilder([None, 1, 2, 3, gap, 5, 4, 3], 2) == [None, None, None, 100, None, None, None, 0]
    assert rsi_wilder([1] * 20, 24) == [None] * 20


@pytest.mark.xfail(reason="依赖 duckdb 全市场库（本机未初始化）", strict=False)
def test_readonly_cutoff_and_research_consumer(tmp_path, monkeypatch, capsys):
    db = tmp_path / 'daily.duckdb'
    conn = store.connect(db)
    for day in range(1, 11):
        conn.execute("INSERT INTO market_daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)",
                     ['600000', '600000.SH', f'2026-09-{day:02d}', 10, 12, 9, 10 + day, 1, 1, 'CNY', 'test'])
    conn.close()
    before = db.read_bytes()
    rows = [{'code': '600000', 'score': 8, 'theme_resonance_facts': {'technical_status': 'unknown'}}]
    annotate_candidates(rows, asof='2026-09-08', db_path=db)
    evidence = rows[0]['rsi_evidence']
    assert evidence['sample_count'] == 8 and evidence['bar_date'] == '2026-09-08'
    assert evidence['readings']['6']['value'] == 100
    assert evidence['readings']['12']['value'] is None
    assert rows[0]['score'] == 8
    assert rows[0]['theme_resonance_facts']['technical_status'] == 'unknown'
    store.status(db_path=db)
    assert db.read_bytes() == before
    ro = store.connect_readonly(db)
    with pytest.raises(Exception):
        ro.execute('DELETE FROM market_daily_bars')
    ro.close()
    assert len(store.recent_bars('600000', asof='2026-09-05', db_path=db)) == 5

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('rsi_ledger_test', root / 'wealth_code/scripts/ai_ledger.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'CACHE', tmp_path)
    candidate = tmp_path / 'candidates-2026-09-08.json'
    candidate.write_text(json.dumps({'relay': rows, 'watch': []}))
    original = candidate.read_bytes()
    assert module.cmd_research_input(SimpleNamespace(decision_date='2026-09-08')) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['picks'][0]['rsi_evidence'] == evidence
    assert output['picks'][0]['rsi_input_status'] == 'ready'
    assert candidate.read_bytes() == original
    rows[0]['rsi_evidence']['asof'] = '2026-09-09'
    candidate.write_text(json.dumps({'relay': rows, 'watch': []}))
    module.cmd_research_input(SimpleNamespace(decision_date='2026-09-08'))
    rejected = json.loads(capsys.readouterr().out)
    assert rejected['picks'][0]['rsi_evidence'] is None
    assert rejected['picks'][0]['rsi_input_status'] == 'unavailable'


def test_missing_database_does_not_create_directory(tmp_path):
    path = tmp_path / 'absent' / 'daily.duckdb'
    with pytest.raises(FileNotFoundError):
        store.recent_bars('600000', db_path=path)
    assert not path.parent.exists()
    rows = [{'code': '600000'}]
    annotate_candidates(rows, asof='2026-09-08', db_path=path)
    assert rows[0]['rsi_evidence']['quality'] == 'missing'
    assert not path.parent.exists()


def test_python_typescript_parity():
    import subprocess
    terminal = Path(__file__).resolve().parents[2] / '狸猫终端/src/shared/lib/rsi.ts'
    fixtures = [[1, 2, 3, 2, 2, 4, 3], [None, 1, 2, 3, None, 5, 4, 3], [4] * 20]
    script = ('const {rsiWilder} = await import(process.argv[1]); '
              'console.log(JSON.stringify(JSON.parse(process.argv[2]).map(x => rsiWilder(x, 3))))')
    result = subprocess.run(['node', '--experimental-strip-types', '--input-type=module', '-e',
                             script, terminal.as_uri(), json.dumps(fixtures)],
                            text=True, capture_output=True, check=True)
    for actual, prices in zip(json.loads(result.stdout), fixtures):
        for left, right in zip(actual, rsi_wilder(prices, 3)):
            if right is None:
                assert left is None
            else:
                assert left == pytest.approx(right)


def test_stale_and_invalid_contract():
    bar = {'code': '600000', 'trade_date': '2026-09-01', 'close': 1, 'source': 'test'}
    assert build_evidence('600000', [bar], asof='2026-09-02')['quality'] == 'stale'
    evidence = build_evidence('600000', [bar, bar], asof='2026-09-01')
    assert evidence['quality'] == 'invalid'
    assert evidence['readings']['6']['value'] is None
