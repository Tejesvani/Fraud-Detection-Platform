"""
Alert event data contract.

Pure validation — no I/O, no Kafka, no side effects.
Returns [] if valid, or a list of {"rule": ..., "message": ...} dicts if not.
"""

import uuid
from datetime import datetime

# ── Constants ─────────────────────────────────────────────────────────────────

VALID_SEVERITIES = {"INFO", "WARNING", "CRITICAL"}
VALID_ACTIONS = {"LOG_ONLY", "REVIEW_TRANSACTION", "BLOCK_CARD"}

# severity must be paired with the correct action
SEVERITY_ACTION_MAP = {
    "INFO":     "LOG_ONLY",
    "WARNING":  "REVIEW_TRANSACTION",
    "CRITICAL": "BLOCK_CARD",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def _is_iso8601(value: str) -> bool:
    try:
        datetime.fromisoformat(value)
        return True
    except ValueError:
        return False


# ── Validator ─────────────────────────────────────────────────────────────────

def validate_alert(event: dict) -> list:
    """
    Validate an alert event dict against the platform's data contract.

    Returns an empty list if all rules pass, otherwise a list of dicts:
        [{"rule": "<rule_name>", "message": "<human-readable error>"}, ...]
    """
    errors = []

    # ── alert_id ──────────────────────────────────────────────────────────────
    alert_id = event.get("alert_id")
    if not isinstance(alert_id, str) or not alert_id:
        errors.append({
            "rule": "alert_id_present",
            "message": "alert_id is missing or empty",
        })
    else:
        if not _is_valid_uuid(alert_id):
            errors.append({
                "rule": "alert_id_uuid",
                "message": f"alert_id '{alert_id}' is not a valid UUID",
            })

    # ── risk_event_id ─────────────────────────────────────────────────────────
    risk_event_id = event.get("risk_event_id")
    if not isinstance(risk_event_id, str) or not risk_event_id:
        errors.append({
            "rule": "risk_event_id_present",
            "message": "risk_event_id is missing or empty",
        })

    # ── transaction_event_id ──────────────────────────────────────────────────
    txn_event_id = event.get("transaction_event_id")
    if not isinstance(txn_event_id, str) or not txn_event_id:
        errors.append({
            "rule": "transaction_event_id_present",
            "message": "transaction_event_id is missing or empty",
        })

    # ── card_id ───────────────────────────────────────────────────────────────
    card_id = event.get("card_id")
    if not isinstance(card_id, str) or not card_id:
        errors.append({
            "rule": "card_id_present",
            "message": "card_id is missing or empty",
        })

    # ── risk_score ────────────────────────────────────────────────────────────
    risk_score = event.get("risk_score")
    if not isinstance(risk_score, (int, float)) or isinstance(risk_score, bool):
        errors.append({
            "rule": "risk_score_present",
            "message": "risk_score is missing or not a number",
        })
    else:
        if not (0.0 <= risk_score <= 1.0):
            errors.append({
                "rule": "risk_score_range",
                "message": f"risk_score {risk_score} is outside valid range [0.0, 1.0]",
            })

    # ── severity ──────────────────────────────────────────────────────────────
    severity = event.get("severity")
    severity_valid = False
    if severity not in VALID_SEVERITIES:
        errors.append({
            "rule": "severity_valid",
            "message": f"severity '{severity}' is not valid",
        })
    else:
        severity_valid = True

    # ── action ────────────────────────────────────────────────────────────────
    action = event.get("action")
    action_valid = False
    if action not in VALID_ACTIONS:
        errors.append({
            "rule": "action_valid",
            "message": f"action '{action}' is not valid",
        })
    else:
        action_valid = True

    # ── severity_action_consistent ────────────────────────────────────────────
    if severity_valid and action_valid:
        expected_action = SEVERITY_ACTION_MAP[severity]
        if action != expected_action:
            errors.append({
                "rule": "severity_action_consistent",
                "message": (
                    f"severity '{severity}' is inconsistent with action '{action}'"
                ),
            })

    # ── reasons ───────────────────────────────────────────────────────────────
    reasons = event.get("reasons")
    if not isinstance(reasons, list):
        errors.append({
            "rule": "reasons_present",
            "message": "reasons is missing or not a list",
        })

    # ── created_at ────────────────────────────────────────────────────────────
    created_at = event.get("created_at")
    if not isinstance(created_at, str) or not created_at:
        errors.append({
            "rule": "created_at_present",
            "message": "created_at is missing or empty",
        })
    else:
        if not _is_iso8601(created_at):
            errors.append({
                "rule": "created_at_iso",
                "message": f"created_at '{created_at}' is not valid ISO-8601",
            })

    # ── schema_version ────────────────────────────────────────────────────────
    schema_version = event.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        errors.append({
            "rule": "schema_version_present",
            "message": "schema_version is missing or not an integer",
        })

    return errors
