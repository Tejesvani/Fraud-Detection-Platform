import json
import os
import uuid
from datetime import datetime, timezone
from enum import Enum

from confluent_kafka import Consumer, Producer, KafkaError

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


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


def delivery_callback(err, msg):
    if err:
        print(f"[ERROR] Delivery to {msg.topic()} failed: {err}")


def run():
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

    consumer.subscribe([INPUT_TOPIC])

    print(f"Alert Service started")
    print(f"  consuming from : {INPUT_TOPIC}")
    print(f"  producing to   : {OUTPUT_TOPIC}")
    print("Press Ctrl+C to stop\n")

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

            risk_event = json.loads(msg.value().decode("utf-8"))

            if risk_event["risk_label"] not in ALERT_POLICY:
                consumer.commit(msg)
                continue

            alert = build_alert(risk_event)

            producer.produce(
                topic=OUTPUT_TOPIC,
                key=alert["card_id"],
                value=json.dumps(alert),
                callback=delivery_callback,
            )
            producer.poll(0)

            consumer.commit(msg)

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
