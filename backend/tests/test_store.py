import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.data.store import validate_rows

class ValidationTests(unittest.TestCase):
    def test_rejects_invalid_and_negative_and_deduplicates(self):
        rows = [
            {"timestamp":"2025-01-01T00:00:00Z","pm25":"5","pm10":"10","station":"A"},
            {"timestamp":"2025-01-01T00:00:00Z","pm25":"9","pm10":"11","station":"A"},
            {"timestamp":"not-a-date","pm25":"1","pm10":"1"},
            {"timestamp":"2025-01-02T00:00:00Z","pm25":"-1","pm10":"1"},
        ]
        clean, rejected = validate_rows(rows)
        self.assertEqual(len(clean), 1)
        self.assertEqual(rejected, 2)
        self.assertEqual(clean[0]["pm25"], 5.0)

    def test_missing_pollutant_is_rejected(self):
        clean, rejected = validate_rows([{"timestamp":"2025-01-01T00:00:00Z","pm25":"2"}])
        self.assertEqual((clean, rejected), ([], 1))

if __name__ == "__main__": unittest.main()
