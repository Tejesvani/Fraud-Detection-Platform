"""
Shared Schema Registry helpers for all fraud-detection-platform services.

Provides AvroSerializer / AvroDeserializer instances backed by Confluent Schema
Registry.  Every producer and consumer in the platform imports from here so that
schema negotiation logic lives in one place.

Usage — producer side:
    from shared.schema_registry import get_avro_serializer
    serializer = get_avro_serializer("TransactionEvent")
    producer.produce(topic, value=serializer(txn_dict, SerializationContext(...)))

Usage — consumer side:
    from shared.schema_registry import get_avro_deserializer
    deserializer = get_avro_deserializer("TransactionEvent")
    event = deserializer(msg.value(), SerializationContext(...))
"""

import json
import os
from functools import lru_cache
from pathlib import Path

from confluent_kafka.schema_registry import SchemaRegistryClient, Schema
from confluent_kafka.schema_registry.avro import AvroSerializer, AvroDeserializer

# ── Config ────────────────────────────────────────────────────────────────────

SCHEMA_REGISTRY_URL = os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081")

# Path to the schemas/ directory (relative to project root)
_SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"

# Map from logical schema name → .avsc filename
_SCHEMA_FILES = {
    "TransactionEvent": "transaction_event.avsc",
    "RiskScoreEvent": "risk_score_event.avsc",
    "AlertEvent": "alert_event.avsc",
}

# Map from Kafka topic name → logical schema name
TOPIC_SCHEMA_MAP = {
    os.environ.get("KAFKA_TOPIC_TRANSACTIONS", "transactions"): "TransactionEvent",
    os.environ.get("KAFKA_TOPIC_RISK_SCORES", "risk_scores"): "RiskScoreEvent",
    os.environ.get("KAFKA_TOPIC_ALERTS", "alerts"): "AlertEvent",
}


# ── Schema Registry client (singleton) ───────────────────────────────────────

@lru_cache(maxsize=1)
def get_schema_registry_client() -> SchemaRegistryClient:
    """Return a cached SchemaRegistryClient instance."""
    return SchemaRegistryClient({"url": SCHEMA_REGISTRY_URL})


# ── Load .avsc files ─────────────────────────────────────────────────────────

@lru_cache(maxsize=8)
def load_avro_schema_str(schema_name: str) -> str:
    """Read an .avsc file and return its JSON string."""
    filename = _SCHEMA_FILES.get(schema_name)
    if filename is None:
        raise ValueError(
            f"Unknown schema '{schema_name}'. "
            f"Valid names: {list(_SCHEMA_FILES.keys())}"
        )
    schema_path = _SCHEMAS_DIR / filename
    return schema_path.read_text()


# ── Serializer / Deserializer factories ──────────────────────────────────────

def get_avro_serializer(schema_name: str) -> AvroSerializer:
    """
    Build an AvroSerializer that registers/validates against Schema Registry.

    The serializer will:
      1. Register the schema (or validate it against an existing version).
      2. Serialize Python dicts to Avro-encoded bytes with the Confluent wire
         format (magic byte + 4-byte schema ID + Avro payload).
    """
    schema_str = load_avro_schema_str(schema_name)
    client = get_schema_registry_client()

    return AvroSerializer(
        schema_registry_client=client,
        schema_str=schema_str,
        conf={"auto.register.schemas": True},
    )


def get_avro_deserializer(schema_name: str) -> AvroDeserializer:
    """
    Build an AvroDeserializer that reads the schema ID from the wire format,
    fetches the writer schema from Schema Registry, and deserializes to a dict.
    """
    schema_str = load_avro_schema_str(schema_name)
    client = get_schema_registry_client()

    return AvroDeserializer(
        schema_registry_client=client,
        schema_str=schema_str,
    )


def get_deserializer_for_topic(topic: str) -> AvroDeserializer:
    """Convenience: get the right deserializer based on the Kafka topic name."""
    schema_name = TOPIC_SCHEMA_MAP.get(topic)
    if schema_name is None:
        raise ValueError(
            f"No schema mapped for topic '{topic}'. "
            f"Known topics: {list(TOPIC_SCHEMA_MAP.keys())}"
        )
    return get_avro_deserializer(schema_name)


# ── Schema registration helper (useful for CI and startup checks) ────────────

def register_all_schemas() -> dict[str, int]:
    """
    Register all .avsc schemas with the Schema Registry under their
    topic-value subjects.  Returns a dict of {subject: schema_id}.

    Subject naming follows the TopicNameStrategy:
        <topic>-value  (e.g. "transactions-value")
    """
    client = get_schema_registry_client()
    results = {}

    for topic, schema_name in TOPIC_SCHEMA_MAP.items():
        subject = f"{topic}-value"
        schema_str = load_avro_schema_str(schema_name)
        schema = Schema(schema_str, schema_type="AVRO")
        schema_id = client.register_schema(subject, schema)
        results[subject] = schema_id

    return results
