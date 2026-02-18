# Fraud Detection Platform

A real-time fraud detection system built on Apache Kafka with schema-aware Avro serialization. Simulated card transactions are streamed through a pipeline that evaluates each transaction for fraud signals, produces risk scores, and emits alerts — all persisted to PostgreSQL via Kafka Connect. Includes a Streamlit UI for manual transaction submission.

---

## Architecture

```
  PRODUCERS                    KAFKA BROKER (localhost:9092)              CONSUMERS
  ─────────                   ┌───────────────────────────┐              ─────────
                              │                           │
┌──────────────────┐          │  ┌─────────────────────┐  │          ┌──────────────────────┐
│                  │ produce  │  │                     │  │  consume │                      │
│   Transaction    │─────────>│──│    transactions     │──│─────────>│  Risk Score          │
│   Streamer       │          │  │    (3 partitions)   │  │          │  Processor           │
│                  │          │  │                     │  │          │                      │
└──────────────────┘          │  │          ▲          │  │          └──────────────────────┘
                              │  └──────────│──────────┘  │                    │
┌──────────────────┐  produce │             │             │                    │
│                  │──────────│─────────────┘             │                    │ 
│   Streamlit UI   │          │                           │                    │ produce
│   (Frontend)     │          │  ┌─────────────────────┐  │                    │
│                  │          │  │                     │<─│────────────────────┘
└────────┬─────────┘          │  │    risk_scores      │  │
         │                    │  │    (3 partitions)   │  │          ┌──────────────────────┐
         │                    │  │                     │──│─────────>│                      │
         │                    │  └─────────────────────┘  │ consume  │  Alert Service       │
         │                    │                           │          │                      │
         │                    │                           │          └──────────────────────┘
         │                    │  ┌─────────────────────┐  │                    │
         │        poll        │  │                     │<─│────────────────────┘
         └───────────────────<│──│    alerts           │  │       produce
                              │  │    (1 partition)    │  │
                              │  │                     │  │
                              │  └─────────────────────┘  │
                              │                           │
                              └───────────────────────────┘
                                          │
                              ┌───────────┴───────────┐
                              │   Schema Registry     │
                              │   (localhost:8081)    │
                              └───────────────────────┘
                                          │
                              ┌───────────┴───────────┐
                              │   Kafka Connect       │
                              │   (AvroConverter)     │
                              │   JDBC Sink           │
                              └───────────┬───────────┘
                                          │ append-only
                              ┌───────────┴───────────┐
                              │   PostgreSQL          │
                              │   (localhost:5433)    │
                              │   3 prod tables       │
                              └───────────────────────┘
```

**Data flow:**

1. **Producers** serialize transaction events as Avro and publish to the `transactions` topic:
   - `transaction_streamer.py` generates one synthetic transaction every 2 seconds.
   - The Streamlit UI submits user transactions (with `"source": "ui"`).
2. **Schema Registry** stores and validates Avro schemas. Producers register schemas automatically on first produce.
3. **Risk Score Processor** deserializes Avro from `transactions`, evaluates each event against fraud signals, and produces an Avro risk event to the `risk_scores` topic.
4. **Alert Service** deserializes Avro from `risk_scores`, maps each risk label to a severity and action, and produces an Avro alert event to the `alerts` topic.
5. **Kafka Connect** (JDBC Sink with AvroConverter) consumes from all three topics and inserts rows into PostgreSQL in append-only mode.
6. For UI-submitted transactions, the Streamlit UI polls the `alerts` topic, matches on `transaction_event_id`, and displays the fraud analysis result.

---

## Folder Structure

