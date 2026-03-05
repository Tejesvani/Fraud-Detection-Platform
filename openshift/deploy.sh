#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Deploy the Fraud Detection Platform to OpenShift
#
# Prerequisites:
#   - oc CLI installed and logged in to your cluster
#   - Strimzi operator installed in the cluster
#   - quay-registry-secret created in the fraud-detection-prod namespace
#
# Usage:
#   export QUAY_NAME=your-quay-username
#   bash deploy.sh
# =============================================================================

set -euo pipefail

# ── Require QUAY_NAME ─────────────────────────────────────────────────────────
if [[ -z "${QUAY_NAME:-}" ]]; then
  echo "ERROR: QUAY_NAME environment variable is not set."
  echo ""
  echo "  export QUAY_NAME=your-quay-username"
  echo "  bash deploy.sh"
  exit 1
fi

echo "Using Quay.io username: $QUAY_NAME"

# Helper: apply a file, substituting ${QUAY_NAME} before sending to the cluster.
# Only files with image references need this; others are applied directly.
oc_apply_templated() {
  envsubst '${QUAY_NAME}' < "$1" | oc apply -f -
}

NAMESPACE="fraud-detection-prod"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

print_step() {
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  $1"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

cd "$SCRIPT_DIR"

# ── Step 1: Namespace ─────────────────────────────────────────────────────────
print_step "1/9  Creating namespace"
oc apply -f 00-namespace.yaml
echo "Namespace ready."

# ── Step 2: Secrets and ConfigMaps ───────────────────────────────────────────
print_step "2/9  Applying secrets and ConfigMaps"
oc apply -f 01-postgres-secret.yaml
oc apply -f 02-app-configmap.yaml
oc apply -f 03-postgres-init-configmap.yaml
echo "Secrets and ConfigMaps applied."

# ── Step 3: PostgreSQL ────────────────────────────────────────────────────────
print_step "3/9  Deploying PostgreSQL"
oc apply -f 04-postgres-pvc.yaml
oc apply -f 06-postgres-service.yaml
oc apply -f 05-postgres-statefulset.yaml

echo "Waiting for PostgreSQL to be ready..."
oc rollout status statefulset/postgres-prod -n "$NAMESPACE" --timeout=120s
echo "PostgreSQL ready."

# ── Step 4: Kafka Cluster (Strimzi) ──────────────────────────────────────────
print_step "4/9  Deploying Kafka cluster (Strimzi)"
oc apply -f 07-kafka-cluster.yaml

echo "Waiting for Kafka cluster to become Ready (up to 5 minutes)..."
oc wait kafka/fraud-detection-cluster \
  --for=condition=Ready \
  --timeout=300s \
  -n "$NAMESPACE"
echo "Kafka cluster ready."

# ── Step 5: Kafka Topics ──────────────────────────────────────────────────────
print_step "5/9  Creating Kafka topics"
oc apply -f 08-kafka-topics.yaml
echo "Topics applied. Strimzi topic operator will provision them shortly."

# ── Step 6: Schema Registry ───────────────────────────────────────────────────
print_step "6/9  Deploying Schema Registry"
oc apply -f 09-schema-registry-deployment.yaml
oc apply -f 10-schema-registry-service.yaml

echo "Waiting for Schema Registry to be ready..."
oc rollout status deployment/schema-registry -n "$NAMESPACE" --timeout=120s
echo "Schema Registry ready."

# ── Step 7: Kafka Connect ─────────────────────────────────────────────────────
print_step "7/9  Deploying Kafka Connect (Strimzi build + JDBC Sink)"
oc_apply_templated 11-kafka-connect.yaml

echo "Waiting for Kafka Connect to become Ready (up to 10 minutes — includes image build)..."
oc wait kafkaconnect/fraud-detection-connect \
  --for=condition=Ready \
  --timeout=600s \
  -n "$NAMESPACE"
echo "Kafka Connect ready."

# ── Step 8: Kafka Connectors ──────────────────────────────────────────────────
print_step "8/9  Registering JDBC Sink connectors"
oc apply -f 12-kafka-connectors.yaml
echo "Connectors applied. Giving them 15 seconds to start..."
sleep 15

# ── Step 9: Application Services + Route ─────────────────────────────────────
print_step "9/9  Deploying application services and Streamlit UI"
oc_apply_templated 13-transaction-streamer.yaml
oc_apply_templated 14-risk-score-processor.yaml
oc_apply_templated 15-alert-service.yaml
oc_apply_templated 16-streamlit-ui.yaml
oc apply -f 17-streamlit-route.yaml
echo "Application deployments applied."

# ── Summary ───────────────────────────────────────────────────────────────────
print_step "Deployment complete — summary"

echo ""
echo "Pod status:"
oc get pods -n "$NAMESPACE"

echo ""
echo "Connector status:"
oc get kafkaconnectors -n "$NAMESPACE" 2>/dev/null || echo "  (connector CRDs may still be initializing)"

echo ""
ROUTE_HOST=$(oc get route streamlit-ui -n "$NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null || echo "")
if [[ -n "$ROUTE_HOST" ]]; then
  echo "Streamlit UI: https://$ROUTE_HOST"
else
  echo "Streamlit route not yet available. Run: oc get route streamlit-ui -n $NAMESPACE"
fi
