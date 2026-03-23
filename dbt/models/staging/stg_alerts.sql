with source as (
    select * from {{ source('raw', 'alerts') }}
)

select
    alert_id,
    risk_event_id,
    transaction_event_id,
    card_id,
    severity,
    action,
    created_at                              as alerted_at,

    -- Extract fields only available in raw_event JSONB
    raw_event->>'risk_score'                as risk_score_at_alert,
    raw_event->'reasons'                    as reasons_json,
    (raw_event->>'schema_version')::int     as schema_version,

    -- Metadata
    inserted_at                             as loaded_at

from source
where alert_id is not null
