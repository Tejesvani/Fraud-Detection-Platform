"""Unit tests for data_quality/contracts/transaction_contract.py"""

import copy
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from data_quality.contracts.transaction_contract import validate_transaction


def rules(errors):
    """Return the set of failing rule names from an error list."""
    return {e["rule"] for e in errors}


# ── Valid event passes all rules ───────────────────────────────────────────────

def test_valid_transaction_passes(valid_transaction):
    assert validate_transaction(valid_transaction) == []


# ── event_id rules ────────────────────────────────────────────────────────────

def test_event_id_present_missing(valid_transaction):
    e = copy.copy(valid_transaction)
    del e["event_id"]
    assert "event_id_present" in rules(validate_transaction(e))


def test_event_id_present_empty(valid_transaction):
    e = {**valid_transaction, "event_id": ""}
    assert "event_id_present" in rules(validate_transaction(e))


def test_event_id_uuid_invalid(valid_transaction):
    e = {**valid_transaction, "event_id": "not-a-uuid"}
    assert "event_id_uuid" in rules(validate_transaction(e))


def test_event_id_uuid_valid_v4(valid_transaction):
    # Explicit v4 UUID should pass
    e = {**valid_transaction, "event_id": str(uuid.uuid4())}
    assert "event_id_uuid" not in rules(validate_transaction(e))


# ── timestamp rules ───────────────────────────────────────────────────────────

def test_timestamp_present_missing(valid_transaction):
    e = copy.copy(valid_transaction)
    del e["timestamp"]
    assert "timestamp_present" in rules(validate_transaction(e))


def test_timestamp_present_empty(valid_transaction):
    e = {**valid_transaction, "timestamp": ""}
    assert "timestamp_present" in rules(validate_transaction(e))


def test_timestamp_iso_invalid(valid_transaction):
    e = {**valid_transaction, "timestamp": "not-a-date"}
    assert "timestamp_iso" in rules(validate_transaction(e))


def test_timestamp_not_future_over_5_min(valid_transaction):
    future_ts = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    e = {**valid_transaction, "timestamp": future_ts}
    assert "timestamp_not_future" in rules(validate_transaction(e))


def test_timestamp_not_future_within_tolerance(valid_transaction):
    # 2 minutes in the future is within the 5-minute tolerance
    near_future = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    e = {**valid_transaction, "timestamp": near_future}
    assert "timestamp_not_future" not in rules(validate_transaction(e))


# ── card_id rules ─────────────────────────────────────────────────────────────

def test_card_id_present_missing(valid_transaction):
    e = copy.copy(valid_transaction)
    del e["card_id"]
    assert "card_id_present" in rules(validate_transaction(e))


def test_card_id_present_empty(valid_transaction):
    e = {**valid_transaction, "card_id": ""}
    assert "card_id_present" in rules(validate_transaction(e))


def test_card_id_format_invalid(valid_transaction):
    e = {**valid_transaction, "card_id": "abc123"}
    assert "card_id_format" in rules(validate_transaction(e))


def test_card_id_format_valid(valid_transaction):
    e = {**valid_transaction, "card_id": "card99"}
    assert "card_id_format" not in rules(validate_transaction(e))


# ── transaction_type rules ────────────────────────────────────────────────────

def test_transaction_type_invalid(valid_transaction):
    e = {**valid_transaction, "transaction_type": "wire_transfer"}
    assert "transaction_type_valid" in rules(validate_transaction(e))


def test_transaction_type_none(valid_transaction):
    e = {**valid_transaction, "transaction_type": None}
    assert "transaction_type_valid" in rules(validate_transaction(e))


def test_transaction_type_all_valid_values(valid_transaction):
    valid_types = [
        "pos_purchase", "online_purchase", "subscription",
        "high_value_retail", "atm_withdrawal", "international_purchase",
    ]
    for t in valid_types:
        e = {**valid_transaction, "transaction_type": t}
        assert "transaction_type_valid" not in rules(validate_transaction(e)), \
            f"{t} should be a valid transaction_type"


# ── merchant_category rules ───────────────────────────────────────────────────

