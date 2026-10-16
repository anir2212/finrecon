"""Audit screens must preserve evidence and honest false-positive accounting."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from audit_analytics import evaluate, screen
from gen.synthetic import generate
from warehouse import connect, ingest, trace


class AuditAnalyticsTests(unittest.TestCase):
    def test_injected_cases_are_found_with_lineage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "generated"
            generate(data, count=120)
            db = connect(root / "warehouse.duckdb")
            try:
                ingest(db, data)
                result = screen(db)
                score = evaluate(data / "audit_ground_truth.json", result["findings"])
                self.assertEqual(score["injected"], 5)
                self.assertEqual(score["true_positives"], 5)
                self.assertEqual(score["false_positives"], 1)
                self.assertEqual(score["false_negatives"], 0)
                duplicate = next(item for item in result["findings"]
                                 if item["kind"] == "duplicate_payment")
                evidence = trace(db, duplicate["figure_id"])
                self.assertEqual({item["values"]["id"] for item in evidence},
                                 {"GL-00040", "GL-00041"})
                self.assertEqual(duplicate["amount_cents"], 100_000)
                self.assertEqual(result["benford"]["n"], 120)
                self.assertGreater(len(result["weekend_posting_ids"]), 0)
                self.assertEqual(result["after_hours_posting_ids"], ["GL-00046"])
                self.assertEqual(screen(db)["findings"], result["findings"])
            finally:
                db.close()

    def test_threshold_inputs_are_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            db = connect(Path(folder) / "warehouse.duckdb")
            try:
                with self.assertRaises(ValueError):
                    screen(db, near_band_cents=100_000)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
