# Fraud Detection Platform

A real-time fraud detection system built on Apache Kafka. Simulated card transactions are streamed through a pipeline that evaluates each transaction for fraud signals, produces risk scores, and emits alerts — all in real time. All events are persisted to PostgreSQL. Includes a Streamlit UI for manual transaction submission.

---

## Architecture

```
  PRODUCERS                    KAFKA BROKER (localhost:9092)              CONSUMERS
  ─────────                   ┌───────────────────────────┐              ─────────
                              │                           │
┌──────────────────┐          │  ┌─────────────────────┐  │          ┌──────────────────────┐
│                  │ produce  │  │                     │  │ consume  │                      │
│   Transaction    │─────────>│──│    transactions     │──│─────────>│  Risk Score          │
│   Streamer       │          │  │    (3 partitions)   │  │          │  Processor           │
│                  │          │  │                     │  │          │                      │
└──────────────────┘          │  │          ▲          │  │          └──────────────────────┘
                              │  └──────────│──────────┘  │                    │
┌──────────────────┐ produce  │             │             │                    │
│                  │──────────│─────────────┘             │                    │ produce
│   Streamlit UI   │          │                           │                    │
│   (Frontend)     │          │  ┌─────────────────────┐  │                    │
│                  │          │  │                     │<─│────────────────────┘
└────────┬─────────┘          │  │    risk_scores      │  │
         │                    │  │    (3 partitions)   │  │          ┌──────────────────────┐
         │                    │  │                     │──│─────────>│                      │
         │                    │  └─────────────────────┘  │ consume  │  Alert Service       │
         │                    │                           │          │                      │
         │                    │                           │          └──────────────────────┘
         │                    │  ┌─────────────────────┐  │                    │
         │           poll     │  │                     │<─│────────────────────┘
         └───────────────────<│──│    alerts           │  │            produce
                    alerts    │  │    (1 partition)    │  │
                              │  │                     │  │
                              │  └─────────────────────┘  │
                              │                           │
                              └───────────────────────────┘

                                        │ consume (all 3 topics)
                                        ▼
                              ┌──────────────────────┐
                              │                      │
                              │  Persistence Service │──────> PostgreSQL
                              │                      │        (fraud_detection_db)
                              └──────────────────────┘
```

The system is a Kafka-based event-driven fraud detection pipeline. It consists of two producers — a background Transaction Streamer and a user-facing Streamlit UI — both publishing transaction events to the Kafka `transactions` topic. A Risk Score Processor consumes from this topic, evaluates fraud risk, and publishes scored results to the `risk_scores` topic. An Alert Service then consumes from `risk_scores`, determines severity and required action, and publishes alert events to the `alerts` topic. The Streamlit UI monitors the `alerts` topic to retrieve the alert for the event produced from the frontend and display the final outcome to the user. A Persistence Service consumes from all three topics and writes every event to PostgreSQL for durable storage. All services communicate asynchronously via Kafka topics, ensuring decoupled, event-driven processing.

**Data flow:**
1. **Producers** publish transaction events to the `transactions` topic:
   - `transaction_streamer.py` generates one synthetic transaction every 2 seconds.
   - The Streamlit UI submits user transactions (with `"source": "ui"`).
2. **Risk Score Processor** consumes from `transactions`, evaluates each event against fraud signals, and produces an immutable risk event to the `risk_scores` topic.
3. **Alert Service** consumes from `risk_scores`, maps each risk label to a severity and action, and produces an alert event to the `alerts` topic.
4. **Persistence Service** consumes from all three topics (`transactions`, `risk_scores`, `alerts`) and persists every event to PostgreSQL with idempotent inserts.
5. For UI-submitted transactions, the Streamlit UI polls the `alerts` topic, matches on `transaction_event_id`, and displays the fraud analysis result.

---

## Folder Structure

