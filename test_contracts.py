"""Contract extraction tests, including corrupt text and stale source evidence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contracts import evaluate, extract, run, trace_finding
from gen.contracts import generate_contracts
from gen.synthetic import generate
from warehouse import connect, ingest


class ContractTests(unittest.TestCase):
    def test_extract_all_fields_and_trace_cap_findings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "generated"
            generate(data, count=120)
            self.assertEqual(generate_contracts(data), 50)
            db = connect(root / "warehouse.duckdb")
            try:
                ingest(db, data)
                result = run(db, data)
                score = evaluate(data / "contract_ground_truth.json", result)
                self.assertEqual(score["fields"], 300)
                self.assertEqual(score["correct_fields"], 300)
                self.assertEqual(score["cap_true_positives"], 2)
                self.assertEqual(score["cap_false_positives"], 0)
                finding = trace_finding(db, "DOC-CT-00010")
                self.assertEqual(finding["invoice_sources"][0]["values"]["id"],
                                 "INV-00010")
                self.assertEqual(finding["contract"]["source_text"], "717.03")
                path = data / "contracts" / "CT-00010.txt"
                path.write_text(path.read_text() + "Added clause.\n")
                with self.assertRaisesRegex(ValueError, "contract changed"):
                    trace_finding(db, "DOC-CT-00010")
            finally:
                db.close()

    def test_missing_or_ambiguous_field_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "CT-00000.txt"
            text = ("Vendor: Example\nPayment terms: Net 30 days.\n"
                    "Unit price: SGD 100.00.\nInvoice cap: SGD 110.00.\n"
                    "Termination notice: 30 days.\nAutomatic renewal: Yes.\n")
            path.write_text(text)
            self.assertEqual(extract(path)["fields"]["cap_cents"]["value"], 11000)
            path.write_text(text.replace("Invoice cap: SGD 110.00.\n", ""))
            with self.assertRaisesRegex(ValueError, "cap_cents"):
                extract(path)
            path.write_text(text + "Invoice cap: SGD 1.00.\n")
            with self.assertRaisesRegex(ValueError, "cap_cents"):
                extract(path)


if __name__ == "__main__":
    unittest.main()
