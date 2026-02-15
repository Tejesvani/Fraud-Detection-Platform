-- ============================================================================
-- Fraud Detection Platform — PostgreSQL Schema Initialization
-- Executed automatically on first container startup via docker-entrypoint-initdb.d
-- Idempotent: safe to re-run (IF NOT EXISTS on all objects)
-- ============================================================================

-- ── transactions ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS transactions (
    event_id                UUID            PRIMARY KEY,
    card_id                 VARCHAR(50)     NOT NULL,
    transaction_timestamp   TIMESTAMPTZ     NOT NULL,
    amount                  NUMERIC(12, 2)  NOT NULL,
    country                 VARCHAR(10),
    device_id               VARCHAR(50),
    created_at              TIMESTAMPTZ     DEFAULT NOW(),
    raw_event               JSONB           NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transactions_card_id
    ON transactions (card_id);

CREATE INDEX IF NOT EXISTS idx_transactions_timestamp
    ON transactions (transaction_timestamp);

-- ── risk_scores ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS risk_scores (
    risk_event_id           UUID            PRIMARY KEY,
    transaction_event_id    UUID            NOT NULL,
    card_id                 VARCHAR(50)     NOT NULL,
    risk_score              NUMERIC(4, 2),
    risk_label              VARCHAR(20),
    evaluated_at            TIMESTAMPTZ     NOT NULL,
    created_at              TIMESTAMPTZ     DEFAULT NOW(),
    raw_event               JSONB           NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_risk_scores_card_id
    ON risk_scores (card_id);

CREATE INDEX IF NOT EXISTS idx_risk_scores_transaction_event_id
    ON risk_scores (transaction_event_id);

-- ── alerts ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS alerts (
    alert_id                UUID            PRIMARY KEY,
    risk_event_id           UUID            NOT NULL,
    transaction_event_id    UUID            NOT NULL,
    card_id                 VARCHAR(50)     NOT NULL,
    severity                VARCHAR(20),
    action                  VARCHAR(50),
    created_at              TIMESTAMPTZ     NOT NULL,
    inserted_at             TIMESTAMPTZ     DEFAULT NOW(),
    raw_event               JSONB           NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_card_id
    ON alerts (card_id);

CREATE INDEX IF NOT EXISTS idx_alerts_severity
    ON alerts (severity);
