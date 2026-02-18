-- ============================================================================
-- Fraud Detection Platform — Production PostgreSQL Schema
-- Executed automatically on first postgres-prod container startup
-- Append-only tables: no upsert, no ON CONFLICT, insert only
-- Column names match Avro schema field names for JDBC Sink auto-mapping
-- ============================================================================

-- ── transactions_prod ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS transactions_prod (
    id                    BIGSERIAL       PRIMARY KEY,
    event_id              VARCHAR(36)     NOT NULL,
    card_id               VARCHAR(50)     NOT NULL,
    transaction_type      VARCHAR(50)     NOT NULL,
    merchant_category     VARCHAR(50)     NOT NULL,
    amount                NUMERIC(12, 2)  NOT NULL,
    country               VARCHAR(10)     NOT NULL,
    device_id             VARCHAR(50)     NOT NULL,
    timestamp             TIMESTAMPTZ     NOT NULL,
    source                VARCHAR(20)     NOT NULL,
    inserted_at           TIMESTAMPTZ     DEFAULT NOW()
);

-- ── risk_scores_prod ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS risk_scores_prod (
    id                      BIGSERIAL       PRIMARY KEY,
    risk_event_id           VARCHAR(36)     NOT NULL,
    transaction_event_id    VARCHAR(36)     NOT NULL,
    card_id                 VARCHAR(50)     NOT NULL,
    risk_score              NUMERIC(4, 2)   NOT NULL,
    risk_label              VARCHAR(20)     NOT NULL,
    evaluated_at            TIMESTAMPTZ     NOT NULL,
    inserted_at             TIMESTAMPTZ     DEFAULT NOW()
);

-- ── alerts_prod ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS alerts_prod (
    id                      BIGSERIAL       PRIMARY KEY,
    alert_id                VARCHAR(36)     NOT NULL,
    risk_event_id           VARCHAR(36)     NOT NULL,
    transaction_event_id    VARCHAR(36)     NOT NULL,
    card_id                 VARCHAR(50)     NOT NULL,
    severity                VARCHAR(20)     NOT NULL,
    action                  VARCHAR(50)     NOT NULL,
    created_at              TIMESTAMPTZ     NOT NULL,
    inserted_at             TIMESTAMPTZ     DEFAULT NOW()
);
