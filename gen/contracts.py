"""Generate local synthetic vendor agreements with private extraction labels."""

from __future__ import annotations

import csv
import json
from pathlib import Path


def money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def generate_contracts(data_dir: Path) -> int:
    data_dir = Path(data_dir)
    with (data_dir / "invoices.csv").open(newline="", encoding="utf-8") as file:
        invoices = list(csv.DictReader(file))
    folder = data_dir / "contracts"
    folder.mkdir(exist_ok=True)
    labels = []
    for index, invoice in enumerate(invoices):
        contract_id = f"CT-{index:05d}"
        amount = int(invoice["amount_cents"])
        cap = amount - 100 if index in (10, 20) else amount + 2_000
        terms = {"vendor": invoice["vendor"], "payment_days": 30 + index % 3 * 15,
                 "unit_price_cents": amount, "cap_cents": cap,
                 "termination_days": 30, "auto_renewal": index % 2 == 0}
        if index % 2:
            text = (f"Agreement {contract_id}\nSupplier: {terms['vendor']}\n"
                    f"Invoices payable within {terms['payment_days']} days.\n"
                    f"Agreed price SGD {money(amount)}.\n"
                    f"Maximum invoice amount SGD {money(cap)}.\n"
                    f"Terminate with {terms['termination_days']} days' notice.\n"
                    f"Renews automatically: {str(terms['auto_renewal']).lower()}.\n")
        else:
            text = (f"Agreement {contract_id}\nVendor: {terms['vendor']}\n"
                    f"Payment terms: Net {terms['payment_days']} days.\n"
                    f"Unit price: SGD {money(amount)}.\n"
                    f"Invoice cap: SGD {money(cap)}.\n"
                    f"Termination notice: {terms['termination_days']} days.\n"
                    f"Automatic renewal: {'Yes' if terms['auto_renewal'] else 'No'}.\n")
        (folder / f"{contract_id}.txt").write_text(text, encoding="utf-8")
        labels.append({"contract_id": contract_id, "invoice_id": invoice["id"],
                       "terms": terms, "cap_violation": amount > cap})
    (data_dir / "contract_ground_truth.json").write_text(
        json.dumps({"contracts": labels}, indent=2) + "\n", encoding="utf-8")
    return len(labels)
