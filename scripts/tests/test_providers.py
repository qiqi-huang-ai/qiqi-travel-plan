import contextlib
import io
import json
import os
import sys
import unittest
import tempfile
import copy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from providers import amap, weather
import migrate_trip
import provider_pipeline

SAMPLE = Path(__file__).resolve().parents[2] / "assets" / "examples" / "sample_trip.json"


class ProviderTests(unittest.TestCase):
    def test_amap_without_key_is_explicit_unavailable(self):
        with patch.dict(os.environ, {}, clear=True):
            result = amap.call('/v3/geocode/geo', {'address': '西湖'})
        self.assertEqual(result['availability'], 'unavailable')
        self.assertEqual(result['items'], [])
        self.assertNotIn('SYNTHETIC_SECRET_VALUE', json.dumps(result))

    def test_weather_structure_failure_is_not_success(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{}'
            def geturl(self): return 'https://api.open-meteo.com/v1/forecast'
        class Opener:
            def open(self, *args, **kwargs): return Response()
        with patch('providers.weather.urllib.request.build_opener', return_value=Opener()):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = weather.main(['--live', '30.25', '120.15'])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.getvalue())['availability'], 'error')

    def test_weather_cli_requires_explicit_live_or_dry_run(self):
        with self.assertRaises(SystemExit):
            weather.main(['30.25', '120.15'])

    def test_weather_dry_run_does_not_make_request(self):
        out = io.StringIO()
        with patch('providers.weather.urllib.request.build_opener') as request, \
             contextlib.redirect_stdout(out):
            code = weather.main(['--dry-run', '30.25', '120.15', '--start', '2026-10-10'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['availability'], 'dry_run')
        request.assert_not_called()

    def test_amap_cli_requires_explicit_live_or_dry_run(self):
        with self.assertRaises(SystemExit):
            amap.main(['geocode', '西湖'])

    def test_amap_dry_run_does_not_read_key_or_create_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = str(Path(td) / 'amap-usage.json')
            out = io.StringIO()
            with patch.dict(os.environ, {'AMAP_API_KEY':'SYNTHETIC_SECRET_VALUE'}, clear=True), \
                 contextlib.redirect_stdout(out):
                code = amap.main(['--dry-run', '--usage-file', ledger, 'geocode', '西湖'])
            payload = json.loads(out.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload['availability'], 'dry_run')
            self.assertFalse(Path(ledger).exists())
            self.assertNotIn('SYNTHETIC_SECRET_VALUE', out.getvalue())

    def test_amap_normalizes_route_payload(self):
        payload = {'route': {'paths': [{'duration':'1500', 'distance':'12000',
                    'tolls':'8', 'steps':[{'duration':'600'}, {'duration':'900'}]}]}}
        items = amap.normalize_payload('route', payload, mode='driving')
        self.assertEqual(items[0]['duration_min'], 25)
        self.assertEqual(items[0]['distance_m'], 12000)
        self.assertEqual(items[0]['fare_cny'], 8.0)
        self.assertNotIn('steps', items[0])

    def test_amap_request_budget_persists_across_commands(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = str(Path(td) / 'amap-usage.json')
            with amap.RequestBudget(1, ledger) as budget:
                budget.reserve()
            with amap.RequestBudget(1, ledger) as budget:
                with self.assertRaises(amap.BudgetExceeded):
                    budget.reserve()

    def test_provider_pipeline_applies_route_without_mutating_source(self):
        trip = migrate_trip.migrate(json.loads(SAMPLE.read_text(encoding='utf-8')))
        original = copy.deepcopy(trip)
        result = {
            'provider':'amap', 'capability':'route', 'availability':'available',
            'retrieved_at':'2026-09-22T10:00:00+08:00',
            'items':[{'mode':'transit','duration_min':32,'distance_m':8000,
                      'walking_min':7,'transfers':1,'fare_cny':4.0}],
            'warnings':[], 'usage':{'requests':1},
        }
        candidate = provider_pipeline.apply_result(trip, result, 'leg-station-hotel')
        leg = next(x for x in candidate['legs'] if x['leg_id'] == 'leg-station-hotel')
        self.assertEqual(trip, original)
        self.assertEqual(leg['mode'], '公交')
        self.assertEqual(leg['time_min'], 32)
        self.assertEqual(leg['time_max'], 32)
        self.assertEqual(leg['walking_min'], 7)
        self.assertEqual(leg['provider'], 'amap')
        self.assertEqual(leg['evidence_level'], 'tool_route')
        self.assertTrue(leg['source_ids'])
        self.assertEqual(candidate['provider_results'][-1]['provider'], 'amap')

    def test_provider_pipeline_applies_weather(self):
        trip = migrate_trip.migrate(json.loads(SAMPLE.read_text(encoding='utf-8')))
        result = {
            'provider':'open-meteo', 'capability':'weather', 'availability':'available',
            'retrieved_at':'2026-09-22T10:00:00+08:00',
            'source_url':'https://api.open-meteo.com/v1/forecast?latitude=31&longitude=120',
            'coverage':{'from':'2026-10-10','to':'2026-10-11'},
            'items':[{'date':'2026-10-10','weather_code':3,'temp_max':24.0,
                      'temp_min':17.0,'precipitation_probability_max':20}],
            'warnings':[], 'usage':{'requests':1},
        }
        candidate = provider_pipeline.apply_result(trip, result)
        self.assertEqual(candidate['itinerary']['weather']['kind'], 'forecast')
        self.assertEqual(candidate['itinerary']['weather']['entries'][0]['date'], '2026-10-10')
        self.assertTrue(candidate['itinerary']['weather']['entries'][0]['source_id'])
        source_id = candidate['itinerary']['weather']['entries'][0]['source_id']
        source = next(x for x in candidate['sources'] if x['source_id'] == source_id)
        self.assertEqual(source['source_type'], 'web')

    def test_provider_pipeline_imports_tikhub_as_experience_only(self):
        trip = migrate_trip.migrate(json.loads(SAMPLE.read_text(encoding='utf-8')))
        result = {
            'provider': 'tikhub', 'capability': 'social_experience', 'availability': 'available',
            'retrieved_at': '2026-09-22T10:00:00+08:00',
            'source_url': 'https://api.tikhub.io/api/v1/xiaohongshu/app_v2/search_notes',
            'items': [{'note_id': 'note-1', 'title': '带娃排队体验', 'excerpt': '建议早一点到',
                       'published_at': None, 'url': 'https://www.xiaohongshu.com/explore/note-1'}],
            'warnings': [], 'usage': {'requests': 1},
        }
        candidate = provider_pipeline.apply_result(trip, result, 'place-museum')
        fact = candidate['facts'][-1]
        self.assertEqual(fact['status'], 'experience')
        self.assertEqual(fact['subject_id'], 'place-museum')
        source = next(x for x in candidate['sources'] if x['source_id'] == fact['source_ids'][0])
        self.assertEqual(source['source_type'], 'social')


if __name__ == '__main__':
    unittest.main()
