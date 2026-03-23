# Fraud Detection Platform

A real-time fraud detection system built on Apache Kafka. Simulated card transactions are streamed through a pipeline that evaluates each transaction for fraud signals, produces risk scores, and emits alerts — all in real time. Events are serialized with **Apache Avro** and validated by **Confluent Schema Registry**. A two-layer data quality system enforces contracts at every producer and audits the full stream independently. All events are persisted to PostgreSQL. Includes a Streamlit UI for manual transaction submission.

---

## Architecture

```
  PRODUCERS                    KAFKA BROKER (localhost:9092)              CONSUMERS
  ─────────                   ┌───────────────────────────┐              ─────────
                              │                           │
┌──────────────────┐          │  ┌─────────────────────┐  │          ┌──────────────────────┐
│                  │ produce  │  │                     │  │ consume  │                      │
│   Transaction    │─────────>│──│    transactions     │──│─────────>│  Risk Score          │
│   Streamer (L1✓) │  (Avro)  │  │    (3 partitions)   │  │  (Avro)  │  Processor (L1✓)     │
│                  │          │  │                     │  │          │                      │
└──────────────────┘          │  │          ▲          │  │          └──────────────────────┘
                              │  └──────────│──────────┘  │                    │
┌──────────────────┐ produce  │             │             │                    │ produce
│                  │──────────│─────────────┘             │                    │  (Avro)
│   Streamlit UI   │  (Avro)  │                           │                    │
│   (Frontend,L1✓) │          │  ┌─────────────────────┐  │                    │
│                  │          │  │                     │<─│────────────────────┘
└────────┬─────────┘          │  │    risk_scores      │  │
         │                    │  │    (3 partitions)   │  │          ┌──────────────────────┐
         │                    │  │                     │──│─────────>│                      │
         │                    │  └─────────────────────┘  │ consume  │  Alert Service (L1✓) │
         │                    │                           │  (Avro)  │                      │
         │                    │                           │          └──────────────────────┘
         │                    │  ┌─────────────────────┐  │                    │
         │           poll     │  │                     │<─│────────────────────┘
         └───────────────────<│──│    alerts           │  │            produce
                    alerts    │  │    (1 partition)    │  │             (Avro)
                    (Avro)    │  │                     │  │
                              │  └─────────────────────┘  │
                              │                           │     ┌─────────────────────────┐
                              │  ┌─────────────────────┐  │<────│  Validation Service     │
                              │  │    dlq              │  │ fail│  (Layer 2 audit)        │
                              │  │    (JSON, 7 days)   │  │     │  consumes all 3 topics  │
                              │  └─────────────────────┘  │     │  in parallel            │
                              │                           │     └─────────────────────────┘
                              └───────────────────────────┘
                                        │
                              ┌─────────▼─────────┐
                              │  Schema Registry  │  ←── All producers register/validate
                              │  (localhost:8081) │       schemas before producing
                              └───────────────────┘
                                        │ consume (all 3 topics)
                                        ▼
                              ┌──────────────────────┐
                              │                      │
                              │  Persistence Service │──────> PostgreSQL
                              │                      │        (fraud_detection_db)
                              └──────────────────────┘
```

`L1✓` — producer-side validation (Layer 1) is applied before every produce call. Invalid events are rejected at the source and never enter the stream.

The system is a Kafka-based event-driven fraud detection pipeline with a two-layer data quality architecture. Two producers — a background Transaction Streamer and a user-facing Streamlit UI — publish Avro-encoded transaction events to the `transactions` topic. A Risk Score Processor consumes, evaluates fraud risk, and publishes scored results to `risk_scores`. An Alert Service maps risk labels to severity and action and publishes to `alerts`. A Persistence Service consumes all three topics and writes every event to PostgreSQL. A Validation Service runs in parallel, independently auditing all three topics and routing failures to a dead-letter queue (`dlq`). All messages are serialized with Apache Avro using Confluent Schema Registry for schema validation and evolution.

**Data flow:**
1. **Producers (L1✓)** validate events in-process, then serialize as Avro and publish to `transactions`:
   - `transaction_streamer.py` generates one synthetic transaction every 2 seconds.
   - The Streamlit UI submits user-selected transactions; validation errors surface in the UI.
2. **Risk Score Processor (L1✓)** consumes from `transactions` (Avro), evaluates four fraud signals, validates the risk event, and produces an immutable risk event to `risk_scores` (Avro).
3. **Alert Service (L1✓)** consumes from `risk_scores` (Avro), maps risk labels to severity and action, validates the alert, and produces an alert event to `alerts` (Avro).
4. **Persistence Service** consumes from all three topics (Avro), deserializes, and persists every event to PostgreSQL with idempotent inserts.
5. **Validation Service (Layer 2)** independently consumes from all three topics in parallel, re-validates every event, and routes failures (including deserialization errors from non-Avro messages) to the `dlq` topic as JSON.
6. For UI-submitted transactions, the Streamlit UI polls the `alerts` topic, deserializes (Avro), matches on `transaction_event_id`, and displays the fraud analysis result.
7. **dbt** runs as a scheduled batch process on top of the streaming pipeline, reading from the raw PostgreSQL tables (`public` schema) and building an analytical layer in the `analytics` schema.

**dbt transformation flow (batch layer):**

```
PostgreSQL (public schema)          dbt (batch, scheduled)          PostgreSQL (analytics schema)
├── transactions          ──────>   ├── stg_transactions    ──────> ├── fact_fraud_events
├── risk_scores           ──────>   ├── stg_risk_scores     ──────> ├── dim_cards
└── alerts                ──────>   └── stg_alerts          ──────> ├── dim_merchants
                                                                    ├── dim_dates
                                                                    ├── agg_daily_fraud_summary
                                                                    └── agg_hourly_card_velocity
```

