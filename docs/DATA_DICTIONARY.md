# FinRecon data dictionary

All raw columns retain CSV strings. The matching staging views cast dates to DATE and money to BIGINT with TRY_CAST, leaving invalid values NULL for quality checks to catch. Each staging view also contains the four lineage columns described below.

## ledger

**Grain:** One general-ledger AP settlement posting per row.

**Excludes:** Cash receipts, journal reversals, and non-SGD postings.

| Column | Meaning |
|---|---|
| `id` | Unique row identifier within this source feed. |
| `reference` | Payment reference used for exact reconciliation; may be absent from other feeds. |
| `posted_on` | Date recorded by this source, without an intra-day time. |
| `posted_at` | Timezone-aware posting instant; generated fixture uses Asia/Singapore time. |
| `counterparty` | Source spelling of the vendor or payment counterparty. |
| `currency` | ISO currency code; this fixture supports SGD only. |
| `amount_cents` | Signed integer amount in the currency's minor units. |
| `account` | GL account code; usually AP-SETTLEMENT with one SUSPENSE anomaly. |
| `contra_account` | Counter-account; paired with account for journal anomaly features. |
| `created_by` | User who recorded the posting. |
| `approved_by` | User who approved the posting. |
| `source_file` | Absolute path of the ingested CSV. |
| `source_row` | One-based CSV line number, including the header at line 1. |
| `source_hash` | SHA-256 of the full CSV bytes at ingestion. |
| `loaded_at` | Timezone-aware UTC ingestion timestamp. |

## bank

**Grain:** One bank statement movement per row; a payment can have multiple rows.

**Excludes:** Intraday balances and bank fees in separate statement lines.

| Column | Meaning |
|---|---|
| `id` | Unique row identifier within this source feed. |
| `reference` | Payment reference used for exact reconciliation; may be absent from other feeds. |
| `posted_on` | Date recorded by this source, without an intra-day time. |
| `counterparty` | Source spelling of the vendor or payment counterparty. |
| `currency` | ISO currency code; this fixture supports SGD only. |
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
| `reference` | Payment reference used for exact reconciliation; may be absent from other feeds. |
| `settled_on` | Processor settlement date. |
| `currency` | ISO currency code; this fixture supports SGD only. |
| `gross_cents` | Gross settlement before processor fee. |
| `fee_cents` | Processor fee; nonnegative integer cents. |
| `net_cents` | Gross less fee; nonnegative integer cents. |
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
| `tx_hash` | Synthetic transaction hash; not verified on-chain. |
| `posted_on` | Date recorded by this source, without an intra-day time. |
| `direction` | Transfer direction relative to the exchange account. |
| `currency` | ISO currency code; this fixture supports SGD only. |
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
| `currency` | ISO currency code; this fixture supports SGD only. |
| `amount_cents` | Signed integer amount in the currency's minor units. |
| `created_by` | User who entered the invoice. |
| `source_file` | Absolute path of the ingested CSV. |
| `source_row` | One-based CSV line number, including the header at line 1. |
| `source_hash` | SHA-256 of the full CSV bytes at ingestion. |
| `loaded_at` | Timezone-aware UTC ingestion timestamp. |

## purchase_orders

**Grain:** One approved purchase order per row.

**Excludes:** PO amendments, line-level budgets, and approval history.

| Column | Meaning |
|---|---|
| `id` | Unique row identifier within this source feed. |
| `vendor` | Vendor named on the order. |
| `approved_on` | Date the order was approved. |
| `currency` | ISO currency code; this fixture supports SGD only. |
| `cap_cents` | Maximum approved amount in cents. |
| `approved_by` | Approver's user ID. |
| `source_file` | Absolute path of the ingested CSV. |
| `source_row` | One-based CSV line number, including the header at line 1. |
| `source_hash` | SHA-256 of the full CSV bytes at ingestion. |
| `loaded_at` | Timezone-aware UTC ingestion timestamp. |

