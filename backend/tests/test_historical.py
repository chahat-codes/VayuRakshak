import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.data import historical
from app.main import app
from unittest.mock import patch

class HistoricalComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(); historical.DB=Path(cls.temp.name)/"daily.sqlite3"; historical.init(); cls.client=TestClient(app)

    def setUp(self):
        with historical.connect() as db: db.execute("DELETE FROM daily_observations")

    def test_daily_import_and_equal_sized_windows(self):
        csv=("date,city,station,pm25,pm10,aqi\n"
             "2025-10-18,Delhi,Station One,40,80,120\n"
             "2025-10-19,Delhi,Station One,42,82,130\n"
             "2025-10-20,Delhi,Station One,60,100,180\n"
             "2025-10-21,Delhi,Station One,50,90,150\n")
        response=self.client.post('/api/historical/import',data={"source_name":"CPCB export","source_url":"https://example.test/data"},files={"file":("daily.csv",csv,"text/csv")})
        self.assertEqual(response.status_code,200,response.text); self.assertEqual(response.json()['imported'],4)
        result=self.client.get('/api/historical/diwali',params={"city":"Delhi","station":"Station One","years":"2025,2026","days":2})
        self.assertEqual(result.status_code,200,result.text)
        data=result.json(); y2025=next(y for y in data['results'] if y['year']==2025); y2026=next(y for y in data['results'] if y['year']==2026)
        self.assertEqual(y2025['windows']['before']['expected_days'],2)
        self.assertEqual(y2025['windows']['before']['observed_days'],2)
        self.assertEqual(y2025['windows']['during']['metrics']['pm25']['mean'],60)
        self.assertEqual(y2026['festival_status'],'upcoming')
        self.assertEqual(y2026['windows']['during']['status'],'upcoming')
        self.assertIn('increased',data['summary']['pm25']['message'])

    def test_validation_preview_rejects_bad_rows_and_counts_duplicates(self):
        csv=("date,city,station,pm25,pm10,aqi\n"
             "2025-10-19,Delhi,Station One,0,,0\n"
             "2025-10-19,Delhi,Station One,8,10,30\n"
             "bad-date,Delhi,Station One,8,10,30\n"
             "2025-10-20,Delhi,,8,10,30\n")
        response=self.client.post('/api/historical/validate',data={"source_name":"CPCB export"},files={"file":("daily.csv",csv,"text/csv")})
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        self.assertEqual(data['valid_rows'],1)
        self.assertEqual(data['duplicate_rows_in_file'],1)
        self.assertEqual(data['rejected_rows'],2)
        self.assertEqual(data['new_rows'],1)
        self.assertEqual(data['valid_metric_days'],{"pm25":1,"pm10":0,"aqi":1})

    def test_zero_baseline_and_missing_values_are_reported_without_fabrication(self):
        csv=("date,city,station,pm25,pm10,aqi\n"
             "2024-10-28,Delhi,Station One,0,,\n"
             "2024-10-29,Delhi,Station One,0,,\n"
             "2024-10-31,Delhi,Station One,12,,\n")
        response=self.client.post('/api/historical/import',data={"source_name":"CPCB export"},files={"file":("daily.csv",csv,"text/csv")})
        self.assertEqual(response.status_code,200,response.text)
        result=self.client.get('/api/historical/diwali',params={"city":"Delhi","station":"Station One","years":"2024,2025","days":2})
        self.assertEqual(result.status_code,200,result.text)
        data=result.json(); year=next(y for y in data['results'] if y['year']==2024)
        self.assertEqual(year['festival_date'],'2024-10-31')
        self.assertEqual(year['windows']['before']['start'],'2024-10-29')
        self.assertEqual(year['windows']['during']['metrics']['pm25']['mean'],12)
        self.assertIsNone(year['windows']['during']['metrics']['pm10']['mean'])
        self.assertEqual(year['comparisons']['pm25']['status'],'zero_baseline')
        self.assertIsNone(year['comparisons']['pm25']['change_percent'])
        self.assertIn('Not enough observed',data['summary']['pm10']['message'])

    def test_air_api_marks_open_meteo_estimates_and_forecasts(self):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        now=datetime.now(ZoneInfo("Asia/Kolkata"))
        past=(now-timedelta(hours=1)).strftime('%Y-%m-%dT%H:00')
        future=(now+timedelta(hours=1)).strftime('%Y-%m-%dT%H:00')
        class Response:
            def raise_for_status(self): pass
            def json(self):
                return {"timezone":"Asia/Kolkata","hourly":{"time":[past,future],"pm2_5":[12,18],"pm10":[24,30]}}
        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return False
            async def get(self,*args,**kwargs): return Response()
        with patch('app.main.httpx.AsyncClient',return_value=Client()):
            response=self.client.get('/api/air-quality/latest')
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        self.assertEqual(data['current']['kind'],'estimated')
        self.assertTrue(any(x['kind']=='forecast' for x in data['forecast']))
        self.assertIsNone(data['aqi'])
        self.assertEqual(data['source'],'Open-Meteo Air Quality API')

    def test_imported_fallback_is_labeled_observed_when_provider_is_down(self):
        with patch('app.main.httpx.AsyncClient',side_effect=RuntimeError('offline')), patch('app.main.read_rows',return_value=[{"timestamp":"2025-10-20T12:00:00","pm25":20,"pm10":30,"station":"Station One"}]):
            response=self.client.get('/api/air-quality/latest')
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        self.assertEqual(data['current']['kind'],'observed')
        self.assertEqual(data['data_kind'],'observed')
        self.assertEqual(data['provider_status'],'unavailable')
        self.assertEqual(data['status'],'available')

    def test_scenario_baseline_is_identified_as_scenario_not_effect_estimate(self):
        async def fake_forecast(hours):
            return {"series":[{"timestamp":"2026-11-02T12:00","pm25":15,"source":"Open-Meteo"}]}
        with patch('app.main.forecast',side_effect=fake_forecast):
            response=self.client.post('/api/simulate',json={})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],'baseline_only')
        self.assertIn('not a validated',response.json()['label'])

    def test_scenario_api_refuses_unvalidated_intervention(self):
        response=self.client.post('/api/simulate',json={"emission_reduction":20})
        self.assertEqual(response.status_code,422)
        self.assertIn('causal model',response.json()['detail'])

    def test_empty_historical_inventory_is_explicit(self):
        response=self.client.get('/api/historical/coverage')
        self.assertEqual(response.status_code,200)
        data=response.json()
        self.assertEqual(data['records_loaded'],0)
        self.assertEqual(data['observations'],[])

    def test_future_measurements_are_rejected(self):
        future=(historical.today().replace(year=historical.today().year+1)).isoformat()
        csv=f"date,city,station,pm25,pm10\n{future},Delhi,Station,12,20\n"
        response=self.client.post('/api/historical/import',data={"source_name":"test"},files={"file":("daily.csv",csv,"text/csv")})
        self.assertEqual(response.status_code,200,response.text); self.assertEqual(response.json()['imported'],0); self.assertEqual(response.json()['rejected'],1)

if __name__=='__main__': unittest.main()
