import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.main import app
from app.data import historical

class HistoricalApiTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls): cls.client=TestClient(app)
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.original=historical.DB; historical.DB=Path(self.tmp.name)/'hist.sqlite3'; historical.init()
 def tearDown(self): historical.DB=self.original; self.tmp.cleanup()
 def test_coverage_and_diwali_comparison_use_imported_records_only(self):
  empty=self.client.get('/api/historical/coverage').json(); self.assertEqual(empty['records_loaded'],0)
  csv='date,city,station,pm25,pm10,aqi\n2022-10-21,Delhi,Central,20,40,80\n2022-10-22,Delhi,Central,22,42,82\n2022-10-23,Delhi,Central,24,45,85\n2022-10-24,Delhi,Central,80,100,200\n2022-10-25,Delhi,Central,70,90,180\n2022-10-26,Delhi,Central,50,70,120\n2022-10-27,Delhi,Central,30,50,90\n2023-11-09,Delhi,Central,20,40,80\n2023-11-10,Delhi,Central,20,40,80\n2023-11-11,Delhi,Central,20,40,80\n2023-11-12,Delhi,Central,40,60,120\n2023-11-13,Delhi,Central,30,50,100\n2023-11-14,Delhi,Central,25,45,90\n2023-11-15,Delhi,Central,20,40,80\n'
  result=self.client.post('/api/historical/import',data={'source_name':'Test station feed'},files={'file':('air.csv',csv,'text/csv')})
  self.assertEqual(result.status_code,200,result.text); self.assertEqual(result.json()['imported'],14)
  compare=self.client.get('/api/historical/diwali',params={'city':'Delhi','station':'Central','years':'2022,2023','days':3})
  self.assertEqual(compare.status_code,200,compare.text); data=compare.json(); self.assertEqual([x['year'] for x in data['results']],[2022,2023]); self.assertIsNotNone(data['results'][0]['windows']['during']['metrics']['pm25']['mean'])
 def test_date_coverage_marks_2026_upcoming(self):
  dates=self.client.get('/api/historical/diwali-dates').json(); self.assertEqual(next(x for x in dates['dates'] if x['year']==2026)['status'],'upcoming')
