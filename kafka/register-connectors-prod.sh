#!/bin/bash
# Register JDBC Sink connectors for the fraud detection platform (Avro mode)
# Usage: ./register-connectors-prod.sh
set -euo pipefail

CONNECT_URL="${CONNECT_URL:-http://localhost:8084}"
CONFIG_DIR="$(cd "$(dirname "$0")/../kafka-local/connect-configs" && pwd)"

CONNECTORS=(
  "transactions-prod-sink"
  "risk-scores-prod-sink"
  "alerts-prod-sink"
)

echo "=== Kafka Connect — Register Prod Connectors ==="
echo "Connect REST API: $CONNECT_URL"
echo ""

# ── Wait for Connect to be ready ──────────────────────────────────────────────

echo "Waiting for Kafka Connect to be ready..."
until curl -sf "$CONNECT_URL/" > /dev/null 2>&1; do
  sleep 2
done
echo "Kafka Connect is ready."
echo ""

# ── Delete existing connectors if present ─────────────────────────────────────

for name in "${CONNECTORS[@]}"; do
  if curl -sf "$CONNECT_URL/connectors/$name" > /dev/null 2>&1; then
    echo "Deleting existing connector: $name"
    curl -sf -X DELETE "$CONNECT_URL/connectors/$name"
    echo ""
    sleep 1
  fi
done

# ── Register connectors ──────────────────────────────────────────────────────

for name in "${CONNECTORS[@]}"; do
  config_file="$CONFIG_DIR/$name.json"

  if [ ! -f "$config_file" ]; then
    echo "[ERROR] Config file not found: $config_file"
    exit 1
  fi

  echo "Registering connector: $name"
  response=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$CONNECT_URL/connectors" \
    -H "Content-Type: application/json" \
    -d @"$config_file")

  if [ "$response" -eq 201 ]; then
    echo "  -> Created successfully (HTTP $response)"
  else
    echo "  -> [ERROR] HTTP $response"
    curl -s -X POST "$CONNECT_URL/connectors" \
      -H "Content-Type: application/json" \
      -d @"$config_file"
    echo ""
    exit 1
  fi
  echo ""
done

# ── Validate connector status ────────────────────────────────────────────────

echo "Waiting 5s for connectors to start..."
sleep 5
echo ""

all_running=true
for name in "${CONNECTORS[@]}"; do
  status=$(curl -s "$CONNECT_URL/connectors/$name/status" | python3 -c "
import sys, json
data = json.load(sys.stdin)
state = data['connector']['state']
task_states = [t['state'] for t in data.get('tasks', [])]
print(f'{state} | tasks: {task_states}')
")
  echo "  $name: $status"

  if echo "$status" | grep -qv "RUNNING"; then
    all_running=false
  fi
done

echo ""
if $all_running; then
  echo "All connectors are RUNNING."
else
  echo "[WARN] Some connectors are NOT running. Check logs with:"
  echo "  docker logs connect-prod --tail 50"
fi
