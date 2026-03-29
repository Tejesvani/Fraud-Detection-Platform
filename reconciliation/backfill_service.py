"""
Backfill Service — replays missing Kafka events back to PostgreSQL.

Runs gap detection first to find missing IDs, then writes those events
directly to PostgreSQL using the same idempotent INSERT queries as the
persistence service.  Safe to run multiple times — ON CONFLICT DO NOTHING
means replaying an already-persisted event is harmless.

Usage:
    python reconciliation/backfill_service.py --topic transactions --hours 24
    python reconciliation/backfill_service.py --topic transactions \
        --start "2026-03-09T00:00:00" --end "2026-03-09T12:00:00"
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from reconciliation.recon_utils import TOPIC_TABLE_MAP, get_db_engine
from reconciliation.gap_detector import detect_gaps

# ── Colors ─────────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

# ── INSERT queries — identical copies from persistence_service.py ──────────────
# These must stay byte-for-byte compatible so backfill rows are indistinguishable
# from rows written by the live persistence service.

_INSERT_TRANSACTION = text("""
    INSERT INTO transactions
        (event_id, card_id, transaction_timestamp, amount, country, device_id, raw_event)
    VALUES
        (:event_id, :card_id, :transaction_timestamp, :amount, :country, :device_id, :raw_event)
    ON CONFLICT (event_id) DO NOTHING
""")

_INSERT_RISK_SCORE = text("""
    INSERT INTO risk_scores
        (risk_event_id, transaction_event_id, card_id, risk_score, risk_label, evaluated_at, raw_event)
    VALUES
        (:risk_event_id, :transaction_event_id, :card_id, :risk_score, :risk_label, :evaluated_at, :raw_event)
    ON CONFLICT (risk_event_id) DO NOTHING
""")

_INSERT_ALERT = text("""
    INSERT INTO alerts
        (alert_id, risk_event_id, transaction_event_id, card_id, severity, action, created_at, raw_event)
    VALUES
        (:alert_id, :risk_event_id, :transaction_event_id, :card_id, :severity, :action, :created_at, :raw_event)
    ON CONFLICT (alert_id) DO NOTHING
""")


# ── Per-topic replay functions ─────────────────────────────────────────────────

def _replay_transaction(conn, event: dict):
    conn.execute(_INSERT_TRANSACTION, {
        "event_id":              event["event_id"],
        "card_id":               event["card_id"],
        "transaction_timestamp": event["timestamp"],
        "amount":                event["amount"],
        "country":               event.get("country"),
        "device_id":             event.get("device_id"),
        "raw_event":             json.dumps(event),
    })


def _replay_risk_score(conn, event: dict):
    conn.execute(_INSERT_RISK_SCORE, {
        "risk_event_id":        event["risk_event_id"],
        "transaction_event_id": event["transaction_event_id"],
        "card_id":              event["card_id"],
        "risk_score":           event["risk_score"],
        "risk_label":           event["risk_label"],
        "evaluated_at":         event["evaluated_at"],
        "raw_event":            json.dumps(event),
    })


def _replay_alert(conn, event: dict):
    conn.execute(_INSERT_ALERT, {
        "alert_id":             event["alert_id"],
        "risk_event_id":        event["risk_event_id"],
        "transaction_event_id": event["transaction_event_id"],
        "card_id":              event["card_id"],
        "severity":             event["severity"],
        "action":               event["action"],
        "created_at":           event["created_at"],
        "raw_event":            json.dumps(event),
    })


_TOPIC_REPLAYERS = {
    "transactions": _replay_transaction,
    "risk_scores":  _replay_risk_score,
    "alerts":       _replay_alert,
}


# ── Main backfill logic ────────────────────────────────────────────────────────

def backfill(topic: str, start: datetime, end: datetime):
    """
    1. Run gap detection to find missing event IDs.
    2. Replay each missing event to PostgreSQL using idempotent INSERTs.
    """
    print(f"\n{BOLD}Backfill — {topic}{RESET}")
    print(f"{'─' * 30}")
    print("Running gap detection...")

    missing_events = detect_gaps(topic, start, end)

    if not missing_events:
        print(f"{GREEN}Nothing to backfill.{RESET}\n")
        return

    print(f"Found {len(missing_events)} missing event(s).\n")
    print("Replaying to PostgreSQL...")

    table_info = TOPIC_TABLE_MAP[topic]
    id_field   = table_info["event_id_field"]
    replayer   = _TOPIC_REPLAYERS[table_info["table"]]

    engine    = get_db_engine()
    replayed  = 0
    already_ok = 0
    failed    = 0

    with engine.begin() as conn:
        for event in missing_events:
            event_id = str(event.get(id_field, ""))
            card_id  = event.get("card_id", "?")

            # Check if it appeared in PostgreSQL since gap detection ran (race condition)
            existing = conn.execute(
                text(
                    f"SELECT 1 FROM {table_info['table']} "
                    f"WHERE {table_info['id_column']} = :eid"
                ),
                {"eid": event_id},
            ).fetchone()

            if existing:
                already_ok += 1
                print(
                    f"  {YELLOW}~ Already exists{RESET}  "
                    f"{id_field}={event_id}  card_id={card_id}"
                )
                continue

            try:
                replayer(conn, event)
                replayed += 1
                print(
                    f"  {GREEN}✓ Replayed{RESET}        "
                    f"{id_field}={event_id}  card_id={card_id}"
                )
            except Exception as exc:
                failed += 1
                print(
                    f"  {RED}✗ Failed{RESET}           "
                    f"{id_field}={event_id}: {exc}"
                )

    not_found = len(missing_events) - replayed - already_ok - failed

    print(f"\nBackfill complete:")
    print(f"  Replayed   : {GREEN}{replayed}{RESET}")
    print(
        f"  Not found  : {YELLOW}{not_found}{RESET}"
        f"  (may have expired from Kafka retention)"
    )
    print(f"  Already ok : {already_ok}  (resolved by race condition)")
    if failed:
        print(f"  Errors     : {RED}{failed}{RESET}")
    print()

    engine.dispose()


# ── CLI ────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay missing Kafka events back to PostgreSQL."
    )
    parser.add_argument(
        "--topic",
        required=True,
        choices=list(TOPIC_TABLE_MAP.keys()),
        help="Kafka topic to backfill",
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

    backfill(args.topic, start_dt, end_dt)