```
fraud-detection-platform/
├── kafka-local/
│   ├── docker-compose.yml            # Zookeeper + Kafka + Schema Registry + Postgres + Connect
│   ├── .env                          # Docker service ports and Postgres credentials
│   ├── connect-image/
│   │   ├── Dockerfile                # Kafka Connect with JDBC + Avro converter plugins
│   │   └── postgresql-42.7.1.jar     # PostgreSQL JDBC driver
│   ├── connect-configs/
│   │   ├── transactions-prod-sink.json
│   │   ├── risk-scores-prod-sink.json
│   │   └── alerts-prod-sink.json
│   └── postgres-init-prod/
│       └── init-prod.sql             # Append-only table schemas
├── kafka/
│   ├── create-topics.sh              # Creates all Kafka topics
│   ├── delete-topics.sh              # Tears down all Kafka topics
│   ├── register-connectors-prod.sh   # Registers JDBC Sink connectors
│   └── validate-prod.sh              # Full pipeline health check
├── schemas/
│   ├── __init__.py                   # Avro serializer/deserializer factory
│   ├── transaction.avsc              # Transaction Avro schema
│   ├── risk_score.avsc               # RiskScore Avro schema
│   └── alert.avsc                    # Alert Avro schema
├── producer/
│   └── transaction_streamer.py       # Kafka producer — Avro synthetic transaction generator
├── processor/
│   └── risk_score_processor.py       # Kafka consumer/producer — Avro risk scoring engine
├── consumer/
│   └── alert_service.py              # Kafka consumer/producer — Avro alert emitter
├── frontend/
│   └── app.py                        # Streamlit UI — Avro transaction submission
├── .env                              # App-level Kafka and Schema Registry config
├── requirements.txt                  # Python dependencies (confluent-kafka[avro], streamlit, etc.)
├── .gitignore
└── README.md
```

---

## Docker Services

| Service           | Container Name        | Port  | Description                                      |
|-------------------|-----------------------|-------|--------------------------------------------------|
| Zookeeper         | `zookeeper-prod`      | 2181  | Kafka metadata coordination                      |
| Kafka             | `kafka-prod`          | 9092  | Message broker (internal: 29092)                 |
| Schema Registry   | `schema-registry-prod`| 8081  | Avro schema storage and validation               |
| PostgreSQL        | `postgres-prod`       | 5433  | Append-only persistence for all events           |
| Kafka Connect     | `connect-prod`        | 8084  | JDBC Sink with AvroConverter to PostgreSQL        |

---

## Kafka Topics

| Topic          | Partitions | Replication | Purpose                                            |
|----------------|------------|-------------|----------------------------------------------------|
| `transactions` | 3          | 1           | Avro card transaction events from streamer and UI  |
| `risk_scores`  | 3          | 1           | Avro scored risk events from the processor         |
| `alerts`       | 1          | 1           | Avro alert events with severity and action         |

Auto topic creation is disabled. Topics are created via `kafka/create-topics.sh`.

---

## Avro Schemas

All messages are serialized as Avro with schemas registered in Confluent Schema Registry. Schemas are defined in the `schemas/` directory as `.avsc` files.

### Transaction (`schemas/transaction.avsc`)

Produced by `transaction_streamer.py` and the Streamlit UI. Keyed by `card_id` (StringSerializer).

```json
{
  "type": "record",
  "name": "Transaction",
  "namespace": "fraud.platform",
  "fields": [
    {"name": "event_id", "type": "string"},
    {"name": "card_id", "type": "string"},
    {"name": "transaction_type", "type": "string"},
    {"name": "merchant_category", "type": "string"},
    {"name": "amount", "type": "double"},
    {"name": "country", "type": "string"},
    {"name": "device_id", "type": "string"},
    {"name": "timestamp", "type": {"type": "long", "logicalType": "timestamp-millis"}},
    {"name": "source", "type": "string"}
  ]
}
```

| Field              | Avro Type        | Description                                            |
|--------------------|------------------|--------------------------------------------------------|
| `event_id`         | string           | UUID — unique identifier for this transaction          |
| `card_id`          | string           | Card used (`card1` through `card10`)                   |
| `transaction_type` | string           | One of the six transaction types (see below)           |
| `merchant_category`| string           | Category of the merchant                               |
| `amount`           | double           | Transaction amount in USD                              |
| `country`          | string           | Two-letter country code                                |
| `device_id`        | string           | Device used for the transaction                        |
| `timestamp`        | long (millis)    | UTC epoch milliseconds when the transaction was created|
| `source`           | string           | `"streamer"` or `"ui"`                                 |

**Transaction types:**

| Value                   | Examples                  | Fraud Relevance     |
|-------------------------|---------------------------|---------------------|
| `pos_purchase`          | grocery, fuel             | Common baseline     |
| `online_purchase`       | e-commerce, digital goods | Higher fraud risk   |
| `subscription`          | streaming, SaaS           | Small, repeated     |
| `high_value_retail`     | electronics, jewelry      | Fraud-prone         |
| `atm_withdrawal`        | cash                      | Different pattern   |
| `international_purchase`| travel, duty-free         | Geographic risk     |

