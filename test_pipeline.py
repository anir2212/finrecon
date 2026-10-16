import csv
import tempfile
import unittest
from pathlib import Path

from gen.synthetic import generate
from pipeline import run
from reconcile_engine import reconcile
from warehouse import connect, ingest, stage_counts, trace


class WarehousePipelineTests(unittest.TestCase):
    def test_all_feeds_stage_without_loss_and_ten_figures_trace(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = run(root / "generated", root / "warehouse.duckdb",
                         root / "reports", seed=17, count=120)
            self.assertEqual(report["evaluation"]["rows"], 123)
            self.assertEqual(report["evaluation"]["correct"], 123)
            self.assertEqual(report["contract_evaluation"]["correct_fields"], 300)
            self.assertEqual(report["copilot_evaluation"]["fact_correct"], 25)
            self.assertEqual(report["copilot_evaluation"]["citation_correct"], 25)
            self.assertEqual(report["quality"]["checks"], 74)
            self.assertEqual(report["audit_evaluation"]["true_positives"], 5)
            self.assertEqual(report["audit_edge_evaluation"]["audit_evaluation"]["true_positives"], 2)
            self.assertEqual(report["traced_figure_count"], 10)
            self.assertTrue(all(pair["raw"] == pair["staged"]
                                for pair in report["stage_counts"].values()))
            db = connect(root / "warehouse.duckdb")
            try:
                evidence = trace(db, "REC-PAY-00001")
                self.assertEqual({item["source_name"] for item in evidence},
                                 {"ledger", "bank"})
                self.assertEqual({(item["source_name"], item["source_row"])
                                  for item in evidence}, {("ledger", 3), ("bank", 2)})
                self.assertEqual(stage_counts(db)["ledger"], (120, 120))
            finally:
                db.close()

    def test_bad_amount_fails_loudly_and_reingest_invalidates_lineage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "generated"
            generate(data, count=40)
            db = connect(root / "warehouse.duckdb")
            try:
                ingest(db, data)
                reconcile(db)
                self.assertEqual(len(trace(db, "REC-PAY-00001")), 2)
                path = data / "bank.csv"
                with path.open(newline="") as file:
                    rows = list(csv.DictReader(file))
                rows[0]["amount_cents"] = "not-a-number"
                with path.open("w", newline="") as file:
                    writer = csv.DictWriter(file, fieldnames=rows[0])
                    writer.writeheader()
                    writer.writerows(rows)
                with self.assertRaisesRegex(ValueError, "source file changed"):
                    trace(db, "REC-PAY-00001")
                ingest(db, data)
                with self.assertRaises(KeyError):
                    trace(db, "REC-PAY-00001")
                with self.assertRaisesRegex(ValueError, "invalid amount"):
                    reconcile(db)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
