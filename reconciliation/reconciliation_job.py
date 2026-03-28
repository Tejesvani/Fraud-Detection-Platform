"""
Reconciliation Job — compares Kafka state against PostgreSQL state to detect missing events.

For each topic, queries:
  - Kafka high-water marks (total messages ever written)
  - Kafka committed offsets for the persistence consumer group (how far it consumed)
  - PostgreSQL row counts (what was actually persisted)

Writes a per-run report to stdout and persists results to the reconciliation_log table.

Usage:
    python reconciliation/reconciliation_job.py
"""

import json
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from reconciliation.recon_utils import (
    TOPIC_TRANSACTIONS,
    TOPIC_RISK_SCORES,
    TOPIC_ALERTS,
    TOPIC_TABLE_MAP,
    get_db_engine,
    get_kafka_watermarks,
    get_committed_offsets,
    get_postgres_count,
)

# ── Colors ─────────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

# ── Config ─────────────────────────────────────────────────────────────────────
THRESHOLD_PCT        = float(os.environ.get("RECONCILIATION_THRESHOLD_PCT", "99.9"))
TOLERANCE_MINUTES    = int(os.environ.get("RECONCILIATION_TOLERANCE_MINUTES", "5"))
PERSISTENCE_GROUP_ID = os.environ.get("KAFKA_GROUP_PERSISTENCE", "persistence-service-group")
TOPICS               = [TOPIC_TRANSACTIONS, TOPIC_RISK_SCORES, TOPIC_ALERTS]

# ── reconciliation_log DDL ─────────────────────────────────────────────────────

_CREATE_RECON_TABLE = text("""
    CREATE TABLE IF NOT EXISTS reconciliation_log (
        id              SERIAL PRIMARY KEY,
        run_timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        topic           VARCHAR(50)     NOT NULL,
        kafka_available BIGINT          NOT NULL,
        kafka_committed BIGINT          NOT NULL,
        postgres_count  BIGINT          NOT NULL,
        delta           BIGINT          NOT NULL,
        match_rate_pct  NUMERIC(6, 3)   NOT NULL,
        status          VARCHAR(20)     NOT NULL,
        details         JSONB
    )
""")

_CREATE_IDX_TOPIC = text(
    "CREATE INDEX IF NOT EXISTS idx_reconciliation_log_topic "
    "ON reconciliation_log (topic)"
)

_CREATE_IDX_TS = text(
    "CREATE INDEX IF NOT EXISTS idx_reconciliation_log_timestamp "
    "ON reconciliation_log (run_timestamp)"
)

_INSERT_RECON_LOG = text("""
    INSERT INTO reconciliation_log
        (topic, kafka_available, kafka_committed, postgres_count, delta, match_rate_pct, status, details)
    VALUES
        (:topic, :kafka_available, :kafka_committed, :postgres_count, :delta, :match_rate_pct, :status, :details)
""")


# ── Core logic ─────────────────────────────────────────────────────────────────

def _determine_status(match_rate: float) -> tuple:
    """Return (status_label, ansi_color) based on match rate."""
    if match_rate >= THRESHOLD_PCT:
        return "OK", GREEN
    elif match_rate >= 95.0:
        return "WARNING", YELLOW
    else:
        return "CRITICAL", RED


def reconcile_topic(engine, topic: str, run_ts: datetime) -> dict:
    """Run reconciliation for a single topic. Returns a result dict."""
    table_info = TOPIC_TABLE_MAP[topic]

    # Step 1 — Kafka watermarks per partition
    watermarks = get_kafka_watermarks(topic)
    kafka_available = sum(high - low for low, high in watermarks.values())

    # Step 2 — Committed offsets (persistence group)
    committed_offsets = get_committed_offsets(topic, PERSISTENCE_GROUP_ID)
    # committed offset is absolute; subtract low watermark to get messages processed
    kafka_committed = sum(
        max(0, committed_offsets.get(p, 0) - low)
        for p, (low, _high) in watermarks.items()
    )

    # Step 3 — PostgreSQL row count
    postgres_count = get_postgres_count(engine, table_info["table"])

    # Step 4 — Derived metrics
    delta = max(0, kafka_committed - postgres_count)
    if kafka_committed > 0:
        match_rate = min(postgres_count / kafka_committed * 100, 100.0)
    else:
        match_rate = 100.0 if postgres_count == 0 else 0.0

    status, color = _determine_status(match_rate)

    details = {
        "watermarks": {
            str(p): {"low": low, "high": high}
            for p, (low, high) in watermarks.items()
        },
        "committed_offsets": {str(p): off for p, off in committed_offsets.items()},
        "threshold_pct": THRESHOLD_PCT,
        "tolerance_minutes": TOLERANCE_MINUTES,
    }

    return {
        "topic":           topic,
        "kafka_available": kafka_available,
        "kafka_committed": kafka_committed,
        "postgres_count":  postgres_count,
        "delta":           delta,
        "match_rate":      match_rate,
        "status":          status,
        "color":           color,
        "details":         details,
    }


