"""Tableau Hyper extract of one published batch's verified BI snapshot.

Power BI Desktop does not run on macOS, so the analyst-tool path is Tableau.
This module writes a multi-table ``.hyper`` extract with Tableau's own Hyper
API (optional dependency, ``requirements-tableau.txt``), then reopens it and
checks every table's row count, cent totals, figure keys and batch IDs against
the snapshot manifest. It does not build or render a Tableau workbook; the
relationships to draw in Tableau are listed in ``RELATIONSHIPS``.

The default ``summary`` audience leaves out the source-link bridge (see
``access.py``); ``reviewer`` includes it for drill-through.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from access import SOURCE_TABLES
from batches import DEFAULT_ROOT, atomic_write_json, resolve_published, sha256_file
from reporting import CENTS, SNAPSHOT_MANIFEST, read_snapshot_table, verify_snapshot


DEFAULT_OUT = Path(__file__).resolve().parent / "data" / "tableau"
SCHEMA = "Extract"
RELATIONSHIPS = (
    ("dim_status.status_key", "fct_reconciliation.status_key"),
    ("dim_date.date_key", "fct_reconciliation.date_key"),
    ("dim_date.date_key", "fct_finding.date_key"),
    ("dim_finding_type.finding_type_key", "fct_finding.finding_type_key"),
    ("dim_batch.batch_id", "fct_reconciliation.batch_id"),
    ("dim_batch.batch_id", "fct_finding.batch_id"),
    ("dim_batch.batch_id", "fct_quality.batch_id"),
)
BRIDGE_RELATIONSHIP = ("fct_reconciliation.figure_id", "bridge_figure_source.figure_id")


def tables_for(audience: str, manifest: dict) -> list[str]:
    if audience not in ("summary", "reviewer"):
        raise ValueError(f"unknown audience: {audience}")
    return [table for table in manifest["tables"]
            if audience == "reviewer" or table not in SOURCE_TABLES]


def _literal(path: Path) -> str:
    return "'" + path.as_posix().replace("'", "''") + "'"


def write_extract(snapshot: Path, batch_id: str, out_dir: Path,
                  audience: str = "summary") -> dict:
    """Write and verify ``finrecon_<audience>.hyper`` for one verified snapshot."""
    snapshot, out_dir = Path(snapshot), Path(out_dir)
    verify_snapshot(snapshot, batch_id)
    manifest = json.loads((snapshot / SNAPSHOT_MANIFEST).read_text(encoding="utf-8"))
    tables = tables_for(audience, manifest)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"finrecon_{audience}.hyper"
    partial = out_dir / f".{target.name}.partial"
    partial.unlink(missing_ok=True)
    try:
        checked = _build_and_verify(snapshot, manifest, tables, partial, batch_id)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(target)
    result = {
        "batch_id": batch_id, "audience": audience,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hyper_file": target.name, "hyper_sha256": sha256_file(target),
        "source_snapshot_manifest_sha256": sha256_file(snapshot / SNAPSHOT_MANIFEST),
        "schema": SCHEMA, "tables": checked,
        "excluded_tables": [table for table in manifest["tables"] if table not in tables],
        "relationships": [list(pair) for pair in RELATIONSHIPS]
        + ([list(BRIDGE_RELATIONSHIP)] if audience == "reviewer" else []),
        "money": "integer SGD cents; divide by 100 only in a Tableau calculated field",
        "workbook": "not built; open the .hyper in Tableau and draw the listed relationships",
    }
    atomic_write_json(out_dir / f"tableau_manifest_{audience}.json", result)
    return result


def _build_and_verify(snapshot: Path, manifest: dict, tables: list[str], partial: Path,
                      batch_id: str) -> dict:
    """Load each Parquet table into Hyper, then reopen the file and check it."""
    from tableauhyperapi import Connection, CreateMode, HyperProcess, Telemetry

    checked = {}
    with tempfile.TemporaryDirectory() as logs:
        with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU,
                          parameters={"log_dir": logs}) as hyper:
            with Connection(hyper.endpoint, partial, CreateMode.CREATE_AND_REPLACE) as db:
                db.execute_command(f'create schema "{SCHEMA}"')
                for table in tables:
                    source = snapshot / manifest["tables"][table]["parquet"]["file"]
                    db.execute_command(
                        f'create table "{SCHEMA}"."{table}" as '
                        f"(select * from external({_literal(source)}))")
            with Connection(hyper.endpoint, partial) as db:
                for table in tables:
                    checked[table] = _verify_table(db, snapshot, table,
                                                   manifest["tables"][table], batch_id)
    return checked


def _verify_table(db, snapshot: Path, table: str, entry: dict, batch_id: str) -> dict:
    from tableauhyperapi import TableName

    name = f'"{SCHEMA}"."{table}"'
    rows = db.execute_scalar_query(f"select count(*) from {name}")
    if rows != entry["rows"]:
        raise ValueError(f"{table}: Hyper has {rows} rows, manifest {entry['rows']}")
    columns, snapshot_rows = read_snapshot_table(snapshot, table)
    hyper_columns = [column.name.unescaped for column in
                     db.catalog.get_table_definition(TableName(SCHEMA, table)).columns]
    if hyper_columns != columns:
        raise ValueError(f"{table}: Hyper columns {hyper_columns} differ from {columns}")
    cents = {column: int(db.execute_scalar_query(
        f'select coalesce(sum("{column}"), 0) from {name}')) for column in CENTS.get(table, ())}
    if cents != entry.get("cents", {}):
        raise ValueError(f"{table}: Hyper cents {cents} differ from manifest")
    result = {"rows": rows, **({"cents": cents} if cents else {})}
    if "figure_id" in columns and table.startswith("fct_"):
        keys = sorted(row[0] for row in db.execute_list_query(
            f'select "figure_id" from {name}'))
        if keys != sorted(row[columns.index("figure_id")] for row in snapshot_rows):
            raise ValueError(f"{table}: Hyper figure keys differ from the snapshot")
        result["figure_keys_match"] = True
    if "batch_id" in columns:
        others = db.execute_scalar_query(
            f'select count(*) from {name} where "batch_id" is distinct from '
            + "'" + batch_id.replace("'", "''") + "'")
        if others:
            raise ValueError(f"{table}: {others} rows from another batch")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batch-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--audience", choices=["summary", "reviewer"], default="summary")
    parser.add_argument("--out", type=Path,
                        help="Output directory (default data/tableau/<batch_id>)")
    args = parser.parse_args()
    paths = resolve_published(args.batch_root)
    out = args.out or DEFAULT_OUT / paths.batch_id
    summary = write_extract(paths.snapshot, paths.batch_id, out, args.audience)
    print(json.dumps({key: summary[key] for key in
                      ("batch_id", "audience", "hyper_file", "excluded_tables")}
                     | {"out": str(out), "tables": {table: value["rows"] for table, value
                                                    in summary["tables"].items()}}, indent=2))
