import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.data import historical
from app.main import app

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

    def test_future_measurements_are_rejected(self):
        future=(historical.today().replace(year=historical.today().year+1)).isoformat()
        csv=f"date,city,station,pm25,pm10\n{future},Delhi,Station,12,20\n"
        response=self.client.post('/api/historical/import',data={"source_name":"test"},files={"file":("daily.csv",csv,"text/csv")})
        self.assertEqual(response.status_code,200,response.text); self.assertEqual(response.json()['imported'],0); self.assertEqual(response.json()['rejected'],1)

if __name__=='__main__': unittest.main()
