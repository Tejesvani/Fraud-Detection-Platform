-- Card-level dimension with aggregated fraud statistics.
-- Grain: one card_id.

with facts as (
    select * from {{ ref('fact_fraud_events') }}
)

select
    card_id,

    count(*)                                                    as total_transactions,
    count(*) filter (where is_flagged)                          as flagged_transactions,
    count(*) filter (where is_blocked)                          as blocked_transactions,

    round(
        count(*) filter (where is_flagged)::numeric
        / nullif(count(*), 0) * 100, 2
    )                                                           as fraud_rate_pct,

    round(avg(amount), 2)                                       as avg_amount,
    max(amount)                                                 as max_amount,
    sum(amount)                                                 as total_amount,

    round(avg(risk_score), 4)                                   as avg_risk_score,
    max(risk_score)                                             as max_risk_score,

    min(transaction_timestamp)                                  as first_seen_at,
    max(transaction_timestamp)                                  as last_seen_at,

    count(distinct country)                                     as distinct_countries,
    count(distinct device_id)                                   as distinct_devices,
    count(distinct merchant_category)                           as distinct_merchants

from facts
group by card_id
