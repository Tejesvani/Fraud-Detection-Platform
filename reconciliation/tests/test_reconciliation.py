"""
Unit tests for reconciliation logic.

Tests pure business logic (match rate, delta, status, gap detection) without
requiring a running Kafka cluster or PostgreSQL instance.
"""

import pytest
from datetime import datetime, timezone, timedelta


# ── Pure helper functions (mirrors the logic in reconciliation_job.py) ─────────
# Extracted here so tests can run without importing the full modules
# (which would try to connect to infrastructure on import).

def calculate_match_rate(postgres_count: int, kafka_committed: int) -> float:
    """Percentage of committed Kafka events present in PostgreSQL."""
    if kafka_committed == 0:
        return 100.0 if postgres_count == 0 else 0.0
    rate = postgres_count / kafka_committed * 100
    return min(rate, 100.0)


def calculate_delta(kafka_committed: int, postgres_count: int) -> int:
    """Number of events committed by Kafka but missing from PostgreSQL."""
    return max(0, kafka_committed - postgres_count)


def determine_status(match_rate: float, threshold: float) -> str:
    """Classify a match rate as OK / WARNING / CRITICAL."""
    if match_rate >= threshold:
        return "OK"
    elif match_rate >= 95.0:
        return "WARNING"
    else:
        return "CRITICAL"


