"""Single source of truth for dataset grain, exclusions and column meaning."""

from __future__ import annotations

from pathlib import Path
import json

from gen.synthetic import HEADERS
from warehouse import DATE_FIELDS, MONEY_FIELDS, TIMESTAMP_FIELDS


COMMON = {
    "id": "Unique row identifier within this source feed.",
    "reference": "Payment reference used for exact reconciliation; may be absent from other feeds.",
    "posted_on": "Date recorded by this source, without an intra-day time.",
    "posted_at": "Timezone-aware posting instant; generated fixture uses Asia/Singapore time.",
    "counterparty": "Source spelling of the vendor or payment counterparty.",
    "currency": "ISO currency code; this fixture supports SGD only.",
    "amount_cents": "Signed integer amount in the currency's minor units.",
    "source_file": "Absolute path of the ingested CSV.",
    "source_row": "One-based CSV line number, including the header at line 1.",
    "source_hash": "SHA-256 of the full CSV bytes at ingestion.",
    "loaded_at": "Timezone-aware UTC ingestion timestamp.",
}

DATASETS = {
    "ledger": {
        "grain": "One general-ledger AP settlement posting per row.",
        "excludes": "Cash receipts, journal reversals, and non-SGD postings.",
        "columns": {"account": "GL account code; usually AP-SETTLEMENT with one SUSPENSE anomaly.",
                    "contra_account": "Counter-account; paired with account for journal anomaly features.",
                    "created_by": "User who recorded the posting.",
                    "approved_by": "User who approved the posting."},
    },
    "bank": {
        "grain": "One bank statement movement per row; a payment can have multiple rows.",
        "excludes": "Intraday balances and bank fees in separate statement lines.",
        "columns": {},
    },
    "processor": {
        "grain": "One processor settlement record per row.",
        "excludes": "Chargebacks and reserve holds.",
        "columns": {"settled_on": "Processor settlement date.",
                    "gross_cents": "Gross settlement before processor fee.",
                    "fee_cents": "Processor fee; nonnegative integer cents.",
                    "net_cents": "Gross less fee; nonnegative integer cents."},
    },
    "exchange": {
        "grain": "One synthetic exchange transfer per row.",
        "excludes": "Market prices, network confirmations, and on-chain verification.",
        "columns": {"tx_hash": "Synthetic transaction hash; not verified on-chain.",
                    "direction": "Transfer direction relative to the exchange account."},
    },
    "invoices": {
        "grain": "One accounts-payable invoice per row.",
        "excludes": "Credit notes, partial invoices, and tax line items.",
        "columns": {"po_id": "Referenced purchase-order ID.",
                    "vendor": "Vendor name recorded on the invoice.",
                    "invoice_on": "Invoice issue date.",
                    "created_by": "User who entered the invoice."},
    },
    "purchase_orders": {
        "grain": "One approved purchase order per row.",
        "excludes": "PO amendments, line-level budgets, and approval history.",
        "columns": {"vendor": "Vendor named on the order.",
                    "approved_on": "Date the order was approved.",
                    "cap_cents": "Maximum approved amount in cents.",
                    "approved_by": "Approver's user ID."},
    },
}


def render() -> str:
    lines = ["# FinRecon data dictionary", "",
             "All raw columns retain CSV strings. The matching staging views cast dates "
             "to DATE and money to BIGINT with TRY_CAST, leaving invalid values NULL "
             "for quality checks to catch. Each staging view also contains the four "
             "lineage columns described below.", ""]
    for name, headers in HEADERS.items():
        doc = DATASETS[name]
        lines += [f"## {name}", "", f"**Grain:** {doc['grain']}", "",
                  f"**Excludes:** {doc['excludes']}", "",
                  "| Column | Meaning |", "|---|---|"]
        for field in [*headers, "source_file", "source_row", "source_hash", "loaded_at"]:
            description = doc["columns"].get(field, COMMON.get(field))
            if description is None:
                raise ValueError(f"undocumented column: {name}.{field}")
            lines.append(f"| `{field}` | {description} |")
        lines.append("")
    return "\n".join(lines)


MARTS = {
    "fct_reconciliation": {
        "description": "One classified ledger/bank reference figure; excludes document and audit findings.",
        "columns": {
            "figure_id": "Stable reference-derived figure ID for source tracing.",
            "reference": COMMON["reference"],
            "status": "Deterministic reconciliation category.",
            "difference_cents": "Ledger cents minus bank cents; SGD in this fixture.",
            "explanation": "Rule-generated reason for the category.",
            "ledger_ids": "JSON array of source ledger IDs.",
            "bank_ids": "JSON array of source bank IDs.",
            "recorded_at": "Timezone-aware figure creation timestamp.",
        },
    },
    "fct_audit_findings": {
        "description": "One review candidate from a rule-based audit screen; excludes contract findings.",
        "columns": {
            "figure_id": "Stable audit finding ID for source tracing.",
            "kind": "Audit screen category, not a fraud verdict.",
            "explanation": "Rule-generated reason for review.",
            "amount_cents": "Absolute reviewed amount in SGD cents.",
            "ledger_ids": "JSON array of source ledger IDs.",
            "recorded_at": "Timezone-aware finding creation timestamp.",
        },
    },
}


def render_dbt_schema() -> str:
    """Build dbt source/model descriptions from the same column registry."""
    lines = ["version: 2", "sources:", "  - name: raw", "    schema: raw",
             "    description: 'Unmodified CSV strings with file and row metadata.'",
             "    tables:"]
    for name, headers in HEADERS.items():
        doc = DATASETS[name]
        description = f"Grain: {doc['grain']} Excludes: {doc['excludes']}"
        lines += [f"      - name: {name}",
                  f"        description: {json.dumps(description)}", "        columns:"]
        for field in headers:
            meaning = doc["columns"].get(field, COMMON.get(field))
            if meaning is None:
                raise ValueError(f"undocumented raw column {name}.{field}")
            lines += [f"          - name: {field}",
                      f"            description: {json.dumps(meaning)}"]
    lines += ["models:"]
    for name, headers in HEADERS.items():
        doc = DATASETS[name]
        lines += [f"  - name: stg_{name}",
                  f"    description: {json.dumps(doc['grain'] + ' Excludes: ' + doc['excludes'])}",
                  "    columns:"]
        for field in [*headers, "source_file", "source_row", "source_hash", "loaded_at"]:
            meaning = doc["columns"].get(field, COMMON.get(field))
            if meaning is None:
                raise ValueError(f"undocumented staged column {name}.{field}")
            lines += [f"      - name: {field}",
                      f"        description: {json.dumps(meaning)}"]
            tests = (["not_null", "unique"] if field == "id" else
                     ["not_null"] if field in DATE_FIELDS | TIMESTAMP_FIELDS | MONEY_FIELDS |
                     {"currency", "source_file", "source_row", "source_hash", "loaded_at"}
                     else [])
            if tests:
                lines.append("        tests:")
                lines += [f"          - {test}" for test in tests]
    for name, mart in MARTS.items():
        lines += [f"  - name: {name}",
                  f"    description: {json.dumps(mart['description'])}",
                  "    columns:"]
        for field, meaning in mart["columns"].items():
            lines += [f"      - name: {field}",
                      f"        description: {json.dumps(meaning)}"]
            if field == "figure_id":
                lines += ["        tests:", "          - not_null", "          - unique"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    path = Path("docs/DATA_DICTIONARY.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render() + "\n", encoding="utf-8")
    print(path)
    schema = Path("models/schema.yml")
    schema.write_text(render_dbt_schema(), encoding="utf-8")
    print(schema)
