# FinRecon data dictionary

All raw columns retain CSV strings. The matching staging views cast dates to DATE and money to BIGINT with TRY_CAST, leaving invalid values NULL for quality checks to catch. Each staging view also contains the four lineage columns described below.

## ledger

**Grain:** One recorded payment toward money the company owed a vendor (Accounts Payable Settlement Posting)

<!--
**Excludes:** Cash receipts, journal reversals, and non-SGD postings
-->


| Column | Meaning |
|---|---|
| `id` | Unique row identifier within this source feed. |
| `reference` | Payment reference used for exact reconciliation which may be absent from other feeds. |
| `posted_on` | Date recorded by this source, without an intra-day time. |
| `posted_at` | Timezone-aware posting instant, this project's synthetic data uses Asia/Singapore time. |
| `counterparty` | Source spelling of the vendor or payment counterparty. |
| `currency` | ISO currency code and this fixture supports SGD only. |
| `amount_cents` | Signed integer amount in the currency's minor units. |
| `account` | GL account code which is usually AP-SETTLEMENT and has a deliberately planted SUSPENSE anomaly for testing. |
| `contra_account` | Counter-account which is paired with account for journal anomaly features. |
| `created_by` | User who recorded the posting. |
| `approved_by` | User who approved the posting. |
| `source_file` | Absolute path of the ingested CSV. |
| `source_row` | One-based CSV line number, including the header at line 1. |
| `source_hash` | SHA-256 of the full CSV bytes at ingestion. |
| `loaded_at` | Timezone-aware UTC ingestion timestamp. |

## bank

**Grain:** One bank statement movement per row and note that a payment can have multiple rows if it's carried out in batches.

<!--
**Excludes:** Intraday balances and bank fees in separate statement lines.
-->

| Column | Meaning |
|---|---|
| `id` | Unique row identifier within this source feed. |
| `reference` | Payment reference used for exact reconciliation which may be absent from other feeds. |
| `posted_on` | Date recorded by this source, without an intra-day time. |
| `counterparty` | Source spelling of the vendor or payment counterparty. |
| `currency` | ISO currency code and this fixture supports SGD only. |
| `amount_cents` | Signed integer amount in the currency's minor units. |
| `source_file` | Absolute path of the ingested CSV. |
| `source_row` | One-based CSV line number, including the header at line 1. |
| `source_hash` | SHA-256 of the full CSV bytes at ingestion. |
| `loaded_at` | Timezone-aware UTC ingestion timestamp. |

## processor

**Grain:** One processor settlement record per row.

**Excludes:** Chargebacks and reserve holds.

| Column | Meaning |
|---|---|
| `id` | Unique row identifier within this source feed. |
| `reference` | Payment reference used for exact reconciliation which may be absent from other feeds. |
| `settled_on` | Processor settlement date. |
| `currency` | ISO currency code and this fixture supports SGD only. |
| `gross_cents` | Gross settlement before processor fee. |
| `fee_cents` | Processor fee of nonnegative integer cents. |
| `net_cents` | Gross less fee of nonnegative integer cents. |
| `source_file` | Absolute path of the ingested CSV. |
| `source_row` | One-based CSV line number, including the header at line 1. |
| `source_hash` | SHA-256 of the full CSV bytes at ingestion. |
| `loaded_at` | Timezone-aware UTC ingestion timestamp. |

## exchange

**Grain:** One synthetic exchange transfer per row.

**Excludes:** Market prices, network confirmations, and on-chain verification.

| Column | Meaning |
|---|---|
| `id` | Unique row identifier within this source feed. |
| `tx_hash` | Synthetic transaction hash |
| `posted_on` | Date recorded by this source |
| `direction` | Transfer direction relative to the exchange account. |
| `currency` | ISO currency code and this fixture supports SGD only. |
| `amount_cents` | Signed integer amount in the currency's minor units. |
| `source_file` | Absolute path of the ingested CSV. |
| `source_row` | One-based CSV line number, including the header at line 1. |
| `source_hash` | SHA-256 of the full CSV bytes at ingestion. |
| `loaded_at` | Timezone-aware UTC ingestion timestamp. |

## invoices

**Grain:** One accounts-payable invoice per row.

