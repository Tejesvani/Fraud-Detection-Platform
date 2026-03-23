with source as (
    select * from {{ source('raw', 'transactions') }}
)

select
    event_id,
    card_id,
    transaction_timestamp,
    amount,
    country,
    device_id,

    -- Extract fields only available in raw_event JSONB
    raw_event->>'transaction_type'      as transaction_type,
    raw_event->>'merchant_category'     as merchant_category,
    (raw_event->>'schema_version')::int as schema_version,

    -- Metadata
    created_at                          as loaded_at

from source
where event_id is not null
