#!/bin/bash
set -euo pipefail

OVERLAY=${1:-dev}
NAMESPACE=fraud-detection

echo "=== Fraud Detection Platform — Kubernetes Deployment ==="
echo "Overlay: $OVERLAY"
echo ""

# Step 1: Create namespace
echo "[1/6] Creating namespace..."
kubectl apply -f k8s/base/namespace.yaml

# Step 2: Install Strimzi Operator (if not already installed)
echo "[2/6] Installing Strimzi Operator..."
kubectl apply -f "https://strimzi.io/install/latest?namespace=$NAMESPACE" -n $NAMESPACE 2>/dev/null || true
echo "Waiting for Strimzi operator to be ready..."
kubectl wait deployment/strimzi-cluster-operator -n $NAMESPACE --for=condition=Available --timeout=120s

# Step 3: Apply secrets (must exist before other resources)
echo "[3/6] Applying secrets..."
if [ ! -f k8s/base/secret.yaml ]; then
  echo "ERROR: k8s/base/secret.yaml not found."
  echo "Copy k8s/base/secret.yaml.template to k8s/base/secret.yaml and fill in credentials."
  exit 1
fi
kubectl apply -f k8s/base/secret.yaml

# Step 4: Apply Kustomize overlay
echo "[4/6] Applying $OVERLAY overlay..."
kubectl apply -k k8s/overlays/$OVERLAY

# Step 5: Wait for Kafka to be ready
echo "[5/6] Waiting for Kafka cluster..."
kubectl wait kafka/fraud-detection-kafka -n $NAMESPACE --for=condition=Ready --timeout=300s

# Step 6: Wait for core services
echo "[6/6] Waiting for services to be ready..."
kubectl wait deployment/schema-registry -n $NAMESPACE --for=condition=Available --timeout=120s
kubectl wait statefulset/postgres -n $NAMESPACE --for=jsonpath='{.status.readyReplicas}'=1 --timeout=120s
kubectl wait deployment/transaction-streamer -n $NAMESPACE --for=condition=Available --timeout=120s
kubectl wait deployment/risk-processor -n $NAMESPACE --for=condition=Available --timeout=120s

echo ""
echo "=== Deployment complete! ==="
echo ""
echo "Access points:"
echo "  Frontend:   minikube service frontend -n $NAMESPACE --url"
echo "  Grafana:    minikube service grafana -n $NAMESPACE --url"
echo "  Prometheus: kubectl port-forward svc/prometheus 9090:9090 -n $NAMESPACE"
echo ""
echo "Useful commands:"
echo "  kubectl get pods -n $NAMESPACE"
echo "  kubectl logs -f deployment/transaction-streamer -n $NAMESPACE"
echo "  kubectl logs -f deployment/risk-processor -n $NAMESPACE"
echo "  kubectl get kafka -n $NAMESPACE"
echo "  kubectl get kafkatopic -n $NAMESPACE"
