import os
import random
import time
import uuid
from datetime import datetime, timezone
from enum import Enum

from confluent_kafka import Producer
from confluent_kafka.serialization import SerializationContext, MessageField, StringSerializer

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from schemas import get_avro_serializer


# ── Transaction type enum ──────────────────────────────────────────────────────

class TransactionType(Enum):
    POS_PURCHASE = "pos_purchase"
    ONLINE_PURCHASE = "online_purchase"
    SUBSCRIPTION = "subscription"
    HIGH_VALUE_RETAIL = "high_value_retail"
    ATM_WITHDRAWAL = "atm_withdrawal"
    INTERNATIONAL_PURCHASE = "international_purchase"


# ── Reference data ─────────────────────────────────────────────────────────────

CARD_IDS = [f"card{i}" for i in range(1, 11)]

HOME_COUNTRY = "US"

FOREIGN_COUNTRIES = ["GB", "NG", "RU", "CN", "BR", "IN", "MX", "JP", "DE", "AE"]

DEVICE_POOL = [f"device_{i:03d}" for i in range(1, 21)]

# Merchant categories mapped to transaction types
MERCHANT_CATEGORIES = {
    TransactionType.POS_PURCHASE: ["grocery", "fuel", "pharmacy", "restaurant", "clothing"],
    TransactionType.ONLINE_PURCHASE: ["e-commerce", "digital_goods", "online_marketplace"],
    TransactionType.SUBSCRIPTION: ["streaming", "saas", "news", "fitness"],
    TransactionType.HIGH_VALUE_RETAIL: ["electronics", "jewelry", "luxury_goods", "appliances"],
    TransactionType.ATM_WITHDRAWAL: ["atm"],
    TransactionType.INTERNATIONAL_PURCHASE: ["travel", "duty_free", "foreign_retail", "hotel"],
}

# Normal amount ranges per transaction type
AMOUNT_RANGES = {
    TransactionType.POS_PURCHASE: (5.0, 150.0),
    TransactionType.ONLINE_PURCHASE: (10.0, 300.0),
    TransactionType.SUBSCRIPTION: (4.99, 29.99),
    TransactionType.HIGH_VALUE_RETAIL: (100.0, 600.0),
    TransactionType.ATM_WITHDRAWAL: (20.0, 300.0),
    TransactionType.INTERNATIONAL_PURCHASE: (15.0, 500.0),
}

# Base probabilities for picking each transaction type (weighted)
TYPE_WEIGHTS = {
    TransactionType.POS_PURCHASE: 35,
    TransactionType.ONLINE_PURCHASE: 25,
    TransactionType.SUBSCRIPTION: 15,
    TransactionType.HIGH_VALUE_RETAIL: 10,
    TransactionType.ATM_WITHDRAWAL: 8,
    TransactionType.INTERNATIONAL_PURCHASE: 7,
}

# Per-card stable device (home device)
CARD_HOME_DEVICES = {card: random.choice(DEVICE_POOL) for card in CARD_IDS}


# ── Fraud pattern injection ────────────────────────────────────────────────────

def maybe_inject_high_value(txn: dict) -> dict:
    """Pattern 1 — High-value purchase (~10% chance)"""
    if random.random() < 0.10:
        txn["amount"] = round(random.uniform(800.0, 2500.0), 2)
        txn["transaction_type"] = random.choice([
            TransactionType.ONLINE_PURCHASE,
            TransactionType.HIGH_VALUE_RETAIL,
        ]).value
        txn["merchant_category"] = random.choice(["electronics", "e-commerce", "luxury_goods"])
    return txn


def maybe_inject_foreign(txn: dict) -> dict:
    """Pattern 2 — Foreign transaction (~8% chance)"""
    if random.random() < 0.08:
        txn["country"] = random.choice(FOREIGN_COUNTRIES)
    return txn


def maybe_inject_new_device(txn: dict) -> dict:
    """Pattern 3 — New/unknown device (~6% chance)"""
    if random.random() < 0.06:
        card = txn["card_id"]
        home_device = CARD_HOME_DEVICES[card]
        other_devices = [d for d in DEVICE_POOL if d != home_device]
        txn["device_id"] = random.choice(other_devices)
    return txn


def maybe_inject_atm_anomaly(txn: dict) -> dict:
    """Pattern 4 — ATM anomaly (~4% chance)"""
    if random.random() < 0.04:
        txn["transaction_type"] = TransactionType.ATM_WITHDRAWAL.value
        txn["merchant_category"] = "atm"
        txn["amount"] = round(random.uniform(500.0, 2000.0), 2)
        if random.random() < 0.5:
            txn["country"] = random.choice(FOREIGN_COUNTRIES)
    return txn


# ── Transaction generator ──────────────────────────────────────────────────────

def generate_transaction() -> dict:
    # Pick transaction type by weight
    types = list(TYPE_WEIGHTS.keys())
    weights = list(TYPE_WEIGHTS.values())
    txn_type = random.choices(types, weights=weights, k=1)[0]

    card_id = random.choice(CARD_IDS)
    merchant_category = random.choice(MERCHANT_CATEGORIES[txn_type])
    lo, hi = AMOUNT_RANGES[txn_type]
    amount = round(random.uniform(lo, hi), 2)

    txn = {
        "event_id": str(uuid.uuid4()),
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "card_id": card_id,
        "transaction_type": txn_type.value,
        "merchant_category": merchant_category,
        "amount": float(amount),
        "country": HOME_COUNTRY,
        "device_id": CARD_HOME_DEVICES[card_id],
        "source": "streamer",
    }

    # Independently apply fraud patterns — combinations happen naturally
    txn = maybe_inject_high_value(txn)
    txn = maybe_inject_foreign(txn)
    txn = maybe_inject_new_device(txn)
    txn = maybe_inject_atm_anomaly(txn)

    return txn


# ── Kafka producer ─────────────────────────────────────────────────────────────

KAFKA_BROKER = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPIC = os.environ.get("KAFKA_TOPIC_TRANSACTIONS", "transactions")


def delivery_callback(err, msg):
    if err:
        print(f"[ERROR] Delivery failed: {err}")
    else:
        print(f"[OK] -> {msg.topic()} [{msg.partition()}] @ offset {msg.offset()}")


def run():
    avro_serializer = get_avro_serializer("transaction")
    string_serializer = StringSerializer("utf_8")

    producer = Producer({
        "bootstrap.servers": KAFKA_BROKER,
        "client.id": "transaction-streamer",
        "queue.buffering.max.messages": 10000,
    })

    print(f"Transaction Streamer started — producing Avro to '{TOPIC}' every 2s")
    print("Press Ctrl+C to stop\n")

    try:
        while True:
            txn = generate_transaction()

            producer.produce(
                topic=TOPIC,
                key=string_serializer(txn["card_id"]),
                value=avro_serializer(txn, SerializationContext(TOPIC, MessageField.VALUE)),
                callback=delivery_callback,
            )
            producer.poll(0)

            print(f"  card={txn['card_id']:<7} type={txn['transaction_type']:<24} "
                  f"amount=${txn['amount']:<9} country={txn['country']} "
                  f"device={txn['device_id']}")

            time.sleep(2)

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        remaining = producer.flush(timeout=5)
        if remaining > 0:
            print(f"[WARN] {remaining} message(s) were not delivered")
        print("Producer closed.")


if __name__ == "__main__":
    run()
