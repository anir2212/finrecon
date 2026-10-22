"""Tableau Hyper extract checks (skipped without tableauhyperapi)."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pipeline
from batches import resolve_published, sha256_file
from reporting import SNAPSHOT_MANIFEST, SnapshotError

HAS_HYPER = importlib.util.find_spec("tableauhyperapi") is not None


@unittest.skipUnless(HAS_HYPER, "install requirements-tableau.txt to build Hyper extracts")
class TableauExtractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.root = Path(cls.folder.name) / "batches"
        pipeline.run_batch(cls.root, keep=3)
        cls.paths = resolve_published(cls.root)
        cls.manifest = json.loads((cls.paths.snapshot / SNAPSHOT_MANIFEST).read_text())

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_summary_extract_matches_snapshot_and_has_no_source_links(self):
        from tableau_export import write_extract

        out = Path(self.folder.name) / "summary"
        result = write_extract(self.paths.snapshot, self.paths.batch_id, out)
        self.assertEqual(result["excluded_tables"], ["bridge_figure_source"])
        self.assertNotIn("bridge_figure_source", result["tables"])
        for table, checked in result["tables"].items():
            entry = self.manifest["tables"][table]
            self.assertEqual(checked["rows"], entry["rows"])
            self.assertEqual(checked.get("cents", {}), entry.get("cents", {}))
        self.assertTrue(result["tables"]["fct_reconciliation"]["figure_keys_match"])
        self.assertEqual(result["hyper_sha256"], sha256_file(out / "finrecon_summary.hyper"))
        saved = json.loads((out / "tableau_manifest_summary.json").read_text())
        self.assertEqual(saved["batch_id"], self.paths.batch_id)
        self.assertIn("not built", saved["workbook"])

    def test_reviewer_extract_includes_bridge_relationship(self):
        from tableau_export import write_extract

        out = Path(self.folder.name) / "reviewer"
        result = write_extract(self.paths.snapshot, self.paths.batch_id, out, "reviewer")
        self.assertEqual(result["tables"]["bridge_figure_source"]["rows"],
                         self.manifest["tables"]["bridge_figure_source"]["rows"])
        self.assertIn(["fct_reconciliation.figure_id", "bridge_figure_source.figure_id"],
                      result["relationships"])

    def test_modified_snapshot_is_refused_and_leaves_no_file(self):
        from tableau_export import write_extract

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pipeline.run_batch(root, keep=3)
            paths = resolve_published(root)
            target = paths.snapshot / "fct_quality.parquet"
            target.write_bytes(target.read_bytes() + b"\0")
            with self.assertRaises(SnapshotError):
                write_extract(paths.snapshot, paths.batch_id, root / "out")
            self.assertEqual(list((root / "out").glob("*")) if (root / "out").exists() else [], [])

    def test_unknown_audience_is_refused(self):
        from tableau_export import tables_for

        with self.assertRaises(ValueError):
            tables_for("everyone", self.manifest)


if __name__ == "__main__":
    unittest.main()
