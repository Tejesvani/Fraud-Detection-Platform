-- Hourly transaction velocity per card.
-- Grain: one card_id per hour.

with facts as (
    select * from {{ ref('fact_fraud_events') }}
)

select
    card_id,
    date_trunc('hour', transaction_timestamp)                   as hour_bucket,

    count(*)                                                    as transaction_count,
    round(sum(amount), 2)                                       as total_amount,
    round(avg(amount), 2)                                       as avg_amount,
    round(avg(risk_score), 4)                                   as avg_risk_score,

    count(*) filter (where is_flagged)                          as flagged_count,
    count(*) filter (where is_blocked)                          as blocked_count,

    count(distinct country)                                     as distinct_countries,
    count(distinct device_id)                                   as distinct_devices

from facts
group by card_id, date_trunc('hour', transaction_timestamp)
