"""Allow-listed audit questions with SQL facts, citations and numeric guard.

This is a deterministic local copilot baseline. An external LLM may rephrase
the returned facts only after ``guard_numbers`` accepts its proposed text.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import duckdb

from quality_checks import run_checks


STATUSES = {"matched", "missing_in_bank", "missing_in_ledger",
            "timing_difference", "fee_fx_difference", "many_to_one",
            "duplicate", "unexplained"}
NUMBER = re.compile(r"(?<![A-Za-z0-9_])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?(?![A-Za-z0-9_])")
IDENTIFIER = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_]*(?:-[A-Za-z0-9_]+)+")
REFERENCE = re.compile(r"PAY-\d{5}|ORPHAN-\d{2}")
FIGURE = re.compile(r"AUD-[A-Za-z0-9_-]+")


def _numbers(text: str) -> set[Decimal]:
    values = set()
    # IDs such as PAY-00029 are citations, not financial numbers.
    for match in NUMBER.finditer(IDENTIFIER.sub("", text)):
        try:
            values.add(Decimal(match.group().replace(",", "")))
        except InvalidOperation as exc:
            raise ValueError("invalid numeric token") from exc
    return values


def guard_numbers(proposed_answer: str, payload: dict) -> str:
    """Reject any free-standing numeric value absent from the supplied facts."""
    allowed = _numbers(json.dumps(payload, default=str))
    extra = _numbers(proposed_answer) - allowed
    if extra:
        raise ValueError(f"answer contains numbers absent from SQL payload: {sorted(extra)}")
    return proposed_answer


def _refuse() -> dict:
    return {"answer": "I can answer only the supported local finance questions.",
            "facts": {}, "citations": [], "refused": True}


def respond(question: str, db: duckdb.DuckDBPyConnection, data_dir: Path) -> dict:
    """Route a narrow natural-language grammar to fixed parameterized queries."""
    question = question.strip()
    count = re.fullmatch(r"How many ([a-z_]+) reconciliation figures\?", question)
    difference = re.fullmatch(
        r"What is the total difference in cents for ([a-z_]+) figures\?", question)
    reference = re.fullmatch(r"Why is (PAY-\d{5}|ORPHAN-\d{2}) a reconciliation break\?",
                             question)
    audit = re.fullmatch(r"Explain audit finding (AUD-[A-Za-z0-9_-]+)\?", question)
    if count and count.group(1) in STATUSES:
        status = count.group(1)
        rows = db.execute("""select figure_id from audit.reconciliation
            where status = ? order by figure_id""", [status]).fetchall()
        facts = {"status": status, "count": len(rows)}
        citations = [row[0] for row in rows]
        answer = f"There are {facts['count']} {status} reconciliation figures."
    elif difference and difference.group(1) in STATUSES:
        status = difference.group(1)
        rows = db.execute("""select figure_id, difference_cents
            from audit.reconciliation where status = ? order by figure_id""",
            [status]).fetchall()
        facts = {"status": status, "difference_cents": sum(row[1] for row in rows)}
        citations = [row[0] for row in rows]
        answer = f"The {status} figures total {facts['difference_cents']} SGD cents of ledger-minus-bank difference."
    elif reference and REFERENCE.fullmatch(reference.group(1)):
        ref = reference.group(1)
        row = db.execute("""select figure_id, status, difference_cents, explanation
            from audit.reconciliation where reference = ?""", [ref]).fetchone()
        if row is None:
            return _refuse()
        facts = {"reference": ref, "status": row[1],
                 "difference_cents": row[2], "explanation": row[3]}
        citations = [row[0]]
        answer = (f"{ref} is {row[1]}; ledger-minus-bank difference is "
                  f"{row[2]} SGD cents. {row[3]}")
    elif audit and FIGURE.fullmatch(audit.group(1)):
        row = db.execute("""select figure_id, kind, explanation, amount_cents
            from audit.findings where figure_id = ?""", [audit.group(1)]).fetchone()
        if row is None:
            return _refuse()
        facts = {"kind": row[1], "explanation": row[2], "amount_cents": row[3]}
        citations = [row[0]]
        answer = f"{row[1]}: {row[2]} The reviewed amount is {row[3]} SGD cents."
    elif question == "Which invoices exceed contract caps?":
        rows = db.execute("""select figure_id, invoice_id, invoice_cents,
            cap_cents from audit.contract_findings order by figure_id""").fetchall()
        facts = {"count": len(rows), "invoices": [{"id": row[1],
                 "invoice_cents": row[2], "cap_cents": row[3]} for row in rows]}
        citations = [row[0] for row in rows]
        answer = f"{len(rows)} invoices exceed contract caps."
    elif question == "Did the quality checks pass?":
        quality = run_checks(db, data_dir)
        facts = {"status": quality["status"], "checks": quality["checks"],
                 "failed": quality["failed"]}
        citations = [item["name"] for item in quality["details"]]
        answer = (f"Quality status is {quality['status']}: {quality['failed']} failed "
                  f"of {quality['checks']} checks.")
    else:
        return _refuse()
    return {"answer": guard_numbers(answer, facts), "facts": facts,
            "citations": citations, "refused": False}


if __name__ == "__main__":
    import argparse
    from batches import cli_paths
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--warehouse", type=Path, help="Default: published batch")
    parser.add_argument("--data-dir", type=Path, help="Default: published batch")
    args = parser.parse_args()
    try:
        args.warehouse, args.data_dir = cli_paths(args.warehouse, args.data_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    database = duckdb.connect(str(args.warehouse), read_only=True)
    try:
        print(json.dumps(respond(args.question, database, args.data_dir), indent=2))
    finally:
        database.close()
