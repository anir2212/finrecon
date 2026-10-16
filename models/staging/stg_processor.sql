select id, reference, try_cast(settled_on as date) as settled_on,
       currency, try_cast(gross_cents as bigint) as gross_cents,
       try_cast(fee_cents as bigint) as fee_cents,
       try_cast(net_cents as bigint) as net_cents,
       source_file, source_row, source_hash, loaded_at
from {{ source('raw', 'processor') }}
