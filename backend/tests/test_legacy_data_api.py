import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.main import app
from app.data import store

class LegacyDataCompatibilityTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.client=TestClient(app)
 def test_observation_import_history_export_and_model_status_routes_remain(self):
  with tempfile.TemporaryDirectory() as folder:
   with unittest.mock.patch.object(store,'DATA',Path(folder)/'observations.csv'):
    csv='timestamp,pm25,pm10,station\n2026-10-20T12:00:00+05:30,12.5,20,Test station\n'
    imported=self.client.post('/api/data/import',files={'file':('obs.csv',csv,'text/csv')})
    self.assertEqual(imported.status_code,200,imported.text);self.assertEqual(imported.json()['imported'],1)
    hist=self.client.get('/api/air-quality/history',params={'station':'Test station'}).json()
    self.assertEqual(hist['count'],1);self.assertEqual(hist['records'][0]['pm25'],'12.5')
    self.assertEqual(self.client.get('/api/reports/export').status_code,200)
  self.assertEqual(self.client.get('/api/model/performance').status_code,200)
  self.assertEqual(self.client.get('/api/model/explanations').status_code,200)
  self.assertEqual(self.client.get('/api/data-sources').status_code,200)