**Excludes:** Credit notes, partial invoices, and tax line items.

| Column | Meaning |
|---|---|
| `id` | Unique row identifier within this source feed. |
| `po_id` | Referenced purchase-order ID. |
| `vendor` | Vendor name recorded on the invoice. |
| `invoice_on` | Invoice issue date. |
| `currency` | ISO currency code and this fixture supports SGD only. |
| `amount_cents` | Signed integer amount in the currency's minor units. |
| `created_by` | User who entered the invoice. |
| `source_file` | Absolute path of the ingested CSV. |
| `source_row` | One-based CSV line number, including the header at line 1. |
| `source_hash` | SHA-256 of the full CSV bytes at ingestion. |
| `loaded_at` | Timezone-aware UTC ingestion timestamp. |

## purchase_orders

**Grain:** One approved purchase order per row.

**Excludes:** PO amendments, line-level budgets (spending limits within a PO), and approval history.

| Column | Meaning |
|---|---|
| `id` | Unique row identifier within this source feed. |
| `vendor` | Vendor named on the order. |
| `approved_on` | Date the order was approved. |
| `currency` | ISO currency code and this fixture supports SGD only. |
| `cap_cents` | Maximum approved amount in cents. |
| `approved_by` | Approver's user ID. |
| `source_file` | Absolute path of the ingested CSV. |
| `source_row` | One-based CSV line number, including the header at line 1. |
| `source_hash` | SHA-256 of the full CSV bytes at ingestion. |
| `loaded_at` | Timezone-aware UTC ingestion timestamp. |

## Warehouse control and BI tables

The `audit` tables below track batch and file publication. The `bi` tables are the read-only snapshot built from persisted findings and controls; the summary audience excludes the source bridge. Money is integer SGD cents. Source business dates use Asia/Singapore; timestamps are UTC.

### audit.load_manifest

**Grain:** One ingested source file per source name and file path.

**Excludes:** Source row contents; the manifest records file identity and load metadata.

| Column | Meaning |
|---|---|
| `source_name` | Feed name such as ledger or bank. |
| `source_file` | Absolute path of the ingested CSV. |
| `sha256` | SHA-256 of the ingested CSV bytes. |
| `row_count` | Number of ingested CSV data rows. |
| `loaded_at` | Timezone-aware UTC ingestion timestamp. |
| `batch_id` | Batch identifier assigned to this delivery; absent on legacy loads. |

### audit.batch_manifest

**Grain:** One batch attempt per batch ID.

**Excludes:** Per-source rows and the separate atomic published-batch pointer.

| Column | Meaning |
|---|---|
| `batch_id` | Unique batch identifier also used in the published batch folder. |
| `status` | Batch state, such as complete or failed. |
| `started_at` | Timezone-aware start timestamp. |
| `completed_at` | Timezone-aware completion timestamp, if finished. |
| `as_of_date` | Latest ledger, bank or invoice business date used for the batch. |
| `business_timezone` | Timezone used to interpret source business dates. |
| `error` | Failure message, if the batch failed. |

### bi.dim_batch

**Grain:** One published batch with its completion and as-of metadata.

**Excludes:** Raw source rows and refresh attempts outside this batch.

| Column | Meaning |
|---|---|
| `batch_key` | Fixed integer key 1 for the one-batch snapshot. |
| `batch_id` | Published batch identifier shared by the BI facts. |
| `status` | Published batch status. |
| `started_at` | Timezone-aware batch start timestamp. |
| `completed_at` | Timezone-aware batch completion timestamp. |
| `as_of_date` | Latest source business date used for age calculations. |
| `as_of_date_key` | As-of date as YYYYMMDD for joining the date dimension. |
| `business_timezone` | Asia/Singapore for source business dates. |
| `calendar` | Age-day convention; Gregorian calendar days include weekends and holidays. |

### bi.dim_date

**Grain:** One calendar day from the earliest source business date through the batch as-of date.

**Excludes:** Holiday calendars and business-day adjustments.

| Column | Meaning |
|---|---|
| `date_key` | Calendar date as integer YYYYMMDD. |
| `calendar_date` | The corresponding DATE value. |
| `year` | Gregorian year. |
| `month` | Calendar month number. |
| `day_of_month` | Calendar day number. |
| `iso_weekday` | ISO weekday number, Monday 1 through Sunday 7. |
| `weekday_name` | English weekday name. |
| `is_weekend` | True for Saturday or Sunday. |

