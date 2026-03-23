-- Merchant category dimension with aggregated fraud statistics.
-- Grain: one merchant_category.

with facts as (
    select * from {{ ref('fact_fraud_events') }}
)

select
    merchant_category,

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

    count(distinct card_id)                                     as distinct_cards,

    min(transaction_timestamp)                                  as first_seen_at,
    max(transaction_timestamp)                                  as last_seen_at

from facts
where merchant_category is not null
group by merchant_category
