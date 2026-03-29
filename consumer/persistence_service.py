import json
import logging
import os
import sys
import time
from urllib.parse import quote_plus

from confluent_kafka import Consumer, KafkaError
from confluent_kafka.serialization import SerializationContext, MessageField
from sqlalchemy import create_engine, text

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from shared.schema_registry import get_avro_deserializer, TOPIC_SCHEMA_MAP
from shared.observability import setup_observability, get_logger

# ── Prometheus metrics ─────────────────────────────────────────────────────────

from prometheus_client import Counter, Histogram, Gauge

EVENTS_PERSISTED = Counter(
    "fraud_events_persisted_total",
    "Total events persisted to PostgreSQL",
    ["topic"],
)
PERSIST_ERRORS = Counter(
    "fraud_persist_errors_total",
    "Total persistence errors",
    ["topic"],
)
DB_WRITE_LATENCY = Histogram(
    "fraud_db_write_latency_seconds",
    "Time to write an event to PostgreSQL",
    ["topic"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)
CONSUMER_LAG = Gauge(
    "fraud_persistence_service_consumer_lag",
    "Consumer lag for persistence service",
)

# ── Logging (stdlib fallback — structlog configured in run()) ──────────────────

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

# Feature flag
SCHEMA_REGISTRY_ENABLED = os.environ.get("SCHEMA_REGISTRY_ENABLED", "true").lower() == "true"

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
    setup_observability(service_name="persistence-service", metrics_port=8003)
    slogger = get_logger()

    consumer = Consumer({
        "bootstrap.servers": KAFKA_BROKER,
        "group.id": GROUP_ID,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    })

    # Initialize per-topic Avro deserializers
    topic_deserializers: dict = {}
    if SCHEMA_REGISTRY_ENABLED:
        try:
            for topic_name, schema_name in TOPIC_SCHEMA_MAP.items():
                topic_deserializers[topic_name] = get_avro_deserializer(schema_name)
            logger.info("[Schema Registry] Avro deserialization ENABLED for %d topics", len(topic_deserializers))
        except Exception as e:
            logger.warning("[Schema Registry] Could not connect — falling back to JSON: %s", e)
            topic_deserializers = {}
    else:
        logger.info("[Schema Registry] Avro deserialization DISABLED")

    consumer.subscribe(TOPICS)

    logger.info("Persistence Service started")
    logger.info("  consuming from : %s", ", ".join(TOPICS))
    logger.info("  persisting to  : PostgreSQL (fraud_detection_db)")
    logger.info("Press Ctrl+C to stop\n")

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
                logger.error("Kafka error: %s", msg.error())
                continue

            topic = msg.topic()
            partition = msg.partition()
            offset = msg.offset()

            # Deserialize: Avro or JSON
            deserializer = topic_deserializers.get(topic)
            try:
                if deserializer is not None:
                    ctx = SerializationContext(topic, MessageField.VALUE)
                    event = deserializer(msg.value(), ctx)
                    # For raw_event column, convert back to JSON string
                    raw_json = json.dumps(event)
                else:
                    raw_json = msg.value().decode("utf-8")
                    event = json.loads(raw_json)
            except Exception as exc:
                logger.warning(
                    "Deserialization failed topic=%-15s partition=%d offset=%d: %s — skipping",
                    topic, partition, offset, exc,
                )
                consumer.commit(msg)
                continue

            handler = TOPIC_HANDLERS.get(topic)
            if handler is None:
                logger.warning("No handler for topic=%s partition=%d offset=%d", topic, partition, offset)
                consumer.commit(msg)
                continue

            try:
                with DB_WRITE_LATENCY.labels(topic=topic).time():
                    handler(event, raw_json)
                consumer.commit(msg)
                EVENTS_PERSISTED.labels(topic=topic).inc()
                logger.info("Persisted  topic=%-15s partition=%d  offset=%d", topic, partition, offset)
                event_id = (event.get("event_id") or event.get("risk_event_id")
                            or event.get("alert_id") or "unknown")
                slogger.info("event_persisted", topic=topic, partition=partition,
                             offset=offset, event_id=event_id)

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
            except Exception:
                PERSIST_ERRORS.labels(topic=topic).inc()
                logger.exception("DB insert failed  topic=%-15s partition=%d  offset=%d — offset NOT committed", topic, partition, offset)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        consumer.close()
        engine.dispose()
        logger.info("Persistence Service closed.")


if __name__ == "__main__":
    run()
