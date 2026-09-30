import sys, unittest, tempfile
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.relay_effect import build, publish, window, fresh
from common.json_io import load_json
from common.market_time import BJ_TZ

def now(t): return datetime.fromisoformat('2026-09-29T'+t).replace(tzinfo=BJ_TZ)
def point(t, amount=1, pct=2, sealed=False):
    return dict(at=now(t).isoformat(), pct=pct, amount_yi=amount, sealed=sealed, seal_yi=None, today_fbt=None)
class RelayTests(unittest.TestCase):
    def test_window_and_zero(self):
        a,b=point('09:30:00',0,0),point('09:45:00',2,4)
        w=window([a,b],b)
        self.assertEqual(w['amount_yi'],2)
        self.assertEqual(w['pct_change'],4)
    def test_lunch_missing_rollback(self):
        for points in [[point('11:30:00'),point('13:00:00',3)],
                       [point('09:30:00',None),point('09:45:00',3)],
                       [point('09:30:00',2),point('09:40:00',1),point('09:45:00',3)],
                       [point('09:30:00'),point('09:47:00',3)]]:
            self.assertIsNone(window(points,points[-1]))
    def test_60_second_tolerance(self):
        a,b=point('09:31:00'),point('09:45:00',3)
        self.assertEqual(window([a,b],b)['start'],a['at'])
    def test_fresh_close(self):
        self.assertTrue(fresh(point('15:00:00')['at'],now('19:00:00')))
        self.assertFalse(fresh(point('11:30:00')['at'],now('19:00:00')))
    def test_fixed_members_and_coverage(self):
        state={'date':'2026-09-29','prev_date':'2026-09-28','samples':[
          {'code':'a','name':'A','prev_boards':2},{'code':'b','name':'B','prev_boards':2},
          {'code':'c','name':'C','prev_boards':1}], 'series':{}}
        quotes={k:{**point('10:00:00',n,p), 'quote_at':point('10:00:00')['at']} for k,n,p in [('a',0,2),('b',None,-6)]}
        result=build(state,quotes,now('10:00:00'))
        self.assertEqual(len(result['stocks']),3)
        self.assertEqual(result['overview']['median_pct'],-2)
        self.assertEqual(result['overview']['up_rate'],50)
        self.assertEqual(result['overview']['amount_count'],1)
        self.assertEqual(result['overview']['amount_yi'],0)
        build(state,quotes,now('10:00:30'))
        self.assertEqual(len(state['series']['a']),1)
    def test_restart_and_basis_change(self):
        with tempfile.TemporaryDirectory() as d:
            cache=Path(d)
            snap={'date':'2026-09-28','zt_list':[{'code':'a','name':'A','boards':2}]}
            def q(t,amt): return {'a':{**point(t,amt), 'quote_at':point(t)['at']}}
            self.assertTrue(publish(cache,snap,q('09:30:00',1),now('09:30:00')))
            self.assertTrue(publish(cache,snap,q('09:45:00',3),now('09:45:00')))
            result=load_json(cache/'relay_effect.json')
            self.assertEqual(result['stocks'][0]['window']['amount_yi'],2)
            self.assertFalse(publish(cache,{**snap,'date':'2026-09-29'}, {},now('18:00:00')))
            self.assertEqual(load_json(cache/'relay_effect.json'),result)
if __name__ == '__main__': unittest.main()
