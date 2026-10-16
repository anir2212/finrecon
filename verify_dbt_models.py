"""Execute dbt model SQL through DuckDB while the dbt adapter is unavailable.

Only ``source('raw', name)`` Jinja calls are translated. This checks SQL
semantics and parity with the active staging views, not dbt compilation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb

from data_dictionary import MARTS, render_dbt_schema
from gen.synthetic import HEADERS


SOURCE_CALL = re.compile(r"\{\{\s*source\('raw',\s*'([a-z_]+)'\)\s*\}\}")


def _translate(sql: str) -> str:
    def source(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in HEADERS:
            raise ValueError(f"unknown dbt source: {name}")
        return f"raw.{name}"

    result = SOURCE_CALL.sub(source, sql)
    if "{{" in result or "{%" in result:
        raise ValueError("unsupported Jinja in local SQL verification")
    return result


def _rows(db: duckdb.DuckDBPyConnection, query: str) -> tuple[list[str], list[tuple]]:
    result = db.execute(query)
    return [column[0] for column in result.description], result.fetchall()


def verify(db: duckdb.DuckDBPyConnection, root: Path) -> dict:
    root = Path(root)
    schema = (root / "models/schema.yml").read_text(encoding="utf-8")
    if schema != render_dbt_schema():
        raise ValueError("models/schema.yml is out of sync with data_dictionary.py")
    staging_rows = 0
    for name in HEADERS:
        model = f"stg_{name}"
        sql = _translate((root / "models/staging" / f"{model}.sql").read_text(
            encoding="utf-8"))
        db.execute(f"create or replace temporary view verify_{model} as {sql}")
        actual = _rows(db, f"select * from verify_{model} order by id")
        expected = _rows(db, f"select * from staging.{name} order by id")
        if actual != expected:
            raise ValueError(f"{model} differs from active staging view")
        staging_rows += len(actual[1])
    mart_rows = 0
    for name in MARTS:
        sql = _translate((root / "models/marts" / f"{name}.sql").read_text(
            encoding="utf-8"))
        db.execute(f"create or replace temporary view verify_{name} as {sql}")
        actual = _rows(db, f"select * from verify_{name} order by figure_id")
        source = ("audit.reconciliation" if name == "fct_reconciliation"
                  else "audit.findings")
        columns = ", ".join(MARTS[name]["columns"])
        expected = _rows(db, f"select {columns} from {source} order by figure_id")
        if actual != expected:
            raise ValueError(f"{name} differs from audit source")
        mart_rows += len(actual[1])
    column_docs = (sum(len(headers) for headers in HEADERS.values())
                   + sum(len(headers) + 4 for headers in HEADERS.values())
                   + sum(len(mart["columns"]) for mart in MARTS.values()))
    return {"staging_models": len(HEADERS), "mart_models": len(MARTS),
            "staging_rows_verified": staging_rows, "mart_rows_verified": mart_rows,
            "documented_columns": column_docs, "dbt_cli_executed": False,
            "scope": "DuckDB execution of model SQL after translating source() only"}


if __name__ == "__main__":
    from warehouse import connect
    db = connect(Path("data/finrecon.duckdb"))
    try:
        report = verify(db, Path(__file__).resolve().parent)
        Path("reports").mkdir(exist_ok=True)
        (Path("reports") / "dbt_sql_verification.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        db.close()
