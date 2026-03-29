"""
Shared utilities for reconciliation, gap detection, and backfill scripts.

Provides Kafka watermark/offset queries and PostgreSQL helpers used across all
three reconciliation tools.
"""

import os
from datetime import datetime
from typing import Dict, Set, Tuple
from urllib.parse import quote_plus

from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient
from sqlalchemy import create_engine, text

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Config ─────────────────────────────────────────────────────────────────────

KAFKA_BROKER = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

TOPIC_TRANSACTIONS = os.environ.get("KAFKA_TOPIC_TRANSACTIONS", "transactions")
TOPIC_RISK_SCORES  = os.environ.get("KAFKA_TOPIC_RISK_SCORES", "risk_scores")
TOPIC_ALERTS       = os.environ.get("KAFKA_TOPIC_ALERTS", "alerts")

# Maps each Kafka topic to its corresponding PostgreSQL table and key columns.
TOPIC_TABLE_MAP = {
    TOPIC_TRANSACTIONS: {
        "table":       "transactions",
        "id_column":   "event_id",
        "time_column": "transaction_timestamp",
        # field name inside the Kafka event dict
        "event_id_field":   "event_id",
        "event_time_field": "timestamp",
    },
    TOPIC_RISK_SCORES: {
        "table":       "risk_scores",
        "id_column":   "risk_event_id",
        "time_column": "evaluated_at",
        "event_id_field":   "risk_event_id",
        "event_time_field": "evaluated_at",
    },
    TOPIC_ALERTS: {
        "table":       "alerts",
        "id_column":   "alert_id",
        "time_column": "created_at",
        "event_id_field":   "alert_id",
        "event_time_field": "created_at",
    },
}

# ── PostgreSQL ─────────────────────────────────────────────────────────────────

def get_db_engine():
    """Return a SQLAlchemy engine using POSTGRES_* env vars."""
    user     = os.environ.get("POSTGRES_USER", "fraud_user")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    host     = os.environ.get("POSTGRES_HOST", "localhost")
    port     = os.environ.get("POSTGRES_PORT", "5432")
    db       = os.environ.get("POSTGRES_DB", "fraud_detection_db")
    url = f"postgresql+psycopg2://{user}:{quote_plus(password)}@{host}:{port}/{db}"
    return create_engine(url, pool_pre_ping=True)


def get_postgres_count(engine, table_name: str) -> int:
    """Return total row count for a table."""
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()


def get_postgres_ids_in_window(
    engine,
    table_name: str,
    id_column: str,
    time_column: str,
    start: datetime,
    end: datetime,
) -> Set[str]:
    """Return the set of primary key IDs within the given time window."""
    sql = text(
        f"SELECT {id_column}::text FROM {table_name} "
        f"WHERE {time_column} BETWEEN :start AND :end"
    )
    with engine.connect() as conn:
        result = conn.execute(sql, {"start": start, "end": end})
        return {str(row[0]) for row in result}


# ── Kafka ──────────────────────────────────────────────────────────────────────

def get_kafka_watermarks(topic: str) -> Dict[int, Tuple[int, int]]:
    """
    Return {partition: (low_watermark, high_watermark)} for every partition of a topic.
    Uses a short-lived consumer that doesn't join any consumer group.
    """
    admin = AdminClient({"bootstrap.servers": KAFKA_BROKER})
    metadata = admin.list_topics(topic=topic, timeout=10)
    topic_meta = metadata.topics.get(topic)
    if topic_meta is None:
        return {}

    partitions = list(topic_meta.partitions.keys())

    consumer = Consumer({
        "bootstrap.servers": KAFKA_BROKER,
        "group.id": "recon-watermark-probe",
        "enable.auto.commit": False,
    })

    watermarks: Dict[int, Tuple[int, int]] = {}
    for p in partitions:
        tp = TopicPartition(topic, p)
        low, high = consumer.get_watermark_offsets(tp, timeout=10)
        watermarks[p] = (low, high)

    consumer.close()
    return watermarks


def get_committed_offsets(topic: str, group_id: str) -> Dict[int, int]:
    """
    Return {partition: committed_offset} for the given consumer group.
    Returns 0 for partitions where no offset has been committed yet.
    """
    admin = AdminClient({"bootstrap.servers": KAFKA_BROKER})
    metadata = admin.list_topics(topic=topic, timeout=10)
    topic_meta = metadata.topics.get(topic)
    if topic_meta is None:
        return {}

    partitions = [TopicPartition(topic, p) for p in topic_meta.partitions.keys()]

    consumer = Consumer({
        "bootstrap.servers": KAFKA_BROKER,
        "group.id": group_id,
        "enable.auto.commit": False,
    })

    committed = consumer.committed(partitions, timeout=10)
    consumer.close()

    result: Dict[int, int] = {}
    for tp in committed:
        # OFFSET_INVALID (-1001) means no offset committed yet for this partition
        result[tp.partition] = tp.offset if tp.offset >= 0 else 0

    return result
