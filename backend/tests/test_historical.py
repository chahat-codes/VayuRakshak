import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.main import app
class RetiredFeatureTests(unittest.TestCase):
    def test_retired_diwali_workflow_is_not_exposed(self):
        client=TestClient(app)
        for path in ('/api/historical/coverage','/api/historical/import','/api/historical/compare','/api/simulate'):
            self.assertEqual(client.get(path).status_code,404,path)
        self.assertEqual(client.post('/api/historical/import').status_code,404)
