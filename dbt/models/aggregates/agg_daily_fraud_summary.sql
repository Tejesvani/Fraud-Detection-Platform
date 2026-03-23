-- Daily fraud summary for dashboard consumption.
-- Grain: one calendar date.

with facts as (
    select * from {{ ref('fact_fraud_events') }}
)

select
    transaction_date                                            as summary_date,

    count(*)                                                    as total_transactions,
    count(*) filter (where is_flagged)                          as flagged_transactions,
    count(*) filter (where is_blocked)                          as blocked_transactions,

    round(
        count(*) filter (where is_flagged)::numeric
        / nullif(count(*), 0) * 100, 2
    )                                                           as fraud_rate_pct,

    round(sum(amount), 2)                                       as total_amount,
    round(sum(amount) filter (where is_blocked), 2)             as blocked_amount,
    round(avg(amount), 2)                                       as avg_amount,

    round(avg(risk_score), 4)                                   as avg_risk_score,
    max(risk_score)                                             as max_risk_score,

    round(avg(end_to_end_latency_ms), 2)                        as avg_latency_ms,
    max(end_to_end_latency_ms)                                  as max_latency_ms,

    count(distinct card_id)                                     as distinct_cards,
    count(distinct merchant_category)                           as distinct_merchants

from facts
group by transaction_date
order by transaction_date
