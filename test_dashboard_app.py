"""In-process Streamlit checks of the batch review screen (skipped without Streamlit)."""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pipeline
from batches import resolve_published
from test_batches import _corrupting_generate

HAS_STREAMLIT = importlib.util.find_spec("streamlit") is not None
APP = str(Path(__file__).resolve().parent / "dashboard.py")


@unittest.skipUnless(HAS_STREAMLIT, "install requirements-dashboard.txt to run the AppTest")
class DashboardAppTests(unittest.TestCase):
    def _app(self, root: Path, role: str = "reviewer"):
        """Render with the batch root and role set for this and every later rerun."""
        from streamlit.testing.v1 import AppTest
        patcher = mock.patch.dict(os.environ, {"FINRECON_BATCH_ROOT": str(root),
                                               "FINRECON_ROLE": role})
        patcher.start()
        self.addCleanup(patcher.stop)
        return AppTest.from_file(APP, default_timeout=60).run()

    def test_published_batch_renders_and_source_rows_verify(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            batch = pipeline.run_batch(root, keep=3)["batch_id"]
            app = self._app(root)
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertTrue(any(batch in item.value for item in app.markdown))
            self.assertGreaterEqual(len(app.dataframe), 4)
            figure = app.selectbox[-1].value
            self.assertTrue(any(f"**Selected** `{figure}`" in item.value
                                for item in app.markdown))
            app.button[0].click().run()
            self.assertTrue(any("Source hashes match" in item.value for item in app.success))

    def test_changed_source_file_is_rejected_on_drill_through(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pipeline.run_batch(root, keep=3)
            app = self._app(root)
            figure = app.selectbox[-1].value
            data_dir = resolve_published(root).data_dir
            for name in ("ledger.csv", "bank.csv"):
                path = data_dir / name
                path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            app.selectbox[-1].select(figure).run()
            app.button[0].click().run()
            self.assertTrue(any("Evidence rejected" in item.value for item in app.error))

    def test_failed_refresh_shows_previous_batch_with_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = pipeline.run_batch(root, keep=3)["batch_id"]
            with mock.patch.object(pipeline, "generate", _corrupting_generate("exchange")):
                with self.assertRaises(ValueError):
                    pipeline.run_batch(root, keep=3)
            app = self._app(root)
            self.assertFalse(app.exception)
            self.assertTrue(any("failed" in item.value and first in item.value
                                for item in app.warning))

    def test_no_published_batch_shows_error_and_no_figures(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self._app(Path(folder))
            self.assertFalse(app.exception)
            self.assertTrue(any("No published batch" in item.value for item in app.error))
            self.assertEqual(len(app.dataframe), 0)

    def test_summary_role_sees_figures_but_no_source_links_or_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pipeline.run_batch(root, keep=3)
            reviewer = self._app(root)
            summary = self._app(root, role="summary")
            self.assertFalse(summary.exception)
            self.assertTrue(any("role:** summary" in item.value for item in summary.markdown))
            self.assertEqual(len(summary.button), 0)
            self.assertEqual(len(summary.dataframe), len(reviewer.dataframe) - 1)
            self.assertFalse(any("fixture labels" in box.label for box in summary.checkbox))
            self.assertTrue(any("does not include source" in item.value for item in summary.info))

    def test_plain_tables_replace_canvas_grids_with_full_text_tables(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pipeline.run_batch(root, keep=3)
            app = self._app(root)
            grids = len(app.dataframe)
            self.assertEqual(len(app.table), 1)  # the aged-break overview is always plain
            box = next(box for box in app.checkbox if box.label.startswith("Plain tables"))
            box.check().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.dataframe), 0)
            self.assertEqual(len(app.table), 1 + grids)
            breaks = app.table[1].value
            published = resolve_published(root)
            from reporting import read_snapshot_table
            columns, rows = read_snapshot_table(published.snapshot, "fct_reconciliation")
            expected = sum(bool(dict(zip(columns, row))["is_break"]) for row in rows)
            self.assertEqual(len(breaks), expected)

    def test_unknown_role_is_denied_before_any_figure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pipeline.run_batch(root, keep=3)
            app = self._app(root, role="admin")
            self.assertFalse(app.exception)
            self.assertTrue(any("Access denied" in item.value for item in app.error))
            self.assertEqual((len(app.dataframe), len(app.metric)), (0, 0))


if __name__ == "__main__":
    unittest.main()
