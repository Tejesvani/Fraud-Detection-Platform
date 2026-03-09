#!/usr/bin/env python3
"""
Schema compatibility checker.

Validates that local .avsc schemas are compatible with the versions currently
registered in Schema Registry.  Used both in CI (schema-ci.yml) and as a
local pre-merge check.

Usage:
    python schemas/scripts/check_compatibility.py [--registry-url URL]

Exit codes:
    0  — All schemas are compatible (or no previous version exists).
    1  — At least one schema is incompatible.
    2  — Could not reach Schema Registry or other runtime error.
"""

import argparse
import json
import sys
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────────────────

SCHEMAS_DIR = Path(__file__).resolve().parent.parent

# Map: subject name → .avsc filename
SUBJECTS = {
    "transactions-value": "transaction_event.avsc",
    "risk_scores-value": "risk_score_event.avsc",
    "alerts-value": "alert_event.avsc",
}


def load_schema(filename: str) -> str:
    """Load an .avsc file and return its JSON string."""
    path = SCHEMAS_DIR / filename
    if not path.exists():
        print(f"ERROR: Schema file not found: {path}")
        sys.exit(2)
    return path.read_text()


def check_compatibility(registry_url: str) -> bool:
    """
    Check each local schema against Schema Registry's compatibility endpoint.
    Returns True if all schemas are compatible, False otherwise.
    """
    all_compatible = True

    for subject, filename in SUBJECTS.items():
        schema_str = load_schema(filename)

        # The Schema Registry compatibility test endpoint
        url = f"{registry_url}/compatibility/subjects/{subject}/versions/latest"

        payload = {
            "schema": schema_str,
            "schemaType": "AVRO",
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
        except requests.ConnectionError:
            print(f"ERROR: Cannot reach Schema Registry at {registry_url}")
            sys.exit(2)

        if resp.status_code == 404:
            # Subject doesn't exist yet — no compatibility to check
            print(f"  {subject:<25} — no previous version (OK, will register as v1)")
            continue

        if resp.status_code != 200:
            print(f"  {subject:<25} — unexpected response {resp.status_code}: {resp.text}")
            all_compatible = False
            continue

        body = resp.json()
        is_compatible = body.get("is_compatible", False)

        if is_compatible:
            print(f"  {subject:<25} — COMPATIBLE")
        else:
            print(f"  {subject:<25} — INCOMPATIBLE")
            all_compatible = False

    return all_compatible


def register_schemas(registry_url: str):
    """Register all local schemas (used to seed the registry in CI)."""
    for subject, filename in SUBJECTS.items():
        schema_str = load_schema(filename)

        url = f"{registry_url}/subjects/{subject}/versions"
        payload = {
            "schema": schema_str,
            "schemaType": "AVRO",
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
        except requests.ConnectionError:
            print(f"ERROR: Cannot reach Schema Registry at {registry_url}")
            sys.exit(2)

        if resp.status_code in (200, 409):  # 409 = already exists
            schema_id = resp.json().get("id", "existing")
            print(f"  Registered {subject:<25} — schema_id={schema_id}")
        else:
            print(f"  Failed to register {subject}: {resp.status_code} {resp.text}")
            sys.exit(2)


def main():
    parser = argparse.ArgumentParser(description="Check Avro schema compatibility against Schema Registry")
    parser.add_argument(
        "--registry-url",
        default="http://localhost:8081",
        help="Schema Registry URL (default: http://localhost:8081)",
    )
    parser.add_argument(
        "--register",
        action="store_true",
        help="Register schemas instead of checking compatibility (used to seed CI registry)",
    )
    args = parser.parse_args()

    print(f"Schema Registry: {args.registry_url}")
    print()

    if args.register:
        print("Registering schemas...")
        register_schemas(args.registry_url)
        print("\nAll schemas registered.")
        return

    print("Checking schema compatibility...")
    compatible = check_compatibility(args.registry_url)

    print()
    if compatible:
        print("All schemas are compatible. Safe to merge.")
        sys.exit(0)
    else:
        print("INCOMPATIBLE schemas detected. Fix before merging.")
        sys.exit(1)


if __name__ == "__main__":
    main()