Staging models are **views** (always fresh, no extra storage). Marts and aggregates are **tables** (pre-computed for query performance). All models write to the `analytics` schema — the raw tables in `public` are never modified.

---

## Folder Structure

```
fraud-detection-platform/
├── .github/
│   └── workflows/
│       └── schema-ci.yml               # CI: validates Avro schema backward compatibility on PRs
├── kafka-local/
│   ├── docker-compose.yml              # Infrastructure-only: Zookeeper, Kafka, Schema Registry, PostgreSQL
│   ├── .env                            # Docker Compose vars (Kafka port, Postgres creds)
│   ├── .env.example                    # Template for kafka-local/.env
│   ├── connect-image/
│   │   └── postgresql-42.7.1.jar       # JDBC driver for future Kafka Connect use
│   └── postgres-init/
│       └── init.sql                    # Auto-creates PostgreSQL tables on first startup
├── kafka/
│   ├── create-topics.sh                # Creates all Kafka topics (incl. dlq)
│   └── delete-topics.sh               # Tears down all Kafka topics
├── schemas/
│   ├── transaction_event.avsc          # Avro schema for the transactions topic
│   ├── risk_score_event.avsc           # Avro schema for the risk_scores topic
│   ├── alert_event.avsc                # Avro schema for the alerts topic
│   ├── compatibility-policy.md         # Schema evolution rules and decisions
│   └── scripts/
│       └── check_compatibility.py      # Local + CI schema compatibility checker
├── shared/
│   ├── __init__.py
│   └── schema_registry.py              # Shared Avro serializer/deserializer helpers
├── data_quality/
│   ├── Dockerfile                      # Docker image for the validation service
│   ├── __init__.py
│   ├── validation_service.py           # Layer 2: parallel audit consumer → dlq
│   ├── dlq_reprocessor.py              # CLI: inspect and replay dead-letter events
│   ├── contracts/
│   │   ├── __init__.py
│   │   ├── transaction_contract.py     # Pure validation rules for TransactionEvent
│   │   ├── risk_score_contract.py      # Pure validation rules for RiskScoreEvent
│   │   └── alert_contract.py           # Pure validation rules for AlertEvent
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py                 # Shared test fixtures (valid sample events)
│       ├── test_transaction_contract.py
│       ├── test_risk_score_contract.py
│       └── test_alert_contract.py
├── producer/
│   ├── Dockerfile                      # Build context: project root
│   └── transaction_streamer.py         # Kafka producer — synthetic transaction generator
├── processor/
│   ├── Dockerfile
│   └── risk_score_processor.py         # Kafka consumer/producer — risk scoring engine
├── consumer/
│   ├── Dockerfile.alert                # Alert service image
│   ├── Dockerfile.persistence          # Persistence service image
│   ├── alert_service.py                # Kafka consumer/producer — alert emitter
│   └── persistence_service.py          # Kafka consumer — persists all events to PostgreSQL
├── frontend/
│   ├── Dockerfile
│   └── app.py                          # Streamlit UI — manual transaction submission
├── dbt/
│   ├── Dockerfile                      # dbt-postgres image (runs dbt run + dbt test)
│   ├── dbt_project.yml                 # Project config — model paths, materialization strategy
│   ├── profiles.yml                    # DB connection via POSTGRES_* env vars
│   ├── models/
│   │   ├── sources.yml                 # Raw table definitions + source freshness checks
│   │   ├── staging/
│   │   │   ├── _staging_models.yml     # Schema tests for staging models
│   │   │   ├── stg_transactions.sql    # Cleans raw transactions, extracts JSONB fields
│   │   │   ├── stg_risk_scores.sql     # Cleans raw risk_scores, extracts JSONB fields
│   │   │   └── stg_alerts.sql          # Cleans raw alerts, extracts JSONB fields
│   │   ├── marts/
│   │   │   ├── _marts_models.yml       # Schema tests for mart models
│   │   │   ├── fact_fraud_events.sql   # Pre-joined fact table (transaction + risk + alert)
│   │   │   ├── dim_cards.sql           # Card-level aggregates (fraud rate, avg amount)
│   │   │   ├── dim_merchants.sql       # Merchant category fraud statistics
│   │   │   └── dim_dates.sql           # Standard date dimension (generated date spine)
│   │   └── aggregates/
│   │       ├── _aggregates_models.yml  # Schema tests for aggregate models
│   │       ├── agg_daily_fraud_summary.sql     # Daily rollup for dashboard consumption
│   │       └── agg_hourly_card_velocity.sql    # Hourly per-card velocity analysis
│   ├── tests/
│   │   ├── assert_risk_score_range.sql         # Fails if risk_score outside [0, 1]
│   │   ├── assert_referential_integrity.sql    # Fails if risk_score has no matching transaction
│   │   ├── assert_positive_amounts.sql         # Fails if amount <= 0
│   │   └── assert_severity_action_consistent.sql  # Fails on invalid severity/action pairs
│   └── macros/
│       └── generate_date_spine.sql     # PostgreSQL generate_series date spine macro
├── docker-compose.yml                  # Full-stack: infrastructure + all application services
├── .env                                # Runtime env vars for Python services (not committed)
├── .env.example                        # Template for root .env
├── requirements.txt                    # Python dependencies (pinned)
└── README.md
```

---

## Environment Configuration

Two separate `.env` files serve different purposes:

| File | Used by | Purpose |
|------|---------|---------|
| `kafka-local/.env` | Docker Compose (infra only) | Kafka ports, PostgreSQL container credentials |
| `.env` (root) | Python services + full-stack compose | Kafka broker, topics, group IDs, Schema Registry, PostgreSQL |

The root `.env` must have matching `POSTGRES_*` values to `kafka-local/.env`.