### RiskScore (`schemas/risk_score.avsc`)

Produced by `risk_score_processor.py`. Keyed by `card_id`. Each event is immutable and auditable.

```json
{
  "type": "record",
  "name": "RiskScore",
  "namespace": "fraud.platform",
  "fields": [
    {"name": "risk_event_id", "type": "string"},
    {"name": "transaction_event_id", "type": "string"},
    {"name": "card_id", "type": "string"},
    {"name": "risk_score", "type": "double"},
    {"name": "risk_label", "type": "string"},
    {"name": "evaluated_at", "type": {"type": "long", "logicalType": "timestamp-millis"}}
  ]
}
```

| Field                  | Avro Type        | Description                                    |
|------------------------|------------------|------------------------------------------------|
| `risk_event_id`        | string           | UUID — unique identifier for this evaluation   |
| `transaction_event_id` | string           | UUID — references the original transaction     |
| `card_id`              | string           | Card that was evaluated                        |
| `risk_score`           | double           | Score between 0.0 and 1.0 (capped)            |
| `risk_label`           | string           | `LOW`, `MEDIUM`, or `HIGH`                     |
| `evaluated_at`         | long (millis)    | UTC epoch milliseconds when scoring was done   |

### Alert (`schemas/alert.avsc`)

Produced by `alert_service.py`. Keyed by `card_id`. Each event is immutable and auditable.

```json
{
  "type": "record",
  "name": "Alert",
  "namespace": "fraud.platform",
  "fields": [
    {"name": "alert_id", "type": "string"},
    {"name": "risk_event_id", "type": "string"},
    {"name": "transaction_event_id", "type": "string"},
    {"name": "card_id", "type": "string"},
    {"name": "severity", "type": "string"},
    {"name": "action", "type": "string"},
    {"name": "created_at", "type": {"type": "long", "logicalType": "timestamp-millis"}}
  ]
}
```

| Field                  | Avro Type        | Description                                        |
|------------------------|------------------|----------------------------------------------------|
| `alert_id`             | string           | UUID — unique identifier for this alert            |
| `risk_event_id`        | string           | UUID — references the risk event                   |
| `transaction_event_id` | string           | UUID — references the original transaction         |
| `card_id`              | string           | Card that triggered the alert                      |
| `severity`             | string           | `INFO`, `WARNING`, or `CRITICAL`                   |
| `action`               | string           | `LOG_ONLY`, `REVIEW_TRANSACTION`, or `BLOCK_CARD`  |
| `created_at`           | long (millis)    | UTC epoch milliseconds when the alert was created  |

---

## PostgreSQL Tables (Append-Only)

All tables use `BIGSERIAL` auto-increment primary keys and `inserted_at DEFAULT NOW()`. No upsert, no `ON CONFLICT`, insert only.

| Table               | Source Topic   | Key Columns                                                      |
|---------------------|----------------|------------------------------------------------------------------|
| `transactions_prod` | `transactions` | event_id, card_id, transaction_type, amount, country, timestamp  |
| `risk_scores_prod`  | `risk_scores`  | risk_event_id, transaction_event_id, card_id, risk_score         |
| `alerts_prod`       | `alerts`       | alert_id, risk_event_id, transaction_event_id, severity, action  |

Kafka Connect JDBC Sink connectors use `AvroConverter` with Schema Registry and `insert.mode=insert`, `pk.mode=none`.

---

## Kafka Connect Sink Connectors

| Connector Name          | Topic          | Target Table         | Converter     |
|-------------------------|----------------|----------------------|---------------|
| `transactions-prod-sink`| `transactions` | `transactions_prod`  | AvroConverter |
| `risk-scores-prod-sink` | `risk_scores`  | `risk_scores_prod`   | AvroConverter |
| `alerts-prod-sink`      | `alerts`       | `alerts_prod`        | AvroConverter |

All connectors use:
- `insert.mode`: `insert` (append-only)
- `pk.mode`: `none`
- `auto.create`: `false`
- `auto.evolve`: `false`
- `value.converter`: `io.confluent.connect.avro.AvroConverter`
- `value.converter.schema.registry.url`: `http://schema-registry:8081`

