"""Weekly quality-runner tests for due time, reports and fail-closed behavior."""

from __future__ import annotations

import json
import os
import plistlib
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from gen.synthetic import generate
from scheduler import LABEL, ZONE, is_due, render_launchd, run_if_due, week_start
from warehouse import connect, ingest


class SchedulerTests(unittest.TestCase):
    def test_due_time_catches_up_once_per_singapore_week(self):
        monday_early = datetime(2026, 10, 12, 8, 59, tzinfo=ZONE)
        monday_nine = datetime(2026, 10, 12, 9, 0, tzinfo=ZONE)
        tuesday = datetime(2026, 10, 13, 10, 0, tzinfo=ZONE)
        self.assertEqual(week_start(tuesday), "2026-10-12")
        self.assertFalse(is_due(monday_early, None))
        self.assertTrue(is_due(monday_nine, None))
        self.assertTrue(is_due(tuesday, None))
        self.assertFalse(is_due(tuesday, "2026-10-12"))
        with self.assertRaises(ValueError):
            is_due(datetime(2026, 10, 13), None)

    def test_quality_pass_then_skip_then_fail_with_persisted_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "sources"
            generate(data, count=40)
            warehouse = root / "warehouse.duckdb"
            db = connect(warehouse)
            try:
                ingest(db, data)
            finally:
                db.close()
            now = datetime.now(ZONE)
            reports = root / "reports"
            first = run_if_due(warehouse, data, reports, now=now, force=True)
            self.assertEqual(first["status"], "pass")
            self.assertEqual(first["failed_checks"], 0)
            self.assertEqual(run_if_due(warehouse, data, reports, now=now)["status"],
                             "skipped")
            with (data / "bank.csv").open("a", encoding="utf-8") as file:
                file.write("\n")
            failed = run_if_due(warehouse, data, reports, now=now, force=True)
            self.assertEqual(failed["status"], "fail")
            self.assertGreater(failed["failed_checks"], 0)
            report = json.loads(Path(failed["report"]).read_text())
            self.assertIn("source_integrity:bank",
                          {item["name"] for item in report["quality"]["failures"]})
            state = json.loads((reports / "scheduler_state.json").read_text())
            self.assertEqual(state["last_status"], "fail")

    def test_missing_warehouse_reports_error_and_plist_is_reviewable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = run_if_due(root / "absent.duckdb", root, root / "reports",
                                now=datetime.now(ZONE), force=True)
            self.assertEqual(result["status"], "error")
            self.assertTrue(Path(result["report"]).is_file())
        project = Path(__file__).resolve().parent
        plist = plistlib.loads(render_launchd(project, Path(sys.executable)))
        self.assertEqual(plist["Label"], LABEL)
        self.assertEqual(plist["StartCalendarInterval"],
                         {"Weekday": 1, "Hour": 9, "Minute": 0})
        self.assertIn("run-if-due", plist["ProgramArguments"])
        self.assertEqual(plist["ProgramArguments"][0], os.path.abspath(sys.executable))


if __name__ == "__main__":
    unittest.main()
