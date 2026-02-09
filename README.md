# Fraud Detection Platform

A real-time fraud detection system built on Apache Kafka. Simulated card transactions are streamed through a pipeline that evaluates each transaction for fraud signals and produces risk scores in real time.

---

## Architecture

```
┌─────────────────────┐       ┌───────────────┐       ┌──────────────────────┐
│ Transaction Streamer│──────>│  Kafka Broker  │──────>│ Risk Score Processor │
│     (Producer)      │ topic:│  (Docker)      │ topic:│ (Consumer + Producer)│
│                     │ trans-│                │ risk_ │                      │
│ Generates synthetic │ action│  localhost:9092│ scores│ Scores each txn and  │
│ card transactions   │   s   │                │       │ emits risk events    │
└─────────────────────┘       └───────────────┘       └──────────────────────┘
```

**Data flow:**
1. `transaction_streamer.py` generates one synthetic transaction every 2 seconds and publishes it to the `transactions` topic.
2. `risk_score_processor.py` consumes from `transactions`, evaluates each event against fraud signals, and produces an immutable risk event to the `risk_scores` topic.

---

## Folder Structure

```
fraud-detection-platform/
├── kafka-local/
│   └── docker-compose.yml          # Spins up Zookeeper + Kafka broker
├── kafka/
│   ├── create-topics.sh            # Creates all Kafka topics
│   └── delete-topics.sh            # Tears down all Kafka topics
├── producer/
│   └── transaction_streamer.py     # Kafka producer — synthetic transaction generator
├── processor/
│   └── risk_score_processor.py     # Kafka consumer/producer — risk scoring engine
├── venv/                           # Python 3.9 virtual environment
└── README.md
```

---

## Kafka Topics

| Topic          | Partitions | Purpose                                       |
|----------------|------------|-----------------------------------------------|
| `transactions` | 3          | Raw card transaction events from the streamer |
| `risk_scores`  | 3          | Scored risk events from the processor         |
| `alerts`       | 1          | Reserved for future alerting system           |

---

## Data Schemas

### Transaction Event (topic: `transactions`)

Produced by `transaction_streamer.py`. Keyed by `card_id`.

```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-02-08T18:08:08.000705+00:00",
  "card_id": "card9",
  "transaction_type": "online_purchase",
  "merchant_category": "e-commerce",
  "amount": 1245.50,
  "country": "NG",
  "device_id": "device_012"
}
```

**Fields:**

| Field              | Type   | Description                                   |
|--------------------|--------|-----------------------------------------------|
| `event_id`         | UUID   | Unique identifier for this transaction        |
| `timestamp`        | ISO-8601 | UTC time the transaction was created        |
| `card_id`          | string | Card used (`card1` through `card10`)          |
| `transaction_type` | enum   | One of the six transaction types (see below)  |
| `merchant_category`| string | Category of the merchant                      |
| `amount`           | float  | Transaction amount in USD                     |
| `country`          | string | Two-letter country code                       |
| `device_id`        | string | Device used for the transaction               |

**Transaction types (enum):**

| Enum Value             | Examples                    | Fraud Relevance       |
|------------------------|-----------------------------|-----------------------|
| `pos_purchase`         | grocery, fuel               | Common baseline       |
| `online_purchase`      | e-commerce, digital goods   | Higher fraud risk     |
| `subscription`         | streaming, SaaS             | Small, repeated       |
| `high_value_retail`    | electronics, jewelry        | Fraud-prone           |
| `atm_withdrawal`       | cash                        | Different pattern     |
| `international_purchase`| travel, duty-free          | Geographic risk       |

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

**Fields:**

| Field                  | Type       | Description                                           |
|------------------------|------------|-------------------------------------------------------|
| `risk_event_id`        | UUID       | Unique identifier for this risk evaluation            |
| `transaction_event_id` | UUID       | References the original transaction `event_id`        |
| `card_id`              | string     | Card that was evaluated                               |
| `risk_score`           | float      | Score between 0.0 and 1.0 (capped)                    |
| `risk_label`           | enum       | `LOW`, `MEDIUM`, or `HIGH`                            |
| `reasons`              | string[]   | List of triggered signal names                        |
| `evaluated_at`         | ISO-8601   | UTC time the scoring was performed                    |

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

## Prerequisites

- **Docker** (for Kafka and Zookeeper)
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
pip install confluent-kafka
```

### 3. Start Kafka

```bash
cd kafka-local
docker compose up -d
```

Wait a few seconds for the broker to be ready.

### 4. Create Topics

```bash
bash kafka/create-topics.sh
```

Verify topics were created:

```bash
docker exec kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --list
```

### 5. Start the Transaction Streamer (Terminal 1)

```bash
venv/bin/python producer/transaction_streamer.py
```

You will see one transaction logged every 2 seconds.

### 6. Start the Risk Score Processor (Terminal 2)

```bash
venv/bin/python processor/risk_score_processor.py
```

You will see color-coded risk evaluations printed for each incoming transaction:
- Green `[LOW   ]` -- normal transaction
- Yellow `[MEDIUM]` -- one signal triggered
- Red `[HIGH  ]` -- multiple signals triggered

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

### Delete and recreate topics (reset)

```bash
bash kafka/delete-topics.sh
bash kafka/create-topics.sh
```

---

## Stopping

1. Press `Ctrl+C` in both the streamer and processor terminals.
2. Shut down Kafka:

```bash
cd kafka-local
docker compose down
```

---

## What's Completed (Phase 1)

- [x] Local Kafka infrastructure (Zookeeper + Broker via Docker)
- [x] Topic creation and management scripts
- [x] Transaction streamer (producer) with realistic synthetic data
- [x] Fraud pattern injection (high-value, foreign, new device, ATM anomaly)
- [x] Risk score processor (consumer/producer) with weighted signal scoring
- [x] Immutable risk event schema emitted to `risk_scores` topic
- [x] Color-coded console output for real-time monitoring
