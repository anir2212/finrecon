"""Rule-based ledger/bank reconciliation with explicit source-row evidence."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import duckdb

from warehouse import add_evidence


def _rows(db: duckdb.DuckDBPyConnection, source: str) -> list[dict]:
    result = db.execute(f"select * from staging.{source} order by id")
    names = [column[0] for column in result.description]
    rows = [dict(zip(names, row, strict=True)) for row in result.fetchall()]
    for row in rows:
        if row["amount_cents"] is None or row["posted_on"] is None:
            raise ValueError(f"{source}: invalid amount or date at source row {row['source_row']}")
        if row["currency"] != "SGD" or not row["reference"]:
            raise ValueError(f"{source}: invalid currency or reference at {row['source_row']}")
    return rows


def _classify(left: dict, candidates: list[dict], fee_tolerance_cents: int,
              date_window_days: int) -> tuple[str, str]:
    if not candidates:
        return "missing_in_bank", "No bank row has this reference and currency."
    farthest_days = max(abs((left["posted_on"] - row["posted_on"]).days)
                        for row in candidates)
    if farthest_days > date_window_days:
        return "unexplained", (f"Bank posting is {farthest_days} day(s) apart, "
                               "beyond the window.")
    amounts = [row["amount_cents"] for row in candidates]
    if len(candidates) > 1:
        if len(set(amounts)) == 1 and amounts[0] == left["amount_cents"]:
            return "duplicate", "Two or more bank rows repeat the full ledger amount."
        if len(candidates) <= 4 and sum(amounts) == left["amount_cents"]:
            return "many_to_one", "Bank rows with the same reference sum to the ledger amount."
        return "unexplained", "Multiple bank rows do not form a bounded exact settlement."
    right = candidates[0]
    days = farthest_days
    delta = abs(left["amount_cents"] - right["amount_cents"])
    if delta == 0:
        if days:
            return "timing_difference", f"Amounts agree; bank posting is {days} day(s) apart."
        return "matched", "Reference, currency, amount, and posting date agree."
    if delta <= fee_tolerance_cents:
        return "fee_fx_difference", f"Amount differs by {delta} SGD cents within fee tolerance."
    return "unexplained", f"Amount differs by {delta} SGD cents beyond fee tolerance."


def reconcile(db: duckdb.DuckDBPyConnection, fee_tolerance_cents: int = 100,
              date_window_days: int = 3) -> list[dict]:
    if fee_tolerance_cents < 0 or date_window_days < 0:
        raise ValueError("tolerances must be nonnegative")
    ledger, bank = _rows(db, "ledger"), _rows(db, "bank")
    bank_by_key = defaultdict(list)
    for row in bank:
        bank_by_key[(row["reference"], row["currency"])].append(row)
    results = []
    db.execute("""create table if not exists audit.reconciliation (
        figure_id varchar primary key, reference varchar, status varchar,
        difference_cents bigint, explanation varchar, ledger_ids varchar,
        bank_ids varchar, recorded_at timestamptz)""")
    db.execute("begin transaction")
    try:
        db.execute("delete from audit.reconciliation")
        db.execute("delete from audit.figure_sources where figure_id like 'REC-%'")

        def record(ref: str, status: str, explanation: str,
                   left_rows: list[dict], right_rows: list[dict]) -> None:
            figure_id = "REC-" + ref
            difference = sum(row["amount_cents"] for row in left_rows) - sum(
                row["amount_cents"] for row in right_rows)
            suggestion = None
            if left_rows and right_rows:
                similarity = max(SequenceMatcher(None,
                    left_rows[0]["counterparty"].casefold(), row["counterparty"].casefold()
                ).ratio() for row in right_rows)
                if 0.70 <= similarity < 1:
                    suggestion = {"counterparty_similarity": round(similarity, 4),
                                  "review_only": True}
            item = {"figure_id": figure_id, "reference": ref, "status": status,
                    "difference_cents": difference, "explanation": explanation,
                    "ledger_ids": [row["id"] for row in left_rows],
                    "bank_ids": [row["id"] for row in right_rows],
                    "counterparty_suggestion": suggestion}
            results.append(item)
            db.execute("insert into audit.reconciliation values (?, ?, ?, ?, ?, ?, ?, ?)", [
                figure_id, ref, status, difference, explanation,
                json.dumps(item["ledger_ids"]), json.dumps(item["bank_ids"]),
                datetime.now(timezone.utc),
            ])
            for source, rows in (("ledger", left_rows), ("bank", right_rows)):
                for row in rows:
                    add_evidence(db, figure_id, source, row["source_file"],
                                 row["source_row"], row["source_hash"])

        for left in ledger:
            candidates = bank_by_key.pop((left["reference"], left["currency"]), [])
            status, explanation = _classify(left, candidates, fee_tolerance_cents,
                                            date_window_days)
            record(left["reference"], status, explanation, [left], candidates)
        for candidates in bank_by_key.values():
            for right in candidates:
                record(right["reference"], "missing_in_ledger",
                       "Bank row has no ledger reference and currency.", [], [right])
        db.execute("commit")
    except BaseException:
        db.execute("rollback")
        raise
    return sorted(results, key=lambda item: item["reference"])


def evaluate(truth_path: Path, results: list[dict]) -> dict:
    """Compare to the separate injected manifest; never use it for matching."""
    truth = json.loads(Path(truth_path).read_text(encoding="utf-8"))
    expected = {item["reference"]: item["expected_status"] for item in truth["issues"]}
    predicted = {item["reference"]: item["status"] for item in results}
    if expected.keys() != predicted.keys():
        raise ValueError("ground truth and result references differ")
    labels = sorted(set(expected.values()) | set(predicted.values()))
    by_type = {}
    for label in labels:
        tp = sum(expected[ref] == label and predicted[ref] == label for ref in expected)
        fp = sum(expected[ref] != label and predicted[ref] == label for ref in expected)
        fn = sum(expected[ref] == label and predicted[ref] != label for ref in expected)
        by_type[label] = {"tp": tp, "fp": fp, "fn": fn,
                          "precision": tp / (tp + fp) if tp + fp else None,
                          "recall": tp / (tp + fn) if tp + fn else None}
    return {"rows": len(expected), "status_counts": dict(Counter(predicted.values())),
            "correct": sum(expected[ref] == predicted[ref] for ref in expected),
            "by_type": by_type, "seed": truth["seed"]}
