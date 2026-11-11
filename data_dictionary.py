"""Single source of truth for dataset grain, exclusions and column meaning."""

from __future__ import annotations

from pathlib import Path
import json

from gen.synthetic import HEADERS
from warehouse import DATE_FIELDS, MONEY_FIELDS, TIMESTAMP_FIELDS


COMMON = {
    "id": "Unique row identifier within this source feed.",
    "reference": "Payment reference used for exact reconciliation which may be absent from other feeds.",
    "posted_on": "Date recorded by this source, without an intra-day time.",
    "posted_at": "Timezone-aware posting instant, this project's synthetic data uses Asia/Singapore time.",
    "counterparty": "Source spelling of the vendor or payment counterparty.",
    "currency": "ISO currency code and this fixture supports SGD only.",
    "amount_cents": "Signed integer amount in the currency's minor units.",
    "source_file": "Absolute path of the ingested CSV.",
    "source_row": "One-based CSV line number, including the header at line 1.",
    "source_hash": "SHA-256 of the full CSV bytes at ingestion.",
    "loaded_at": "Timezone-aware UTC ingestion timestamp.",
}

DATASETS = {
    "ledger": {
        "grain": "One recorded payment toward money the company owed a vendor (Accounts Payable Settlement Posting)",
        "excludes": "Cash receipts, journal reversals, and non-SGD postings",
        "excludes_comment": True,
        "extra_blank_after_excludes": True,
        "columns": {"account": "GL account code which is usually AP-SETTLEMENT and has a deliberately planted SUSPENSE anomaly for testing.",
                    "contra_account": "Counter-account which is paired with account for journal anomaly features.",
                    "created_by": "User who recorded the posting.",
                    "approved_by": "User who approved the posting."},
    },
    "bank": {
        "grain": "One bank statement movement per row and note that a payment can have multiple rows if it's carried out in batches.",
        "excludes": "Intraday balances and bank fees in separate statement lines.",
        "excludes_comment": True,
        "columns": {},
    },
    "processor": {
        "grain": "One processor settlement record per row.",
        "excludes": "Chargebacks and reserve holds.",
        "columns": {"settled_on": "Processor settlement date.",
                    "gross_cents": "Gross settlement before processor fee.",
                    "fee_cents": "Processor fee of nonnegative integer cents.",
                    "net_cents": "Gross less fee of nonnegative integer cents."},
    },
    "exchange": {
        "grain": "One synthetic exchange transfer per row.",
        "excludes": "Market prices, network confirmations, and on-chain verification.",
        "columns": {"tx_hash": "Synthetic transaction hash",
                    "posted_on": "Date recorded by this source",
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
        "excludes": "PO amendments, line-level budgets (spending limits within a PO), and approval history.",
        "columns": {"vendor": "Vendor named on the order.",
                    "approved_on": "Date the order was approved.",
                    "cap_cents": "Maximum approved amount in cents.",
                    "approved_by": "Approver's user ID."},
    },
}

EXTRA_TABLES = {
    "audit.load_manifest": {
        "grain": "One ingested source file per source name and file path.",
        "excludes": "Source row contents; the manifest records file identity and load metadata.",
        "columns": {
            "source_name": "Feed name such as ledger or bank.",
            "source_file": "Absolute path of the ingested CSV.",
            "sha256": "SHA-256 of the ingested CSV bytes.",
            "row_count": "Number of ingested CSV data rows.",
            "loaded_at": "Timezone-aware UTC ingestion timestamp.",
            "batch_id": "Batch identifier assigned to this delivery; absent on legacy loads.",
        },
    },
    "audit.batch_manifest": {
        "grain": "One batch attempt per batch ID.",
        "excludes": "Per-source rows and the separate atomic published-batch pointer.",
        "columns": {
            "batch_id": "Unique batch identifier also used in the published batch folder.",
            "status": "Batch state, such as complete or failed.",
            "started_at": "Timezone-aware start timestamp.",
            "completed_at": "Timezone-aware completion timestamp, if finished.",
            "as_of_date": "Latest ledger, bank or invoice business date used for the batch.",
            "business_timezone": "Timezone used to interpret source business dates.",
            "error": "Failure message, if the batch failed.",
        },
    },
    "bi.dim_batch": {
        "grain": "One published batch with its completion and as-of metadata.",
        "excludes": "Raw source rows and refresh attempts outside this batch.",
        "columns": {
            "batch_key": "Fixed integer key 1 for the one-batch snapshot.",
            "batch_id": "Published batch identifier shared by the BI facts.",
            "status": "Published batch status.",
            "started_at": "Timezone-aware batch start timestamp.",
            "completed_at": "Timezone-aware batch completion timestamp.",
            "as_of_date": "Latest source business date used for age calculations.",
            "as_of_date_key": "As-of date as YYYYMMDD for joining the date dimension.",
            "business_timezone": "Asia/Singapore for source business dates.",
            "calendar": "Age-day convention; Gregorian calendar days include weekends and holidays.",
        },
    },
    "bi.dim_date": {
        "grain": "One calendar day from the earliest source business date through the batch as-of date.",
        "excludes": "Holiday calendars and business-day adjustments.",
        "columns": {
            "date_key": "Calendar date as integer YYYYMMDD.",
            "calendar_date": "The corresponding DATE value.",
            "year": "Gregorian year.",
            "month": "Calendar month number.",
            "day_of_month": "Calendar day number.",
            "iso_weekday": "ISO weekday number, Monday 1 through Sunday 7.",
            "weekday_name": "English weekday name.",
            "is_weekend": "True for Saturday or Sunday.",
        },
    },
    "bi.dim_status": {
        "grain": "One of the eight deterministic reconciliation categories.",
        "excludes": "Fixture truth labels and human decisions.",
        "columns": {
            "status_key": "Stable integer key for a reconciliation category.",
            "status": "Category name used by reconciliation facts.",
            "is_break": "True when the category needs review as a break.",
            "description": "Plain-language rule meaning.",
        },
    },
    "bi.dim_finding_type": {
        "grain": "One rule, anomaly-model or contract-cap review type.",
        "excludes": "Fraud verdicts and analyst decisions.",
        "columns": {
            "finding_type_key": "Stable integer key for the finding type.",
            "finding_type": "Review type name used by finding facts.",
            "finding_source": "Origin group: rule screen, anomaly model or contract cap.",
        },
    },
    "bi.fct_reconciliation": {
        "grain": "One classified REC figure from persisted reconciliation and source links.",
        "excludes": "Audit/contract findings and raw source file paths.",
        "columns": {
            "batch_id": "Published batch identifier.",
            "figure_id": "Stable REC-prefixed figure key.",
            "reference": "Payment reference used for exact reconciliation.",
            "status_key": "Key joining bi.dim_status.",
            "status": "Deterministic reconciliation category.",
            "is_break": "True when the category needs review as a break.",
            "date_key": "Earliest linked source business date as YYYYMMDD.",
            "posted_on": "Earliest linked source business date.",
            "ledger_cents": "Sum of linked ledger amounts in signed SGD cents.",
            "bank_cents": "Sum of linked bank amounts in signed SGD cents.",
            "difference_cents": "Ledger cents minus bank cents.",
            "age_days": "Calendar days from posted_on through the batch as-of date.",
            "source_rows": "Number of linked ledger and bank source rows.",
            "unresolved_rows": "Linked rows whose date or amount could not be resolved.",
            "explanation": "Rule-generated reason for the category.",
            "ledger_ids": "JSON array of source ledger IDs.",
            "bank_ids": "JSON array of source bank IDs.",
        },
    },
    "bi.fct_finding": {
        "grain": "One AUD, ANOM or DOC review finding; a source row can appear in several findings.",
        "excludes": "Fraud verdicts and raw source file paths; amounts are not additive across findings.",
        "columns": {
            "batch_id": "Published batch identifier.",
            "figure_id": "Stable finding figure key.",
            "finding_type_key": "Key joining bi.dim_finding_type.",
            "finding_type": "Rule, anomaly-model or contract-cap review type.",
            "finding_source": "Origin group of the finding type.",
            "amount_cents": "Absolute reviewed amount in SGD cents; do not sum across overlapping findings.",
            "date_key": "Earliest linked source business date as YYYYMMDD.",
            "posted_on": "Earliest linked source business date.",
            "age_days": "Calendar days from posted_on through the batch as-of date.",
            "explanation": "Generated reason for human review.",
            "source_ids": "JSON array of linked source IDs, without raw file paths.",
        },
    },
    "bi.fct_quality": {
        "grain": "One named control outcome in the batch quality report.",
        "excludes": "The control's full evidence records and raw source rows.",
        "columns": {
            "batch_id": "Published batch identifier.",
            "check_name": "Unique named quality control.",
            "check_group": "Prefix of check_name before the first colon.",
            "status": "Recorded pass/fail status of the control.",
            "evidence_count": "Number of evidence items attached to the control.",
            "description": "Human-readable control description.",
        },
    },
    "bi.bridge_figure_source": {
        "grain": "One figure-to-source-row link copied from audit.figure_sources.",
        "excludes": "Money amounts; this bridge is excluded from the summary-role export and Hyper extract.",
        "columns": {
            "batch_id": "Published batch identifier.",
            "figure_id": "Figure key linked to the source row.",
            "source_name": "Feed that contains the source row.",
            "source_file": "Absolute path of the source CSV.",
            "source_row": "One-based CSV line number including the header.",
            "source_hash": "SHA-256 of the source CSV at ingestion.",
        },
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
        lines += [f"## {name}", "", f"**Grain:** {doc['grain']}", ""]
        if doc.get("excludes_comment"):
            lines += ["<!--", f"**Excludes:** {doc['excludes']}", "-->", ""]
            if doc.get("extra_blank_after_excludes"):
                lines.append("")
        else:
            lines += [f"**Excludes:** {doc['excludes']}", ""]
        lines += ["| Column | Meaning |", "|---|---|"]
        for field in [*headers, "source_file", "source_row", "source_hash", "loaded_at"]:
            description = doc["columns"].get(field, COMMON.get(field))
            if description is None:
                raise ValueError(f"undocumented column: {name}.{field}")
            lines.append(f"| `{field}` | {description} |")
        lines.append("")
    lines += ["## Warehouse control and BI tables", "",
              "The `audit` tables below track batch and file publication. The `bi` tables "
              "are the read-only snapshot built from persisted findings and controls; "
              "the summary audience excludes the source bridge. Money is integer SGD cents. "
              "Source business dates use Asia/Singapore; timestamps are UTC.", ""]
    for name, doc in EXTRA_TABLES.items():
        lines += [f"### {name}", "", f"**Grain:** {doc['grain']}", "",
                  f"**Excludes:** {doc['excludes']}", "",
                  "| Column | Meaning |", "|---|---|"]
        for field, description in doc["columns"].items():
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
