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
        "client.id": "risk-score-processor",    # name for the producer client
        "queue.buffering.max.messages": 10000,  # safety cap on producer queue
    })

    consumer.subscribe([INPUT_TOPIC])

    print(f"Risk Score Processor started")
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

            txn = json.loads(msg.value().decode("utf-8"))
            risk_event = score_transaction(txn)

            # Emit risk event to risk_scores topic, keyed by card_id
            producer.produce(
                topic=OUTPUT_TOPIC,
                key=risk_event["card_id"],
                value=json.dumps(risk_event),
                callback=delivery_callback,
            )
            producer.poll(0)

            consumer.commit(msg)

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
