"""Read-only, BI-ready (Tableau or Power BI) star model and file snapshots for one batch.

The ``bi`` schema is derived from persisted warehouse facts; it never
recomputes a reconciliation decision. Grains:

* ``fct_reconciliation``: one row per ``REC-`` figure.
* ``fct_finding``: one row per review finding (rule ``AUD-``, optional model
  ``ANOM-``, contract ``DOC-``). Amounts are reviewed amounts and are not
  additive across findings, because one ledger row can appear in several.
* ``fct_quality``: one row per control outcome of the batch's quality run.
* ``bridge_figure_source``: one row per figure/source-row link. It carries no
  money, so joining it for drill-through cannot double totals.
* ``dim_batch``, ``dim_date``, ``dim_status``, ``dim_finding_type``: filter
  dimensions with fixed integer keys.

Money stays in integer SGD cents. ``age_days`` is the number of calendar days
from a figure's earliest source business date to the batch ``as_of_date``.
Dates are source business dates on the Gregorian calendar in Asia/Singapore;
timestamps are stored in UTC.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb

from batches import BUSINESS_TIMEZONE, atomic_write_json, sha256_file


STATUSES = (
    (1, "matched", False, "Reference, currency, amount and posting date agree."),
    (2, "timing_difference", True, "Amounts agree; posting dates differ within the window."),
    (3, "fee_fx_difference", True, "Amounts differ within the fee/FX tolerance."),
    (4, "many_to_one", True, "Several bank rows sum exactly to one ledger posting."),
    (5, "duplicate", True, "Bank rows repeat the full ledger amount."),
    (6, "missing_in_bank", True, "No bank row has this reference and currency."),
    (7, "missing_in_ledger", True, "No ledger row has this bank reference and currency."),
    (8, "unexplained", True, "Difference is outside every explained rule."),
)
FINDING_TYPES = (
    (1, "duplicate_payment", "rule_screen"),
    (2, "near_threshold", "rule_screen"),
    (3, "split_purchase", "rule_screen"),
    (4, "sod_conflict", "rule_screen"),
    (5, "after_hours", "rule_screen"),
    (6, "isolation_forest_candidate", "anomaly_model"),
    (7, "invoice_above_contract_cap", "contract_cap"),
)
TABLES = ("dim_batch", "dim_date", "dim_status", "dim_finding_type",
          "fct_reconciliation", "fct_finding", "fct_quality", "bridge_figure_source")
ORDER = {"dim_batch": "batch_id", "dim_date": "date_key", "dim_status": "status_key",
         "dim_finding_type": "finding_type_key", "fct_reconciliation": "figure_id",
         "fct_finding": "figure_id", "fct_quality": "check_name",
         "bridge_figure_source": "figure_id, source_name, source_row"}
CENTS = {"fct_reconciliation": ("ledger_cents", "bank_cents", "difference_cents"),
         "fct_finding": ("amount_cents",)}
SNAPSHOT_MANIFEST = "snapshot_manifest.json"


class SnapshotError(ValueError):
    """A snapshot is stale, modified, from another batch or out of parity."""


def _exists(db: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    return db.execute("""select count(*) from information_schema.tables
        where table_schema = ? and table_name = ?""", [schema, table]).fetchone()[0] > 0


def data_as_of_date(db: duckdb.DuckDBPyConnection) -> date:
    """Latest ledger, bank or invoice business date: the batch data cut-off."""
    value = db.execute("""select max(day) from (
        select max(posted_on) as day from staging.ledger
        union all select max(posted_on) from staging.bank
        union all select max(invoice_on) from staging.invoices)""").fetchone()[0]
    if value is None:
        raise ValueError("cannot derive an as-of date from empty ledger, bank and invoice feeds")
    return value


def build_star(db: duckdb.DuckDBPyConnection, *, batch_id: str, status: str,
               started_at, completed_at, as_of_date: date, quality: dict) -> dict:
    """Create the ``bi`` schema for one batch and return its row counts."""
    earliest = db.execute("""select min(day) from (
        select min(posted_on) as day from staging.ledger
        union all select min(posted_on) from staging.bank
        union all select min(invoice_on) from staging.invoices)""").fetchone()[0]
    latest = data_as_of_date(db)
    if as_of_date < latest:
        raise ValueError(f"as-of date {as_of_date} precedes source data ending {latest}")
    db.execute("begin transaction")
    try:
        db.execute("create schema if not exists bi")
        db.execute("""create or replace table bi.dim_batch (batch_key integer,
            batch_id varchar, status varchar, started_at timestamptz,
            completed_at timestamptz, as_of_date date, as_of_date_key integer,
            business_timezone varchar, calendar varchar)""")
        db.execute("insert into bi.dim_batch values (1, ?, ?, ?, ?, ?, ?, ?, ?)",
                   [batch_id, status, started_at, completed_at, as_of_date,
                    int(as_of_date.strftime("%Y%m%d")), BUSINESS_TIMEZONE,
                    "Gregorian calendar days; weekends and holidays are not excluded"])
        db.execute("""create or replace table bi.dim_date as
            select cast(strftime(day, '%Y%m%d') as integer) as date_key,
                   cast(day as date) as calendar_date,
                   year(day) as year, month(day) as month, day(day) as day_of_month,
                   isodow(day) as iso_weekday, strftime(day, '%A') as weekday_name,
                   isodow(day) >= 6 as is_weekend
            from range(cast(? as date), cast(? as date) + interval 1 day,
                       interval 1 day) as t(day)""", [earliest, as_of_date])
        db.execute("""create or replace table bi.dim_status (status_key integer,
            status varchar, is_break boolean, description varchar)""")
        db.executemany("insert into bi.dim_status values (?, ?, ?, ?)",
                       [list(item) for item in STATUSES])
        db.execute("""create or replace table bi.dim_finding_type (
            finding_type_key integer, finding_type varchar, finding_source varchar)""")
        db.executemany("insert into bi.dim_finding_type values (?, ?, ?)",
                       [list(item) for item in FINDING_TYPES])

        db.execute("""create or replace table bi.fct_reconciliation as
            with src as (
              select fs.figure_id, fs.source_name,
                     coalesce(l.posted_on, b.posted_on) as posted_on,
                     coalesce(l.amount_cents, b.amount_cents) as amount_cents
              from audit.figure_sources fs
              left join staging.ledger l on fs.source_name = 'ledger'
                and l.source_file = fs.source_file and l.source_row = fs.source_row
                and l.source_hash = fs.source_hash
              left join staging.bank b on fs.source_name = 'bank'
                and b.source_file = fs.source_file and b.source_row = fs.source_row
                and b.source_hash = fs.source_hash
              where fs.figure_id like 'REC-%'),
            agg as (
              select figure_id, min(posted_on) as posted_on,
                     cast(sum(case when source_name = 'ledger' then amount_cents else 0 end)
                          as bigint) as ledger_cents,
                     cast(sum(case when source_name = 'bank' then amount_cents else 0 end)
                          as bigint) as bank_cents,
                     count(*) as source_rows,
                     count(*) filter (where posted_on is null or amount_cents is null)
                       as unresolved_rows
              from src group by figure_id)
            select ? as batch_id, r.figure_id, r.reference, s.status_key, r.status,
                   s.is_break, cast(strftime(a.posted_on, '%Y%m%d') as integer) as date_key,
                   a.posted_on, a.ledger_cents, a.bank_cents, r.difference_cents,
                   cast(date_diff('day', a.posted_on, cast(? as date)) as integer) as age_days,
                   a.source_rows, a.unresolved_rows, r.explanation, r.ledger_ids, r.bank_ids
            from audit.reconciliation r
            join agg a on a.figure_id = r.figure_id
            join bi.dim_status s on s.status = r.status""", [batch_id, as_of_date])

        parts = []
        if _exists(db, "audit", "findings"):
            parts.append("""select figure_id, kind as finding_type,
                abs(amount_cents) as amount_cents, explanation,
                ledger_ids as source_ids from audit.findings""")
        if _exists(db, "audit", "anomalies"):
            parts.append("""select figure_id, 'isolation_forest_candidate',
                abs(amount_cents),
                'IsolationForest low-density review candidate; score ' ||
                  printf('%.6f', score) || ' is not a fraud probability.',
                to_json([ledger_id])::varchar from audit.anomalies""")
        if _exists(db, "audit", "contract_findings"):
            parts.append("""select figure_id, kind, invoice_cents,
                'Invoice ' || invoice_id || ' is ' || (invoice_cents - cap_cents) ||
                  ' cents above the extracted cap in ' || contract_id || '.',
                to_json([invoice_id])::varchar from audit.contract_findings""")
        findings_sql = " union all ".join(parts) or """select null::varchar as figure_id,
            null::varchar as finding_type, null::bigint as amount_cents,
            null::varchar as explanation, null::varchar as source_ids where false"""
        db.execute(f"""create or replace table bi.fct_finding as
            with f as ({findings_sql}),
            dates as (
              select fs.figure_id, min(coalesce(l.posted_on, i.invoice_on)) as posted_on
              from audit.figure_sources fs
              left join staging.ledger l on fs.source_name = 'ledger'
                and l.source_file = fs.source_file and l.source_row = fs.source_row
                and l.source_hash = fs.source_hash
              left join staging.invoices i on fs.source_name = 'invoices'
                and i.source_file = fs.source_file and i.source_row = fs.source_row
                and i.source_hash = fs.source_hash
              group by fs.figure_id)
            select ? as batch_id, f.figure_id, t.finding_type_key, f.finding_type,
                   t.finding_source, cast(f.amount_cents as bigint) as amount_cents,
                   cast(strftime(d.posted_on, '%Y%m%d') as integer) as date_key,
                   d.posted_on,
                   cast(date_diff('day', d.posted_on, cast(? as date)) as integer) as age_days,
                   f.explanation, f.source_ids
            from f join bi.dim_finding_type t on t.finding_type = f.finding_type
            left join dates d on d.figure_id = f.figure_id""", [batch_id, as_of_date])

        db.execute("""create or replace table bi.fct_quality (batch_id varchar,
            check_name varchar, check_group varchar, status varchar,
            evidence_count integer, description varchar)""")
        db.executemany("insert into bi.fct_quality values (?, ?, ?, ?, ?, ?)", [
            [batch_id, item["name"], item["name"].split(":", 1)[0], item["status"],
             len(item["evidence"]), item["description"]] for item in quality["details"]])

        db.execute("""create or replace table bi.bridge_figure_source as
            select ? as batch_id, figure_id, source_name, source_file,
                   source_row, source_hash from audit.figure_sources""", [batch_id])
        counts = {table: db.execute(f"select count(*) from bi.{table}").fetchone()[0]
                  for table in TABLES}
        _check_star(db, quality, counts)
        db.execute("commit")
    except BaseException:
        db.execute("rollback")
        raise
    return counts


def _check_star(db: duckdb.DuckDBPyConnection, quality: dict, counts: dict) -> None:
    """Fail closed if a fact lost rows, dates or money while being modelled."""
    expected_rec = db.execute("select count(*) from audit.reconciliation").fetchone()[0]
    if counts["fct_reconciliation"] != expected_rec or expected_rec == 0:
        raise ValueError("reconciliation fact does not have one row per REC figure")
    bad = db.execute("""select count(*) from bi.fct_reconciliation
        where unresolved_rows > 0 or posted_on is null
           or ledger_cents - bank_cents != difference_cents""").fetchone()[0]
    if bad:
        raise ValueError(f"{bad} reconciliation fact rows do not tie to source cents")
    expected_findings = sum(db.execute(f"select count(*) from audit.{table}").fetchone()[0]
                            for table in ("findings", "anomalies", "contract_findings")
                            if _exists(db, "audit", table))
    if counts["fct_finding"] != expected_findings:
        raise ValueError("finding fact does not have one row per finding")
    if db.execute("select count(*) from bi.fct_finding where posted_on is null").fetchone()[0]:
        raise ValueError("finding fact has a figure without a source date")
    if counts["fct_quality"] != quality["checks"]:
        raise ValueError("quality fact does not have one row per control outcome")
    if counts["bridge_figure_source"] != db.execute(
            "select count(*) from audit.figure_sources").fetchone()[0]:
        raise ValueError("bridge does not have one row per figure source link")


def warehouse_totals(db: duckdb.DuckDBPyConnection) -> dict:
    """Independent totals from the warehouse tables the star model came from."""
    rec_ids = sorted(row[0] for row in db.execute(
        "select figure_id from audit.reconciliation").fetchall())
    finding_ids = sorted(row[0] for table in ("findings", "anomalies", "contract_findings")
                         if _exists(db, "audit", table)
                         for row in db.execute(f"select figure_id from audit.{table}").fetchall())
    return {
        "fct_reconciliation": {
            "rows": len(rec_ids), "keys": rec_ids,
            "cents": {
                "ledger_cents": int(db.execute(
                    "select coalesce(sum(amount_cents), 0) from staging.ledger").fetchone()[0]),
                "bank_cents": int(db.execute(
                    "select coalesce(sum(amount_cents), 0) from staging.bank").fetchone()[0]),
                "difference_cents": int(db.execute(
                    "select coalesce(sum(difference_cents), 0) from audit.reconciliation"
                ).fetchone()[0])}},
        "fct_finding": {"rows": len(finding_ids), "keys": finding_ids},
        "bridge_figure_source": {"rows": db.execute(
            "select count(*) from audit.figure_sources").fetchone()[0]},
    }


def export_snapshot(db: duckdb.DuckDBPyConnection, directory: Path, *,
                    batch_id: str, completed_at: str, as_of_date: date) -> dict:
    """Write each ``bi`` table as CSV and Parquet plus a hash manifest."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    tables = {}
    for table in TABLES:
        entry = {"rows": db.execute(f"select count(*) from bi.{table}").fetchone()[0]}
        query = f"select * from bi.{table} order by {ORDER[table]}"
        for fmt, options in (("csv", "(header, delimiter ',')"), ("parquet", "(format parquet)")):
            path = directory / f"{table}.{fmt}"
            db.execute(f"copy ({query}) to '{path.as_posix()}' {options}")
            entry[fmt] = {"file": path.name, "sha256": sha256_file(path)}
        if table in CENTS:
            entry["cents"] = {column: int(db.execute(
                f"select coalesce(sum({column}), 0) from bi.{table}").fetchone()[0])
                for column in CENTS[table]}
        tables[table] = entry
    manifest = {"batch_id": batch_id, "completed_at": completed_at,
                "as_of_date": as_of_date.isoformat(),
                "business_timezone": BUSINESS_TIMEZONE,
                "money": "integer SGD cents; format as currency only in presentation",
                "tables": tables}
    atomic_write_json(directory / SNAPSHOT_MANIFEST, manifest)
    return manifest


