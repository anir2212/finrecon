"""Role checks for the analyst view and the summary snapshot export."""

import json
import tempfile
import unittest
from pathlib import Path

import pipeline
from access import (SOURCE_ROWS, TOKEN_HEADER, AccessDenied, export_summary_snapshot,
                    resolve_access)
from batches import resolve_published
from dashboard import evidence_for
from reporting import SNAPSHOT_MANIFEST, SnapshotError, verify_snapshot

PROXY = {"FINRECON_ROLE_HEADER": "X-Forwarded-Role", "FINRECON_PROXY_TOKEN": "s3cret",
         "FINRECON_USER_HEADER": "X-Forwarded-User"}


class RoleResolutionTests(unittest.TestCase):
    def test_local_mode_defaults_to_least_privilege(self):
        access = resolve_access({}, {})
        self.assertEqual((access.role, access.mode), ("summary", "local"))
        self.assertTrue(access.can("figures"))
        self.assertFalse(access.can(SOURCE_ROWS))
        self.assertTrue(resolve_access({}, {"FINRECON_ROLE": "reviewer"}).can(SOURCE_ROWS))

    def test_unknown_local_role_is_denied(self):
        access = resolve_access({}, {"FINRECON_ROLE": "admin"})
        self.assertFalse(access.allowed)
        self.assertFalse(access.can("figures"))

    def test_proxy_mode_requires_the_shared_token(self):
        headers = {"x-forwarded-role": "reviewer", "x-forwarded-user": "ana"}
        self.assertFalse(resolve_access(headers, PROXY).allowed)
        wrong = {**headers, TOKEN_HEADER: "guess"}
        self.assertFalse(resolve_access(wrong, PROXY).allowed)
        access = resolve_access({**headers, TOKEN_HEADER.lower(): "s3cret"}, PROXY)
        self.assertEqual((access.role, access.user, access.mode), ("reviewer", "ana", "proxy"))

    def test_proxy_mode_ignores_local_role_and_denies_missing_role(self):
        env = {**PROXY, "FINRECON_ROLE": "reviewer"}
        access = resolve_access({TOKEN_HEADER: "s3cret"}, env)
        self.assertFalse(access.allowed)
        self.assertIn("no FinRecon role", access.reason)
        self.assertFalse(resolve_access({TOKEN_HEADER: "s3cret", "X-Forwarded-Role": "x"},
                                        env).allowed)

    def test_proxy_mode_without_configured_token_denies_everyone(self):
        env = {"FINRECON_ROLE_HEADER": "X-Forwarded-Role"}
        access = resolve_access({TOKEN_HEADER: "", "X-Forwarded-Role": "reviewer"}, env)
        self.assertFalse(access.allowed)


class EnforcementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.root = Path(cls.folder.name) / "batches"
        pipeline.run_batch(cls.root, keep=3)
        cls.paths = resolve_published(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_summary_role_cannot_read_source_rows(self):
        with self.assertRaises(AccessDenied):
            evidence_for("REC-PAY-00001", self.paths.warehouse, resolve_access({}, {}))
        evidence = evidence_for("REC-PAY-00001", self.paths.warehouse,
                                resolve_access({}, {"FINRECON_ROLE": "reviewer"}))
        self.assertTrue(evidence)

    def test_summary_export_drops_source_links_and_still_verifies(self):
        out = Path(self.folder.name) / "summary"
        summary = export_summary_snapshot(self.paths.snapshot, out, self.paths.batch_id)
        self.assertEqual(summary["audience"], "summary")
        self.assertNotIn("bridge_figure_source", summary["tables"])
        self.assertFalse(any(path.stem == "bridge_figure_source" for path in out.iterdir()))
        verify_snapshot(out, self.paths.batch_id)
        full = json.loads((self.paths.snapshot / SNAPSHOT_MANIFEST).read_text())
        for table in summary["tables"]:
            self.assertEqual(summary["tables"][table], full["tables"][table])
        with self.assertRaises(FileExistsError):
            export_summary_snapshot(self.paths.snapshot, out, self.paths.batch_id)

    def test_summary_export_refuses_a_modified_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pipeline.run_batch(root, keep=3)
            paths = resolve_published(root)
            target = paths.snapshot / "fct_finding.parquet"
            target.write_bytes(target.read_bytes() + b"\0")
            with self.assertRaises(SnapshotError):
                export_summary_snapshot(paths.snapshot, root / "out", paths.batch_id)
            self.assertFalse((root / "out").exists())


if __name__ == "__main__":
    unittest.main()