def is_in_tolerance_window(event_ts: datetime, tolerance_minutes: int) -> bool:
    """
    Returns True if the event timestamp falls within the tolerance window
    (i.e., it is recent enough to be excluded from reconciliation comparison).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=tolerance_minutes)
    return event_ts >= cutoff


def detect_missing_ids(kafka_ids: set, postgres_ids: set) -> set:
    """Events in Kafka but not yet in PostgreSQL."""
    return kafka_ids - postgres_ids


# ══════════════════════════════════════════════════════════════════════════════
# Match rate tests
# ══════════════════════════════════════════════════════════════════════════════

class TestMatchRateCalculation:
    def test_perfect_match(self):
        assert calculate_match_rate(1000, 1000) == 100.0

    def test_two_missing(self):
        rate = calculate_match_rate(998, 1000)
        assert abs(rate - 99.8) < 0.01

    def test_empty_topic_and_table(self):
        assert calculate_match_rate(0, 0) == 100.0

    def test_zero_committed_with_postgres_rows(self):
        # Shouldn't happen in practice, but guard against divide-by-zero
        assert calculate_match_rate(5, 0) == 0.0

    def test_match_rate_capped_at_100(self):
        # postgres_count > kafka_committed can happen briefly during startup
        assert calculate_match_rate(1001, 1000) == 100.0

    def test_large_gap(self):
        rate = calculate_match_rate(500, 1000)
        assert abs(rate - 50.0) < 0.01

    def test_single_row_match(self):
        assert calculate_match_rate(1, 1) == 100.0

    def test_single_row_missing(self):
        assert calculate_match_rate(0, 1) == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Delta calculation tests
# ══════════════════════════════════════════════════════════════════════════════

class TestDeltaCalculation:
    def test_no_delta(self):
        assert calculate_delta(1000, 1000) == 0

    def test_two_missing(self):
        assert calculate_delta(1000, 998) == 2

    def test_all_missing(self):
        assert calculate_delta(1000, 0) == 1000

    def test_no_negative_delta(self):
        # postgres_count > kafka_committed — delta must be 0, never negative
        assert calculate_delta(1000, 1001) == 0

    def test_empty_everything(self):
        assert calculate_delta(0, 0) == 0


# ══════════════════════════════════════════════════════════════════════════════
# Status determination tests
# ══════════════════════════════════════════════════════════════════════════════

class TestStatusDetermination:
    def test_ok_at_exact_threshold(self):
        assert determine_status(99.9, 99.9) == "OK"

    def test_ok_above_threshold(self):
        assert determine_status(100.0, 99.9) == "OK"

    def test_warning_just_below_threshold(self):
        assert determine_status(99.8, 99.9) == "WARNING"

    def test_warning_at_95_boundary(self):
        assert determine_status(95.0, 99.9) == "WARNING"

    def test_critical_just_below_95(self):
        assert determine_status(94.9, 99.9) == "CRITICAL"

    def test_critical_large_gap(self):
        assert determine_status(50.0, 99.9) == "CRITICAL"

    def test_critical_zero_match(self):
        assert determine_status(0.0, 99.9) == "CRITICAL"

    def test_custom_threshold_ok(self):
        assert determine_status(99.5, 99.0) == "OK"

    def test_custom_threshold_warning(self):
        assert determine_status(98.9, 99.0) == "WARNING"

    def test_perfect_score_always_ok(self):
        assert determine_status(100.0, 100.0) == "OK"


# ══════════════════════════════════════════════════════════════════════════════
# Tolerance window tests
# ══════════════════════════════════════════════════════════════════════════════

class TestToleranceWindow:
    def test_very_recent_event_is_in_window(self):
        recent = datetime.now(timezone.utc) - timedelta(seconds=30)
        assert is_in_tolerance_window(recent, tolerance_minutes=5) is True

    def test_event_just_inside_window(self):
        just_in = datetime.now(timezone.utc) - timedelta(minutes=4, seconds=59)
        assert is_in_tolerance_window(just_in, tolerance_minutes=5) is True

    def test_event_just_outside_window(self):
        just_out = datetime.now(timezone.utc) - timedelta(minutes=5, seconds=1)
        assert is_in_tolerance_window(just_out, tolerance_minutes=5) is False

    def test_old_event_excluded(self):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        assert is_in_tolerance_window(old, tolerance_minutes=5) is False

    def test_zero_tolerance(self):
        # Zero-minute tolerance: only events in the exact future are "in window"
        just_past = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert is_in_tolerance_window(just_past, tolerance_minutes=0) is False

    def test_large_tolerance(self):
        # 60-min tolerance: events from 30 min ago are still "in window"
        recent = datetime.now(timezone.utc) - timedelta(minutes=30)
        assert is_in_tolerance_window(recent, tolerance_minutes=60) is True


# ══════════════════════════════════════════════════════════════════════════════
# Gap detection logic tests
# ══════════════════════════════════════════════════════════════════════════════

class TestGapDetectionLogic:
    def test_no_gaps(self):
        kafka_ids = {"id-1", "id-2", "id-3"}
        pg_ids    = {"id-1", "id-2", "id-3"}
        assert detect_missing_ids(kafka_ids, pg_ids) == set()

    def test_two_missing(self):
        kafka_ids = {"id-1", "id-2", "id-3", "id-4"}
        pg_ids    = {"id-1", "id-3"}
        assert detect_missing_ids(kafka_ids, pg_ids) == {"id-2", "id-4"}

    def test_all_missing(self):
        kafka_ids = {"id-1", "id-2"}
        pg_ids    = set()
        assert detect_missing_ids(kafka_ids, pg_ids) == {"id-1", "id-2"}

    def test_empty_kafka(self):
        # Extra rows in PostgreSQL — not a gap from Kafka's perspective
        kafka_ids = set()
        pg_ids    = {"id-1"}
        assert detect_missing_ids(kafka_ids, pg_ids) == set()

    def test_both_empty(self):
        assert detect_missing_ids(set(), set()) == set()

    def test_single_missing(self):
        assert detect_missing_ids({"id-1"}, set()) == {"id-1"}

    def test_single_present(self):
        assert detect_missing_ids({"id-1"}, {"id-1"}) == set()


# ══════════════════════════════════════════════════════════════════════════════
# Edge case / integration scenario tests
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_brand_new_topic_no_data(self):
        """Empty topic + empty table = healthy."""
        assert calculate_match_rate(0, 0) == 100.0
        assert calculate_delta(0, 0) == 0
        assert determine_status(100.0, 99.9) == "OK"

    def test_topic_has_data_but_table_empty(self):
        """Persistence service never ran — critical gap."""
        rate = calculate_match_rate(0, 100)
        assert rate == 0.0
        assert calculate_delta(100, 0) == 100
        assert determine_status(rate, 99.9) == "CRITICAL"

    def test_typical_healthy_run(self):
        """Normal pipeline: ~2k events, 100% match."""
        kafka_committed = 1842
        postgres_count  = 1842
        rate  = calculate_match_rate(postgres_count, kafka_committed)
        delta = calculate_delta(kafka_committed, postgres_count)
        assert rate == 100.0
        assert delta == 0
        assert determine_status(rate, 99.9) == "OK"

    def test_typical_warning_run(self):
        """Pipeline lagging slightly — 2 events behind threshold."""
        kafka_committed = 1842
        postgres_count  = 1840
        rate  = calculate_match_rate(postgres_count, kafka_committed)
        delta = calculate_delta(kafka_committed, postgres_count)
        assert abs(rate - 99.89) < 0.01
        assert delta == 2
        assert determine_status(rate, 99.9) == "WARNING"

    def test_missing_ids_are_correct_subset(self):
        kafka_ids = {f"id-{i}" for i in range(100)}
        pg_ids    = {f"id-{i}" for i in range(100) if i % 10 != 0}  # every 10th missing
        missing   = detect_missing_ids(kafka_ids, pg_ids)
        assert len(missing) == 10
        assert missing == {f"id-{i}" for i in range(0, 100, 10)}
