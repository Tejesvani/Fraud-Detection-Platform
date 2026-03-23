-- Fails if any risk_score is outside [0.0, 1.0]
select
    event_id,
    risk_score
from {{ ref('fact_fraud_events') }}
where risk_score is not null
  and (risk_score < 0.0 or risk_score > 1.0)
