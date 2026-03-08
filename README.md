# Fraud Detection Platform

A real-time fraud detection system built on Apache Kafka. Simulated card transactions are streamed through a pipeline that evaluates each transaction for fraud signals, produces risk scores, and emits alerts — all in real time. Events are serialized with **Apache Avro** and validated by **Confluent Schema Registry**. All events are persisted to PostgreSQL. Includes a Streamlit UI for manual transaction submission.

---

## Architecture

```
  PRODUCERS                    KAFKA BROKER (localhost:9092)              CONSUMERS
  ─────────                   ┌───────────────────────────┐              ─────────
                              │                           │
┌──────────────────┐          │  ┌─────────────────────┐  │          ┌──────────────────────┐
│                  │ produce  │  │                     │  │ consume  │                      │
│   Transaction    │─────────>│──│    transactions     │──│─────────>│  Risk Score          │
│   Streamer       │  (Avro)  │  │    (3 partitions)   │  │  (Avro)  │  Processor           │
│                  │          │  │                     │  │          │                      │
└──────────────────┘          │  │          ▲          │  │          └──────────────────────┘
                              │  └──────────│──────────┘  │                    │
┌──────────────────┐ produce  │             │             │                    │
│                  │──────────│─────────────┘             │                    │ produce
│   Streamlit UI   │  (Avro)  │                           │                    │  (Avro)
│   (Frontend)     │          │  ┌─────────────────────┐  │                    │
│                  │          │  │                     │<─│────────────────────┘
└────────┬─────────┘          │  │    risk_scores      │  │
         │                    │  │    (3 partitions)   │  │          ┌──────────────────────┐
         │                    │  │                     │──│─────────>│                      │
         │                    │  └─────────────────────┘  │ consume  │  Alert Service       │
         │                    │                           │  (Avro)  │                      │
         │                    │                           │          └──────────────────────┘
         │                    │  ┌─────────────────────┐  │                    │
         │           poll     │  │                     │<─│────────────────────┘
         └───────────────────<│──│    alerts           │  │            produce
                    alerts    │  │    (1 partition)    │  │             (Avro)
                    (Avro)    │  │                     │  │
                              │  └─────────────────────┘  │
                              │                           │
                              └───────────────────────────┘
                                        │
                              ┌─────────▼─────────┐
                              │  Schema Registry   │  ←── All producers register/validate
                              │  (localhost:8081)  │       schemas before producing
                              └───────────────────┘
                                        │ consume (all 3 topics)
                                        ▼
                              ┌──────────────────────┐
                              │                      │
                              │  Persistence Service │──────> PostgreSQL
                              │                      │        (fraud_detection_db)
                              └──────────────────────┘
```

The system is a Kafka-based event-driven fraud detection pipeline. Two producers — a background Transaction Streamer and a user-facing Streamlit UI — publish Avro-encoded transaction events to the `transactions` topic. A Risk Score Processor consumes, evaluates fraud risk, and publishes scored results to `risk_scores`. An Alert Service maps risk labels to severity and action and publishes to `alerts`. A Persistence Service consumes all three topics and writes every event to PostgreSQL. All messages are serialized with Apache Avro using Confluent Schema Registry for schema validation and evolution.

**Data flow:**
1. **Producers** serialize events as Avro and publish to `transactions`:
   - `transaction_streamer.py` generates one synthetic transaction every 2 seconds.
   - The Streamlit UI submits user-selected transactions.
2. **Risk Score Processor** consumes from `transactions` (Avro), evaluates four fraud signals, and produces an immutable risk event to `risk_scores` (Avro).
3. **Alert Service** consumes from `risk_scores` (Avro), maps risk labels to severity and action, and produces an alert event to `alerts` (Avro).
4. **Persistence Service** consumes from all three topics (Avro), deserializes, and persists every event to PostgreSQL with idempotent inserts.
5. For UI-submitted transactions, the Streamlit UI polls the `alerts` topic, deserializes (Avro), matches on `transaction_event_id`, and displays the fraud analysis result.

---

## Folder Structure