def test_merchant_category_present_missing(valid_transaction):
    e = copy.copy(valid_transaction)
    del e["merchant_category"]
    assert "merchant_category_present" in rules(validate_transaction(e))


def test_merchant_category_present_empty(valid_transaction):
    e = {**valid_transaction, "merchant_category": ""}
    assert "merchant_category_present" in rules(validate_transaction(e))


# ── amount rules ──────────────────────────────────────────────────────────────

def test_amount_present_missing(valid_transaction):
    e = copy.copy(valid_transaction)
    del e["amount"]
    assert "amount_present" in rules(validate_transaction(e))


def test_amount_present_string(valid_transaction):
    e = {**valid_transaction, "amount": "fifty"}
    assert "amount_present" in rules(validate_transaction(e))


def test_amount_positive_zero(valid_transaction):
    e = {**valid_transaction, "amount": 0.0}
    assert "amount_positive" in rules(validate_transaction(e))


def test_amount_positive_negative(valid_transaction):
    e = {**valid_transaction, "amount": -5.0}
    assert "amount_positive" in rules(validate_transaction(e))


def test_amount_max_at_limit(valid_transaction):
    e = {**valid_transaction, "amount": 100000.0}
    assert "amount_max" in rules(validate_transaction(e))


def test_amount_max_below_limit(valid_transaction):
    e = {**valid_transaction, "amount": 99999.99}
    assert "amount_max" not in rules(validate_transaction(e))


# ── country rules ─────────────────────────────────────────────────────────────

def test_country_present_missing(valid_transaction):
    e = copy.copy(valid_transaction)
    del e["country"]
    assert "country_present" in rules(validate_transaction(e))


def test_country_present_empty(valid_transaction):
    e = {**valid_transaction, "country": ""}
    assert "country_present" in rules(validate_transaction(e))


def test_country_iso_invalid(valid_transaction):
    e = {**valid_transaction, "country": "ZZ"}
    assert "country_iso" in rules(validate_transaction(e))


def test_country_iso_lowercase(valid_transaction):
    e = {**valid_transaction, "country": "us"}
    assert "country_iso" in rules(validate_transaction(e))


def test_country_iso_valid(valid_transaction):
    for code in ["US", "GB", "DE", "JP"]:
        e = {**valid_transaction, "country": code}
        assert "country_iso" not in rules(validate_transaction(e)), \
            f"{code} should be a valid country code"


# ── device_id rules ───────────────────────────────────────────────────────────

def test_device_id_present_missing(valid_transaction):
    e = copy.copy(valid_transaction)
    del e["device_id"]
    assert "device_id_present" in rules(validate_transaction(e))


def test_device_id_present_empty(valid_transaction):
    e = {**valid_transaction, "device_id": ""}
    assert "device_id_present" in rules(validate_transaction(e))


# ── schema_version rules ──────────────────────────────────────────────────────

def test_schema_version_present_missing(valid_transaction):
    e = copy.copy(valid_transaction)
    del e["schema_version"]
    assert "schema_version_present" in rules(validate_transaction(e))


def test_schema_version_present_string(valid_transaction):
    e = {**valid_transaction, "schema_version": "1"}
    assert "schema_version_present" in rules(validate_transaction(e))


def test_schema_version_bool_rejected(valid_transaction):
    # bool is a subclass of int in Python — we explicitly reject it
    e = {**valid_transaction, "schema_version": True}
    assert "schema_version_present" in rules(validate_transaction(e))


# ── Multiple simultaneous failures ────────────────────────────────────────────

def test_multiple_failures(valid_transaction):
    e = {
        **valid_transaction,
        "amount": -5.0,        # fails amount_positive
        "country": "ZZ",       # fails country_iso
        "schema_version": "x", # fails schema_version_present
    }
    result_rules = rules(validate_transaction(e))
    assert "amount_positive" in result_rules
    assert "country_iso" in result_rules
    assert "schema_version_present" in result_rules
    # All other rules should still pass
    assert "event_id_present" not in result_rules
    assert "card_id_present" not in result_rules