---

## Fraud Simulation

The transaction streamer injects fraud-like patterns probabilistically. Patterns are independent of each other, so combinations happen naturally.

| Pattern             | Probability | What it does                                               |
|---------------------|-------------|------------------------------------------------------------|
| High-value purchase | ~10%        | Amount $800-$2500, electronics/e-commerce/luxury merchant  |
| Foreign transaction | ~8%         | Switches country from US to a foreign country              |
| New device          | ~6%         | Swaps device to one different from the card's home device  |
| ATM anomaly         | ~4%         | Large ATM withdrawal ($500-$2000), 50% chance foreign      |

---

## Risk Scoring

The processor evaluates each transaction against four weighted signals. The score is the sum of triggered weights, capped at 1.0.

| Signal           | Condition                              | Weight |
|------------------|----------------------------------------|--------|
| `HIGH_AMOUNT`    | `amount >= 800`                        | +0.4   |
| `FOREIGN_COUNTRY`| `country != "US"`                      | +0.3   |
| `NEW_DEVICE`     | Device differs from card's home device | +0.2   |
| `ATM_ANOMALY`    | ATM withdrawal with `amount >= 500`    | +0.3   |

**Risk labels:**

| Label    | Score Range        |
|----------|--------------------|
| `LOW`    | score < 0.3        |
| `MEDIUM` | 0.3 <= score < 0.7 |
| `HIGH`   | score >= 0.7       |

**New device heuristic:** The processor maintains a lightweight in-memory map of `card_id -> first observed device_id`. The first device seen for a card is treated as its home device. Any subsequent transaction from that card with a different device triggers the signal. This resets on processor restart.

---

## Alerting

The alert service maps each risk label to a severity and action:

| Risk Label | Severity   | Action               | UI Message                                      |
|------------|------------|----------------------|-------------------------------------------------|
| `LOW`      | `INFO`     | `LOG_ONLY`           | Transaction submitted successfully               |
| `MEDIUM`   | `WARNING`  | `REVIEW_TRANSACTION` | Transaction looks suspicious, flagged for review |
| `HIGH`     | `CRITICAL` | `BLOCK_CARD`         | Transaction looks fraudulent, card blocked       |

---

## Prerequisites

- **Docker** and **Docker Compose** (for all infrastructure services)
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

### 3. Build and Start Infrastructure

```bash
cd kafka-local
docker compose build
docker compose up -d
```

This starts Zookeeper, Kafka, Schema Registry, PostgreSQL, and Kafka Connect.

Wait ~15 seconds for all services to initialize.

### 4. Create Topics

```bash
bash kafka/create-topics.sh
```

### 5. Register Kafka Connect Sink Connectors

```bash
bash kafka/register-connectors-prod.sh
```

This deletes any existing connectors, registers all three JDBC Sink connectors, and verifies they are RUNNING.

### 6. Start the Transaction Streamer (Terminal 1)

```bash
python producer/transaction_streamer.py
```

You will see one Avro-serialized transaction logged every 2 seconds.

### 7. Start the Risk Score Processor (Terminal 2)

```bash
python processor/risk_score_processor.py
```

You will see color-coded risk evaluations:
- Green `[LOW   ]` -- normal transaction
- Yellow `[MEDIUM]` -- one signal triggered
- Red `[HIGH  ]` -- multiple signals triggered

### 8. Start the Alert Service (Terminal 3)

```bash
python consumer/alert_service.py
```

You will see color-coded alerts:
- Green `[INFO    ]` -- LOG_ONLY
- Yellow `[WARNING ]` -- REVIEW_TRANSACTION
- Red `[CRITICAL]` -- BLOCK_CARD

### 9. Start the Streamlit UI (Terminal 4)

```bash
streamlit run frontend/app.py
```

Opens at `http://localhost:8501`. Submit a transaction and see the fraud analysis result in real time.

### 10. Validate the Pipeline

```bash
bash kafka/validate-prod.sh
```

Checks Schema Registry, Kafka Connect connector status, topic listing, and PostgreSQL row counts.

---

## Inspecting the Pipeline

### List all topics

```bash
docker exec kafka-prod kafka-topics \
  --bootstrap-server localhost:9092 \
  --list
```

### Check Schema Registry subjects

```bash
curl -s http://localhost:8081/subjects | python3 -m json.tool
```