```bash
cp .env.example .env
# edit if needed — defaults work out-of-the-box
```

### Root `.env` Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka broker address |
| `KAFKA_TOPIC_TRANSACTIONS` | `transactions` | Topic for raw transaction events |
| `KAFKA_TOPIC_RISK_SCORES` | `risk_scores` | Topic for scored risk events |
| `KAFKA_TOPIC_ALERTS` | `alerts` | Topic for alert events |
| `KAFKA_TOPIC_DLQ` | `dlq` | Dead-letter queue topic name |
| `KAFKA_GROUP_RISK_PROCESSOR` | `risk-score-processor-group` | Consumer group for risk processor |
| `KAFKA_GROUP_ALERT_SERVICE` | `alert-service-group` | Consumer group for alert service |
| `KAFKA_GROUP_PERSISTENCE` | `persistence-service-group` | Consumer group for persistence service |
| `KAFKA_GROUP_VALIDATION` | `validation-service-group` | Consumer group for validation service |
| `SCHEMA_REGISTRY_URL` | `http://localhost:8081` | Schema Registry endpoint |
| `SCHEMA_REGISTRY_ENABLED` | `true` | Set to `false` to fall back to plain JSON |
| `POSTGRES_USER` | `fraud_user` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `fraud_password` | PostgreSQL password |
| `POSTGRES_HOST` | `localhost` | PostgreSQL host |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_DB` | `fraud_detection_db` | PostgreSQL database name |

---

## Schema Registry & Avro Serialization

All Kafka messages are serialized with **Apache Avro** and validated by **Confluent Schema Registry** before being written to a topic. This catches contract violations at the producer — not at 3am in the persistence layer.

### How it works

- Schemas are defined as `.avsc` files in `schemas/`.
- `shared/schema_registry.py` provides singleton `AvroSerializer` / `AvroDeserializer` instances used by all services.
- On first produce, the serializer auto-registers the schema under the subject `<topic>-value` (TopicNameStrategy).
- Each message is encoded in the **Confluent wire format**: `[magic byte][4-byte schema ID][Avro payload]`.
- If `SCHEMA_REGISTRY_ENABLED=false`, all services fall back to plain JSON automatically.

### Registered Subjects

| Subject | Schema File | Compatibility |
|---------|-------------|---------------|
| `transactions-value` | `transaction_event.avsc` | BACKWARD |
| `risk_scores-value` | `risk_score_event.avsc` | BACKWARD |
| `alerts-value` | `alert_event.avsc` | BACKWARD |

**BACKWARD compatibility** means new consumers can always read data written by older producers. Adding fields is safe if they have a default. Changing types or removing required fields is blocked.

### Schema Evolution CI

The `.github/workflows/schema-ci.yml` workflow runs on every PR that touches `schemas/`:
1. Starts a temporary Zookeeper + Kafka + Schema Registry stack.
2. Registers the **base branch** schemas (current production state).
3. Checks the **PR schemas** for backward compatibility.
4. Fails the PR if any schema breaks the compatibility contract.

### Checking compatibility locally

```bash
# Check local schemas against the running Schema Registry
python schemas/scripts/check_compatibility.py

# Register schemas (seed a fresh registry)
python schemas/scripts/check_compatibility.py --register
```

---

## Data Quality & Validation

The platform enforces data contracts through a **two-layer validation architecture**. Both layers use the same pure contract functions — `data_quality/contracts/` is the single source of truth.

### Layer 1 — Producer-side validation (primary defense)

Every service that produces events validates the data **before** serializing or publishing to Kafka. If validation fails, the event is logged with full error details and dropped — it never enters the stream. This validation is in-process, adds microseconds of latency, and scales automatically with the producer.

| Service | Contract | Failure behavior |
|---------|----------|-----------------|
| `producer/transaction_streamer.py` | `validate_transaction` | Logged in red, skipped, producer continues |
| `processor/risk_score_processor.py` | `validate_risk_score` | Logged in red, offset committed, loop continues |
| `consumer/alert_service.py` | `validate_alert` | Logged in red, offset committed, loop continues |
| `frontend/app.py` | `validate_transaction` | Errors displayed per rule via `st.error()`, not produced |

### Layer 2 — Parallel validation service (audit / monitoring)

`data_quality/validation_service.py` is a separate long-running consumer that **does not gate the pipeline**. It independently reads all three topics using its own consumer group (`validation-service-group`) and re-validates every event against the same contracts. It also catches messages that bypass Avro serialization entirely (e.g., plain JSON injected directly into Kafka).

Three categories of failure are routed to the `dlq` topic:
1. **Deserialization errors** — non-Avro bytes that can't be parsed (external producers, corrupted messages).
2. **Business rule violations** — events that parse correctly but fail contract rules.
3. **Edge cases** — bugs in producer-side validation, race conditions, or future producers you don't control.

Periodic quality summaries print every 60 seconds or every 100 events:
```
[QUALITY] processed=400 valid=399 failed=1 (deser_errors=1) (0.25% failure rate)
```

**Why both layers?** Producer-side validation prevents bad data at near-zero cost and is the main line of defense. The parallel validator provides operational visibility into data quality trends and is a safety net for anything that slips through — including external producers you don't control.

### Data contracts

Contracts are **pure functions** with no I/O, no Kafka imports, and no side effects: `dict in → list[dict] out`. Every rule is checked independently so all failures are returned at once, not just the first.

| Contract | Rules enforced |
|----------|---------------|
| `transaction_contract.py` | Presence checks (event_id, timestamp, card_id, etc.), UUID v4 format, ISO-8601 timestamp, timestamp not > 5 min in the future, card_id format (`card[N]`), valid transaction type enum, amount range (> 0, < 100 000), ISO-3166 alpha-2 country code, schema_version is int |
| `risk_score_contract.py` | UUID presence and format for risk_event_id and transaction_event_id, risk_score in [0.0, 1.0], valid risk_label enum, label/score consistency (LOW < 0.3, MEDIUM 0.3–0.7, HIGH > 0.7), reasons is a list, ISO-8601 evaluated_at, schema_version is int |
| `alert_contract.py` | UUID presence and format for alert_id, severity and action are valid enums, severity/action pair consistency (INFO→LOG_ONLY, WARNING→REVIEW_TRANSACTION, CRITICAL→BLOCK_CARD), risk_score in [0.0, 1.0], reasons is a list, ISO-8601 created_at, schema_version is int |

### Dead-letter queue

Failed events are published to the `dlq` topic as plain JSON (not Avro) with this envelope:

```json
{
  "original_topic": "transactions",
  "original_event": { "...": "the original event dict or raw payload string" },
  "validation_errors": [
    { "rule": "amount_positive", "message": "amount must be > 0, got -5.0" },
    { "rule": "country_iso",     "message": "country 'ZZ' is not a valid ISO-3166 alpha-2 code" }
  ],
  "failed_at": "2026-03-08T12:00:00.000000+00:00",
  "service": "validation-service"
}
```

The `dlq` topic uses plain JSON because it is operational/internal data — no Avro schema is registered for it. It has a **7-day retention** (longer than the main topics) so you have time to inspect and reprocess.

### DLQ reprocessor CLI

```bash
# Inspect all failed events — read-only, safe to run anytime
python data_quality/dlq_reprocessor.py inspect