```
fraud-detection-platform/
├── kafka-local/
│   ├── docker-compose.yml          # Spins up Zookeeper + Kafka broker + PostgreSQL
│   └── postgres-init/
│       └── init.sql                # Auto-creates PostgreSQL tables on first startup
│   ├── .env 
│   ├── .env.example  
├── kafka/
│   ├── create-topics.sh            # Creates all Kafka topics
│   └── delete-topics.sh            # Tears down all Kafka topics
├── producer/
│   └── transaction_streamer.py     # Kafka producer — synthetic transaction generator
├── processor/
│   └── risk_score_processor.py     # Kafka consumer/producer — risk scoring engine
├── consumer/
│   ├── alert_service.py            # Kafka consumer/producer — alert emitter
│   └── persistence_service.py      # Kafka consumer — persists all events to PostgreSQL
├── frontend/
│   └── app.py                      # Streamlit UI — manual transaction submission
├── .env                            # Environment variables (not committed — in .gitignore)
├── .env.example                    # Template showing all available env vars
├── requirements.txt                # Python dependencies (pinned versions)
├── venv/                           # Python 3.9 virtual environment
├── .gitignore
└── README.md
```

---

## Environment Configuration

All services read configuration from environment variables with sensible defaults. A `.env` file at the project root is loaded automatically via `python-dotenv`. The project works out-of-the-box without a `.env` file — all defaults match the local development setup.

Copy `.env.example` to `.env` and adjust values for your environment:

```bash
cp .env.example .env
```

### Available Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka broker address |
| `KAFKA_TOPIC_TRANSACTIONS` | `transactions` | Topic for raw transaction events |
| `KAFKA_TOPIC_RISK_SCORES` | `risk_scores` | Topic for scored risk events |
| `KAFKA_TOPIC_ALERTS` | `alerts` | Topic for alert events |
| `KAFKA_GROUP_RISK_PROCESSOR` | `risk-score-processor-group` | Consumer group for risk processor |
| `KAFKA_GROUP_ALERT_SERVICE` | `alert-service-group` | Consumer group for alert service |
| `KAFKA_GROUP_PERSISTENCE` | `persistence-service-group` | Consumer group for persistence service |
| `POSTGRES_USER` | `username` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `password` | PostgreSQL password |
| `POSTGRES_HOST` | `localhost` | PostgreSQL host |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_DB` | `fraud_detection_db` | PostgreSQL database name |

Docker Compose also reads from `.env` for Kafka and PostgreSQL container configuration.

---

## Kafka Topics

| Topic          | Partitions | Purpose                                            |
|----------------|------------|----------------------------------------------------|
| `transactions` | 3          | Raw card transaction events from streamer and UI   |
| `risk_scores`  | 3          | Scored risk events from the processor              |
| `alerts`       | 1          | Alert events with severity and action              |

---

## Data Schemas

### Transaction Event (topic: `transactions`)

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
  "source": "ui"
}
```

| Field              | Type     | Description                                            |
|--------------------|----------|--------------------------------------------------------|
| `event_id`         | UUID     | Unique identifier for this transaction                 |
| `timestamp`        | ISO-8601 | UTC time the transaction was created                   |
| `card_id`          | string   | Card used (`card1` through `card10`)                   |
| `transaction_type` | enum     | One of the six transaction types (see below)           |
| `merchant_category`| string   | Category of the merchant                               |
| `amount`           | float    | Transaction amount in USD                              |
| `country`          | string   | Two-letter country code                                |
| `device_id`        | string   | Device used for the transaction                        |
| `source`           | string   | `"ui"` when submitted from the frontend (optional)     |

**Transaction types (enum):**

| Enum Value              | Examples                  | Fraud Relevance     |
|-------------------------|---------------------------|---------------------|
| `pos_purchase`          | grocery, fuel             | Common baseline     |
| `online_purchase`       | e-commerce, digital goods | Higher fraud risk   |
| `subscription`          | streaming, SaaS           | Small, repeated     |
| `high_value_retail`     | electronics, jewelry      | Fraud-prone         |
| `atm_withdrawal`        | cash                      | Different pattern   |
| `international_purchase`| travel, duty-free         | Geographic risk     |

