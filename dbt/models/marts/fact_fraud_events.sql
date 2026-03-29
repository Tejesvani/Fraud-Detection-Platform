-- One row per transaction, enriched with risk scoring and alert outcome.
-- Grain: one transaction event.

with transactions as (
    select * from {{ ref('stg_transactions') }}
),

risk_scores as (
    select * from {{ ref('stg_risk_scores') }}
),

alerts as (
    select * from {{ ref('stg_alerts') }}
)

select
    -- Transaction fields
    t.event_id,
    t.card_id,
    t.transaction_type,
    t.merchant_category,
    t.amount,
    t.country,
    t.device_id,
    t.transaction_timestamp,

    -- Risk scoring fields
    r.risk_event_id,
    r.risk_score,
    r.risk_label,
    r.reasons_json                          as risk_reasons,
    r.evaluated_at,

    -- Alert fields
    a.alert_id,
    a.severity,
    a.action,
    a.alerted_at,

    -- Derived metrics
    extract(epoch from (r.evaluated_at - t.transaction_timestamp)) * 1000
        as scoring_latency_ms,
    extract(epoch from (a.alerted_at - t.transaction_timestamp)) * 1000
        as end_to_end_latency_ms,

    -- Flags
    case when a.severity = 'CRITICAL' then true else false end
        as is_blocked,
    case when a.severity in ('WARNING', 'CRITICAL') then true else false end
        as is_flagged,

    -- Date key for joining to dim_dates
    t.transaction_timestamp::date           as transaction_date,

    -- Schema versions
    t.schema_version                        as transaction_schema_version,
    r.schema_version                        as risk_schema_version,
    a.schema_version                        as alert_schema_version

from transactions t
left join risk_scores r
    on r.transaction_event_id = t.event_id
left join alerts a
    on a.transaction_event_id = t.event_id
