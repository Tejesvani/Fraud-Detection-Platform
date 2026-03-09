"""
Shared test fixtures for data quality contract tests.

Each fixture returns a fully valid event dict that passes all validation rules.
Individual tests copy the fixture and mutate a single field to exercise one rule.
"""

import uuid
from datetime import datetime, timezone

import pytest


@pytest.fixture
def valid_transaction():
    return {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "card_id": "card1",
        "transaction_type": "pos_purchase",
        "merchant_category": "grocery",
        "amount": 50.0,
        "country": "US",
        "device_id": "device_001",
        "schema_version": 1,
    }


@pytest.fixture
def valid_risk_score():
    return {
        "risk_event_id": str(uuid.uuid4()),
        "transaction_event_id": str(uuid.uuid4()),
        "card_id": "card1",
        "risk_score": 0.4,
        "risk_label": "MEDIUM",
        "reasons": ["HIGH_AMOUNT"],
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
    }


@pytest.fixture
def valid_alert():
    return {
        "alert_id": str(uuid.uuid4()),
        "risk_event_id": str(uuid.uuid4()),
        "transaction_event_id": str(uuid.uuid4()),
        "card_id": "card1",
        "risk_score": 0.4,
        "severity": "WARNING",
        "action": "REVIEW_TRANSACTION",
        "reasons": ["HIGH_AMOUNT"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
    }
