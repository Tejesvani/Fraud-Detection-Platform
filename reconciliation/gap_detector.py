"""
Gap Detector — pinpoints exactly which events are in Kafka but missing from PostgreSQL.

Consumes the full Kafka log for a topic, compares event IDs against PostgreSQL,
and prints any missing events with context.

Usage:
    # Last 24 hours (default)
    python reconciliation/gap_detector.py --topic transactions --hours 24

    # Explicit time window
    python reconciliation/gap_detector.py --topic transactions \
        --start "2026-03-09T00:00:00" --end "2026-03-09T12:00:00"
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Set

from confluent_kafka import Consumer, KafkaError, TopicPartition
from confluent_kafka.serialization import SerializationContext, MessageField

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from shared.schema_registry import get_avro_deserializer, TOPIC_SCHEMA_MAP
from reconciliation.recon_utils import (
    KAFKA_BROKER,
    TOPIC_TABLE_MAP,
    get_db_engine,
    get_postgres_ids_in_window,
)

# ── Colors ─────────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

SCHEMA_REGISTRY_ENABLED = os.environ.get("SCHEMA_REGISTRY_ENABLED", "true").lower() == "true"
GAP_DETECTOR_GROUP      = "gap-detector-group"
# Stop consuming after this many consecutive empty polls (end-of-topic signal)
_EMPTY_POLLS_LIMIT      = 10


def _get_deserializer(topic: str):
    """Return an Avro deserializer for the topic, or None if unavailable."""
    if not SCHEMA_REGISTRY_ENABLED:
        return None
    try:
        schema_name = TOPIC_SCHEMA_MAP.get(topic)
        return get_avro_deserializer(schema_name) if schema_name else None
    except Exception as exc:
        print(f"{YELLOW}[WARN] Could not load Avro deserializer: {exc} — falling back to JSON{RESET}")
        return None


def consume_kafka_events_in_window(
    topic: str,
    start: datetime,
    end: datetime,
) -> List[dict]:
    """
    Consume messages from the topic that fall within [start, end].

    Uses offsets_for_times() to seek directly to the start timestamp — this
    avoids reading the entire retention window and makes the tool fast even on
    topics with millions of messages.  Manual assign() skips the consumer group
    rebalance overhead for this one-shot CLI tool.
    """
    deserializer = _get_deserializer(topic)
    table_info   = TOPIC_TABLE_MAP[topic]
    time_field   = table_info["event_time_field"]

    consumer = Consumer({
        "bootstrap.servers":  KAFKA_BROKER,
        "group.id":           GAP_DETECTOR_GROUP,
        "auto.offset.reset":  "earliest",
        "enable.auto.commit": False,
    })

    # Discover partitions
    metadata      = consumer.list_topics(topic, timeout=10)
    partition_ids = list(metadata.topics[topic].partitions.keys())

    # Seek each partition to the first message at or after `start`
    start_ts_ms   = int(start.timestamp() * 1000)
    seek_tps      = [TopicPartition(topic, p, start_ts_ms) for p in partition_ids]
    time_offsets  = consumer.offsets_for_times(seek_tps, timeout=10)

    assigned: List[TopicPartition] = []
    for tp in time_offsets:
        if tp.offset >= 0:
            # A message exists at or after start_ts — seek to it
            assigned.append(TopicPartition(topic, tp.partition, tp.offset))
        else:
            # No message at or after start_ts in this partition — skip it by
            # assigning to the high-water mark so polls return EOF immediately
            _, high = consumer.get_watermark_offsets(
                TopicPartition(topic, tp.partition), timeout=5
            )
            assigned.append(TopicPartition(topic, tp.partition, high))

    consumer.assign(assigned)

    events_in_window: List[dict] = []
    empty_polls    = 0
    past_end_count = 0  # partitions that have gone past `end`

    while empty_polls < _EMPTY_POLLS_LIMIT:
        msg = consumer.poll(timeout=2.0)

        if msg is None:
            empty_polls += 1
            continue

        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                empty_polls += 1
            continue

        empty_polls = 0

        try:
            if deserializer is not None:
                ctx   = SerializationContext(topic, MessageField.VALUE)
                event = deserializer(msg.value(), ctx)
            else:
                event = json.loads(msg.value().decode("utf-8"))
        except Exception:
            continue

        ts_str = event.get(time_field) or event.get("timestamp")
        if not ts_str:
            continue

        try:
            ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts > end:
                # Messages in a partition are time-ordered; once we pass `end`
                # we can stop counting as useful, but keep draining for EOF
                past_end_count += 1
                if past_end_count >= len(partition_ids) * 3:
                    break  # all partitions well past end — safe to stop early
                continue
            if ts >= start:
                events_in_window.append(event)
        except (ValueError, TypeError):
            pass

    consumer.close()
    return events_in_window


def detect_gaps(topic: str, start: datetime, end: datetime) -> List[dict]:
    """
    Detect events present in Kafka but absent from PostgreSQL for a time window.
    Returns a list of missing event dicts.
    Prints a formatted report to stdout.
    """
    engine     = get_db_engine()
    table_info = TOPIC_TABLE_MAP[topic]
    id_col     = table_info["id_column"]
    id_field   = table_info["event_id_field"]

    print(f"\n{BOLD}Gap Detection — {topic}{RESET}")
    print(
        f"Time window: "
        f"{start.strftime('%Y-%m-%d %H:%M')} to {end.strftime('%Y-%m-%d %H:%M')}"
    )
    print(f"{'─' * 45}")

    print("Querying PostgreSQL...", end=" ", flush=True)
    pg_ids = get_postgres_ids_in_window(
        engine,
        table_info["table"],
        id_col,
        table_info["time_column"],
        start,
        end,
    )
    print(f"found {len(pg_ids):,} rows")

    print("Consuming from Kafka...", end=" ", flush=True)
    kafka_events = consume_kafka_events_in_window(topic, start, end)
    print(f"found {len(kafka_events):,} events")

    kafka_id_to_event = {
        str(e[id_field]): e for e in kafka_events if id_field in e
    }
    kafka_ids: Set[str] = set(kafka_id_to_event.keys())
    missing_ids = kafka_ids - pg_ids

    print()
    print(f"  Kafka events in window   : {len(kafka_ids):,}")
    print(f"  PostgreSQL rows in window: {len(pg_ids):,}")

    if not missing_ids:
        print(f"  Missing events           : {GREEN}0{RESET}")
        print(f"\n{GREEN}✓ No gaps detected.{RESET}\n")
        engine.dispose()
        return []

    print(f"  Missing events           : {RED}{len(missing_ids):,}{RESET}")
    print(f"\nMissing event details:")

    missing_events = []
    for i, mid in enumerate(sorted(missing_ids), 1):
        event      = kafka_id_to_event[mid]
        card_id    = event.get("card_id", "?")
        amount     = event.get("amount")
        ts_val     = (
            event.get("timestamp")
            or event.get("evaluated_at")
            or event.get("created_at", "?")
        )
        amount_str = f"${amount:,.2f}" if amount is not None else "N/A"
        print(
            f"  {i:>3}. {id_col}={mid}  "
            f"card_id={card_id}  amount={amount_str}  timestamp={ts_val}"
        )
        missing_events.append(event)

    print()
    engine.dispose()
    return missing_events


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect events in Kafka that are missing from PostgreSQL."
    )
    parser.add_argument(
        "--topic",
        required=True,
        choices=list(TOPIC_TABLE_MAP.keys()),
        help="Kafka topic to inspect",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Time window in hours relative to now (default: 24)",
    )
    parser.add_argument("--start", help="Start of time window (ISO-8601)")
    parser.add_argument("--end",   help="End of time window (ISO-8601)")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if args.start and args.end:
        start_dt = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
        end_dt   = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    else:
        end_dt   = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(hours=args.hours)

    detect_gaps(args.topic, start_dt, end_dt)