"""Synthetic finance feeds, deterministic reconciliation and row-level lineage."""
import argparse
import csv
from difflib import SequenceMatcher
import json
import random
from collections import defaultdict
from pathlib import Path


def generate(directory, seed=7, n=40):
    rng = random.Random(seed)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    ledger = []
    bank = []
    truth = []
    for i in range(n):
        ref = f"PAY-{i:04d}"
        amount = rng.randint(1000, 20000)
        ledger.append({"id": f"L-{i}", "ref": ref, "currency": "SGD", "amount_cents": -amount})
        if i % 13 == 0:
            truth.append({"ref": ref, "issue": "missing_bank"})
        else:
            delta = 100 if i % 11 == 0 else 0
            bank.append({"id": f"B-{i}", "ref": ref, "currency": "SGD", "amount_cents": -(amount+delta)})
            if delta:
                truth.append({"ref": ref, "issue": "amount_mismatch"})
        if i % 17 == 0 and i % 13 != 0:
            bank.append({"id": f"B-DUP-{i}", "ref": ref, "currency": "SGD", "amount_cents": -amount})
            truth.append({"ref": ref, "issue": "duplicate_bank"})
    for name, rows in (("ledger.csv", ledger), ("bank.csv", bank)):
        with (directory/name).open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["id", "ref", "currency", "amount_cents"])
            writer.writeheader(); writer.writerows(rows)
    (directory/"ground_truth.json").write_text(json.dumps(truth, indent=2))
    return len(ledger), len(bank)


def load(path):
    with Path(path).open(newline="") as f:
        rows = list(csv.DictReader(f))
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate source row IDs in {path}")
    for row in rows:
        if row["currency"] != "SGD":
            raise ValueError("unsupported currency")
        row["amount_cents"] = int(row["amount_cents"])
        row["source"] = str(path)
    return rows


def reconcile(ledger, bank, tolerance_cents=0):
    if tolerance_cents < 0:
        raise ValueError("nonnegative tolerance required")
    bank_by_ref = defaultdict(list)
    for row in bank:
        bank_by_ref[(row["ref"], row["currency"])].append(row)
    results = []
    for left in ledger:
        candidates = bank_by_ref.pop((left["ref"], left["currency"]), [])
        if not candidates:
            status = "missing_bank"
        elif len(candidates) > 1:
            status = "many_to_one" if abs(left["amount_cents"] - sum(r["amount_cents"] for r in candidates)) <= tolerance_cents else "duplicate_bank"
        elif abs(left["amount_cents"] - candidates[0]["amount_cents"]) <= tolerance_cents:
            status = "matched"
        else:
            status = "amount_mismatch"
        results.append({"ref": left["ref"], "status": status, "ledger_rows": [left["id"]], "bank_rows": [r["id"] for r in candidates], "difference_cents": left["amount_cents"] - sum(r["amount_cents"] for r in candidates)})
    for candidates in bank_by_ref.values():
        for row in candidates:
            results.append({"ref": row["ref"], "status": "missing_ledger", "ledger_rows": [], "bank_rows": [row["id"]], "difference_cents": -row["amount_cents"]})
    return results


def fuzzy_suggestions(ledger, bank, reconciled, minimum_similarity=.8):
    """Review-only suggestions; never changes reconciliation status."""
    left_by_id = {row["id"]: row for row in ledger}
    unmatched = [r for r in reconciled if r["status"] == "missing_bank"]
    used_bank = {bid for r in reconciled if r["status"] != "missing_ledger" for bid in r["bank_rows"]}
    suggestions = []
    for result in unmatched:
        left = left_by_id[result["ledger_rows"][0]]
        for right in bank:
            if right["id"] in used_bank or left["currency"] != right["currency"] or left["amount_cents"] != right["amount_cents"]:
                continue
            similarity = SequenceMatcher(None, left["ref"], right["ref"]).ratio()
            if similarity >= minimum_similarity:
                suggestions.append({"ledger_row": left["id"], "bank_row": right["id"], "reference_similarity": similarity, "status": "needs_manual_review"})
    return suggestions


def quality(ledger, bank):
    return {"ledger_rows": len(ledger), "bank_rows": len(bank), "ledger_total_cents": sum(r["amount_cents"] for r in ledger), "bank_total_cents": sum(r["amount_cents"] for r in bank), "missing_references": sum(not r["ref"] for r in ledger+bank)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["generate", "reconcile"])
    p.add_argument("--data-dir", default="data")
    args = p.parse_args()
    root = Path(args.data_dir)
    if args.command == "generate":
        print("generated rows:", generate(root))
        return
    ledger, bank = load(root/"ledger.csv"), load(root/"bank.csv")
    matches = reconcile(ledger, bank)
    result = {"quality": quality(ledger, bank), "matches": matches, "fuzzy_suggestions": fuzzy_suggestions(ledger, bank, matches)}
    (root/"reconciliation.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({"quality": result["quality"], "status_counts": {s: sum(r["status"] == s for r in result["matches"]) for s in sorted({r["status"] for r in result["matches"]})}}, indent=2))


if __name__ == "__main__":
    main()
