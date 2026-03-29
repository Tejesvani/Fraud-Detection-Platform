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
from data_quality.contracts.alert_contract import validate_alert

# ── Prometheus metrics ─────────────────────────────────────────────────────────

from prometheus_client import Counter, Histogram, Gauge

ALERTS_GENERATED = Counter(
    "fraud_alerts_generated_total",
    "Total alerts generated",
    ["severity"],  # 'INFO' | 'WARNING' | 'CRITICAL'
)
ALERTS_REJECTED = Counter(
    "fraud_alert_events_rejected_total",
    "Alert events rejected by validation",
)
ALERT_LATENCY = Histogram(
    "fraud_alert_latency_seconds",
    "Time to generate and produce an alert",
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1],
)
CONSUMER_LAG = Gauge(
    "fraud_alert_service_consumer_lag",
    "Consumer lag for alert service",
)


# ── Enums ──────────────────────────────────────────────────────────────────────

class Severity(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class Action(Enum):
    LOG_ONLY = "LOG_ONLY"
    REVIEW_TRANSACTION = "REVIEW_TRANSACTION"
    BLOCK_CARD = "BLOCK_CARD"


# ── Risk label → Severity + Action mapping ─────────────────────────────────────

ALERT_POLICY = {
    "LOW":    (Severity.INFO,     Action.LOG_ONLY),
    "MEDIUM": (Severity.WARNING,  Action.REVIEW_TRANSACTION),
    "HIGH":   (Severity.CRITICAL, Action.BLOCK_CARD),
}

SCHEMA_VERSION = 1


# ── Alert builder ──────────────────────────────────────────────────────────────

def build_alert(risk_event: dict) -> dict:
    """Map a risk event to an immutable alert event."""
    severity, action = ALERT_POLICY[risk_event["risk_label"]]

    return {
        "alert_id": str(uuid.uuid4()),
        "risk_event_id": risk_event["risk_event_id"],
        "transaction_event_id": risk_event["transaction_event_id"],
        "card_id": risk_event["card_id"],
        "risk_score": risk_event["risk_score"],
        "severity": severity.value,
        "action": action.value,
        "reasons": risk_event["reasons"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": SCHEMA_VERSION,
    }


# ── Console output ─────────────────────────────────────────────────────────────

SEVERITY_COLORS = {
    "INFO":     "\033[92m",  # green
    "WARNING":  "\033[93m",  # yellow
    "CRITICAL": "\033[91m",  # red
}
RESET = "\033[0m"


def print_alert(alert: dict):
    severity = alert["severity"]
    color = SEVERITY_COLORS.get(severity, "")

    print(
        f"{color}[{severity:<8}]{RESET} "
        f"action={alert['action']:<20} "
        f"card={alert['card_id']:<7} "
        f"risk_event={alert['risk_event_id'][:8]}..."
    )
    if alert["reasons"]:
        print(f"          reasons: {', '.join(alert['reasons'])}")
    print()


# ── Kafka config ───────────────────────────────────────────────────────────────

KAFKA_BROKER = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
INPUT_TOPIC = os.environ.get("KAFKA_TOPIC_RISK_SCORES", "risk_scores")
OUTPUT_TOPIC = os.environ.get("KAFKA_TOPIC_ALERTS", "alerts")
GROUP_ID = os.environ.get("KAFKA_GROUP_ALERT_SERVICE", "alert-service-group")

# Feature flag
SCHEMA_REGISTRY_ENABLED = os.environ.get("SCHEMA_REGISTRY_ENABLED", "true").lower() == "true"


def delivery_callback(err, msg):
    if err:
        print(f"[ERROR] Delivery to {msg.topic()} failed: {err}")


def run():
    setup_observability(service_name="alert-service", metrics_port=8002)
    logger = get_logger()

    consumer = Consumer({
        "bootstrap.servers": KAFKA_BROKER,
        "group.id": GROUP_ID,
        "auto.offset.reset": "latest",
        "enable.auto.commit": False,
    })

    producer = Producer({
        "bootstrap.servers": KAFKA_BROKER,
        "client.id": "alert-service",
        "queue.buffering.max.messages": 10000,
    })

    # Initialize Avro serde
    risk_deserializer = None
    alert_serializer = None

    if SCHEMA_REGISTRY_ENABLED:
        try:
            risk_deserializer = get_avro_deserializer("RiskScoreEvent")
            alert_serializer = get_avro_serializer("AlertEvent")
            print("[Schema Registry] Avro serde ENABLED")
        except Exception as e:
            print(f"[Schema Registry] Could not connect — falling back to JSON: {e}")
    else:
        print("[Schema Registry] Avro serde DISABLED")

    consumer.subscribe([INPUT_TOPIC])

    print(f"Alert Service started")
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

            # Deserialize
            if risk_deserializer is not None:
                ctx = SerializationContext(INPUT_TOPIC, MessageField.VALUE)
                risk_event = risk_deserializer(msg.value(), ctx)
            else:
                risk_event = json.loads(msg.value().decode("utf-8"))

            if risk_event["risk_label"] not in ALERT_POLICY:
                consumer.commit(msg)
                continue

            with ALERT_LATENCY.time():
                alert = build_alert(risk_event)

                # Layer 1 — producer-side validation (primary defense)
                errors = validate_alert(alert)
                if errors:
                    print(
                        f"\033[91m[REJECTED] alert_id={alert['alert_id']} "
                        f"errors: {errors}\033[0m"
                    )
                    logger.warning("alert_rejected", alert_id=alert["alert_id"],
                                   card_id=alert["card_id"], errors=errors)
                    ALERTS_REJECTED.inc()
                    consumer.commit(msg)
                    continue

                # Serialize
                if alert_serializer is not None:
                    ctx = SerializationContext(OUTPUT_TOPIC, MessageField.VALUE)
                    value_bytes = alert_serializer(alert, ctx)
                else:
                    value_bytes = json.dumps(alert).encode("utf-8")

                producer.produce(
                    topic=OUTPUT_TOPIC,
                    key=alert["card_id"],
                    value=value_bytes,
                    callback=delivery_callback,
                )
                producer.poll(0)

            ALERTS_GENERATED.labels(severity=alert["severity"]).inc()
            logger.info("alert_generated", alert_id=alert["alert_id"],
                        card_id=alert["card_id"], severity=alert["severity"],
                        action=alert["action"],
                        risk_event_id=alert["risk_event_id"])

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

            print_alert(alert)

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        remaining = producer.flush(timeout=5)
        if remaining > 0:
            print(f"[WARN] {remaining} message(s) were not delivered")
        consumer.close()
        print("Alert Service closed.")


if __name__ == "__main__":
    run()