```
fraud-detection-platform/
├── .github/
│   └── workflows/
│       └── schema-ci.yml           # CI: validates Avro schema backward compatibility on PRs
├── kafka-local/
│   ├── docker-compose.yml          # Zookeeper + Kafka + Schema Registry + PostgreSQL
│   ├── .env                        # Docker Compose vars (Kafka port, Postgres creds)
│   ├── .env.example                # Template for kafka-local/.env
│   ├── connect-image/
│   │   └── postgresql-42.7.1.jar   # JDBC driver for future Kafka Connect use
│   └── postgres-init/
│       └── init.sql                # Auto-creates PostgreSQL tables on first startup
├── kafka/
│   ├── create-topics.sh            # Creates all Kafka topics
│   └── delete-topics.sh            # Tears down all Kafka topics
├── schemas/
│   ├── transaction_event.avsc      # Avro schema for the transactions topic
│   ├── risk_score_event.avsc       # Avro schema for the risk_scores topic
│   ├── alert_event.avsc            # Avro schema for the alerts topic
│   ├── compatibility-policy.md     # Schema evolution rules and decisions
│   └── scripts/
│       └── check_compatibility.py  # Local + CI schema compatibility checker
├── shared/
│   ├── __init__.py
│   └── schema_registry.py          # Shared Avro serializer/deserializer helpers
├── producer/
│   └── transaction_streamer.py     # Kafka producer — synthetic transaction generator
├── processor/
│   └── risk_score_processor.py     # Kafka consumer/producer — risk scoring engine
├── consumer/
│   ├── alert_service.py            # Kafka consumer/producer — alert emitter
│   └── persistence_service.py      # Kafka consumer — persists all events to PostgreSQL
├── frontend/
│   └── app.py                      # Streamlit UI — manual transaction submission
├── .env                            # Runtime env vars for Python services (not committed)
├── .env.example                    # Template for root .env
├── requirements.txt                # Python dependencies (pinned)
└── README.md
```

---

## Environment Configuration

Two separate `.env` files serve different purposes:

| File | Used by | Purpose |
|------|---------|---------|
| `kafka-local/.env` | Docker Compose | Kafka ports, PostgreSQL container credentials |
| `.env` (root) | Python services | Kafka broker, topics, group IDs, Schema Registry, PostgreSQL |

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
| `KAFKA_GROUP_RISK_PROCESSOR` | `risk-score-processor-group` | Consumer group for risk processor |
| `KAFKA_GROUP_ALERT_SERVICE` | `alert-service-group` | Consumer group for alert service |
| `KAFKA_GROUP_PERSISTENCE` | `persistence-service-group` | Consumer group for persistence service |
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

## Kafka Topics

| Topic | Partitions | Wire Format | Purpose |
|-------|------------|-------------|---------|
| `transactions` | 3 | Avro | Raw card transaction events |
| `risk_scores` | 3 | Avro | Scored risk events from the processor |
| `alerts` | 1 | Avro | Alert events with severity and action |

> **Note:** Because messages are Avro-encoded, `kafka-console-consumer` will display binary output. Use `kafka-avro-console-consumer` (from Confluent tools) or the Schema Registry UI to inspect messages in a readable format.

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
| `MEDIUM` | 0.3 ≤ score < 0.7 |
| `HIGH` | score ≥ 0.7 |

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

- **Docker** (for Kafka, Zookeeper, Schema Registry, and PostgreSQL)
- **Python 3.9+**

---

## Running Locally

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

Expected output: `alerts`, `risk_scores`, `transactions`

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

### 10. Start the Streamlit UI (Terminal 5)

```bash
streamlit run frontend/app.py
```

Opens at `http://localhost:8501`. Fill in the transaction form and submit to see the fraud analysis result in real time (polls the `alerts` topic with a 30-second timeout).

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
docker exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group risk-score-processor-group \
  --describe

docker exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group alert-service-group \
  --describe

docker exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group persistence-service-group \
  --describe
```

### Delete and recreate topics (reset state)

```bash
bash kafka/delete-topics.sh
bash kafka/create-topics.sh
```

> **Note:** Messages are Avro-encoded with the Confluent wire format, so `kafka-console-consumer` will display binary output. Use `kafka-avro-console-consumer` from the Confluent CLI tools or inspect via the Schema Registry API.

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

## Stopping

1. Press `Ctrl+C` in each service terminal.
2. Shut down Docker infrastructure:

```bash
cd kafka-local
docker compose down
```

To also remove the PostgreSQL data volume (full reset):

```bash
docker compose down -v
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
- [x] Topic creation and management scripts (`transactions`, `risk_scores`, `alerts`)
- [x] Transaction streamer (producer) with realistic synthetic data
- [x] Fraud pattern injection (high-value, foreign, new device, ATM anomaly)
- [x] Risk score processor (consumer/producer) with weighted signal scoring
- [x] Alert service (consumer/producer) with severity/action mapping
- [x] Persistence service — consumes all topics and writes to PostgreSQL
- [x] Streamlit UI for manual transaction submission with real-time fraud analysis (Avro-aware)
- [x] Color-coded console output across all services
- [x] Environment variable externalization via `.env` and `python-dotenv`
- [x] `schema_version` field on all events for application-level auditability
