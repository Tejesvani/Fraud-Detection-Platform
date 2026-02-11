import json
import uuid
import time
from datetime import datetime, timezone
from typing import Optional

import streamlit as st
from confluent_kafka import Producer, Consumer, TopicPartition, KafkaError


# ── Kafka config ───────────────────────────────────────────────────────────────

KAFKA_BROKER = "localhost:9092"
TRANSACTIONS_TOPIC = "transactions"
ALERTS_TOPIC = "alerts"
ALERT_POLL_TIMEOUT_S = 30

# ── Reference data (mirrors producer) ─────────────────────────────────────────

CARD_IDS = [f"card{i}" for i in range(1, 11)]

TRANSACTION_TYPES = [
    "pos_purchase",
    "online_purchase",
    "subscription",
    "high_value_retail",
    "atm_withdrawal",
    "international_purchase",
]

TRANSACTION_TYPE_LABELS = {
    "pos_purchase": "POS Purchase",
    "online_purchase": "Online Purchase",
    "subscription": "Subscription",
    "high_value_retail": "High-Value Retail",
    "atm_withdrawal": "ATM Withdrawal",
    "international_purchase": "International Purchase",
}

MERCHANT_CATEGORIES = {
    "pos_purchase": ["grocery", "fuel", "pharmacy", "restaurant", "clothing"],
    "online_purchase": ["e-commerce", "digital_goods", "online_marketplace"],
    "subscription": ["streaming", "saas", "news", "fitness"],
    "high_value_retail": ["electronics", "jewelry", "luxury_goods", "appliances"],
    "atm_withdrawal": ["atm"],
    "international_purchase": ["travel", "duty_free", "foreign_retail", "hotel"],
}

COUNTRIES = ["US", "GB", "NG", "RU", "CN", "BR", "IN", "MX", "JP", "DE", "AE"]

DEVICE_IDS = [f"device_{i:03d}" for i in range(1, 21)]

# ── Severity styling ──────────────────────────────────────────────────────────

SEVERITY_STYLE = {
    "INFO":     {"color": "#2ecc71", "icon": "checkmark"},
    "WARNING":  {"color": "#f39c12", "icon": "warning"},
    "CRITICAL": {"color": "#e74c3c", "icon": "rotating_light"},
}


# ── Kafka helpers ──────────────────────────────────────────────────────────────

def get_producer():
    """Return a cached Kafka producer."""
    if "producer" not in st.session_state:
        st.session_state.producer = Producer({
            "bootstrap.servers": KAFKA_BROKER,
            "client.id": "ui-producer",
        })
    return st.session_state.producer


def produce_transaction(txn: dict):
    """Produce a transaction event to Kafka."""
    producer = get_producer()
    producer.produce(
        topic=TRANSACTIONS_TOPIC,
        key=txn["card_id"],
        value=json.dumps(txn),
    )
    producer.flush(timeout=5)


def create_alert_consumer() -> Consumer:
    """Create a consumer assigned to all alert partitions, seeked to the end.

    Uses assign() instead of subscribe() to skip the consumer group protocol
    (join/rebalance), which can take several seconds and cause a race condition.
    """
    consumer = Consumer({
        "bootstrap.servers": KAFKA_BROKER,
        "group.id": f"ui-alert-listener-{uuid.uuid4()}",
        "auto.offset.reset": "latest",
    })

    # Get partition metadata and assign directly
    metadata = consumer.list_topics(ALERTS_TOPIC, timeout=5)
    partitions = [
        TopicPartition(ALERTS_TOPIC, p)
        for p in metadata.topics[ALERTS_TOPIC].partitions
    ]
    consumer.assign(partitions)

    # poll() once to finalize partition assignment before seeking
    consumer.poll(timeout=1.0)

    # Seek each partition to the end so we only read new messages
    for tp in partitions:
        _, high = consumer.get_watermark_offsets(tp, timeout=5)
        consumer.seek(TopicPartition(ALERTS_TOPIC, tp.partition, high))

    return consumer


def poll_for_alert(consumer: Consumer, event_id: str, timeout_s: int = ALERT_POLL_TIMEOUT_S) -> Optional[dict]:
    """Poll the pre-assigned consumer until we find the matching alert or timeout."""
    deadline = time.time() + timeout_s

    try:
        while time.time() < deadline:
            msg = consumer.poll(timeout=1.0)

            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                continue

            alert = json.loads(msg.value().decode("utf-8"))

            if alert.get("transaction_event_id") == event_id:
                return alert
    finally:
        consumer.close()

    return None


# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Fraud Detection", page_icon="shield", layout="centered")
st.title("Fraud Detection Platform")
st.caption("Submit a card transaction and see the fraud analysis in real time.")

st.divider()

# ── Transaction form ──────────────────────────────────────────────────────────

st.subheader("Submit Transaction")

col1, col2 = st.columns(2)

with col1:
    card_id = st.selectbox("Card ID", CARD_IDS)
    txn_type = st.selectbox(
        "Transaction Type",
        TRANSACTION_TYPES,
        format_func=lambda t: TRANSACTION_TYPE_LABELS[t],
    )
    merchant_category = st.selectbox("Merchant Category", MERCHANT_CATEGORIES[txn_type])

with col2:
    amount = st.number_input("Amount (USD)", min_value=0.01, value=50.00, step=0.01)
    country = st.selectbox("Country", COUNTRIES)
    device_id = st.selectbox("Device ID", DEVICE_IDS)

st.divider()

# ── Submit & poll ──────────────────────────────────────────────────────────────

if st.button("Submit Transaction", type="primary", use_container_width=True):
    event_id = str(uuid.uuid4())

    txn = {
        "event_id": event_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "card_id": card_id,
        "transaction_type": txn_type,
        "merchant_category": merchant_category,
        "amount": amount,
        "country": country,
        "device_id": device_id,
        "source": "ui",
    }

    # Set up consumer BEFORE producing so it's ready to catch the alert
    alert_consumer = create_alert_consumer()

    produce_transaction(txn)

    with st.spinner("Processing transaction..."):
        alert = poll_for_alert(alert_consumer, event_id)

    if alert is None:
        st.warning("Timed out waiting for alert. Make sure the processor and alert service are running.")
    else:
        severity = alert["severity"]

        st.divider()

        if severity == "INFO":
            st.success("Transaction submitted successfully. No fraud signals detected.")

        elif severity == "WARNING":
            st.warning(
                "This transaction looks suspicious. "
                "It has been flagged for review."
            )

        elif severity == "CRITICAL":
            st.error(
                "This transaction looks fraudulent. "
                "The card has been blocked and the user has been alerted."
            )

        # Details
        st.subheader("Analysis Details")

        result_col1, result_col2 = st.columns(2)

        with result_col1:
            st.metric("Severity", severity)
            st.metric("Action", alert["action"])

        with result_col2:
            st.metric("Card", alert["card_id"])
            st.metric("Risk Score", alert.get("risk_score", "N/A"))

        # Reasons
        if alert["reasons"]:
            st.markdown("**Triggered Signals:**")
            for reason in alert["reasons"]:
                st.markdown(f"- `{reason}`")

        # Raw JSON
        with st.expander("Raw Alert Event"):
            st.json(alert)