### Check a registered schema

```bash
curl -s http://localhost:8081/subjects/transactions-value/versions/latest | python3 -m json.tool
```

### Check connector status

```bash
curl -s http://localhost:8084/connectors/transactions-prod-sink/status | python3 -m json.tool
```

### Read Avro messages (via console consumer with schema registry)

```bash
docker exec schema-registry-prod kafka-avro-console-consumer \
  --bootstrap-server kafka:29092 \
  --topic transactions \
  --from-beginning \
  --property schema.registry.url=http://schema-registry:8081
```

### Check consumer group lag

```bash
docker exec kafka-prod kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group risk-score-processor-group \
  --describe
```

### Query PostgreSQL directly

```bash
docker exec -it postgres-prod psql -U fraud_prod_user -d fraud_detection_prod \
  -c "SELECT COUNT(*) FROM transactions_prod;"
```

### Delete and recreate topics (full reset)

```bash
bash kafka/delete-topics.sh
bash kafka/create-topics.sh
```

---

## Stopping

1. Press `Ctrl+C` in the streamer, processor, and alert service terminals.
2. Stop the Streamlit UI with `Ctrl+C`.
3. Shut down all infrastructure:

```bash
cd kafka-local
docker compose down
```

To fully reset (including PostgreSQL data and Kafka offsets):

```bash
cd kafka-local
docker compose down -v
```

---

## Environment Variables

### Application (`/.env`)

| Variable                       | Default                        | Description                     |
|--------------------------------|--------------------------------|---------------------------------|
| `KAFKA_BOOTSTRAP_SERVERS`      | `localhost:9092`               | Kafka broker address            |
| `KAFKA_TOPIC_TRANSACTIONS`     | `transactions`                 | Transactions topic name         |
| `KAFKA_TOPIC_RISK_SCORES`      | `risk_scores`                  | Risk scores topic name          |
| `KAFKA_TOPIC_ALERTS`           | `alerts`                       | Alerts topic name               |
| `KAFKA_GROUP_RISK_PROCESSOR`   | `risk-score-processor-group`   | Risk processor consumer group   |
| `KAFKA_GROUP_ALERT_SERVICE`    | `alert-service-group`          | Alert service consumer group    |
| `SCHEMA_REGISTRY_URL`          | `http://localhost:8081`        | Schema Registry URL             |

### Docker (`/kafka-local/.env`)

| Variable                | Default                 | Description                       |
|-------------------------|-------------------------|-----------------------------------|
| `POSTGRES_PROD_USER`    | `fraud_prod_user`       | PostgreSQL username               |
| `POSTGRES_PROD_PASSWORD`| `fraud_prod_password`   | PostgreSQL password               |
| `POSTGRES_PROD_DB`      | `fraud_detection_prod`  | PostgreSQL database name          |
| `POSTGRES_PROD_PORT`    | `5433`                  | PostgreSQL host port              |
| `SCHEMA_REGISTRY_PORT`  | `8081`                  | Schema Registry host port         |
| `CONNECT_REST_PORT`     | `8084`                  | Kafka Connect REST API host port  |

---

## What's Completed

- [x] Local Kafka infrastructure (Zookeeper + Broker + Schema Registry + PostgreSQL + Connect via Docker)
- [x] Topic creation and management scripts (`transactions`, `risk_scores`, `alerts`)
- [x] Avro schema definitions for all three event types (`schemas/*.avsc`)
- [x] Shared Avro serializer/deserializer factory (`schemas/__init__.py`)
- [x] Transaction streamer (Avro producer) with realistic synthetic data
- [x] Fraud pattern injection (high-value, foreign, new device, ATM anomaly)
- [x] Risk score processor (Avro consumer/producer) with weighted signal scoring
- [x] Alert service (Avro consumer/producer) with severity/action mapping
- [x] Streamlit UI with Avro serialization for manual transaction submission
- [x] Kafka Connect JDBC Sink connectors with AvroConverter for all three topics
- [x] Append-only PostgreSQL tables (no upsert, no delete)
- [x] Connector registration script (`register-connectors-prod.sh`)
- [x] Pipeline validation script (`validate-prod.sh`)
- [x] Color-coded console output across all services
- [x] All credentials externalized via environment variables
- [x] No auto topic creation — topics managed via script
