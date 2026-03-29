with source as (
    select * from {{ source('raw', 'risk_scores') }}
)

select
    risk_event_id,
    transaction_event_id,
    card_id,
    risk_score,
    risk_label,
    evaluated_at,

    -- Extract fields only available in raw_event JSONB
    raw_event->'reasons'                    as reasons_json,
    (raw_event->>'schema_version')::int     as schema_version,

    -- Metadata
    created_at                              as loaded_at

from source
where risk_event_id is not null