# Re-publish DLQ events for a given topic back to the original topic
python data_quality/dlq_reprocessor.py reprocess --topic transactions
```

`inspect` prints each failed event with its original topic, card_id, validation errors, and timestamp, followed by a summary: total events, breakdown by topic, and the top-5 most common rule failures.

`reprocess` extracts `original_event` from matching DLQ messages and re-publishes to the original topic using the appropriate Avro serializer. Use this after fixing a contract bug or updating a rule, to replay events that were incorrectly rejected.

---

## Data Warehouse & Analytics (dbt)

The raw PostgreSQL tables (`public` schema) are OLTP-optimized — designed for fast writes from the Kafka consumers, not for analytics queries. **dbt** transforms these raw event tables into a structured analytical model that's purpose-built for querying.

### Why dbt

- The raw tables store denormalized events with fields split between typed columns and a `raw_event` JSONB blob — dbt staging models reconcile this.
- Joining `transactions → risk_scores → alerts` on every dashboard query is expensive; `fact_fraud_events` pre-joins them into one row per transaction.
- Aggregates like daily fraud rate, per-card velocity, and merchant-level risk are expensive to compute ad-hoc; pre-computed tables make them instant.
- dbt runs as a **batch process** on top of the streaming pipeline — it reads after the persistence service has written events, and writes to the `analytics` schema without touching the raw tables.

### Three-layer model

**Staging (views)** — Clean and type-cast the raw tables. Extract fields from `raw_event` JSONB that aren't in the typed columns. Always fresh because they're views.

| Model | Source table | Key additions |
|-------|-------------|---------------|
| `stg_transactions` | `public.transactions` | Extracts `transaction_type`, `merchant_category`, `schema_version` from JSONB |
| `stg_risk_scores` | `public.risk_scores` | Extracts `reasons`, `schema_version` from JSONB |
| `stg_alerts` | `public.alerts` | Extracts `risk_score_at_alert`, `reasons`, `schema_version` from JSONB |

**Marts (tables)** — Pre-joined, pre-computed analytical tables. Materialized as tables for query performance.

| Model | Grain | Key columns |
|-------|-------|-------------|
| `fact_fraud_events` | One row per transaction | All fields from all 3 staging models, plus `scoring_latency_ms`, `end_to_end_latency_ms`, `is_blocked`, `is_flagged` |
| `dim_cards` | One row per `card_id` | `total_transactions`, `fraud_rate_pct`, `avg_risk_score`, `distinct_countries`, `distinct_devices` |
| `dim_merchants` | One row per `merchant_category` | `total_transactions`, `fraud_rate_pct`, `avg_amount`, `distinct_cards` |
| `dim_dates` | One row per calendar date | Year, month, day, week, quarter, `is_weekend` — generated via date spine macro |

**Aggregates (tables)** — Pre-rolled-up summaries for dashboards.

| Model | Grain | Key columns |
|-------|-------|-------------|
| `agg_daily_fraud_summary` | One row per day | `fraud_rate_pct`, `blocked_amount`, `avg_latency_ms`, `distinct_cards` |
| `agg_hourly_card_velocity` | One row per card per hour | `transaction_count`, `flagged_count`, `blocked_count`, `distinct_countries` |

### dbt Tests

53 tests run on every `dbt test` execution:

- **Schema tests** — `not_null` and `unique` on all primary keys; `not_null` on all foreign keys and critical fields; `accepted_values` for `transaction_type`, `risk_label`, `severity`, `action`.
- **Custom tests** — `assert_risk_score_range` (score in \[0, 1\]), `assert_referential_integrity` (every risk score has a matching transaction), `assert_positive_amounts` (amount > 0), `assert_severity_action_consistent` (INFO→LOG\_ONLY, WARNING→REVIEW\_TRANSACTION, CRITICAL→BLOCK\_CARD).
- **Source freshness** — warns after 10 minutes, errors after 30 minutes of no new data.

### Running dbt

```bash
# Locally (from dbt/ directory — requires PostgreSQL running)
cd dbt
dbt run --profiles-dir .          # build all 9 models
dbt test --profiles-dir .         # run all 53 tests
dbt source freshness --profiles-dir .        # check source freshness

