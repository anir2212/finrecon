"""Deterministic finance data-quality controls with actionable evidence.

The quality contract is a producer-side delivery specification, not an input
to reconciliation. Checks return data; the CLI decides whether to fail a job.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from gen.synthetic import HEADERS
from warehouse import stage_counts


def run_checks(db: duckdb.DuckDBPyConnection, data_dir: Path, *,
               as_of: datetime | None = None, max_age_hours: int = 24) -> dict:
    """Check delivery, lineage, typing, control totals and finance invariants.

    Every failed check includes source IDs or file paths. ``as_of`` permits a
    replay at a known time; naive datetimes are rejected to avoid TZ mistakes.
    """
    if max_age_hours < 0:
        raise ValueError("max_age_hours must be nonnegative")
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    data_dir = Path(data_dir)
    contract = json.loads((data_dir / "quality_contract.json").read_text(
        encoding="utf-8"))
    checks: list[dict] = []

    def check(name: str, failed: list, *, description: str) -> None:
        checks.append({"name": name, "status": "fail" if failed else "pass",
                       "description": description, "evidence": failed})

    counts = stage_counts(db)
    manifest_rows = db.execute("""select source_name, source_file, sha256,
        row_count, loaded_at from audit.load_manifest order by source_name""").fetchall()
    manifests = {name: (Path(path), digest, count, loaded)
                 for name, path, digest, count, loaded in manifest_rows}
    for name in HEADERS:
        expected = contract["expected_source_rows"][name]
        raw, staged = counts[name]
        check(f"row_count:{name}", [] if raw == staged == expected else [{
            "source": name, "expected": expected, "raw": raw, "staged": staged}],
            description="Raw and staged row counts equal the delivery contract.")
        manifest = manifests.get(name)
        problems = []
        if manifest is None:
            problems.append({"source": name, "reason": "missing manifest"})
        else:
            path, digest, recorded_count, loaded = manifest
            if not path.is_file():
                problems.append({"source": name, "path": str(path),
                                 "reason": "missing source file"})
            elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                problems.append({"source": name, "path": str(path),
                                 "reason": "source changed after ingestion"})
            if recorded_count != raw:
                problems.append({"source": name, "reason": "manifest row-count mismatch",
                                 "manifest": recorded_count, "raw": raw})
        check(f"source_integrity:{name}", problems,
              description="The loaded source still exists and matches its recorded hash.")
        if manifest is not None:
            age_hours = (as_of - manifest[3]).total_seconds() / 3600
            check(f"freshness:{name}", [] if 0 <= age_hours <= max_age_hours else [{
                "source": name, "loaded_at": manifest[3].isoformat(),
                "as_of": as_of.isoformat(), "age_hours": round(age_hours, 3)}],
                description=f"Loaded within {max_age_hours} hours, without future timestamps.")

    ledger = db.execute("select id, posted_on from staging.ledger").fetchall()
    by_day = {}
    for row_id, day in ledger:
        if day is not None:
            by_day.setdefault(day.isoformat(), set()).add(row_id)
    expected_days = contract["expected_days"]
    check("daily_calendar", sorted(set(expected_days) ^ set(by_day)),
          description="Every expected business day has ledger activity and no unexpected day exists.")
    for day in expected_days:
        path = data_dir / "daily_ledger" / f"ledger-{day}.csv"
        problems = []
        if not path.is_file():
            problems.append({"path": str(path), "reason": "missing daily delivery"})
        else:
            with path.open(newline="", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                if reader.fieldnames != HEADERS["ledger"]:
                    problems.append({"path": str(path), "reason": "invalid daily header"})
                else:
                    rows = list(reader)
                    ids = [row["id"] for row in rows]
                    if len(ids) != len(set(ids)) or set(ids) != by_day.get(day, set()):
                        problems.append({"path": str(path), "reason": "daily IDs differ from staged ledger",
                                         "daily_ids": sorted(ids),
                                         "staged_ids": sorted(by_day.get(day, set()))})
                    if any(row["posted_on"] != day for row in rows):
                        problems.append({"path": str(path), "reason": "wrong posting date"})
        check(f"daily_delivery:{day}", problems,
              description="Expected daily file exists with the same IDs and posting date as the warehouse.")

    for source, date_field, money_fields in (
        ("ledger", "posted_on", ("amount_cents",)),
        ("bank", "posted_on", ("amount_cents",)),
        ("processor", "settled_on", ("gross_cents", "fee_cents", "net_cents")),
        ("exchange", "posted_on", ("amount_cents",)),
        ("invoices", "invoice_on", ("amount_cents",)),
        ("purchase_orders", "approved_on", ("cap_cents",)),
    ):
        where = " or ".join([f"{column} is null" for column in
                             ("id", date_field, "currency", *money_fields)])
        bad = db.execute(f"select id, source_file, source_row from staging.{source} "
                         f"where {where} or currency != ?", [contract["currency"]]).fetchall()
        check(f"typed_required:{source}", [{"id": row_id, "file": path, "row": line}
                                           for row_id, path, line in bad],
              description="Required dates and amounts parse and currency is supported.")

    posting = db.execute("""select id, posted_on, posted_at, account,
        contra_account from staging.ledger""").fetchall()
    bad_times = [row_id for row_id, day, instant, _, _ in posting
                 if instant is None or day is None or
                 instant.astimezone(ZoneInfo("Asia/Singapore")).date() != day]
    check("ledger_posting_timestamp", bad_times,
          description="Every posting has a parseable instant whose Singapore date matches posted_on.")
    bad_pairs = [row_id for row_id, _, _, debit, credit in posting
                 if not debit or not credit or debit == credit]
    check("ledger_account_pair", bad_pairs,
          description="Every journal posting has nonempty, distinct debit and contra accounts.")

    for source, field, operator in (("ledger", "amount_cents", ">= 0"),
                                    ("bank", "amount_cents", ">= 0"),
                                    ("processor", "gross_cents", "< 0"),
                                    ("processor", "fee_cents", "< 0"),
                                    ("processor", "net_cents", "< 0"),
                                    ("exchange", "amount_cents", "< 0"),
                                    ("invoices", "amount_cents", "<= 0"),
                                    ("purchase_orders", "cap_cents", "<= 0")):
        ids = [row[0] for row in db.execute(
            f"select id from staging.{source} where {field} {operator}").fetchall()]
        check(f"sign:{source}:{field}", ids,
              description=f"{source}.{field} follows its signed-amount convention.")

    bad_processor = db.execute("""select id from staging.processor
        where gross_cents != fee_cents + net_cents""").fetchall()
    check("processor_fee_identity", [row[0] for row in bad_processor],
          description="Every processor gross amount equals fee plus net amount.")
    bad_processor_gl = db.execute("""select p.id from staging.processor p
        left join staging.ledger l on p.reference = l.reference
        where l.id is null or p.gross_cents != -l.amount_cents""").fetchall()
    check("processor_gl_control", [row[0] for row in bad_processor_gl],
          description="Processor gross amounts tie to the referenced ledger debit.")
    bad_invoices = db.execute("""select i.id from staging.invoices i
        left join staging.purchase_orders po on i.po_id = po.id
        where po.id is null or i.vendor != po.vendor or
              i.currency != po.currency or i.amount_cents > po.cap_cents""").fetchall()
    check("invoice_po_integrity", [row[0] for row in bad_invoices],
          description="Each invoice has a same-vendor, same-currency PO and stays within cap.")
    for source in HEADERS:
        repeated = db.execute(f"""select id from staging.{source}
            group by id having count(*) > 1""").fetchall()
        check(f"unique_id:{source}", [row[0] for row in repeated],
              description="Source IDs are unique within each feed.")

    failures = [item for item in checks if item["status"] == "fail"]
    return {"status": "fail" if failures else "pass", "checks": len(checks),
            "passed": len(checks) - len(failures), "failed": len(failures),
            "failures": failures, "details": checks}
