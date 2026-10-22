"""M8 acceptance checks: atomic batch publication, BI parity and integrity refusals."""

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import duckdb

import batches
import pipeline
import reporting
from dashboard import ages, load_state
from gen.synthetic import generate as real_generate


def _corrupting_generate(feed: str):
    """Generate normally, then break one feed's header so ingestion fails mid-batch."""
    def generate(directory, **kwargs):
        result = real_generate(directory, **kwargs)
        path = Path(directory) / f"{feed}.csv"
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        path.write_text("broken_header\n" + "".join(lines[1:]), encoding="utf-8")
        return result
    return generate


class BatchPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.root = Path(cls.folder.name) / "batches"
        cls.first = pipeline.run_batch(cls.root, keep=3)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_published_batch_is_complete_and_every_report_is_stamped(self):
        paths = batches.resolve_published(self.root)
        self.assertEqual(paths.batch_id, self.first["batch_id"])
        self.assertEqual(self.first["report"]["evaluation"]["correct"], 123)
        pointer = batches.read_pointer(self.root)
        self.assertEqual(pointer["as_of_source"], "latest ledger/bank/invoice business date")
        for name, digest in pointer["reports"].items():
            path = paths.reports / name
            self.assertEqual(batches.sha256_file(path), digest)
            stamp = json.loads(path.read_text(encoding="utf-8"))["batch"]
            self.assertEqual((stamp["batch_id"], stamp["status"]), (paths.batch_id, "complete"))

    def test_snapshot_counts_keys_and_cents_match_warehouse(self):
        paths = batches.resolve_published(self.root)
        db = duckdb.connect(str(paths.warehouse), read_only=True)
        try:
            parity = reporting.verify_snapshot(paths.snapshot, paths.batch_id, db)
            totals = reporting.warehouse_totals(db)
        finally:
            db.close()
        self.assertTrue(parity["warehouse_parity_checked"])
        counts = self.first["star_row_counts"]
        self.assertEqual(counts["fct_reconciliation"], 123)
        self.assertEqual(counts["fct_quality"], 74)
        for fmt in ("csv", "parquet"):
            rec = parity["formats"][fmt]["fct_reconciliation"]
            self.assertEqual(rec["rows"], totals["fct_reconciliation"]["rows"])
            self.assertEqual(rec["cents"], totals["fct_reconciliation"]["cents"])
            self.assertEqual(parity["formats"][fmt]["fct_finding"]["rows"],
                             totals["fct_finding"]["rows"])
            self.assertEqual(parity["formats"][fmt]["bridge_figure_source"]["rows"],
                             totals["bridge_figure_source"]["rows"])
        for fmt in ("csv", "parquet"):
            columns, rows = reporting.read_snapshot_table(paths.snapshot, "fct_reconciliation", fmt)
            keys = sorted(row[columns.index("figure_id")] for row in rows)
            self.assertEqual(keys, totals["fct_reconciliation"]["keys"])

    def test_view_state_shows_published_batch_without_errors(self):
        state = load_state(self.root, now=datetime.now(timezone.utc))
        self.assertTrue(state["ok"], state["errors"])
        self.assertEqual(state["paths"].batch_id, self.first["batch_id"])
        self.assertEqual(state["errors"], [])

    def test_stale_batch_is_flagged_but_still_shown(self):
        later = datetime.fromisoformat(batches.read_pointer(self.root)["completed_at"]) \
            + timedelta(hours=200)
        state = load_state(self.root, now=later, stale_hours=168)
        self.assertTrue(state["ok"])
        self.assertTrue(any("Stale batch" in warning for warning in state["warnings"]))


class FailedRefreshTests(unittest.TestCase):
    def test_failure_between_feeds_leaves_previous_batch_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = pipeline.run_batch(root, keep=3)
            with mock.patch.object(pipeline, "generate", _corrupting_generate("exchange")):
                with self.assertRaises(ValueError):
                    pipeline.run_batch(root, keep=3)
            self.assertEqual(batches.read_pointer(root)["batch_id"], first["batch_id"])
            attempts = batches.list_attempts(root)
            self.assertEqual([item["status"] for item in attempts], ["complete", "failed"])
            failed = batches.batch_paths(root, attempts[-1]["batch_id"])
            self.assertIn("expected header", attempts[-1]["error"])
            db = duckdb.connect(str(failed.warehouse), read_only=True)
            try:
                loaded = {row[0] for row in db.execute(
                    "select source_name from audit.load_manifest").fetchall()}
            finally:
                db.close()
            # The failure happened between feeds: ledger, bank and processor loaded first.
            self.assertEqual(loaded, {"ledger", "bank", "processor"})
            state = load_state(root)
            self.assertTrue(state["ok"], state["errors"])
            self.assertEqual(state["paths"].batch_id, first["batch_id"])
            self.assertEqual(state["refresh_status"], "latest refresh failed")
            self.assertTrue(any("failed" in warning for warning in state["warnings"]))
            self.assertEqual(batches.resolve_published(root).batch_id, first["batch_id"])

    def test_first_failed_run_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with mock.patch.object(pipeline, "generate", _corrupting_generate("bank")):
                with self.assertRaises(ValueError):
                    pipeline.run_batch(root, keep=3)
            self.assertIsNone(batches.read_pointer(root))
            with self.assertRaises(FileNotFoundError):
                batches.resolve_published(root)
            state = load_state(root)
            self.assertFalse(state["ok"])
            self.assertTrue(any("Latest failed attempt" in error for error in state["errors"]))


class IntegrityRefusalTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.result = pipeline.run_batch(self.root, keep=3)
        self.paths = batches.resolve_published(self.root)

    def tearDown(self):
        self.folder.cleanup()

    def test_modified_snapshot_file_is_rejected(self):
        target = self.paths.snapshot / "fct_reconciliation.csv"
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaises(reporting.SnapshotError):
            reporting.verify_snapshot(self.paths.snapshot, self.paths.batch_id)
        state = load_state(self.root)
        self.assertFalse(state["ok"])
        self.assertTrue(any("BI snapshot rejected" in error for error in state["errors"]))

    def test_snapshot_from_another_batch_is_rejected(self):
        with self.assertRaises(reporting.SnapshotError):
            reporting.verify_snapshot(self.paths.snapshot, "B20000101-000000-000000")

    def test_modified_report_blocks_all_figures(self):
        report = self.paths.reports / "quality_report.json"
        body = json.loads(report.read_text(encoding="utf-8"))
        body["passed"] = 0
        report.write_text(json.dumps(body), encoding="utf-8")
        state = load_state(self.root)
        self.assertFalse(state["ok"])
        self.assertIn("Report changed after publication: quality_report.json.", state["errors"])

    def test_explicit_as_of_date_drives_age_and_earlier_date_is_refused(self):
        as_of = date(2026, 10, 15)
        db = duckdb.connect(str(self.paths.warehouse))
        try:
            quality = json.loads((self.paths.reports / "quality_report.json").read_text())
            reporting.build_star(db, batch_id=self.paths.batch_id, status="complete",
                                 started_at=datetime.now(timezone.utc),
                                 completed_at=datetime.now(timezone.utc),
                                 as_of_date=as_of, quality=quality)
            rows = db.execute(
                "select age_days, posted_on from bi.fct_reconciliation").fetchall()
            with self.assertRaises(ValueError):
                reporting.build_star(db, batch_id=self.paths.batch_id, status="complete",
                                     started_at=datetime.now(timezone.utc),
                                     completed_at=datetime.now(timezone.utc),
                                     as_of_date=date(2026, 1, 1), quality=quality)
        finally:
            db.close()
        self.assertEqual(len(rows), 123)
        self.assertTrue(all(age == (as_of - posted).days for age, posted in rows))
        self.assertEqual(ages([date(2026, 10, 1)], as_of), [14])


class PointerHelperTests(unittest.TestCase):
    def test_batch_ids_and_paths_are_validated(self):
        self.assertRegex(batches.new_batch_id(datetime(2026, 10, 20, 1, 2, 3, 4, timezone.utc)),
                         r"^B20261020-010203-000004$")
        with self.assertRaises(ValueError):
            batches.new_batch_id(datetime(2026, 10, 20))
        with self.assertRaises(ValueError):
            batches.batch_paths(Path("/tmp"), "../escape")

    def test_publish_refuses_incomplete_batch_and_prune_keeps_published(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ids = [f"B2026102{day}-000000-000000" for day in (1, 2, 3)]
            for batch_id, status in zip(ids, ("complete", "failed", "running")):
                paths = batches.batch_paths(root, batch_id)
                paths.directory.mkdir()
                batches.write_manifest(paths, status=status)
            with self.assertRaises(ValueError):
                batches.publish(root, {"batch_id": ids[2]})
            batches.publish(root, {"batch_id": ids[0]})
            removed = batches.prune(root, keep=1)
            self.assertEqual(removed, [ids[1]])
            self.assertTrue((root / ids[0]).is_dir() and (root / ids[2]).is_dir())
            with self.assertRaises(ValueError):
                batches.write_manifest(batches.batch_paths(root, ids[0]), status="bogus")

    def test_cli_paths_need_both_explicit_paths_or_a_published_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                batches.cli_paths(Path("w.duckdb"), None)
            self.assertEqual(batches.cli_paths(Path("w.duckdb"), Path("d"), Path(folder)),
                             (Path("w.duckdb"), Path("d")))
            with self.assertRaises(FileNotFoundError):
                batches.cli_paths(None, None, Path(folder))


if __name__ == "__main__":
    unittest.main()