# Run a single model and all downstream dependents
dbt run --profiles-dir . --select stg_transactions+

# Via Docker (after data has accumulated in PostgreSQL)
docker compose run dbt
```

> **Note:** The `dbt` binary in your PATH may be `dbt-fusion` (a preview Rust rewrite) which has known crashes. Use the venv's dbt explicitly if needed: `../venv/bin/dbt run --profiles-dir .`

### Querying analytical tables

```bash
docker exec -it postgres psql -U fraud_user -d fraud_detection_db
```

```sql
-- Top riskiest cards
SELECT card_id, total_transactions, fraud_rate_pct, avg_risk_score
FROM analytics.dim_cards ORDER BY fraud_rate_pct DESC;

-- Fraud rate by merchant category
SELECT merchant_category, total_transactions, fraud_rate_pct
FROM analytics.dim_merchants ORDER BY fraud_rate_pct DESC;

-- Daily fraud summary
SELECT * FROM analytics.agg_daily_fraud_summary ORDER BY summary_date DESC;

-- End-to-end latency by severity
SELECT severity, count(*), round(avg(end_to_end_latency_ms)::numeric, 2) AS avg_latency_ms
FROM analytics.fact_fraud_events GROUP BY severity;
```

---

## Kafka Topics

| Topic | Partitions | Wire Format | Retention | Purpose |
|-------|------------|-------------|-----------|---------|
| `transactions` | 3 | Avro | Default (7d) | Raw card transaction events |
| `risk_scores` | 3 | Avro | Default (7d) | Scored risk events from the processor |
| `alerts` | 1 | Avro | Default (7d) | Alert events with severity and action |
| `dlq` | 1 | JSON | 7 days | Dead-letter queue for events that fail validation or deserialization |

> **Note:** Because main-topic messages are Avro-encoded, `kafka-console-consumer` will display binary output. Use `kafka-avro-console-consumer` (from Confluent tools) or the Schema Registry API to inspect messages in a readable format. DLQ messages are plain JSON and readable with `kafka-console-consumer`.

---

## Data Schemas

All events carry a `schema_version` integer field (default: `1`). This is an application-level field for auditability — it appears in the `raw_event` JSONB column in PostgreSQL so you can tell which schema version produced a given event.

### TransactionEvent (topic: `transactions`)

Produced by `transaction_streamer.py` and the Streamlit UI. Keyed by `card_id`.

```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-02-08T18:08:08.000705+00:00",
  "card_id": "card9",
  "transaction_type": "online_purchase",
  "merchant_category": "e-commerce",
  "amount": 1245.50,
  "country": "NG",
  "device_id": "device_012",
  "schema_version": 1
}
```

| Field | Avro Type | Description |
|-------|-----------|-------------|
| `event_id` | string | UUID v4 unique transaction identifier |
| `timestamp` | string | ISO-8601 UTC time the transaction occurred |
| `card_id` | string | Card used (`card1`–`card10`) |
| `transaction_type` | enum | One of six transaction types (see below) |
| `merchant_category` | string | Merchant category label |
| `amount` | double | Transaction amount in USD |
| `country` | string | ISO 3166-1 alpha-2 country code |
| `device_id` | string | Device used (`device_001`–`device_020`) |
| `schema_version` | int (default: 1) | Application-level schema version for auditability |

**Transaction types:**

| Value | Examples | Fraud Relevance |
|-------|----------|-----------------|
| `pos_purchase` | grocery, fuel | Common baseline |
| `online_purchase` | e-commerce, digital goods | Higher fraud risk |
| `subscription` | streaming, SaaS | Small, repeated |
| `high_value_retail` | electronics, jewelry | Fraud-prone |
| `atm_withdrawal` | cash | Different pattern |
| `international_purchase` | travel, duty-free | Geographic risk |

### RiskScoreEvent (topic: `risk_scores`)

Produced by `risk_score_processor.py`. Keyed by `card_id`. Immutable and auditable.

```json
{
  "risk_event_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "transaction_event_id": "550e8400-e29b-41d4-a716-446655440000",
  "card_id": "card9",
  "risk_score": 0.7,
  "risk_label": "HIGH",
  "reasons": ["HIGH_AMOUNT", "FOREIGN_COUNTRY"],
  "evaluated_at": "2026-02-08T18:08:08.123456+00:00",
  "schema_version": 1
}
```

| Field | Avro Type | Description |
|-------|-----------|-------------|
| `risk_event_id` | string | UUID v4 unique risk evaluation identifier |
| `transaction_event_id` | string | References the original `TransactionEvent.event_id` |
| `card_id` | string | Card that was evaluated |
| `risk_score` | double | Score between 0.0 and 1.0 (capped) |
| `risk_label` | enum | `LOW`, `MEDIUM`, or `HIGH` |
| `reasons` | string[] | List of triggered signal names |
| `evaluated_at` | string | ISO-8601 UTC time scoring was performed |
| `schema_version` | int (default: 1) | Application-level schema version |

### AlertEvent (topic: `alerts`)

Produced by `alert_service.py`. Keyed by `card_id`. Immutable and auditable.

```json
{
  "alert_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "risk_event_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "transaction_event_id": "550e8400-e29b-41d4-a716-446655440000",
  "card_id": "card9",
  "risk_score": 0.7,
  "severity": "CRITICAL",
  "action": "BLOCK_CARD",
  "reasons": ["HIGH_AMOUNT", "FOREIGN_COUNTRY"],
  "created_at": "2026-02-08T18:08:08.234567+00:00",
  "schema_version": 1
}
```

| Field | Avro Type | Description |
|-------|-----------|-------------|
| `alert_id` | string | UUID v4 unique alert identifier |
| `risk_event_id` | string | References the `RiskScoreEvent` that triggered this alert |
| `transaction_event_id` | string | References the original transaction (denormalized) |
| `card_id` | string | Card that triggered the alert |
| `risk_score` | double | Risk score at alert creation time |
| `severity` | enum | `INFO`, `WARNING`, or `CRITICAL` |
| `action` | enum | `LOG_ONLY`, `REVIEW_TRANSACTION`, or `BLOCK_CARD` |
| `reasons` | string[] | Risk signals carried from the risk event |
| `created_at` | string | ISO-8601 UTC time the alert was created |
| `schema_version` | int (default: 1) | Application-level schema version |

---

## PostgreSQL Schema

The database is automatically initialized on first container startup via `kafka-local/postgres-init/init.sql`. Three tables mirror the Kafka topics:

| Table | Primary Key | Purpose |
|-------|-------------|---------|
| `transactions` | `event_id` | Raw transaction events |
| `risk_scores` | `risk_event_id` | Risk evaluation results |
| `alerts` | `alert_id` | Alert events with severity and action |

All tables include a `raw_event` JSONB column storing the full deserialized event, and indexes on `card_id` for efficient lookups. Inserts are idempotent via `ON CONFLICT DO NOTHING`.

---

## Fraud Simulation

The transaction streamer injects fraud-like patterns probabilistically. Patterns are independent, so combinations happen naturally.

| Pattern | Probability | What it does |
|---------|-------------|--------------|
| High-value purchase | ~10% | Amount $800–$2500, electronics/e-commerce/luxury merchant |
| Foreign transaction | ~8% | Switches country from US to a foreign country |
| New device | ~6% | Swaps device to one different from the card's home device |
| ATM anomaly | ~4% | Large ATM withdrawal ($500–$2000), 50% chance foreign |

---

## Risk Scoring

The processor evaluates each transaction against four weighted signals. The score is the sum of triggered weights, capped at 1.0.

| Signal | Condition | Weight |
|--------|-----------|--------|
| `HIGH_AMOUNT` | `amount >= 800` | +0.4 |
| `FOREIGN_COUNTRY` | `country != "US"` | +0.3 |
| `NEW_DEVICE` | Device differs from card's home device | +0.2 |
| `ATM_ANOMALY` | ATM withdrawal with `amount >= 500` | +0.3 |

**Risk labels:**

| Label | Score Range |
|-------|-------------|
| `LOW` | score < 0.3 |
| `MEDIUM` | 0.3 ≤ score ≤ 0.7 |
| `HIGH` | score > 0.7 |

**New device heuristic:** The processor maintains a lightweight in-memory map of `card_id → first observed device_id`. The first device seen for a card becomes its "home device". Any subsequent transaction with a different device triggers the signal. This resets on processor restart.

---

## Alerting

| Risk Label | Severity | Action | UI Message |
|------------|----------|--------|------------|
| `LOW` | `INFO` | `LOG_ONLY` | Transaction submitted successfully |
| `MEDIUM` | `WARNING` | `REVIEW_TRANSACTION` | Transaction looks suspicious, flagged for review |
| `HIGH` | `CRITICAL` | `BLOCK_CARD` | Transaction looks fraudulent, card blocked |

---

## Prerequisites

- **Docker and Docker Compose v2+**
- **Python 3.9+** (for development / manual mode only)

---

## Running with Docker (Recommended)

The root `docker-compose.yml` starts the entire platform — infrastructure and all application services — with a single command. Use this for demos, end-to-end testing, or running the platform without modifying code.

### Quick Start

**1. Clone the repository:**
```bash
git clone https://github.com/Tejesvani/Fraud-Detection-Platform.git
cd Fraud-Detection-Platform
```

**2. Create the environment file:**
```bash
cp .env.example .env
```
The defaults work out-of-the-box. No edits needed.

**3. Start the entire platform:**
```bash
docker compose up --build
```
This starts all infrastructure (Zookeeper, Kafka, Schema Registry, PostgreSQL) and all application services (transaction streamer, risk processor, alert service, persistence service, validation service, Streamlit UI) in the correct dependency order, using health checks to gate startup. First run takes 1–2 minutes while Docker builds images.

**4. Open the Streamlit UI:**
```
http://localhost:8501
```

**5. Watch the pipeline:**
```bash
docker compose logs -f transaction-streamer
docker compose logs -f risk-processor
docker compose logs -f alert-service
docker compose logs -f validation-service
```

**6. Verify data in PostgreSQL:**
```bash
docker exec -it postgres psql -U fraud_user -d fraud_detection_db
```
```sql
SELECT 'transactions' AS tbl, COUNT(*) FROM transactions
UNION ALL SELECT 'risk_scores', COUNT(*) FROM risk_scores
UNION ALL SELECT 'alerts', COUNT(*) FROM alerts;
```

**7. Test the DLQ (inject a bad event that bypasses Avro serialization):**
```bash
echo '{"event_id":"bad","amount":-1}' | \
  docker exec -i kafka kafka-console-producer \
    --bootstrap-server localhost:9092 \
    --topic transactions
