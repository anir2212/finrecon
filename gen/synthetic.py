"""Create independent-looking finance feeds with a private issue manifest.

All monetary amounts are integer SGD cents. The manifest is evaluation data;
the reconciliation engine reads only the CSV feeds.
"""

from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path


HEADERS = {
    "ledger": ["id", "reference", "posted_on", "posted_at", "counterparty", "currency", "amount_cents", "account", "contra_account", "created_by", "approved_by"],
    "bank": ["id", "reference", "posted_on", "counterparty", "currency", "amount_cents"],
    "processor": ["id", "reference", "settled_on", "currency", "gross_cents", "fee_cents", "net_cents"],
    "exchange": ["id", "tx_hash", "posted_on", "direction", "currency", "amount_cents"],
    "invoices": ["id", "po_id", "vendor", "invoice_on", "currency", "amount_cents", "created_by"],
    "purchase_orders": ["id", "vendor", "approved_on", "currency", "cap_cents", "approved_by"],
}


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def generate(directory: Path, seed: int = 17, count: int = 120) -> dict:
    if count < 40:
        raise ValueError("count must be at least 40 for the issue grid")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    feeds: dict[str, list[dict]] = {name: [] for name in HEADERS}
    truth = []
    audit_truth = []
    anomaly_truth = []
    base = date(2026, 9, 1)
    for i in range(count):
        ref = f"PAY-{i:05d}"
        amount = rng.randint(1_000, 120_000)
        when = base + timedelta(days=i % 30)
        vendor = f"Vendor {i % 23:02d}"
        if i in (40, 41):
            amount, when, vendor = 50_000, base + timedelta(days=10), "Vendor duplicate"
            if i == 41:
                audit_truth.append({"kind": "duplicate_payment", "ids": ["GL-00040", "GL-00041"]})
        elif i == 42:
            amount = 99_900
            audit_truth.append({"kind": "near_threshold", "ids": ["GL-00042"]})
        elif i in (43, 44):
            amount, when, vendor = (55_000 if i == 43 else 45_000), base + timedelta(days=12), "Vendor split"
            if i == 44:
                audit_truth.append({"kind": "split_purchase", "ids": ["GL-00043", "GL-00044"]})
        elif i == 47:
            amount = 1_000_000
        feeds["ledger"].append({
            "id": f"GL-{i:05d}", "reference": ref, "posted_on": when.isoformat(),
            "posted_at": f"{when.isoformat()}T{'22:30:00' if i == 46 else '10:00:00'}+08:00",
            "counterparty": vendor, "currency": "SGD", "amount_cents": -amount,
            "account": "SUSPENSE" if i == 48 else "AP-SETTLEMENT",
            "contra_account": "CASH",
            "created_by": "temp-user" if i == 48 else f"clerk-{i % 5}",
            "approved_by": "operator-45" if i == 45 else f"manager-{i % 3}",
        })
        bank_base = {"reference": ref, "posted_on": when.isoformat(),
                     "counterparty": vendor, "currency": "SGD"}
        issue = "matched"
        if i % 29 == 0:
            issue = "missing_in_bank"
        elif i % 31 == 0:
            issue = "duplicate"
            for part in range(2):
                feeds["bank"].append({"id": f"BK-{i:05d}-{part}", **bank_base,
                                      "amount_cents": -amount})
        elif i % 23 == 0:
            issue = "timing_difference"
            feeds["bank"].append({"id": f"BK-{i:05d}", **bank_base,
                                  "posted_on": (when + timedelta(days=2)).isoformat(),
                                  "amount_cents": -amount})
        elif i % 19 == 0:
            issue = "fee_fx_difference"
            feeds["bank"].append({"id": f"BK-{i:05d}", **bank_base,
                                  "amount_cents": -(amount + 75)})
        elif i % 17 == 0:
            issue = "many_to_one"
            first = amount // 2
            feeds["bank"].extend([
                {"id": f"BK-{i:05d}-a", **bank_base, "amount_cents": -first},
                {"id": f"BK-{i:05d}-b", **bank_base, "amount_cents": -(amount - first)},
            ])
        elif i % 37 == 0:
            issue = "unexplained"
            feeds["bank"].append({"id": f"BK-{i:05d}", **bank_base,
                                  "amount_cents": -(amount + 517)})
        else:
            feeds["bank"].append({"id": f"BK-{i:05d}", **bank_base,
                                  "counterparty": vendor.replace("Vendor", "Vendr") if i % 13 == 0 else vendor,
                                  "amount_cents": -amount})
        truth.append({"reference": ref, "expected_status": issue})
        if i == 45:
            feeds["ledger"][-1]["created_by"] = "operator-45"
            audit_truth.append({"kind": "sod_conflict", "ids": ["GL-00045"]})
        if i == 46:
            audit_truth.append({"kind": "after_hours", "ids": ["GL-00046"]})
        if i in (46, 47, 48):
            anomaly_truth.append(f"GL-{i:05d}")

        if i % 3 == 0:
            fee = max(10, (amount * 12 + 500) // 1000)
            feeds["processor"].append({
                "id": f"PS-{i:05d}", "reference": ref, "settled_on": when.isoformat(),
                "currency": "SGD", "gross_cents": amount, "fee_cents": fee,
                "net_cents": amount - fee,
            })
        if i < 50:
            po = f"PO-{i:05d}"
            feeds["purchase_orders"].append({
                "id": po, "vendor": vendor, "approved_on": (when - timedelta(days=2)).isoformat(),
                "currency": "SGD", "cap_cents": amount + 2_000,
                "approved_by": f"manager-{i % 3}",
            })
            feeds["invoices"].append({
                "id": f"INV-{i:05d}", "po_id": po, "vendor": vendor,
                "invoice_on": when.isoformat(), "currency": "SGD",
                "amount_cents": amount, "created_by": f"clerk-{i % 5}",
            })
        if i % 20 == 0:
            feeds["exchange"].append({
                "id": f"EX-{i:05d}", "tx_hash": f"0x{i:064x}",
                "posted_on": when.isoformat(), "direction": "withdrawal",
                "currency": "SGD", "amount_cents": amount,
            })

    for i in range(3):
        ref = f"ORPHAN-{i:02d}"
        feeds["bank"].append({
            "id": f"BK-ORPHAN-{i}", "reference": ref,
            "posted_on": (base + timedelta(days=i)).isoformat(),
            "counterparty": "Unmapped sender", "currency": "SGD",
            "amount_cents": -(1000 + i * 200),
        })
        truth.append({"reference": ref, "expected_status": "missing_in_ledger"})

    for name, rows in feeds.items():
        _write_csv(directory / f"{name}.csv", rows, HEADERS[name])
    # The daily ledger export is a separately delivered control feed. Its
    # calendar is fixed before quality checks run, so a missing day is visible.
    daily = directory / "daily_ledger"
    daily.mkdir(exist_ok=True)
    expected_days = [(base + timedelta(days=day)).isoformat() for day in range(30)]
    for day in expected_days:
        _write_csv(daily / f"ledger-{day}.csv",
                   [row for row in feeds["ledger"] if row["posted_on"] == day],
                   HEADERS["ledger"])
    (directory / "quality_contract.json").write_text(json.dumps({
        "expected_days": expected_days,
        "expected_source_rows": {name: len(rows) for name, rows in feeds.items()},
        "currency": "SGD",
    }, indent=2) + "\n", encoding="utf-8")
    (directory / "ground_truth.json").write_text(
        json.dumps({"seed": seed, "transactions": count, "issues": truth}, indent=2) + "\n",
        encoding="utf-8",
    )
    (directory / "audit_ground_truth.json").write_text(
        json.dumps({"findings": audit_truth}, indent=2) + "\n", encoding="utf-8")
    (directory / "anomaly_ground_truth.json").write_text(
        json.dumps({"injected_ids": anomaly_truth}, indent=2) + "\n",
        encoding="utf-8")
    return {name: len(rows) for name, rows in feeds.items()}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/generated"))
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--count", type=int, default=120)
    args = parser.parse_args()
    print(json.dumps(generate(args.output, args.seed, args.count), indent=2))
