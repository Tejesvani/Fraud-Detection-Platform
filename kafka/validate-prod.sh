#!/bin/bash
# Validation script for the fraud detection Avro pipeline
# Usage: ./validate-prod.sh
set -euo pipefail

CONNECT_URL="${CONNECT_URL:-http://localhost:8084}"
SCHEMA_REGISTRY_URL="${SCHEMA_REGISTRY_URL:-http://localhost:8081}"
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PROD_PORT:-5433}"
POSTGRES_DB="${POSTGRES_PROD_DB:-fraud_detection_prod}"
POSTGRES_USER="${POSTGRES_PROD_USER:-fraud_prod_user}"
POSTGRES_PASSWORD="${POSTGRES_PROD_PASSWORD:-fraud_prod_password}"

PASS="\033[92m[PASS]\033[0m"
FAIL="\033[91m[FAIL]\033[0m"

echo "=== Fraud Detection Platform — Pipeline Validation ==="
echo ""

# ── 1. Schema Registry ───────────────────────────────────────────────────────

echo "1. Schema Registry"
if curl -sf "$SCHEMA_REGISTRY_URL/" > /dev/null 2>&1; then
  echo -e "   $PASS Schema Registry is reachable at $SCHEMA_REGISTRY_URL"
else
  echo -e "   $FAIL Schema Registry is NOT reachable at $SCHEMA_REGISTRY_URL"
fi

subjects=$(curl -sf "$SCHEMA_REGISTRY_URL/subjects" 2>/dev/null || echo "[]")
echo "   Registered subjects: $subjects"
echo ""

# ── 2. Kafka Connect ─────────────────────────────────────────────────────────

echo "2. Kafka Connect"
if curl -sf "$CONNECT_URL/" > /dev/null 2>&1; then
  echo -e "   $PASS Kafka Connect is reachable at $CONNECT_URL"
else
  echo -e "   $FAIL Kafka Connect is NOT reachable at $CONNECT_URL"
fi

connectors=$(curl -sf "$CONNECT_URL/connectors" 2>/dev/null || echo "[]")
echo "   Registered connectors: $connectors"

for name in transactions-prod-sink risk-scores-prod-sink alerts-prod-sink; do
  if echo "$connectors" | grep -q "$name"; then
    status=$(curl -sf "$CONNECT_URL/connectors/$name/status" | python3 -c "
import sys, json
data = json.load(sys.stdin)
state = data['connector']['state']
tasks = [t['state'] for t in data.get('tasks', [])]
print(f'{state} (tasks: {tasks})')
" 2>/dev/null || echo "UNKNOWN")
    echo "   $name: $status"
  else
    echo -e "   $FAIL $name not registered"
  fi
done
echo ""

# ── 3. Kafka Topics ──────────────────────────────────────────────────────────

echo "3. Kafka Topics"
docker exec kafka-prod kafka-topics \
  --bootstrap-server localhost:9092 \
  --list 2>/dev/null | while read -r topic; do
    echo "   - $topic"
  done
echo ""

# ── 4. PostgreSQL Tables ─────────────────────────────────────────────────────

PSQL_CMD="docker exec postgres-prod psql -U $POSTGRES_USER -d $POSTGRES_DB"

echo "4. PostgreSQL — Row counts"
for table in transactions_prod risk_scores_prod alerts_prod; do
  count=$($PSQL_CMD -t -c "SELECT COUNT(*) FROM $table;" 2>/dev/null | tr -d ' ' || echo "ERROR")
  echo "   $table: $count rows"
done
echo ""

# ── 5. Sample data ───────────────────────────────────────────────────────────

echo "5. PostgreSQL — Latest rows"
for table in transactions_prod risk_scores_prod alerts_prod; do
  echo "   --- $table (last 3) ---"
  $PSQL_CMD -c "SELECT * FROM $table ORDER BY inserted_at DESC LIMIT 3;" 2>/dev/null || echo "   (query failed)"
  echo ""
done

echo "=== Validation Complete ==="
