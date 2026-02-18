"""Avro schema definitions and serializer/deserializer factories for the fraud platform."""

import json
import os
from pathlib import Path

from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer, AvroDeserializer

SCHEMA_DIR = Path(__file__).parent

SCHEMA_REGISTRY_URL = os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081")


def _load_schema(name: str) -> str:
    """Load an Avro schema file and return its JSON string."""
    return (SCHEMA_DIR / f"{name}.avsc").read_text()


def get_schema_registry_client() -> SchemaRegistryClient:
    """Create a Schema Registry client from environment config."""
    return SchemaRegistryClient({"url": SCHEMA_REGISTRY_URL})


def get_avro_serializer(schema_name: str) -> AvroSerializer:
    """Create an AvroSerializer for the given schema name (transaction, risk_score, alert)."""
    client = get_schema_registry_client()
    schema_str = _load_schema(schema_name)
    return AvroSerializer(client, schema_str)


def get_avro_deserializer(schema_name: str) -> AvroDeserializer:
    """Create an AvroDeserializer for the given schema name."""
    client = get_schema_registry_client()
    schema_str = _load_schema(schema_name)
    return AvroDeserializer(client, schema_str)