def read_snapshot_table(directory: Path, table: str, fmt: str = "parquet"):
    """Return (columns, rows) for one verified snapshot file."""
    if table not in TABLES or fmt not in ("csv", "parquet"):
        raise ValueError("unknown snapshot table or format")
    path = (Path(directory) / f"{table}.{fmt}").as_posix()
    reader = "read_parquet" if fmt == "parquet" else "read_csv"
    with duckdb.connect() as db:
        result = db.execute(f"select * from {reader}(?)", [path])
        return [column[0] for column in result.description], result.fetchall()


def verify_snapshot(directory: Path, expected_batch_id: str,
                    db: duckdb.DuckDBPyConnection | None = None) -> dict:
    """Reject a stale or modified snapshot; optionally prove parity with DuckDB."""
    directory = Path(directory)
    path = directory / SNAPSHOT_MANIFEST
    if not path.is_file():
        raise SnapshotError(f"snapshot manifest missing: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("batch_id") != expected_batch_id:
        raise SnapshotError(f"snapshot belongs to batch {manifest.get('batch_id')}, "
                            f"not {expected_batch_id}")
    expected = warehouse_totals(db) if db is not None else None
    result = {"batch_id": expected_batch_id, "formats": {}}
    for fmt in ("csv", "parquet"):
        checked = {}
        for table, entry in manifest["tables"].items():
            file_path = directory / entry[fmt]["file"]
            if not file_path.is_file() or sha256_file(file_path) != entry[fmt]["sha256"]:
                raise SnapshotError(f"stale or modified snapshot file: {file_path.name}")
            columns, rows = read_snapshot_table(directory, table, fmt)
            if len(rows) != entry["rows"]:
                raise SnapshotError(f"{file_path.name}: row count differs from manifest")
            if "batch_id" in columns:
                index = columns.index("batch_id")
                if any(row[index] != expected_batch_id for row in rows):
                    raise SnapshotError(f"{file_path.name}: rows from another batch")
            sums = {column: sum(row[columns.index(column)] for row in rows)
                    for column in CENTS.get(table, ())}
            if sums != entry.get("cents", {}):
                raise SnapshotError(f"{file_path.name}: cents totals differ from manifest")
            checked[table] = {"rows": len(rows), **({"cents": sums} if sums else {})}
            if expected is not None and table in expected:
                target = expected[table]
                if len(rows) != target["rows"]:
                    raise SnapshotError(f"{file_path.name}: row count differs from DuckDB")
                if "keys" in target and sorted(
                        row[columns.index("figure_id")] for row in rows) != target["keys"]:
                    raise SnapshotError(f"{file_path.name}: figure keys differ from DuckDB")
                if "cents" in target and sums != target["cents"]:
                    raise SnapshotError(f"{file_path.name}: cents totals differ from DuckDB")
        result["formats"][fmt] = checked
    result["warehouse_parity_checked"] = expected is not None
    return result
