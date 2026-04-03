#!/bin/bash
set -euo pipefail

OVERLAY=${1:-dev}
NAMESPACE=fraud-detection

echo "=== Fraud Detection Platform — AKS Deployment ==="
echo "Overlay: $OVERLAY"
echo ""

# Step 1: Verify AKS connection
echo "[1/7] Verifying AKS connection..."
kubectl get nodes || { echo "ERROR: Cannot connect to AKS. Run: az aks get-credentials --resource-group fraud-detection-rg --name fraud-detection-aks"; exit 1; }

# Step 2: Verify namespace and secrets exist
echo "[2/7] Checking prerequisites..."
kubectl get namespace $NAMESPACE || { echo "ERROR: Namespace not found. Run: kubectl create namespace $NAMESPACE"; exit 1; }
kubectl get secret ghcr-secret -n $NAMESPACE || { echo "ERROR: GHCR pull secret not found. Run: kubectl create secret docker-registry ghcr-secret --docker-server=ghcr.io --docker-username=<github-username> --docker-password=<github-token> -n $NAMESPACE"; exit 1; }
kubectl get secret fraud-detection-secrets -n $NAMESPACE || { echo "ERROR: PostgreSQL secret not found. Run: kubectl create secret generic fraud-detection-secrets --from-literal=POSTGRES_USER=<user> --from-literal=POSTGRES_PASSWORD=<password> -n $NAMESPACE"; exit 1; }

# Step 3: Verify Strimzi operator is running
echo "[3/7] Checking Strimzi operator..."
kubectl get deployment strimzi-cluster-operator -n $NAMESPACE 2>/dev/null || {
  echo "Strimzi not found — installing..."
  kubectl apply -f "https://strimzi.io/install/latest?namespace=$NAMESPACE" -n $NAMESPACE
  echo "Waiting for Strimzi operator to be ready..."
  kubectl wait deployment/strimzi-cluster-operator -n $NAMESPACE --for=condition=Available --timeout=120s
}

# Step 4: Apply Kustomize overlay
echo "[4/7] Applying $OVERLAY overlay..."
kubectl apply -k k8s/overlays/$OVERLAY

# Step 5: Wait for Kafka
echo "[5/7] Waiting for Kafka cluster (this takes 2-3 minutes)..."
kubectl wait kafka/fraud-detection-kafka -n $NAMESPACE --for=condition=Ready --timeout=300s 2>/dev/null || echo "WARN: Kafka still starting — check with: kubectl get kafka -n $NAMESPACE"

# Step 6: Wait for core services
echo "[6/7] Waiting for services..."
kubectl wait deployment/schema-registry -n $NAMESPACE --for=condition=Available --timeout=180s 2>/dev/null || true
kubectl wait deployment/transaction-streamer -n $NAMESPACE --for=condition=Available --timeout=120s 2>/dev/null || true
kubectl wait deployment/risk-processor -n $NAMESPACE --for=condition=Available --timeout=120s 2>/dev/null || true
kubectl wait deployment/persistence-service -n $NAMESPACE --for=condition=Available --timeout=120s 2>/dev/null || true

# Step 7: Print access info
echo ""
echo "[7/7] Getting access URLs..."
echo ""
echo "=== Deployment complete! ==="
echo ""

FRONTEND_IP=$(kubectl get svc frontend -n $NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "pending")
GRAFANA_IP=$(kubectl get svc grafana-external -n $NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "pending")

echo "Access points:"
echo "  Frontend (Streamlit): http://$FRONTEND_IP:8501"
echo "  Grafana:              http://$GRAFANA_IP:3000  (admin/admin)"
echo ""
echo "If IPs show 'pending', wait a minute and run:"
echo "  kubectl get svc frontend grafana-external -n $NAMESPACE"
echo ""
echo "Useful commands:"
echo "  kubectl get pods -n $NAMESPACE"
echo "  kubectl logs -f deployment/transaction-streamer -n $NAMESPACE"
echo "  kubectl logs -f deployment/risk-processor -n $NAMESPACE"
echo "  kubectl logs -f deployment/validation-service -n $NAMESPACE"
echo ""
echo "Cost management:"
echo "  Stop cluster:  az aks stop --resource-group fraud-detection-rg --name fraud-detection-aks"
echo "  Start cluster: az aks start --resource-group fraud-detection-rg --name fraud-detection-aks"
echo "  Delete all:    az group delete --name fraud-detection-rg --yes"
