"""
Transaction event data contract.

Pure validation — no I/O, no Kafka, no side effects.
Returns [] if valid, or a list of {"rule": ..., "message": ...} dicts if not.
"""

import re
import uuid
from datetime import datetime, timezone

# ── Constants ─────────────────────────────────────────────────────────────────

VALID_TRANSACTION_TYPES = {
    "pos_purchase",
    "online_purchase",
    "subscription",
    "high_value_retail",
    "atm_withdrawal",
    "international_purchase",
}

# ISO 3166-1 alpha-2 codes supported by this platform.
# Extend as needed; intent is to catch obviously invalid values.
VALID_COUNTRIES = {
    "US", "GB", "NG", "RU", "CN", "BR", "IN", "MX", "JP", "DE",
    "AE", "FR", "CA", "AU", "KR", "IT", "ES", "NL", "SE", "CH",
    "SG", "HK", "ZA",
}

_CARD_ID_RE = re.compile(r"^card[0-9]+$")

# Transactions must not be more than 5 minutes in the future.
_FUTURE_TOLERANCE_SECONDS = 300


# ── Validator ─────────────────────────────────────────────────────────────────

def validate_transaction(event: dict) -> list:
    """
    Validate a transaction event dict against the platform's data contract.

    Returns an empty list if all rules pass, otherwise a list of dicts:
        [{"rule": "<rule_name>", "message": "<human-readable error>"}, ...]

    Every rule is checked independently — all failures are returned, not just
    the first one.
    """
    errors = []

    # ── event_id ──────────────────────────────────────────────────────────────
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        errors.append({
            "rule": "event_id_present",
            "message": "event_id is missing or empty",
        })
    else:
        try:
            val = uuid.UUID(event_id)
            if val.version != 4:
                raise ValueError("not v4")
        except (ValueError, AttributeError):
            errors.append({
                "rule": "event_id_uuid",
                "message": f"event_id '{event_id}' is not a valid UUID",
            })

    # ── timestamp ─────────────────────────────────────────────────────────────
    ts_raw = event.get("timestamp")
    if not isinstance(ts_raw, str) or not ts_raw:
        errors.append({
            "rule": "timestamp_present",
            "message": "timestamp is missing or empty",
        })
    else:
        try:
            ts = datetime.fromisoformat(ts_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            delta = (ts - datetime.now(timezone.utc)).total_seconds()
            if delta > _FUTURE_TOLERANCE_SECONDS:
                errors.append({
                    "rule": "timestamp_not_future",
                    "message": f"timestamp is {int(delta)} seconds in the future",
                })
        except ValueError:
            errors.append({
                "rule": "timestamp_iso",
                "message": f"timestamp '{ts_raw}' is not valid ISO-8601",
            })

    # ── card_id ───────────────────────────────────────────────────────────────
    card_id = event.get("card_id")
    if not isinstance(card_id, str) or not card_id:
        errors.append({
            "rule": "card_id_present",
            "message": "card_id is missing or empty",
        })
    else:
        if not _CARD_ID_RE.match(card_id):
            errors.append({
                "rule": "card_id_format",
                "message": f"card_id '{card_id}' does not match expected format card[N]",
            })

    # ── transaction_type ──────────────────────────────────────────────────────
    txn_type = event.get("transaction_type")
    if txn_type not in VALID_TRANSACTION_TYPES:
        errors.append({
            "rule": "transaction_type_valid",
            "message": f"transaction_type '{txn_type}' is not a valid enum value",
        })

    # ── merchant_category ─────────────────────────────────────────────────────
    merchant_category = event.get("merchant_category")
    if not isinstance(merchant_category, str) or not merchant_category:
        errors.append({
            "rule": "merchant_category_present",
            "message": "merchant_category is missing or empty",
        })

    # ── amount ────────────────────────────────────────────────────────────────
    amount = event.get("amount")
    if not isinstance(amount, (int, float)) or isinstance(amount, bool):
        errors.append({
            "rule": "amount_present",
            "message": "amount is missing or not a number",
        })
    else:
        if amount <= 0:
            errors.append({
                "rule": "amount_positive",
                "message": f"amount must be > 0, got {amount}",
            })
        if amount >= 100000:
            errors.append({
                "rule": "amount_max",
                "message": f"amount {amount} exceeds maximum allowed (100000)",
            })

    # ── country ───────────────────────────────────────────────────────────────
    country = event.get("country")
    if not isinstance(country, str) or not country:
        errors.append({
            "rule": "country_present",
            "message": "country is missing or empty",
        })
    else:
        if country not in VALID_COUNTRIES:
            errors.append({
                "rule": "country_iso",
                "message": f"country '{country}' is not a valid ISO-3166 alpha-2 code",
            })

    # ── device_id ─────────────────────────────────────────────────────────────
    device_id = event.get("device_id")
    if not isinstance(device_id, str) or not device_id:
        errors.append({
            "rule": "device_id_present",
            "message": "device_id is missing or empty",
        })

    # ── schema_version ────────────────────────────────────────────────────────
    schema_version = event.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        errors.append({
            "rule": "schema_version_present",
            "message": "schema_version is missing or not an integer",
        })

    return errors
