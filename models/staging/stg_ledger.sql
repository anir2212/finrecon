select id, reference, try_cast(posted_on as date) as posted_on,
       try_cast(posted_at as timestamptz) as posted_at,
       counterparty, currency, try_cast(amount_cents as bigint) as amount_cents,
       account, contra_account, created_by, approved_by,
       source_file, source_row, source_hash, loaded_at
from {{ source('raw', 'ledger') }}
