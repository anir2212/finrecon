"""Check dbt-compatible SQL against the active DuckDB staging and audit data."""

import tempfile
import unittest
from pathlib import Path

from audit_analytics import screen
from gen.synthetic import generate
from reconcile_engine import reconcile
from verify_dbt_models import verify
from warehouse import connect, ingest


class DbtSqlTests(unittest.TestCase):
    def test_all_models_preserve_rows_and_column_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "sources"
            generate(data, count=40)
            db = connect(root / "warehouse.duckdb")
            try:
                ingest(db, data)
                reconcile(db)
                screen(db)
                result = verify(db, Path(__file__).resolve().parent)
                self.assertEqual(result["staging_models"], 6)
                self.assertEqual(result["mart_models"], 2)
                self.assertEqual(result["documented_columns"], 124)
                self.assertGreater(result["staging_rows_verified"], 40)
                self.assertFalse(result["dbt_cli_executed"])
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
