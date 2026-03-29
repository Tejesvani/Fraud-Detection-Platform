"""
Parallel Validation Service — Layer 2 (audit / monitoring layer).

Independently consumes from all three Kafka topics (transactions, risk_scores,
alerts), re-validates every event against the same data contracts used by the
producers, and routes any failures to the dead-letter queue (dlq) topic as
plain JSON.

This service does NOT gate the existing pipeline.  All upstream consumers
(risk-score-processor, alert-service, persistence-service) read from their
topics independently.  This service simply observes and audits.

Three categories of failure are caught:
  1. Deserialization errors — events that can't be parsed (e.g. plain JSON sent
     to an Avro topic, corrupted bytes, external producers bypassing the schema).
  2. Business rule violations — events that parse correctly but fail data
     contract validation (e.g. negative amount, invalid country code).
  3. Edge cases — bugs in producer-side validation, race conditions, or future
     external producers you don't control.

Periodic summary line every 60 seconds or 100 events, whichever comes first:
    [QUALITY] processed=142 valid=142 failed=0 (0.00% failure rate)
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

from confluent_kafka import Consumer, Producer, KafkaError
from confluent_kafka.serialization import SerializationContext, MessageField

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from shared.schema_registry import TOPIC_SCHEMA_MAP, get_avro_deserializer
from shared.observability import setup_observability, get_logger

from data_quality.contracts.transaction_contract import validate_transaction
from data_quality.contracts.risk_score_contract import validate_risk_score
from data_quality.contracts.alert_contract import validate_alert

# ── Prometheus metrics ─────────────────────────────────────────────────────────

from prometheus_client import Counter, Gauge

EVENTS_VALIDATED = Counter(
    "fraud_events_validated_total",
    "Total events validated",
    ["topic", "result"],  # result: 'valid' | 'failed' | 'deser_error'
)
VALIDATION_FAILURES = Counter(
    "fraud_validation_failures_total",
    "Validation failures by rule",
    ["topic", "rule"],
)
DLQ_EVENTS_PRODUCED = Counter(
    "fraud_dlq_events_produced_total",
    "Total events sent to DLQ",
    ["topic"],
)
CONSUMER_LAG = Gauge(
    "fraud_validation_service_consumer_lag",
    "Consumer lag for validation service",
)


# ── Config ────────────────────────────────────────────────────────────────────

KAFKA_BROKER = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

TOPIC_TRANSACTIONS = os.environ.get("KAFKA_TOPIC_TRANSACTIONS", "transactions")
TOPIC_RISK_SCORES  = os.environ.get("KAFKA_TOPIC_RISK_SCORES", "risk_scores")
TOPIC_ALERTS       = os.environ.get("KAFKA_TOPIC_ALERTS", "alerts")
TOPIC_DLQ          = os.environ.get("KAFKA_TOPIC_DLQ", "dlq")

TOPICS = [TOPIC_TRANSACTIONS, TOPIC_RISK_SCORES, TOPIC_ALERTS]

GROUP_ID = os.environ.get("KAFKA_GROUP_VALIDATION", "validation-service-group")

SCHEMA_REGISTRY_ENABLED = os.environ.get("SCHEMA_REGISTRY_ENABLED", "true").lower() == "true"

# Periodic summary: every 60 s or SUMMARY_BATCH events, whichever comes first
SUMMARY_INTERVAL_S = 60
SUMMARY_BATCH = 100


# ── Topic → validator mapping ─────────────────────────────────────────────────

def _get_validator(topic: str):
    return {
        TOPIC_TRANSACTIONS: validate_transaction,
        TOPIC_RISK_SCORES:  validate_risk_score,
        TOPIC_ALERTS:       validate_alert,
    }.get(topic)


# ── Console colors ────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    setup_observability(service_name="validation-service", metrics_port=8004)
    logger = get_logger()

    consumer = Consumer({
        "bootstrap.servers": KAFKA_BROKER,
        "group.id": GROUP_ID,
        "auto.offset.reset": "latest",
        "enable.auto.commit": False,
    })

    dlq_producer = Producer({
        "bootstrap.servers": KAFKA_BROKER,
        "client.id": "validation-service-dlq",
    })

    # Initialize per-topic Avro deserializers
    topic_deserializers: dict = {}
    if SCHEMA_REGISTRY_ENABLED:
        try:
            for topic_name, schema_name in TOPIC_SCHEMA_MAP.items():
                topic_deserializers[topic_name] = get_avro_deserializer(schema_name)
            print(
                f"[Schema Registry] Avro deserialization ENABLED "
                f"for {len(topic_deserializers)} topics"
            )
        except Exception as e:
            print(f"[Schema Registry] Could not connect — falling back to JSON: {e}")
            topic_deserializers = {}
    else:
        print("[Schema Registry] Avro deserialization DISABLED")

    consumer.subscribe(TOPICS)

    print("Validation Service started")
    print(f"  consuming from : {', '.join(TOPICS)}")
    print(f"  DLQ topic      : {TOPIC_DLQ}")
    print("Press Ctrl+C to stop\n")

    # ── Counters ──────────────────────────────────────────────────────────────
    total_processed = 0
    total_valid = 0
    total_failed = 0
    total_deser_errors = 0
    last_summary_time = time.time()
    _last_lag_check = 0.0
    _LAG_CHECK_INTERVAL = 10  # seconds

    def _print_summary():
        failure_rate = (total_failed / total_processed * 100) if total_processed else 0.0
        print(
            f"{CYAN}[QUALITY]{RESET} "
            f"processed={total_processed} "
            f"valid={total_valid} "
            f"failed={total_failed} "
            f"(deser_errors={total_deser_errors}) "
            f"({failure_rate:.2f}% failure rate)"
        )

    try:
        while True:
            msg = consumer.poll(timeout=1.0)

            if msg is None:
                # Check if a time-based summary is due even when idle
                if time.time() - last_summary_time >= SUMMARY_INTERVAL_S:
                    _print_summary()
                    last_summary_time = time.time()
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                print(f"[ERROR] {msg.error()}")
                continue

            topic = msg.topic()

            # ── Deserialize ───────────────────────────────────────────────────
            deserializer = topic_deserializers.get(topic)
            try:
                if deserializer is not None:
                    ctx = SerializationContext(topic, MessageField.VALUE)
                    event = deserializer(msg.value(), ctx)
                else:
                    event = json.loads(msg.value().decode("utf-8"))
            except Exception as exc:
                # Deserialization failed — route raw payload to DLQ
                raw_payload = msg.value().decode("utf-8", errors="replace")
                print(
                    f"{YELLOW}[DESER_ERROR]{RESET} "
                    f"topic={topic:<15} error={exc}"
                )

                dlq_envelope = {
                    "original_topic": topic,
                    "original_event": raw_payload,
                    "validation_errors": [
                        {
                            "rule": "deserialization",
                            "message": f"Failed to deserialize: {exc}",
                        }
                    ],
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                    "service": "validation-service",
                }
                dlq_bytes = json.dumps(dlq_envelope).encode("utf-8")
                dlq_producer.produce(
                    topic=TOPIC_DLQ,
                    key="unknown",
                    value=dlq_bytes,
                )
                dlq_producer.poll(0)

                EVENTS_VALIDATED.labels(topic=topic, result="deser_error").inc()
                DLQ_EVENTS_PRODUCED.labels(topic=topic).inc()
                logger.warning("deser_error_routed_to_dlq", topic=topic, error=str(exc))

                total_processed += 1
                total_failed += 1
                total_deser_errors += 1
                consumer.commit(msg)
                continue

            # ── Validate ──────────────────────────────────────────────────────
            validator = _get_validator(topic)
            if validator is None:
                consumer.commit(msg)
                continue

            errors = validator(event)
            total_processed += 1

            if not errors:
                # Determine a suitable event identifier for the log line
                event_id = (
                    event.get("event_id")
                    or event.get("risk_event_id")
                    or event.get("alert_id")
                    or "unknown"
                )
                print(f"{GREEN}[VALID]{RESET}    topic={topic:<15} event_id={event_id}")
                EVENTS_VALIDATED.labels(topic=topic, result="valid").inc()
                logger.info("event_valid", topic=topic, event_id=event_id)
                total_valid += 1
            else:
                # Route to DLQ
                card_id = event.get("card_id", "unknown")
                dlq_envelope = {
                    "original_topic": topic,
                    "original_event": event,
                    "validation_errors": errors,
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                    "service": "validation-service",
                }
                dlq_bytes = json.dumps(dlq_envelope).encode("utf-8")
                dlq_producer.produce(
                    topic=TOPIC_DLQ,
                    key=card_id,
                    value=dlq_bytes,
                )
                dlq_producer.poll(0)

                EVENTS_VALIDATED.labels(topic=topic, result="failed").inc()
                DLQ_EVENTS_PRODUCED.labels(topic=topic).inc()
                for err in errors:
                    VALIDATION_FAILURES.labels(topic=topic, rule=err["rule"]).inc()
                logger.warning("event_failed_validation", topic=topic,
                               card_id=card_id, errors=errors)

                print(
                    f"{RED}[FAILED]{RESET}   topic={topic:<15} "
                    f"card={card_id}  errors={errors}"
                )
                total_failed += 1

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

            # ── Periodic summary ──────────────────────────────────────────────
            now = time.time()
            if (
                total_processed % SUMMARY_BATCH == 0
                or now - last_summary_time >= SUMMARY_INTERVAL_S
            ):
                _print_summary()
                last_summary_time = now

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        remaining = dlq_producer.flush(timeout=5)
        if remaining > 0:
            print(f"[WARN] {remaining} DLQ message(s) were not delivered")
        consumer.close()
        _print_summary()
        print("Validation Service closed.")


if __name__ == "__main__":
    run()