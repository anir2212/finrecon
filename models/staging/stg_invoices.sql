select id, po_id, vendor, try_cast(invoice_on as date) as invoice_on,
       currency, try_cast(amount_cents as bigint) as amount_cents,
       created_by, source_file, source_row, source_hash, loaded_at
from {{ source('raw', 'invoices') }}
