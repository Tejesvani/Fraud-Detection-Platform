-- Fails if any transaction has a non-positive amount
select
    event_id,
    amount
from {{ ref('stg_transactions') }}
where amount <= 0
