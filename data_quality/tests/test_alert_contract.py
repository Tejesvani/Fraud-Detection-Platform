"""Unit tests for data_quality/contracts/alert_contract.py"""

import copy
import uuid

import pytest

from data_quality.contracts.alert_contract import validate_alert


def rules(errors):
    return {e["rule"] for e in errors}


# ── Valid event passes all rules ───────────────────────────────────────────────

def test_valid_alert_passes(valid_alert):
    assert validate_alert(valid_alert) == []


# ── alert_id rules ────────────────────────────────────────────────────────────

def test_alert_id_present_missing(valid_alert):
    e = copy.copy(valid_alert)
    del e["alert_id"]
    assert "alert_id_present" in rules(validate_alert(e))


def test_alert_id_present_empty(valid_alert):
    e = {**valid_alert, "alert_id": ""}
    assert "alert_id_present" in rules(validate_alert(e))


def test_alert_id_uuid_invalid(valid_alert):
    e = {**valid_alert, "alert_id": "not-a-uuid"}
    assert "alert_id_uuid" in rules(validate_alert(e))


def test_alert_id_uuid_valid(valid_alert):
    e = {**valid_alert, "alert_id": str(uuid.uuid4())}
    assert "alert_id_uuid" not in rules(validate_alert(e))


# ── risk_event_id rules ───────────────────────────────────────────────────────

def test_risk_event_id_present_missing(valid_alert):
    e = copy.copy(valid_alert)
    del e["risk_event_id"]
    assert "risk_event_id_present" in rules(validate_alert(e))


def test_risk_event_id_empty(valid_alert):
    e = {**valid_alert, "risk_event_id": ""}
    assert "risk_event_id_present" in rules(validate_alert(e))


# ── transaction_event_id rules ────────────────────────────────────────────────

def test_transaction_event_id_present_missing(valid_alert):
    e = copy.copy(valid_alert)
    del e["transaction_event_id"]
    assert "transaction_event_id_present" in rules(validate_alert(e))


# ── card_id rules ─────────────────────────────────────────────────────────────

def test_card_id_present_missing(valid_alert):
    e = copy.copy(valid_alert)
    del e["card_id"]
    assert "card_id_present" in rules(validate_alert(e))


# ── risk_score rules ──────────────────────────────────────────────────────────

def test_risk_score_present_missing(valid_alert):
    e = copy.copy(valid_alert)
    del e["risk_score"]
    assert "risk_score_present" in rules(validate_alert(e))


def test_risk_score_range_below(valid_alert):
    e = {**valid_alert, "risk_score": -0.1}
    assert "risk_score_range" in rules(validate_alert(e))


def test_risk_score_range_above(valid_alert):
    e = {**valid_alert, "risk_score": 1.5}
    assert "risk_score_range" in rules(validate_alert(e))


def test_risk_score_boundary_zero(valid_alert):
    e = {**valid_alert, "risk_score": 0.0}
    assert "risk_score_range" not in rules(validate_alert(e))


def test_risk_score_boundary_one(valid_alert):
    e = {**valid_alert, "risk_score": 1.0}
    assert "risk_score_range" not in rules(validate_alert(e))


# ── severity rules ────────────────────────────────────────────────────────────

def test_severity_invalid(valid_alert):
    e = {**valid_alert, "severity": "URGENT"}
    assert "severity_valid" in rules(validate_alert(e))


def test_severity_none(valid_alert):
    e = {**valid_alert, "severity": None}
    assert "severity_valid" in rules(validate_alert(e))


# ── action rules ──────────────────────────────────────────────────────────────

def test_action_invalid(valid_alert):
    e = {**valid_alert, "action": "DO_NOTHING"}
    assert "action_valid" in rules(validate_alert(e))


def test_action_none(valid_alert):
    e = {**valid_alert, "action": None}
    assert "action_valid" in rules(validate_alert(e))


# ── severity_action_consistent ────────────────────────────────────────────────

def test_severity_action_info_log_only(valid_alert):
    e = {**valid_alert, "severity": "INFO", "action": "LOG_ONLY"}
    assert "severity_action_consistent" not in rules(validate_alert(e))


def test_severity_action_warning_review(valid_alert):
    e = {**valid_alert, "severity": "WARNING", "action": "REVIEW_TRANSACTION"}
    assert "severity_action_consistent" not in rules(validate_alert(e))


def test_severity_action_critical_block(valid_alert):
    e = {**valid_alert, "severity": "CRITICAL", "action": "BLOCK_CARD"}
    assert "severity_action_consistent" not in rules(validate_alert(e))


def test_severity_action_info_but_block_card(valid_alert):
    # INFO should map to LOG_ONLY, not BLOCK_CARD
    e = {**valid_alert, "severity": "INFO", "action": "BLOCK_CARD"}
    assert "severity_action_consistent" in rules(validate_alert(e))


def test_severity_action_critical_but_log_only(valid_alert):
    # CRITICAL should map to BLOCK_CARD, not LOG_ONLY
    e = {**valid_alert, "severity": "CRITICAL", "action": "LOG_ONLY"}
    assert "severity_action_consistent" in rules(validate_alert(e))


# ── reasons rules ─────────────────────────────────────────────────────────────

def test_reasons_present_missing(valid_alert):
    e = copy.copy(valid_alert)
    del e["reasons"]
    assert "reasons_present" in rules(validate_alert(e))


def test_reasons_not_list(valid_alert):
    e = {**valid_alert, "reasons": "HIGH_AMOUNT"}
    assert "reasons_present" in rules(validate_alert(e))


def test_reasons_empty_list_valid(valid_alert):
    e = {**valid_alert, "reasons": []}
    assert "reasons_present" not in rules(validate_alert(e))


# ── created_at rules ──────────────────────────────────────────────────────────

def test_created_at_present_missing(valid_alert):
    e = copy.copy(valid_alert)
    del e["created_at"]
    assert "created_at_present" in rules(validate_alert(e))


def test_created_at_iso_invalid(valid_alert):
    e = {**valid_alert, "created_at": "yesterday evening"}
    assert "created_at_iso" in rules(validate_alert(e))


# ── schema_version rules ──────────────────────────────────────────────────────

def test_schema_version_present_missing(valid_alert):
    e = copy.copy(valid_alert)
    del e["schema_version"]
    assert "schema_version_present" in rules(validate_alert(e))


def test_schema_version_bool_rejected(valid_alert):
    e = {**valid_alert, "schema_version": True}
    assert "schema_version_present" in rules(validate_alert(e))


# ── Multiple simultaneous failures ────────────────────────────────────────────

def test_multiple_failures(valid_alert):
    e = {
        **valid_alert,
        "severity": "INFO",
        "action": "BLOCK_CARD",   # inconsistent with INFO
        "created_at": "bad-date", # fails created_at_iso
        "risk_score": 2.5,        # fails risk_score_range
    }
    result_rules = rules(validate_alert(e))
    assert "severity_action_consistent" in result_rules
    assert "created_at_iso" in result_rules
    assert "risk_score_range" in result_rules