### Risk Score Event (topic: `risk_scores`)

Produced by `risk_score_processor.py`. Keyed by `card_id`. Each event is immutable and auditable.

```json
{
  "risk_event_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "transaction_event_id": "550e8400-e29b-41d4-a716-446655440000",
  "card_id": "card9",
  "risk_score": 0.7,
  "risk_label": "HIGH",
  "reasons": ["HIGH_AMOUNT", "FOREIGN_COUNTRY"],
  "evaluated_at": "2026-02-08T18:08:08.123456+00:00"
}
```

| Field                  | Type     | Description                                    |
|------------------------|----------|------------------------------------------------|
| `risk_event_id`        | UUID     | Unique identifier for this risk evaluation     |
| `transaction_event_id` | UUID     | References the original transaction `event_id` |
| `card_id`              | string   | Card that was evaluated                        |
| `risk_score`           | float    | Score between 0.0 and 1.0 (capped)            |
| `risk_label`           | enum     | `LOW`, `MEDIUM`, or `HIGH`                     |
| `reasons`              | string[] | List of triggered signal names                 |
| `evaluated_at`         | ISO-8601 | UTC time the scoring was performed             |

### Alert Event (topic: `alerts`)

Produced by `alert_service.py`. Keyed by `card_id`. Each event is immutable and auditable.

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
  "created_at": "2026-02-08T18:08:08.234567+00:00"
}
```

| Field                  | Type     | Description                                    |
|------------------------|----------|------------------------------------------------|
| `alert_id`             | UUID     | Unique identifier for this alert               |
| `risk_event_id`        | UUID     | References the risk event                      |
| `transaction_event_id` | UUID     | References the original transaction            |
| `card_id`              | string   | Card that triggered the alert                  |
| `risk_score`           | float    | The computed risk score                        |
| `severity`             | enum     | `INFO`, `WARNING`, or `CRITICAL`               |
| `action`               | enum     | `LOG_ONLY`, `REVIEW_TRANSACTION`, or `BLOCK_CARD` |
| `reasons`              | string[] | List of triggered signal names                 |
| `created_at`           | ISO-8601 | UTC time the alert was created                 |

---

## PostgreSQL Schema

The database is automatically initialized on first container startup via `kafka-local/postgres-init/init.sql`. Three tables mirror the Kafka topics:

| Table          | Primary Key      | Purpose                                |
|----------------|------------------|----------------------------------------|
| `transactions` | `event_id`       | Raw transaction events                 |
| `risk_scores`  | `risk_event_id`  | Risk evaluation results                |
| `alerts`       | `alert_id`       | Alert events with severity and action  |

All tables include a `raw_event` JSONB column storing the full original Kafka event, and indexes on `card_id` for efficient lookups. Inserts are idempotent via `ON CONFLICT DO NOTHING`.

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

| Signal           | Condition                          | Weight |
|------------------|------------------------------------|--------|
| `HIGH_AMOUNT`    | `amount >= 800`                    | +0.4   |
| `FOREIGN_COUNTRY`| `country != "US"`                  | +0.3   |
| `NEW_DEVICE`     | Device differs from card's home device | +0.2 |
| `ATM_ANOMALY`    | ATM withdrawal with `amount >= 500`| +0.3   |

**Risk labels:**

| Label    | Score Range       |
|----------|-------------------|
| `LOW`    | score < 0.3       |
| `MEDIUM` | 0.3 <= score < 0.7|
| `HIGH`   | score >= 0.7      |

**New device heuristic:** Since the processor is stateless (no external database), it maintains a lightweight in-memory map of `card_id -> first observed device_id`. The first device seen for a card is treated as its home device. Any subsequent transaction from that card with a different device triggers the signal. This resets on processor restart.

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

- **Docker** (for Kafka, Zookeeper, and PostgreSQL)
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

Edit `.env` if you need to change any defaults (Kafka broker, PostgreSQL credentials, etc.). The default values work out-of-the-box for local development.

### 4. Start Infrastructure (Kafka + PostgreSQL)

```bash
cd kafka-local
docker compose --env-file ../.env up -d
```

Wait a few seconds for the broker and database to be ready. PostgreSQL tables are created automatically on first startup.

### 5. Create Topics

```bash
bash kafka/create-topics.sh
```

Verify topics were created:

```bash
docker exec kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --list
```

### 6. Start the Transaction Streamer (Terminal 1)

```bash
venv/bin/python producer/transaction_streamer.py
```

You will see one transaction logged every 2 seconds.

### 7. Start the Risk Score Processor (Terminal 2)

```bash
venv/bin/python processor/risk_score_processor.py
```

You will see color-coded risk evaluations printed for each incoming transaction:
- Green `[LOW   ]` -- normal transaction
- Yellow `[MEDIUM]` -- one signal triggered
- Red `[HIGH  ]` -- multiple signals triggered

### 8. Start the Alert Service (Terminal 3)

```bash
venv/bin/python consumer/alert_service.py
```

You will see color-coded alerts:
- Green `[INFO    ]` -- LOG_ONLY
- Yellow `[WARNING ]` -- REVIEW_TRANSACTION
- Red `[CRITICAL]` -- BLOCK_CARD

### 9. Start the Persistence Service (Terminal 4)

```bash
venv/bin/python consumer/persistence_service.py
```

You will see structured log entries for each event persisted to PostgreSQL:

```
Persisted  topic=transactions   partition=0  offset=42
```

### 10. Start the Streamlit UI (Terminal 5)

```bash
streamlit run frontend/app.py
```

Opens at `http://localhost:8501`. Submit a transaction and see the fraud analysis result in real time.