```
Then check:
```bash
docker compose logs validation-service | grep -E "DESER_ERROR|FAILED"
```

**8. Inspect the DLQ:**
```bash
python data_quality/dlq_reprocessor.py inspect
```

**9. Build analytical models with dbt (after data has accumulated):**
```bash
# Run as a one-off container — builds all 9 models then runs all 53 tests
docker compose run dbt

# Or locally (from dbt/ directory)
cd dbt && dbt run --profiles-dir . && dbt test --profiles-dir .
```

**11. Stop everything:**
```bash
docker compose down        # stop, keep data volumes
docker compose down -v     # stop + full reset (removes volumes)
```

### Services started by Docker Compose

| Service | Container | Port | Description |
|---------|-----------|------|-------------|
| Zookeeper | `zookeeper` | 2181 | Kafka coordination |
| Kafka | `kafka` | 9092 | Message broker (external), 29092 (internal) |
| Schema Registry | `schema-registry` | 8081 | Avro schema validation |
| PostgreSQL | `postgres` | 5432 | Event persistence |
| kafka-init | `kafka-init` | — | Creates all 4 topics then exits |
| Transaction Streamer | `transaction-streamer` | — | Produces synthetic transactions every 2s |
| Risk Processor | `risk-processor` | — | Scores transactions for fraud risk |
| Alert Service | `alert-service` | — | Maps risk scores to severity/action alerts |
| Persistence Service | `persistence-service` | — | Writes all events to PostgreSQL |
| Validation Service | `validation-service` | — | Audits all events, routes failures to DLQ |
| Streamlit UI | `frontend` | 8501 | Manual transaction submission |
| dbt | `dbt` | — | Batch: builds analytical models in `analytics` schema (run with `docker compose run dbt`) |

All application services use the internal Kafka address `kafka:29092` and Schema Registry at `http://schema-registry:8081`. These environment variables are injected by the compose file automatically.

