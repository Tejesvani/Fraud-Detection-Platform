"""
Risk score event data contract.

Pure validation — no I/O, no Kafka, no side effects.
Returns [] if valid, or a list of {"rule": ..., "message": ...} dicts if not.
"""

import uuid
from datetime import datetime

# ── Constants ─────────────────────────────────────────────────────────────────

VALID_RISK_LABELS = {"LOW", "MEDIUM", "HIGH"}

# risk_label must be consistent with risk_score range:
#   LOW    → score < 0.3
#   MEDIUM → 0.3 <= score <= 0.7
#   HIGH   → score > 0.7
_LABEL_RANGES = {
    "LOW":    (0.0, 0.3),    # [0.0, 0.3)
    "MEDIUM": (0.3, 0.7),    # [0.3, 0.7]
    "HIGH":   (0.7, 1.0),    # (0.7, 1.0]
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

def validate_risk_score(event: dict) -> list:
    """
    Validate a risk score event dict against the platform's data contract.

    Returns an empty list if all rules pass, otherwise a list of dicts:
        [{"rule": "<rule_name>", "message": "<human-readable error>"}, ...]
    """
    errors = []

    # ── risk_event_id ─────────────────────────────────────────────────────────
    risk_event_id = event.get("risk_event_id")
    if not isinstance(risk_event_id, str) or not risk_event_id:
        errors.append({
            "rule": "risk_event_id_present",
            "message": "risk_event_id is missing or empty",
        })
    else:
        if not _is_valid_uuid(risk_event_id):
            errors.append({
                "rule": "risk_event_id_uuid",
                "message": f"risk_event_id '{risk_event_id}' is not a valid UUID",
            })

    # ── transaction_event_id ──────────────────────────────────────────────────
    txn_event_id = event.get("transaction_event_id")
    if not isinstance(txn_event_id, str) or not txn_event_id:
        errors.append({
            "rule": "transaction_event_id_present",
            "message": "transaction_event_id is missing or empty",
        })
    else:
        if not _is_valid_uuid(txn_event_id):
            errors.append({
                "rule": "transaction_event_id_uuid",
                "message": f"transaction_event_id '{txn_event_id}' is not a valid UUID",
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
    score_valid = False
    if not isinstance(risk_score, (int, float)) or isinstance(risk_score, bool):
        errors.append({
            "rule": "risk_score_present",
            "message": "risk_score is missing or not a number",
        })
    else:
        score_valid = True
        if not (0.0 <= risk_score <= 1.0):
            errors.append({
                "rule": "risk_score_range",
                "message": f"risk_score {risk_score} is outside valid range [0.0, 1.0]",
            })
            score_valid = False

    # ── risk_label ────────────────────────────────────────────────────────────
    risk_label = event.get("risk_label")
    label_valid = False
    if risk_label not in VALID_RISK_LABELS:
        errors.append({
            "rule": "risk_label_valid",
            "message": f"risk_label '{risk_label}' is not valid",
        })
    else:
        label_valid = True

    # ── risk_label_consistent ─────────────────────────────────────────────────
    if score_valid and label_valid:
        lo, hi = _LABEL_RANGES[risk_label]
        if risk_label == "LOW":
            consistent = risk_score < 0.3
        elif risk_label == "MEDIUM":
            consistent = 0.3 <= risk_score < 0.7
        else:  # HIGH
            consistent = risk_score >= 0.7
        if not consistent:
            errors.append({
                "rule": "risk_label_consistent",
                "message": (
                    f"risk_label '{risk_label}' is inconsistent with "
                    f"risk_score {risk_score}"
                ),
            })

    # ── reasons ───────────────────────────────────────────────────────────────
    reasons = event.get("reasons")
    if not isinstance(reasons, list):
        errors.append({
            "rule": "reasons_present",
            "message": "reasons is missing or not a list",
        })

    # ── evaluated_at ──────────────────────────────────────────────────────────
    evaluated_at = event.get("evaluated_at")
    if not isinstance(evaluated_at, str) or not evaluated_at:
        errors.append({
            "rule": "evaluated_at_present",
            "message": "evaluated_at is missing or empty",
        })
    else:
        if not _is_iso8601(evaluated_at):
            errors.append({
                "rule": "evaluated_at_iso",
                "message": f"evaluated_at '{evaluated_at}' is not valid ISO-8601",
            })

    # ── schema_version ────────────────────────────────────────────────────────
    schema_version = event.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        errors.append({
            "rule": "schema_version_present",
            "message": "schema_version is missing or not an integer",
        })

    return errors
