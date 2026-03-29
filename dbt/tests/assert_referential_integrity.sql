-- Fails if any risk_score references a transaction that doesn't exist
select
    r.risk_event_id,
    r.transaction_event_id
from {{ ref('stg_risk_scores') }} r
left join {{ ref('stg_transactions') }} t
    on t.event_id = r.transaction_event_id
where t.event_id is null