---

## Inspecting Kafka Streams

### List all topics

```bash
docker exec kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --list
```

### Read raw transactions from the beginning

```bash
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic transactions \
  --from-beginning
```

### Read risk score events from the beginning

```bash
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic risk_scores \
  --from-beginning
```

### Read alerts from the beginning

```bash
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic alerts \
  --from-beginning
```

### Read with keys displayed

```bash
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic transactions \
  --from-beginning \
  --property print.key=true \
  --property key.separator=" | "
```

### Check consumer group lag

```bash
docker exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group risk-score-processor-group \
  --describe
```

```bash
docker exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group alert-service-group \
  --describe
```

```bash
docker exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group persistence-service-group \
  --describe
```

### Delete and recreate topics (reset)

```bash
bash kafka/delete-topics.sh
bash kafka/create-topics.sh
```

---

## Querying PostgreSQL

Connect to the database:

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

### Transaction with its risk score and alert

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

1. Press `Ctrl+C` in the streamer, processor, alert service, and persistence service terminals.
2. Stop the Streamlit UI with `Ctrl+C`.
3. Shut down infrastructure:

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
- [x] PostgreSQL database with automated schema initialization
- [x] Topic creation and management scripts (`transactions`, `risk_scores`, `alerts`)
- [x] Transaction streamer (producer) with realistic synthetic data
- [x] Fraud pattern injection (high-value, foreign, new device, ATM anomaly)
- [x] Risk score processor (consumer/producer) with weighted signal scoring
- [x] Immutable risk event schema emitted to `risk_scores` topic
- [x] Alert service (consumer/producer) with severity/action mapping
- [x] Immutable alert event schema emitted to `alerts` topic
- [x] Persistence service — consumes all topics and writes to PostgreSQL
- [x] Streamlit UI for manual transaction submission with real-time fraud analysis
- [x] Color-coded console output across all services
- [x] Environment variable externalization via `.env` and `python-dotenv`