### bi.dim_status

**Grain:** One of the eight deterministic reconciliation categories.

**Excludes:** Fixture truth labels and human decisions.

| Column | Meaning |
|---|---|
| `status_key` | Stable integer key for a reconciliation category. |
| `status` | Category name used by reconciliation facts. |
| `is_break` | True when the category needs review as a break. |
| `description` | Plain-language rule meaning. |

### bi.dim_finding_type

**Grain:** One rule, anomaly-model or contract-cap review type.

**Excludes:** Fraud verdicts and analyst decisions.

| Column | Meaning |
|---|---|
| `finding_type_key` | Stable integer key for the finding type. |
| `finding_type` | Review type name used by finding facts. |
| `finding_source` | Origin group: rule screen, anomaly model or contract cap. |

### bi.fct_reconciliation

**Grain:** One classified REC figure from persisted reconciliation and source links.

**Excludes:** Audit/contract findings and raw source file paths.

| Column | Meaning |
|---|---|
| `batch_id` | Published batch identifier. |
| `figure_id` | Stable REC-prefixed figure key. |
| `reference` | Payment reference used for exact reconciliation. |
| `status_key` | Key joining bi.dim_status. |
| `status` | Deterministic reconciliation category. |
| `is_break` | True when the category needs review as a break. |
| `date_key` | Earliest linked source business date as YYYYMMDD. |
| `posted_on` | Earliest linked source business date. |
| `ledger_cents` | Sum of linked ledger amounts in signed SGD cents. |
| `bank_cents` | Sum of linked bank amounts in signed SGD cents. |
| `difference_cents` | Ledger cents minus bank cents. |
| `age_days` | Calendar days from posted_on through the batch as-of date. |
| `source_rows` | Number of linked ledger and bank source rows. |
| `unresolved_rows` | Linked rows whose date or amount could not be resolved. |
| `explanation` | Rule-generated reason for the category. |
| `ledger_ids` | JSON array of source ledger IDs. |
| `bank_ids` | JSON array of source bank IDs. |

### bi.fct_finding

**Grain:** One AUD, ANOM or DOC review finding; a source row can appear in several findings.

**Excludes:** Fraud verdicts and raw source file paths; amounts are not additive across findings.

| Column | Meaning |
|---|---|
| `batch_id` | Published batch identifier. |
| `figure_id` | Stable finding figure key. |
| `finding_type_key` | Key joining bi.dim_finding_type. |
| `finding_type` | Rule, anomaly-model or contract-cap review type. |
| `finding_source` | Origin group of the finding type. |
| `amount_cents` | Absolute reviewed amount in SGD cents; do not sum across overlapping findings. |
| `date_key` | Earliest linked source business date as YYYYMMDD. |
| `posted_on` | Earliest linked source business date. |
| `age_days` | Calendar days from posted_on through the batch as-of date. |
| `explanation` | Generated reason for human review. |
| `source_ids` | JSON array of linked source IDs, without raw file paths. |

### bi.fct_quality

**Grain:** One named control outcome in the batch quality report.

**Excludes:** The control's full evidence records and raw source rows.

| Column | Meaning |
|---|---|
| `batch_id` | Published batch identifier. |
| `check_name` | Unique named quality control. |
| `check_group` | Prefix of check_name before the first colon. |
| `status` | Recorded pass/fail status of the control. |
| `evidence_count` | Number of evidence items attached to the control. |
| `description` | Human-readable control description. |

### bi.bridge_figure_source

**Grain:** One figure-to-source-row link copied from audit.figure_sources.

**Excludes:** Money amounts; this bridge is excluded from the summary-role export and Hyper extract.

| Column | Meaning |
|---|---|
| `batch_id` | Published batch identifier. |
| `figure_id` | Figure key linked to the source row. |
| `source_name` | Feed that contains the source row. |
| `source_file` | Absolute path of the source CSV. |
| `source_row` | One-based CSV line number including the header. |
| `source_hash` | SHA-256 of the source CSV at ingestion. |

