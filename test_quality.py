"""Mutation tests: each control must catch a realistic bad delivery or posting."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gen.synthetic import generate
from quality_checks import run_checks
from warehouse import connect, ingest


class QualityChecksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "generated"
        generate(self.data, count=40)
        self.db = connect(self.root / "warehouse.duckdb")
        self.addCleanup(self.db.close)
        ingest(self.db, self.data)

    def names(self):
        return {item["name"] for item in run_checks(self.db, self.data)["failures"]}

    def test_clean_fixture_passes_all_checks(self):
        result = run_checks(self.db, self.data)
        self.assertEqual(result["status"], "pass")
        self.assertGreater(result["checks"], 60)

    def test_missing_daily_file_and_source_mutation_fail(self):
        (self.data / "daily_ledger" / "ledger-2026-09-03.csv").unlink()
        self.assertIn("daily_delivery:2026-09-03", self.names())
        with (self.data / "bank.csv").open("a", encoding="utf-8") as file:
            file.write("\n")
        self.assertIn("source_integrity:bank", self.names())

    def test_bad_processor_fee_and_gl_control_fail(self):
        self.db.execute("update raw.processor set fee_cents = '99999' where id = 'PS-00000'")
        self.db.execute("update raw.processor set gross_cents = '1' where id = 'PS-00003'")
        names = self.names()
        self.assertIn("processor_fee_identity", names)
        self.assertIn("processor_gl_control", names)

    def test_orphan_and_over_cap_invoice_fail(self):
        self.db.execute("update raw.invoices set po_id = 'MISSING' where id = 'INV-00000'")
        self.db.execute("update raw.invoices set amount_cents = '9999999' where id = 'INV-00001'")
        self.assertIn("invoice_po_integrity", self.names())

    def test_stale_load_and_bad_sign_fail(self):
        old = datetime.now(timezone.utc) - timedelta(days=4)
        self.db.execute("update audit.load_manifest set loaded_at = ? where source_name = 'ledger'", [old])
        self.db.execute("update raw.ledger set amount_cents = '50' where id = 'GL-00000'")
        names = self.names()
        self.assertIn("freshness:ledger", names)
        self.assertIn("sign:ledger:amount_cents", names)

    def test_bad_date_and_missing_rows_fail(self):
        self.db.execute("update raw.bank set posted_on = 'yesterday' where id = 'BK-00001'")
        self.db.execute("delete from raw.ledger where id = 'GL-00003'")
        names = self.names()
        self.assertIn("typed_required:bank", names)
        self.assertIn("row_count:ledger", names)
        self.assertIn("daily_delivery:2026-09-04", names)

    def test_bad_posting_instant_and_account_pair_fail(self):
        self.db.execute("update raw.ledger set posted_at = 'invalid' where id = 'GL-00000'")
        self.db.execute("update raw.ledger set contra_account = account where id = 'GL-00001'")
        names = self.names()
        self.assertIn("ledger_posting_timestamp", names)
        self.assertIn("ledger_account_pair", names)


if __name__ == "__main__":
    unittest.main()
