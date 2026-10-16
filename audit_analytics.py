"""Explainable audit screens; all amounts and evidence come from staged rows."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from warehouse import add_evidence


def screen(db: duckdb.DuckDBPyConnection, *, approval_cents: int = 100_000,
           near_band_cents: int = 1_000) -> dict:
    """Screen ledger payments and persist row-traceable review findings.

    Screens indicate review candidates, not proven fraud. A duplicate payment
    pair is excluded from split-purchase findings to avoid double counting.
    """
    if approval_cents <= 0 or not 0 < near_band_cents < approval_cents:
        raise ValueError("invalid approval threshold or near-threshold band")
    result = db.execute("""select id, reference, posted_on, posted_at, counterparty,
        amount_cents, created_by, approved_by, source_file, source_row,
        source_hash from staging.ledger order by id""")
    names = [column[0] for column in result.description]
    rows = [dict(zip(names, value, strict=True)) for value in result.fetchall()]
    if any(row["posted_on"] is None or row["posted_at"] is None or
           row["amount_cents"] is None for row in rows):
        raise ValueError("audit screen requires valid ledger dates, times and amounts")

    by_vendor_day = defaultdict(list)
    for row in rows:
        by_vendor_day[(row["counterparty"], row["posted_on"])].append(row)
    proposed: list[tuple[str, list[dict], str]] = []
    duplicate_groups: set[tuple[str, object]] = set()
    for key, group in by_vendor_day.items():
        by_amount = defaultdict(list)
        for row in group:
            by_amount[row["amount_cents"]].append(row)
        for same_amount in by_amount.values():
            if len(same_amount) > 1:
                proposed.append(("duplicate_payment", same_amount,
                                 "Same vendor, date and amount across distinct ledger IDs."))
                duplicate_groups.add(key)
    for row in rows:
        amount = -row["amount_cents"]
        if approval_cents - near_band_cents <= amount < approval_cents:
            proposed.append(("near_threshold", [row],
                             "Payment falls immediately below approval threshold."))
        if row["created_by"] == row["approved_by"]:
            proposed.append(("sod_conflict", [row],
                             "The same user created and approved the payment."))
        hour = row["posted_at"].astimezone(ZoneInfo("Asia/Singapore")).hour
        if hour < 8 or hour >= 18:
            proposed.append(("after_hours", [row],
                             "Posting occurred outside the 08:00–17:59 Singapore review window."))
    for key, group in by_vendor_day.items():
        amounts = [-row["amount_cents"] for row in group]
        if (len(group) > 1 and key not in duplicate_groups
                and all(0 < amount < approval_cents for amount in amounts)
                and sum(amounts) >= approval_cents):
            proposed.append(("split_purchase", group,
                             "Same-day payments to one vendor jointly reach approval threshold."))

    db.execute("""create table if not exists audit.findings (
        figure_id varchar primary key, kind varchar, explanation varchar,
        amount_cents bigint, ledger_ids varchar, recorded_at timestamptz)""")
    findings = []
    db.execute("begin transaction")
    try:
        db.execute("delete from audit.findings")
        db.execute("delete from audit.figure_sources where figure_id like 'AUD-%'")
        for kind, evidence, explanation in proposed:
            ids = sorted(row["id"] for row in evidence)
            figure_id = f"AUD-{kind}-{'-'.join(ids)}"
            amount = sum(-row["amount_cents"] for row in evidence)
            item = {"figure_id": figure_id, "kind": kind,
                    "explanation": explanation, "amount_cents": amount,
                    "currency": "SGD", "ledger_ids": ids}
            findings.append(item)
            db.execute("insert into audit.findings values (?, ?, ?, ?, ?, ?)", [
                figure_id, kind, explanation, amount, json.dumps(ids),
                datetime.now(timezone.utc)])
            for row in evidence:
                add_evidence(db, figure_id, "ledger", row["source_file"],
                             row["source_row"], row["source_hash"])
        db.execute("commit")
    except BaseException:
        db.execute("rollback")
        raise

    # Benford is descriptive here. Uniformly generated bounded amounts do not
    # satisfy its assumptions; no fraud classification uses this statistic.
    leading = Counter(int(str(abs(row["amount_cents"]))[0]) for row in rows
                      if row["amount_cents"] != 0)
    n = sum(leading.values())
    expected = {digit: n * math.log10(1 + 1 / digit) for digit in range(1, 10)}
    chi_square = sum((leading[digit] - expected[digit]) ** 2 / expected[digit]
                     for digit in range(1, 10)) if n else None
    weekend_ids = [row["id"] for row in rows if row["posted_on"].weekday() >= 5]
    after_hours_ids = [row["id"] for row in rows if
                       row["posted_at"].astimezone(ZoneInfo("Asia/Singapore")).hour < 8 or
                       row["posted_at"].astimezone(ZoneInfo("Asia/Singapore")).hour >= 18]
    return {"findings": sorted(findings, key=lambda item: item["figure_id"]),
            "counts": dict(Counter(item["kind"] for item in findings)),
            "benford": {"n": n, "leading_digit_counts": dict(sorted(leading.items())),
                        "chi_square": round(chi_square, 4) if chi_square is not None else None,
                        "interpretation": "descriptive only; synthetic bounded uniform amounts"},
            "weekend_posting_ids": weekend_ids,
            "after_hours_posting_ids": after_hours_ids,
            "threshold_cents": approval_cents, "near_band_cents": near_band_cents}


def evaluate(truth_path: Path, findings: list[dict]) -> dict:
    """Score screen hits against an isolated synthetic injection manifest."""
    truth = json.loads(Path(truth_path).read_text(encoding="utf-8"))
    expected = {(item["kind"], tuple(sorted(item["ids"])))
                for item in truth["findings"]}
    actual = {(item["kind"], tuple(item["ledger_ids"])) for item in findings}
    kinds = sorted({kind for kind, _ in expected | actual})
    by_kind = {}
    for kind in kinds:
        tp = len({key for key in actual & expected if key[0] == kind})
        fp = len({key for key in actual - expected if key[0] == kind})
        fn = len({key for key in expected - actual if key[0] == kind})
        by_kind[kind] = {"tp": tp, "fp": fp, "fn": fn,
                         "precision": tp / (tp + fp) if tp + fp else None,
                         "recall": tp / (tp + fn) if tp + fn else None}
    return {"injected": len(expected), "detected": len(actual),
            "true_positives": len(actual & expected),
            "false_positives": len(actual - expected),
            "false_negatives": len(expected - actual), "by_kind": by_kind}
