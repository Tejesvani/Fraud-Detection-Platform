"""
DLQ Reprocessor — CLI tool for reviewing and replaying dead-letter events.

Commands
--------
inspect
    Consume all messages from the dlq topic (from beginning) and print a
    human-readable summary.  Shows a breakdown by original topic and the
    top-5 most common validation rule failures.

reprocess --topic <original_topic>
    Filter DLQ events that originated from <original_topic>, extract the
    original_event, and re-publish it to that topic using the appropriate
    Avro serializer.

Usage
-----
    python data_quality/dlq_reprocessor.py inspect
    python data_quality/dlq_reprocessor.py reprocess --topic transactions
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

from confluent_kafka import Consumer, KafkaError, Producer
from confluent_kafka.serialization import SerializationContext, MessageField

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from shared.schema_registry import get_avro_serializer, TOPIC_SCHEMA_MAP


# ── Config ────────────────────────────────────────────────────────────────────

KAFKA_BROKER   = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPIC_DLQ      = os.environ.get("KAFKA_TOPIC_DLQ", "dlq")
GROUP_INSPECT  = "dlq-inspector-group"
GROUP_REPROCESS = "dlq-reprocessor-group"

GREEN = "\033[92m"
RED   = "\033[91m"
CYAN  = "\033[96m"
BOLD  = "\033[1m"
RESET = "\033[0m"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _drain_dlq(group_id: str) -> list[dict]:
    """Consume all messages from the DLQ topic from the beginning."""
    consumer = Consumer({
        "bootstrap.servers": KAFKA_BROKER,
        "group.id": group_id,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    })
    consumer.subscribe([TOPIC_DLQ])

    messages = []
    empty_polls = 0

    print(f"Draining DLQ topic '{TOPIC_DLQ}' from the beginning...")

    try:
        while empty_polls < 5:
            msg = consumer.poll(timeout=2.0)

            if msg is None:
                empty_polls += 1
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    empty_polls += 1
                    continue
                print(f"[ERROR] {msg.error()}")
                continue

            empty_polls = 0
            try:
                envelope = json.loads(msg.value().decode("utf-8"))
                messages.append(envelope)
            except json.JSONDecodeError as exc:
                print(f"[WARN] Could not decode DLQ message: {exc}")

    finally:
        consumer.close()

    return messages


# ── inspect command ───────────────────────────────────────────────────────────

def cmd_inspect():
    messages = _drain_dlq(GROUP_INSPECT)

    if not messages:
        print("DLQ is empty — no failed events found.")
        return

    print(f"\nFound {BOLD}{len(messages)}{RESET} failed event(s) in DLQ\n")
    print("─" * 70)

    for i, envelope in enumerate(messages, 1):
        topic   = envelope.get("original_topic", "unknown")
        event   = envelope.get("original_event", {})
        errors  = envelope.get("validation_errors", [])
        failed  = envelope.get("failed_at", "unknown")
        card_id = event.get("card_id", "unknown")

        # Pick the best event identifier available
        event_id = (
            event.get("event_id")
            or event.get("risk_event_id")
            or event.get("alert_id")
            or "unknown"
        )

        print(f"{CYAN}[{i}]{RESET} topic={topic}  card={card_id}  event_id={event_id}")
        print(f"     failed_at : {failed}")
        print(f"     errors    :")
        for err in errors:
            print(f"       {RED}• [{err['rule']}]{RESET} {err['message']}")
        print()

    print("─" * 70)

    # ── Summary ───────────────────────────────────────────────────────────────
    topic_counts = Counter(m.get("original_topic", "unknown") for m in messages)
    rule_counts = Counter(
        err["rule"]
        for m in messages
        for err in m.get("validation_errors", [])
    )

    print(f"\n{BOLD}Summary{RESET}")
    print(f"  Total failed events : {len(messages)}")
    print()
    print(f"  By original topic:")
    for topic, count in sorted(topic_counts.items(), key=lambda x: -x[1]):
        print(f"    {topic:<20} {count}")
    print()
    print(f"  Top 5 validation rule failures:")
    for rule, count in rule_counts.most_common(5):
        print(f"    {rule:<40} {count}")


# ── reprocess command ─────────────────────────────────────────────────────────

def cmd_reprocess(original_topic: str):
    messages = _drain_dlq(GROUP_REPROCESS)

    matching = [m for m in messages if m.get("original_topic") == original_topic]

    if not matching:
        print(f"No DLQ events found for topic '{original_topic}'.")
        return

    print(f"\nRe-processing {len(matching)} event(s) back to '{original_topic}'...\n")

    producer = Producer({
        "bootstrap.servers": KAFKA_BROKER,
        "client.id": "dlq-reprocessor",
    })

    # Determine if we should use Avro serialization
    schema_name = TOPIC_SCHEMA_MAP.get(original_topic)
    avro_serializer = None
    schema_registry_enabled = os.environ.get("SCHEMA_REGISTRY_ENABLED", "true").lower() == "true"

    if schema_name and schema_registry_enabled:
        try:
            avro_serializer = get_avro_serializer(schema_name)
            print(f"[Schema Registry] Avro serialization ENABLED for '{schema_name}'\n")
        except Exception as e:
            print(f"[Schema Registry] Could not connect — falling back to JSON: {e}\n")

    reprocessed = 0

    for envelope in matching:
        event = envelope.get("original_event", {})
        card_id = event.get("card_id", "unknown")

        try:
            if avro_serializer is not None:
                ctx = SerializationContext(original_topic, MessageField.VALUE)
                value_bytes = avro_serializer(event, ctx)
            else:
                value_bytes = json.dumps(event).encode("utf-8")

            producer.produce(
                topic=original_topic,
                key=card_id,
                value=value_bytes,
            )
            producer.poll(0)

            event_id = (
                event.get("event_id")
                or event.get("risk_event_id")
                or event.get("alert_id")
                or "unknown"
            )
            print(f"{GREEN}[REPROCESSED]{RESET} event_id={event_id}  card={card_id}")
            reprocessed += 1

        except Exception as exc:
            print(f"{RED}[FAILED]{RESET} Could not re-publish event: {exc}")

    remaining = producer.flush(timeout=10)
    if remaining > 0:
        print(f"[WARN] {remaining} message(s) were not delivered")

    print(f"\nDone. Re-processed {reprocessed}/{len(matching)} event(s).")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="DLQ Reprocessor — inspect or replay dead-letter events",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # inspect
    subparsers.add_parser("inspect", help="Print all DLQ events with a summary")

    # reprocess
    reprocess_parser = subparsers.add_parser(
        "reprocess",
        help="Re-publish DLQ events for a given original topic",
    )
    reprocess_parser.add_argument(
        "--topic",
        required=True,
        help="Original topic to re-publish events to (e.g. transactions)",
    )

    args = parser.parse_args()

    if args.command == "inspect":
        cmd_inspect()
    elif args.command == "reprocess":
        cmd_reprocess(args.topic)


if __name__ == "__main__":
    main()