---

## Running Locally (Development Mode)

> For the quickest setup, use Docker Compose above. This section is for development — running services manually gives you faster iteration: you can restart a single service, attach a debugger, or modify code without rebuilding Docker images.

### 1. Create and Activate Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables

```bash
cp .env.example .env
```

The defaults in `.env.example` work out-of-the-box for local development. Edit only if you need non-default ports or credentials. If you change `POSTGRES_*` values, update `kafka-local/.env` to match.

### 4. Start Infrastructure

```bash
cd kafka-local
docker compose up -d
```

This starts Zookeeper, Kafka, Schema Registry, and PostgreSQL. PostgreSQL tables are created automatically on first startup. Wait ~10 seconds for all services to be ready.

Verify all four containers are running:

```bash
docker compose ps
```

Verify Schema Registry is up:

```bash
curl http://localhost:8081/subjects
# expected: []
```

### 5. Create Kafka Topics

```bash
bash kafka/create-topics.sh
```

Verify:

```bash
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list
```

Expected output: `alerts`, `dlq`, `risk_scores`, `transactions`

### 6. Start the Transaction Streamer (Terminal 1)

```bash
python producer/transaction_streamer.py
```

You will see one transaction logged every 2 seconds, along with a confirmation that Avro serialization is enabled:

```
[Schema Registry] Avro serialization ENABLED
Transaction Streamer started — producing to 'transactions' every 2s
```

### 7. Start the Risk Score Processor (Terminal 2)

```bash
python processor/risk_score_processor.py
```

Color-coded risk evaluations print for each incoming transaction:
- Green `[LOW   ]` — normal transaction
- Yellow `[MEDIUM]` — one signal triggered
- Red `[HIGH  ]` — multiple signals triggered

### 8. Start the Alert Service (Terminal 3)

```bash
python consumer/alert_service.py
```

Color-coded alerts:
- Green `[INFO    ]` — LOG_ONLY
- Yellow `[WARNING ]` — REVIEW_TRANSACTION
- Red `[CRITICAL]` — BLOCK_CARD

### 9. Start the Persistence Service (Terminal 4)

```bash
python consumer/persistence_service.py
```

Structured log entries for each event persisted to PostgreSQL:

```
2026-03-08 12:00:01 [INFO] Persisted  topic=transactions   partition=0  offset=42
```

### 10. Start the Validation Service (Terminal 5)

```bash
python data_quality/validation_service.py
```

You will see `[VALID]` (green) for each passing event and periodic `[QUALITY]` summaries (cyan). Validation failures appear as `[FAILED]` (red) with rule details, and deserialization errors as `[DESER_ERROR]` (yellow). Failed events are automatically routed to the `dlq` topic.

```
[VALID]    topic=transactions    event_id=f47ac10b-...
[QUALITY]  processed=100 valid=100 failed=0 (deser_errors=0) (0.00% failure rate)
```

### 11. Start the Streamlit UI (Terminal 6)

```bash
streamlit run frontend/app.py
```

Opens at `http://localhost:8501`. Fill in the transaction form and submit to see the fraud analysis result in real time (polls the `alerts` topic with a 30-second timeout). If a field fails validation, errors are shown in the UI before the transaction is produced.

---

## Inspecting Kafka

### List topics

```bash
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list
```

### Check Schema Registry subjects

```bash
# List all registered subjects
curl http://localhost:8081/subjects

# View the registered schema for a subject
curl http://localhost:8081/subjects/transactions-value/versions/latest
```

### Check consumer group lag

```bash
# Pipeline consumers
docker exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group risk-score-processor-group --describe

docker exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group alert-service-group --describe

docker exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group persistence-service-group --describe

# Validation service (independent audit consumer)
docker exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group validation-service-group --describe
```

### Inspect the DLQ topic

```bash
# Read raw DLQ messages (JSON-encoded, human readable)
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic dlq \
  --from-beginning \
  --max-messages 10
```

### Delete and recreate topics (reset state)

```bash
bash kafka/delete-topics.sh
bash kafka/create-topics.sh
```

> **Note:** Main topic messages are Avro-encoded, so `kafka-console-consumer` will display binary output. Use `kafka-avro-console-consumer` or the Schema Registry API to inspect them. DLQ messages are plain JSON and fully readable with `kafka-console-consumer`.

---

## Querying PostgreSQL

```bash
docker exec -it postgres psql -U fraud_user -d fraud_detection_db
```

### Count events per table

```sql
SELECT 'transactions' AS table_name, COUNT(*) FROM transactions
UNION ALL
SELECT 'risk_scores', COUNT(*) FROM risk_scores
UNION ALL
SELECT 'alerts', COUNT(*) FROM alerts;
```

