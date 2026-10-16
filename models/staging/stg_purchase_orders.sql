select id, vendor, try_cast(approved_on as date) as approved_on,
       currency, try_cast(cap_cents as bigint) as cap_cents,
       approved_by, source_file, source_row, source_hash, loaded_at
from {{ source('raw', 'purchase_orders') }}
