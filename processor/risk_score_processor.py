import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from enum import Enum

from confluent_kafka import Consumer, Producer, KafkaError
from confluent_kafka.serialization import SerializationContext, MessageField

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from shared.schema_registry import get_avro_serializer, get_avro_deserializer
from shared.observability import setup_observability, get_logger
from data_quality.contracts.risk_score_contract import validate_risk_score

# ── Prometheus metrics ─────────────────────────────────────────────────────────

from prometheus_client import Counter, Histogram, Gauge

EVENTS_SCORED = Counter(
    "fraud_events_scored_total",
    "Total events scored",
    ["risk_label"],  # 'LOW' | 'MEDIUM' | 'HIGH'
)
EVENTS_REJECTED = Counter(
    "fraud_risk_events_rejected_total",
    "Risk events rejected by validation",
)
SCORING_LATENCY = Histogram(
    "fraud_scoring_latency_seconds",
    "Time to score a transaction",
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1],
)
RISK_SCORE_HISTOGRAM = Histogram(
    "fraud_risk_score_distribution",
    "Distribution of risk scores",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)
CONSUMER_LAG = Gauge(
    "fraud_risk_processor_consumer_lag",
    "Consumer lag for risk processor",
)


# ── Risk label enum ────────────────────────────────────────────────────────────