### Recent high-risk alerts

```sql
SELECT alert_id, card_id, severity, action, created_at
FROM alerts
WHERE severity = 'CRITICAL'
ORDER BY created_at DESC
LIMIT 10;
```

### Full event trace (transaction → risk → alert)

```sql
SELECT
    t.event_id,
    t.card_id,
    t.amount,
    t.country,
    r.risk_score,
    r.risk_label,
    a.severity,
    a.action
FROM transactions t
JOIN risk_scores r ON r.transaction_event_id = t.event_id
JOIN alerts a ON a.transaction_event_id = t.event_id
ORDER BY t.transaction_timestamp DESC
LIMIT 10;
```

---

## Testing

### Unit tests

```bash
# Run all data contract tests
pytest data_quality/tests/ -v

# Run tests for a specific contract
pytest data_quality/tests/test_transaction_contract.py -v
pytest data_quality/tests/test_risk_score_contract.py -v
pytest data_quality/tests/test_alert_contract.py -v
```

96 tests cover every validation rule in isolation, boundary values (e.g., `risk_score` exactly 0.0, 0.3, 0.7, 1.0), cross-field consistency (label/score, severity/action), and multi-failure scenarios.

### dbt tests

```bash
# Run all 53 dbt tests (schema tests + custom assertions)
cd dbt
dbt test --profiles-dir .

# Check source freshness (warns >10 min, errors >30 min)
dbt source freshness --profiles-dir .
```

### End-to-end DLQ test

Inject a malformed event directly into Kafka (bypassing Avro) to test deserialization error handling and DLQ routing:

```bash
# Inject a plain-JSON message to the transactions topic
echo '{"event_id":"not-a-uuid","timestamp":"garbage","card_id":"","amount":-999,"country":"ZZ"}' | \
  docker exec -i kafka kafka-console-producer \
    --bootstrap-server localhost:9092 \
    --topic transactions

# Verify the validation service caught it
docker compose logs validation-service | grep -E "DESER_ERROR|FAILED|QUALITY"

# Inspect the DLQ — should show the failed event
python data_quality/dlq_reprocessor.py inspect
```

Expected validation service output:
```
[DESER_ERROR] topic=transactions    error=Unexpected magic byte 123...
[QUALITY] processed=N valid=N-1 failed=1 (deser_errors=1) (X.XX% failure rate)
```

---

## Stopping

### Docker Compose (full-stack)

```bash
docker compose down        # stop all containers, keep data volumes
docker compose down -v     # stop all containers, remove volumes (full reset)
```

### Development mode (manual services)

1. Press `Ctrl+C` in each service terminal (streamer, processor, alert, persistence, validation, Streamlit).
2. Shut down Docker infrastructure:

```bash
cd kafka-local
docker compose down        # keep data
docker compose down -v     # full reset
```

---

## What's Completed

- [x] Local Kafka infrastructure (Zookeeper + Broker via Docker)
- [x] Schema Registry (Confluent, BACKWARD compatibility mode)
- [x] Apache Avro schemas for all three event types (`TransactionEvent`, `RiskScoreEvent`, `AlertEvent`)
- [x] Shared `schema_registry.py` — centralized Avro serializer/deserializer helpers used by all services
- [x] All services produce and consume Avro with automatic JSON fallback (`SCHEMA_REGISTRY_ENABLED=false`)
- [x] GitHub Actions CI — validates Avro schema backward compatibility on every PR touching `schemas/`
- [x] PostgreSQL database with automated schema initialization
- [x] Topic creation and management scripts (`transactions`, `risk_scores`, `alerts`, `dlq`)
- [x] Transaction streamer (producer) with realistic synthetic data
- [x] Fraud pattern injection (high-value, foreign, new device, ATM anomaly)
- [x] Risk score processor (consumer/producer) with weighted signal scoring
- [x] Alert service (consumer/producer) with severity/action mapping
- [x] Persistence service — consumes all topics and writes to PostgreSQL with idempotent inserts
- [x] Streamlit UI for manual transaction submission with real-time fraud analysis (Avro-aware)
- [x] Color-coded console output across all services
- [x] Environment variable externalization via `.env` and `python-dotenv`
- [x] `schema_version` field on all events for application-level auditability
- [x] Two-layer data validation — producer-side (Layer 1, primary defense) + parallel audit service (Layer 2)
- [x] Data contracts for all three event types — pure validation functions, 40+ rules, single source of truth
- [x] Dead-letter queue (`dlq`) with JSON envelope and 7-day retention
- [x] Deserialization error handling — non-Avro messages routed to DLQ instead of crashing consumers
- [x] DLQ reprocessor CLI — inspect failed events and replay corrected ones back to original topics
- [x] Unit tests for all data contracts (96 tests, pytest)
- [x] Full-stack Docker Compose at project root — one command starts infrastructure + all application services
- [x] Health checks on all infrastructure services (Zookeeper, Kafka, Schema Registry, PostgreSQL)
- [x] Dockerfiles for all application services (monorepo build context, project root)
- [x] dbt analytics layer — staging views, fact/dimension tables, pre-computed aggregates
- [x] 9 dbt models across 3 layers (staging views, mart tables, aggregate tables)
- [x] 53 dbt tests (schema tests, custom SQL assertions, source freshness checks)
- [x] `analytics` schema separation — raw OLTP tables in `public` untouched by dbt
- [x] Date dimension with PostgreSQL `generate_series` date spine macro
- [x] End-to-end latency tracking (`scoring_latency_ms`, `end_to_end_latency_ms`) in fact table
- [x] dbt Dockerized — `docker compose run dbt` builds all models and runs all tests