# ── Report printing ────────────────────────────────────────────────────────────

_W = 67  # report width


def print_report(results: list, run_ts: datetime):
    print(f"\n{'═' * _W}")
    print(f"  {BOLD}RECONCILIATION REPORT — {run_ts.strftime('%Y-%m-%dT%H:%M:%SZ')}{RESET}")
    print(f"{'═' * _W}")

    for r in results:
        color = r["color"]
        icon  = "✓" if r["status"] == "OK" else ("⚠" if r["status"] == "WARNING" else "✗")

        print(f"\n  Topic: {r['topic']}")
        print(f"  {'─' * (_W - 4)}")
        print(f"  {'Kafka available messages':<34}: {r['kafka_available']:>12,}")
        print(f"  {'Kafka committed (persist)':<34}: {r['kafka_committed']:>12,}")
        print(f"  {'PostgreSQL row count':<34}: {r['postgres_count']:>12,}")
        print(f"  {'Delta':<34}: {r['delta']:>12,}")
        print(f"  {'Match rate':<34}: {r['match_rate']:>11.2f}%")

        status_str = f"{icon} {r['status']}"
        if r["status"] == "WARNING":
            status_str += f" (below {THRESHOLD_PCT}% threshold)"
        elif r["status"] == "CRITICAL":
            status_str += " (large gap detected)"
        print(f"  {'Status':<34}: {color}{status_str}{RESET}")

    print()

    ok_count  = sum(1 for r in results if r["status"] == "OK")
    gap_count = len(results) - ok_count

    total_committed = sum(r["kafka_committed"] for r in results)
    total_pg        = sum(r["postgres_count"]  for r in results)
    overall_rate    = (
        min(total_pg / total_committed * 100, 100.0) if total_committed > 0 else 100.0
    )

    summary_color = GREEN if gap_count == 0 else (YELLOW if overall_rate >= THRESHOLD_PCT else RED)

    print(f"{'═' * _W}")
    print(
        f"  {BOLD}SUMMARY: {ok_count}/{len(results)} topics OK, "
        f"{gap_count} topic(s) with gaps{RESET}"
    )
    print(f"  Overall match rate: {summary_color}{overall_rate:.2f}%{RESET}")
    print(f"{'═' * _W}\n")


# ── Entry point ────────────────────────────────────────────────────────────────

def run():
    engine = get_db_engine()

    # Ensure reconciliation_log exists (works on both fresh and existing databases)
    with engine.begin() as conn:
        conn.execute(_CREATE_RECON_TABLE)
        conn.execute(_CREATE_IDX_TOPIC)
        conn.execute(_CREATE_IDX_TS)

    run_ts  = datetime.now(timezone.utc)
    results = []

    for topic in TOPICS:
        try:
            result = reconcile_topic(engine, topic, run_ts)
            results.append(result)
        except Exception as exc:
            print(f"{RED}[ERROR] Failed to reconcile topic={topic}: {exc}{RESET}")

    print_report(results, run_ts)

    # Persist results to reconciliation_log
    with engine.begin() as conn:
        for r in results:
            conn.execute(_INSERT_RECON_LOG, {
                "topic":           r["topic"],
                "kafka_available": r["kafka_available"],
                "kafka_committed": r["kafka_committed"],
                "postgres_count":  r["postgres_count"],
                "delta":           r["delta"],
                "match_rate_pct":  round(r["match_rate"], 3),
                "status":          r["status"],
                "details":         json.dumps(r["details"]),
            })

    engine.dispose()


if __name__ == "__main__":
    run()
