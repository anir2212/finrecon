select id, tx_hash, try_cast(posted_on as date) as posted_on,
       direction, currency, try_cast(amount_cents as bigint) as amount_cents,
       source_file, source_row, source_hash, loaded_at
from {{ source('raw', 'exchange') }}
