"""Unit tests for data_quality/contracts/risk_score_contract.py"""

import copy
import uuid

import pytest

from data_quality.contracts.risk_score_contract import validate_risk_score


def rules(errors):
    return {e["rule"] for e in errors}


# ── Valid event passes all rules ───────────────────────────────────────────────

def test_valid_risk_score_passes(valid_risk_score):
    assert validate_risk_score(valid_risk_score) == []


# ── risk_event_id rules ───────────────────────────────────────────────────────

def test_risk_event_id_present_missing(valid_risk_score):
    e = copy.copy(valid_risk_score)
    del e["risk_event_id"]
    assert "risk_event_id_present" in rules(validate_risk_score(e))


def test_risk_event_id_uuid_invalid(valid_risk_score):
    e = {**valid_risk_score, "risk_event_id": "not-a-uuid"}
    assert "risk_event_id_uuid" in rules(validate_risk_score(e))


# ── transaction_event_id rules ────────────────────────────────────────────────

def test_transaction_event_id_present_missing(valid_risk_score):
    e = copy.copy(valid_risk_score)
    del e["transaction_event_id"]
    assert "transaction_event_id_present" in rules(validate_risk_score(e))


def test_transaction_event_id_uuid_invalid(valid_risk_score):
    e = {**valid_risk_score, "transaction_event_id": "bad-id"}
    assert "transaction_event_id_uuid" in rules(validate_risk_score(e))


# ── card_id rules ─────────────────────────────────────────────────────────────

def test_card_id_present_missing(valid_risk_score):
    e = copy.copy(valid_risk_score)
    del e["card_id"]
    assert "card_id_present" in rules(validate_risk_score(e))


def test_card_id_empty(valid_risk_score):
    e = {**valid_risk_score, "card_id": ""}
    assert "card_id_present" in rules(validate_risk_score(e))


# ── risk_score rules ──────────────────────────────────────────────────────────

def test_risk_score_present_missing(valid_risk_score):
    e = copy.copy(valid_risk_score)
    del e["risk_score"]
    assert "risk_score_present" in rules(validate_risk_score(e))


def test_risk_score_present_string(valid_risk_score):
    e = {**valid_risk_score, "risk_score": "0.5"}
    assert "risk_score_present" in rules(validate_risk_score(e))


def test_risk_score_range_below(valid_risk_score):
    e = {**valid_risk_score, "risk_score": -0.1}
    assert "risk_score_range" in rules(validate_risk_score(e))


def test_risk_score_range_above(valid_risk_score):
    e = {**valid_risk_score, "risk_score": 1.1}
    assert "risk_score_range" in rules(validate_risk_score(e))


# ── risk_score boundary values ────────────────────────────────────────────────

def test_risk_score_boundary_zero(valid_risk_score):
    e = {**valid_risk_score, "risk_score": 0.0, "risk_label": "LOW"}
    assert "risk_score_range" not in rules(validate_risk_score(e))


def test_risk_score_boundary_one(valid_risk_score):
    e = {**valid_risk_score, "risk_score": 1.0, "risk_label": "HIGH"}
    assert "risk_score_range" not in rules(validate_risk_score(e))


def test_risk_score_boundary_0_3_is_medium(valid_risk_score):
    # 0.3 is the boundary between LOW and MEDIUM — MEDIUM is correct
    e = {**valid_risk_score, "risk_score": 0.3, "risk_label": "MEDIUM"}
    assert "risk_label_consistent" not in rules(validate_risk_score(e))


def test_risk_score_boundary_0_7_is_high(valid_risk_score):
    # 0.7 is the boundary between MEDIUM and HIGH — HIGH is correct
    e = {**valid_risk_score, "risk_score": 0.7, "risk_label": "HIGH"}
    assert "risk_label_consistent" not in rules(validate_risk_score(e))


# ── risk_label rules ──────────────────────────────────────────────────────────

def test_risk_label_invalid(valid_risk_score):
    e = {**valid_risk_score, "risk_label": "EXTREME"}
    assert "risk_label_valid" in rules(validate_risk_score(e))


# ── risk_label_consistent ─────────────────────────────────────────────────────

def test_risk_label_low_consistent(valid_risk_score):
    e = {**valid_risk_score, "risk_score": 0.1, "risk_label": "LOW"}
    assert "risk_label_consistent" not in rules(validate_risk_score(e))


def test_risk_label_medium_consistent(valid_risk_score):
    e = {**valid_risk_score, "risk_score": 0.5, "risk_label": "MEDIUM"}
    assert "risk_label_consistent" not in rules(validate_risk_score(e))


def test_risk_label_high_consistent(valid_risk_score):
    e = {**valid_risk_score, "risk_score": 0.9, "risk_label": "HIGH"}
    assert "risk_label_consistent" not in rules(validate_risk_score(e))


def test_risk_label_low_but_score_is_high(valid_risk_score):
    e = {**valid_risk_score, "risk_score": 0.9, "risk_label": "LOW"}
    assert "risk_label_consistent" in rules(validate_risk_score(e))


def test_risk_label_high_but_score_is_low(valid_risk_score):
    e = {**valid_risk_score, "risk_score": 0.1, "risk_label": "HIGH"}
    assert "risk_label_consistent" in rules(validate_risk_score(e))


def test_risk_label_medium_but_score_too_high(valid_risk_score):
    # score > 0.7 should be HIGH, not MEDIUM
    e = {**valid_risk_score, "risk_score": 0.8, "risk_label": "MEDIUM"}
    assert "risk_label_consistent" in rules(validate_risk_score(e))


# ── reasons rules ─────────────────────────────────────────────────────────────

def test_reasons_present_missing(valid_risk_score):
    e = copy.copy(valid_risk_score)
    del e["reasons"]
    assert "reasons_present" in rules(validate_risk_score(e))


def test_reasons_present_not_list(valid_risk_score):
    e = {**valid_risk_score, "reasons": "HIGH_AMOUNT"}
    assert "reasons_present" in rules(validate_risk_score(e))


def test_reasons_empty_list_is_valid(valid_risk_score):
    e = {**valid_risk_score, "reasons": []}
    assert "reasons_present" not in rules(validate_risk_score(e))


# ── evaluated_at rules ────────────────────────────────────────────────────────

def test_evaluated_at_present_missing(valid_risk_score):
    e = copy.copy(valid_risk_score)
    del e["evaluated_at"]
    assert "evaluated_at_present" in rules(validate_risk_score(e))


def test_evaluated_at_iso_invalid(valid_risk_score):
    e = {**valid_risk_score, "evaluated_at": "yesterday"}
    assert "evaluated_at_iso" in rules(validate_risk_score(e))


# ── schema_version rules ──────────────────────────────────────────────────────

def test_schema_version_present_missing(valid_risk_score):
    e = copy.copy(valid_risk_score)
    del e["schema_version"]
    assert "schema_version_present" in rules(validate_risk_score(e))


def test_schema_version_bool_rejected(valid_risk_score):
    e = {**valid_risk_score, "schema_version": True}
    assert "schema_version_present" in rules(validate_risk_score(e))
