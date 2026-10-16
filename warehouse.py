"""Auditable CSV ingestion and row-level lineage in a local DuckDB warehouse."""

from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from gen.synthetic import HEADERS


DATE_FIELDS = {"posted_on", "settled_on", "invoice_on", "approved_on"}
TIMESTAMP_FIELDS = {"posted_at"}
MONEY_FIELDS = {"amount_cents", "gross_cents", "fee_cents", "net_cents", "cap_cents"}


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = duckdb.connect(str(path))
    db.execute("create schema if not exists raw")
    db.execute("create schema if not exists staging")
    db.execute("create schema if not exists audit")
    db.execute("""create table if not exists audit.load_manifest (
        source_name varchar, source_file varchar, sha256 varchar,
        row_count integer, loaded_at timestamptz,
        primary key (source_name, source_file))""")
    db.execute("""create table if not exists audit.figure_sources (
        figure_id varchar, source_name varchar, source_file varchar,
        source_row integer, source_hash varchar,
        primary key (figure_id, source_name, source_file, source_row))""")
    for name, columns in HEADERS.items():
        fields = ", ".join(f"{column} varchar" for column in columns)
        db.execute(f"""create table if not exists raw.{name} (
            {fields}, source_file varchar, source_row integer,
            source_hash varchar, loaded_at timestamptz)""")
        present = {row[1] for row in db.execute(
            f"pragma table_info('raw.{name}')").fetchall()}
        required = set(columns) | {"source_file", "source_row", "source_hash", "loaded_at"}
        if present - required or (required - present) - {"posted_at", "contra_account"}:
            raise ValueError(f"raw.{name}: unsupported warehouse schema; migrate explicitly")
        for added in ("posted_at", "contra_account"):
            if added in required - present:
                db.execute(f"alter table raw.{name} add column {added} varchar")
        selected = []
        for column in columns:
            if column in DATE_FIELDS:
                selected.append(f"try_cast({column} as date) as {column}")
            elif column in TIMESTAMP_FIELDS:
                selected.append(f"try_cast({column} as timestamptz) as {column}")
            elif column in MONEY_FIELDS:
                selected.append(f"try_cast({column} as bigint) as {column}")
            else:
                selected.append(column)
        selected.extend(["source_file", "source_row", "source_hash", "loaded_at"])
        db.execute(f"create or replace view staging.{name} as select {', '.join(selected)} from raw.{name}")
    return db


def ingest(db: duckdb.DuckDBPyConnection, directory: Path) -> dict[str, int]:
    """Atomically replace each feed; never silently skip malformed or duplicate rows."""
    counts = {}
    for name, columns in HEADERS.items():
        path = (Path(directory) / f"{name}.csv").resolve()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with path.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames != columns:
                raise ValueError(f"{path}: expected header {columns}, got {reader.fieldnames}")
            rows = list(reader)
        ids = [row["id"] for row in rows]
        if len(ids) != len(set(ids)) or any(not value for value in ids):
            raise ValueError(f"{path}: missing or duplicate source ID")
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError(f"{path}: malformed CSV row")
        path_text = str(path)
        now = datetime.now(timezone.utc)
        placeholders = ", ".join("?" for _ in range(len(columns) + 4))
        insert_columns = ", ".join([*columns, "source_file", "source_row",
                                    "source_hash", "loaded_at"])
        db.execute("begin transaction")
        try:
            # A changed file invalidates figures that cite its old bytes.
            db.execute("delete from audit.figure_sources where source_name = ? and source_file = ?",
                       [name, path_text])
            db.execute(f"delete from raw.{name} where source_file = ?", [path_text])
            if rows:
                db.executemany(f"insert into raw.{name} ({insert_columns}) values ({placeholders})", [
                    [row[column] for column in columns] + [path_text, line, digest, now]
                    for line, row in enumerate(rows, start=2)
                ])
            db.execute("delete from audit.load_manifest where source_name = ? and source_file = ?",
                       [name, path_text])
            db.execute("insert into audit.load_manifest values (?, ?, ?, ?, ?)",
                       [name, path_text, digest, len(rows), now])
            db.execute("commit")
        except BaseException:
            db.execute("rollback")
            raise
        counts[name] = len(rows)
    return counts


def stage_counts(db: duckdb.DuckDBPyConnection) -> dict[str, tuple[int, int]]:
    """Return raw/staged counts; staging views must never discard a row."""
    return {name: (
        db.execute(f"select count(*) from raw.{name}").fetchone()[0],
        db.execute(f"select count(*) from staging.{name}").fetchone()[0],
    ) for name in HEADERS}


def add_evidence(db: duckdb.DuckDBPyConnection, figure_id: str, source_name: str,
                 source_file: str, source_row: int, source_hash: str) -> None:
    if source_name not in HEADERS:
        raise ValueError("unknown source")
    db.execute("insert or replace into audit.figure_sources values (?, ?, ?, ?, ?)",
               [figure_id, source_name, source_file, source_row, source_hash])


def trace(db: duckdb.DuckDBPyConnection, figure_id: str) -> list[dict]:
    """Return exact source rows and reject lineage pointing at a replaced file."""
    evidence = db.execute("""select source_name, source_file, source_row, source_hash
        from audit.figure_sources where figure_id = ?
        order by source_name, source_row""", [figure_id]).fetchall()
    if not evidence:
        raise KeyError(f"unknown figure: {figure_id}")
    results = []
    for name, path, row_number, digest in evidence:
        if name not in HEADERS:
            raise ValueError("invalid source in lineage")
        current = db.execute("""select sha256 from audit.load_manifest
            where source_name = ? and source_file = ?""", [name, path]).fetchone()
        if current is None or current[0] != digest:
            raise ValueError(f"stale lineage for {figure_id}")
        source_path = Path(path)
        if not source_path.is_file() or hashlib.sha256(source_path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"source file changed since ingestion for {figure_id}")
        result = db.execute(f"""select * from raw.{name}
            where source_file = ? and source_row = ? and source_hash = ?""",
            [path, row_number, digest])
        row = result.fetchone()
        if row is None:
            raise ValueError(f"missing source row for {figure_id}")
        results.append({"source_name": name, "source_file": path,
                        "source_row": row_number, "sha256": digest,
                        "values": dict(zip([column[0] for column in result.description], row,
                                           strict=True))})
    return results
