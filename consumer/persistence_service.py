import json
import logging
import os
from urllib.parse import quote_plus

from confluent_kafka import Consumer, KafkaError
from sqlalchemy import create_engine, text

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("persistence-service")

# ── Kafka config ───────────────────────────────────────────────────────────────

KAFKA_BROKER = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

TOPIC_TRANSACTIONS = os.environ.get("KAFKA_TOPIC_TRANSACTIONS", "transactions")
TOPIC_RISK_SCORES = os.environ.get("KAFKA_TOPIC_RISK_SCORES", "risk_scores")
TOPIC_ALERTS = os.environ.get("KAFKA_TOPIC_ALERTS", "alerts")
TOPICS = [TOPIC_TRANSACTIONS, TOPIC_RISK_SCORES, TOPIC_ALERTS]

GROUP_ID = os.environ.get("KAFKA_GROUP_PERSISTENCE", "persistence-service-group")

# ── PostgreSQL config ──────────────────────────────────────────────────────────

_PG_USER = os.environ.get("POSTGRES_USER", "fraud_user")
_PG_PASS = os.environ.get("POSTGRES_PASSWORD")
_PG_HOST = os.environ.get("POSTGRES_HOST", "localhost")
_PG_PORT = os.environ.get("POSTGRES_PORT", "5432")
_PG_DB = os.environ.get("POSTGRES_DB", "fraud_detection_db")

DB_URL = f"postgresql+psycopg2://{_PG_USER}:{quote_plus(_PG_PASS)}@{_PG_HOST}:{_PG_PORT}/{_PG_DB}"

engine = create_engine(
    DB_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

# ── Insert queries (idempotent via ON CONFLICT DO NOTHING) ─────────────────────

INSERT_TRANSACTION = text("""
    INSERT INTO transactions (event_id, card_id, transaction_timestamp, amount, country, device_id, raw_event)
    VALUES (:event_id, :card_id, :transaction_timestamp, :amount, :country, :device_id, :raw_event)
    ON CONFLICT (event_id) DO NOTHING
""")

INSERT_RISK_SCORE = text("""
    INSERT INTO risk_scores (risk_event_id, transaction_event_id, card_id, risk_score, risk_label, evaluated_at, raw_event)
    VALUES (:risk_event_id, :transaction_event_id, :card_id, :risk_score, :risk_label, :evaluated_at, :raw_event)
    ON CONFLICT (risk_event_id) DO NOTHING
""")

INSERT_ALERT = text("""
    INSERT INTO alerts (alert_id, risk_event_id, transaction_event_id, card_id, severity, action, created_at, raw_event)
    VALUES (:alert_id, :risk_event_id, :transaction_event_id, :card_id, :severity, :action, :created_at, :raw_event)
    ON CONFLICT (alert_id) DO NOTHING
""")


# ── Topic-based routing ───────────────────────────────────────────────────────

def persist_transaction(event: dict, raw_json: str):
    with engine.begin() as conn:
        conn.execute(INSERT_TRANSACTION, {
            "event_id": event["event_id"],
            "card_id": event["card_id"],
            "transaction_timestamp": event["timestamp"],
            "amount": event["amount"],
            "country": event.get("country"),
            "device_id": event.get("device_id"),
            "raw_event": raw_json,
        })


def persist_risk_score(event: dict, raw_json: str):
    with engine.begin() as conn:
        conn.execute(INSERT_RISK_SCORE, {
            "risk_event_id": event["risk_event_id"],
            "transaction_event_id": event["transaction_event_id"],
            "card_id": event["card_id"],
            "risk_score": event["risk_score"],
            "risk_label": event["risk_label"],
            "evaluated_at": event["evaluated_at"],
            "raw_event": raw_json,
        })


def persist_alert(event: dict, raw_json: str):
    with engine.begin() as conn:
        conn.execute(INSERT_ALERT, {
            "alert_id": event["alert_id"],
            "risk_event_id": event["risk_event_id"],
            "transaction_event_id": event["transaction_event_id"],
            "card_id": event["card_id"],
            "severity": event["severity"],
            "action": event["action"],
            "created_at": event["created_at"],
            "raw_event": raw_json,
        })


TOPIC_HANDLERS = {
    TOPIC_TRANSACTIONS: persist_transaction,
    TOPIC_RISK_SCORES: persist_risk_score,
    TOPIC_ALERTS: persist_alert,
}


# ── Main consumer loop ────────────────────────────────────────────────────────

def run():
    consumer = Consumer({
        "bootstrap.servers": KAFKA_BROKER,
        "group.id": GROUP_ID,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    })

    consumer.subscribe(TOPICS)

    logger.info("Persistence Service started")
    logger.info("  consuming from : %s", ", ".join(TOPICS))
    logger.info("  persisting to  : PostgreSQL (fraud_detection_db)")
    logger.info("Press Ctrl+C to stop\n")

    try:
        while True:
            msg = consumer.poll(timeout=1.0)

            if msg is None:
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("Kafka error: %s", msg.error())
                continue

            topic = msg.topic()
            partition = msg.partition()
            offset = msg.offset()
            raw_json = msg.value().decode("utf-8")
            event = json.loads(raw_json)

            handler = TOPIC_HANDLERS.get(topic)
            if handler is None:
                logger.warning("No handler for topic=%s partition=%d offset=%d", topic, partition, offset)
                consumer.commit(msg)
                continue

            try:
                handler(event, raw_json)
                consumer.commit(msg)
                logger.info("Persisted  topic=%-15s partition=%d  offset=%d", topic, partition, offset)
            except Exception:
                logger.exception("DB insert failed  topic=%-15s partition=%d  offset=%d — offset NOT committed", topic, partition, offset)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        consumer.close()
        engine.dispose()
        logger.info("Persistence Service closed.")


if __name__ == "__main__":
    run()
