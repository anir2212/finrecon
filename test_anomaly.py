"""Optional anomaly-screen invariants on the seeded local journal fixture."""

import tempfile
import unittest
from pathlib import Path

from anomaly_detection import evaluate, screen
from gen.synthetic import generate
from warehouse import connect, ingest, trace


class AnomalyTests(unittest.TestCase):
    def test_seeded_outliers_are_reported_with_source_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "sources"
            generate(data, count=120)
            db = connect(root / "warehouse.duckdb")
            try:
                ingest(db, data)
                result = screen(db)
                score = evaluate(data / "anomaly_ground_truth.json", result)
                if result["status"] == "unavailable":
                    self.assertEqual(score["status"], "unmeasured")
                    return
                self.assertEqual(result["rows_scored"], 120)
                self.assertEqual(score["injected"], 3)
                self.assertEqual(score["tp"] + score["fn"], 3)
                self.assertEqual(score["tp"] + score["fp"], score["flagged"])
                self.assertTrue(all(len(trace(db, item["figure_id"])) == 1
                                    for item in result["findings"]))
            finally:
                db.close()

    def test_contamination_rejects_invalid_ranges(self):
        with tempfile.TemporaryDirectory() as folder:
            db = connect(Path(folder) / "warehouse.duckdb")
            try:
                with self.assertRaises(ValueError):
                    screen(db, contamination=1)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
