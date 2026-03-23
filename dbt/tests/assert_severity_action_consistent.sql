-- Fails if severity/action pairs are inconsistent
select
    alert_id,
    severity,
    action
from {{ ref('stg_alerts') }}
where not (
    (severity = 'INFO'     and action = 'LOG_ONLY')
    or (severity = 'WARNING'  and action = 'REVIEW_TRANSACTION')
    or (severity = 'CRITICAL' and action = 'BLOCK_CARD')
)