class RiskLabel(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# ── Signal weights ─────────────────────────────────────────────────────────────

WEIGHT_HIGH_AMOUNT = 0.4
WEIGHT_FOREIGN_COUNTRY = 0.3
WEIGHT_NEW_DEVICE = 0.2
WEIGHT_ATM_ANOMALY = 0.3

HOME_COUNTRY = "US"

# Stateful Stream: card_id → first observed device_id (considered as "home device")
card_known_devices: dict[str, str] = {}

SCHEMA_VERSION = 1


# ── Scoring engine ─────────────────────────────────────────────────────────────

def score_transaction(txn: dict) -> dict:
    """Evaluate a transaction and return an immutable risk event."""
    score = 0.0
    reasons: list[str] = []

    # Signal 1 — High amount
    if txn["amount"] >= 800:
        score += WEIGHT_HIGH_AMOUNT
        reasons.append("HIGH_AMOUNT")

    # Signal 2 — Foreign country
    if txn["country"] != HOME_COUNTRY:
        score += WEIGHT_FOREIGN_COUNTRY
        reasons.append("FOREIGN_COUNTRY")

    # Signal 3 — New device (heuristic: first device seen per card becomes "home")
    card_id = txn["card_id"]
    device_id = txn["device_id"]

    if card_id not in card_known_devices:
        card_known_devices[card_id] = device_id
    elif card_known_devices[card_id] != device_id:
        score += WEIGHT_NEW_DEVICE
        reasons.append("NEW_DEVICE")

    # Signal 4 — ATM anomaly
    if txn["transaction_type"] == "atm_withdrawal" and txn["amount"] >= 500:
        score += WEIGHT_ATM_ANOMALY
        reasons.append("ATM_ANOMALY")

    # Cap at 1.0
    score = min(score, 1.0)

    # Label
    if score >= 0.7:
        label = RiskLabel.HIGH
    elif score >= 0.3:
        label = RiskLabel.MEDIUM
    else:
        label = RiskLabel.LOW

    return {
        "risk_event_id": str(uuid.uuid4()),
        "transaction_event_id": txn["event_id"],
        "card_id": txn["card_id"],
        "risk_score": round(score, 2),
        "risk_label": label.value,
        "reasons": reasons,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": SCHEMA_VERSION,
    }


# ── Console output ─────────────────────────────────────────────────────────────

LABEL_COLORS = {
    "LOW": "\033[92m",     # green
    "MEDIUM": "\033[93m",  # yellow
    "HIGH": "\033[91m",    # red
}
RESET = "\033[0m"


def print_risk_event(event: dict, txn: dict):
    label = event["risk_label"]
    color = LABEL_COLORS.get(label, "")

    print(
        f"{color}[{label:<6}]{RESET} "
        f"score={event['risk_score']:.2f}  "
        f"card={event['card_id']:<7} "
        f"type={txn['transaction_type']:<24} "
        f"amount=${txn['amount']:<9} "
        f"country={txn['country']}  "
        f"device={txn['device_id']}"
    )
    if event["reasons"]:
        print(f"        signals: {', '.join(event['reasons'])}")
    print()


# ── Kafka config ───────────────────────────────────────────────────────────────

KAFKA_BROKER = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
INPUT_TOPIC = os.environ.get("KAFKA_TOPIC_TRANSACTIONS", "transactions")
OUTPUT_TOPIC = os.environ.get("KAFKA_TOPIC_RISK_SCORES", "risk_scores")
GROUP_ID = os.environ.get("KAFKA_GROUP_RISK_PROCESSOR", "risk-score-processor-group")

# Feature flag
SCHEMA_REGISTRY_ENABLED = os.environ.get("SCHEMA_REGISTRY_ENABLED", "true").lower() == "true"


def delivery_callback(err, msg):
    if err:
        print(f"[ERROR] Delivery to {msg.topic()} failed: {err}")


def run():
    setup_observability(service_name="risk-processor", metrics_port=8001)
    logger = get_logger()

    consumer = Consumer({
        "bootstrap.servers": KAFKA_BROKER,
        "group.id": GROUP_ID,
        "auto.offset.reset": "latest",
        "enable.auto.commit": False,
    })

    producer = Producer({
        "bootstrap.servers": KAFKA_BROKER,
        "client.id": "risk-score-processor",
        "queue.buffering.max.messages": 10000,
    })

    # Initialize Avro serializer/deserializer
    txn_deserializer = None
    risk_serializer = None

    if SCHEMA_REGISTRY_ENABLED:
        try:
            txn_deserializer = get_avro_deserializer("TransactionEvent")
            risk_serializer = get_avro_serializer("RiskScoreEvent")
            print("[Schema Registry] Avro serde ENABLED")
        except Exception as e:
            print(f"[Schema Registry] Could not connect — falling back to JSON: {e}")
    else:
        print("[Schema Registry] Avro serde DISABLED")

    consumer.subscribe([INPUT_TOPIC])

    print(f"Risk Score Processor started")
    print(f"  consuming from : {INPUT_TOPIC}")
    print(f"  producing to   : {OUTPUT_TOPIC}")
    print("Press Ctrl+C to stop\n")

    _last_lag_check = 0.0
    _LAG_CHECK_INTERVAL = 10  # seconds

    try:
        while True:
            msg = consumer.poll(timeout=1.0)

            if msg is None:
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                print(f"[ERROR] {msg.error()}")
                continue

            # Deserialize: Avro or JSON
            try:
                if txn_deserializer is not None:
                    ctx = SerializationContext(INPUT_TOPIC, MessageField.VALUE)
                    txn = txn_deserializer(msg.value(), ctx)
                else:
                    txn = json.loads(msg.value().decode("utf-8"))
            except Exception as exc:
                print(f"\033[93m[DESER_ERROR]\033[0m topic={INPUT_TOPIC} error={exc} — skipping message")
                consumer.commit(msg)
                continue

            with SCORING_LATENCY.time():
                risk_event = score_transaction(txn)

            EVENTS_SCORED.labels(risk_label=risk_event["risk_label"]).inc()
            RISK_SCORE_HISTOGRAM.observe(risk_event["risk_score"])

            # Layer 1 — producer-side validation (primary defense)
            errors = validate_risk_score(risk_event)
            if errors:
                print(
                    f"\033[91m[REJECTED] risk_event_id={risk_event['risk_event_id']} "
                    f"errors: {errors}\033[0m"
                )
                logger.warning("risk_event_rejected",
                               risk_event_id=risk_event["risk_event_id"],
                               card_id=risk_event["card_id"], errors=errors)
                EVENTS_REJECTED.inc()
                consumer.commit(msg)
                continue

            logger.info("risk_event_scored",
                        risk_event_id=risk_event["risk_event_id"],
                        transaction_event_id=risk_event["transaction_event_id"],
                        card_id=risk_event["card_id"],
                        risk_score=risk_event["risk_score"],
                        risk_label=risk_event["risk_label"],
                        reasons=risk_event["reasons"])

            # Serialize: Avro or JSON
            if risk_serializer is not None:
                ctx = SerializationContext(OUTPUT_TOPIC, MessageField.VALUE)
                value_bytes = risk_serializer(risk_event, ctx)
            else:
                value_bytes = json.dumps(risk_event).encode("utf-8")

            producer.produce(
                topic=OUTPUT_TOPIC,
                key=risk_event["card_id"],
                value=value_bytes,
                callback=delivery_callback,
            )
            producer.poll(0)

            consumer.commit(msg)

            now = time.time()
            if now - _last_lag_check >= _LAG_CHECK_INTERVAL:
                try:
                    total_lag = 0
                    for tp in consumer.assignment():
                        (low, high) = consumer.get_watermark_offsets(tp, timeout=5.0)
                        committed_offsets = consumer.committed([tp], timeout=5.0)
                        if committed_offsets and committed_offsets[0].offset >= 0:
                            total_lag += high - committed_offsets[0].offset
                        else:
                            total_lag += high - low
                    CONSUMER_LAG.set(total_lag)
                except Exception:
                    pass
                _last_lag_check = now

            print_risk_event(risk_event, txn)

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        remaining = producer.flush(timeout=5)
        if remaining > 0:
            print(f"[WARN] {remaining} message(s) were not delivered")
        consumer.close()
        print("Processor closed.")


if __name__ == "__main__":
    run()
