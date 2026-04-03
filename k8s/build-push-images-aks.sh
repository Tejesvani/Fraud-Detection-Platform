#!/bin/bash
set -uo pipefail

REGISTRY="ghcr.io/tejesvani/fraud-detection-platform"
TAG=${1:-latest}
PLATFORM=${PLATFORM:-linux/amd64}
BUILDER_NAME=${BUILDER_NAME:-aks-builder}

echo "=== Building and pushing AKS-compatible images to $REGISTRY ==="
echo "Tag: $TAG"
echo "Platform: $PLATFORM"
echo ""

# Ensure logged in to GHCR
if ! docker info 2>/dev/null | grep -q "Username:"; then
  echo "NOTE: Make sure you are logged in to GHCR:"
  echo "  echo \$GITHUB_TOKEN | docker login ghcr.io -u <your-github-username> --password-stdin"
  echo ""
fi

SERVICES=(
  "transaction-streamer:producer/Dockerfile:."
  "risk-processor:processor/Dockerfile:."
  "alert-service:consumer/Dockerfile.alert:."
  "persistence-service:consumer/Dockerfile.persistence:."
  "validation-service:data_quality/Dockerfile:."
  "frontend:frontend/Dockerfile:."
  "dbt:dbt/Dockerfile:dbt/"
  "reconciliation:reconciliation/Dockerfile:."
)

BUILT_OK=()
BUILT_FAIL=()

# ─────────────────────────────────────────────
# Ensure buildx builder exists and is active
# ─────────────────────────────────────────────
ensure_builder() {
  if ! docker buildx inspect "$BUILDER_NAME" >/dev/null 2>&1; then
    echo "Creating buildx builder: $BUILDER_NAME"
    docker buildx create --name "$BUILDER_NAME" --use
  else
    echo "Using existing buildx builder: $BUILDER_NAME"
    docker buildx use "$BUILDER_NAME"
  fi

  docker buildx inspect --bootstrap >/dev/null
}

# ─────────────────────────────────────────────
# Build and push one image
# ─────────────────────────────────────────────
build_and_push_image() {
  local SERVICE="$1"
  local DOCKERFILE="$2"
  local CONTEXT="$3"
  local IMAGE="$REGISTRY/$SERVICE:$TAG"

  echo "  Building and pushing $IMAGE"
  echo "  Dockerfile: $DOCKERFILE"
  echo "  Context: $CONTEXT"

  if ! docker buildx build \
    --platform "$PLATFORM" \
    -t "$IMAGE" \
    -f "$DOCKERFILE" \
    --push \
    "$CONTEXT"; then
    return 1
  fi

  if [ "$TAG" != "latest" ]; then
    local LATEST_IMAGE="$REGISTRY/$SERVICE:latest"
    echo "  Tagging and pushing $LATEST_IMAGE"
    if ! docker buildx build \
      --platform "$PLATFORM" \
      -t "$LATEST_IMAGE" \
      -f "$DOCKERFILE" \
      --push \
      "$CONTEXT"; then
      return 1
    fi
  fi

  return 0
}

# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Phase 1: Buildx Build & Push for AKS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

ensure_builder

for entry in "${SERVICES[@]}"; do
  SERVICE="$(echo "$entry" | cut -d: -f1)"
  DOCKERFILE="$(echo "$entry" | cut -d: -f2)"
  CONTEXT="$(echo "$entry" | cut -d: -f3)"

  echo ""
  echo "▶ $SERVICE"

  if build_and_push_image "$SERVICE" "$DOCKERFILE" "$CONTEXT"; then
    BUILT_OK+=("$SERVICE")
    echo "  ✔ Build + Push OK"
  else
    BUILT_FAIL+=("$SERVICE")
    echo "  ✖ Build + Push FAILED"
  fi
done

# ─────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Summary"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
if [ ${#BUILT_OK[@]} -gt 0 ]; then
  echo "Successful:"
  for s in "${BUILT_OK[@]}"; do
    echo "  ✔ $s"
  done
fi

if [ ${#BUILT_FAIL[@]} -gt 0 ]; then
  echo ""
  echo "Failed:"
  for s in "${BUILT_FAIL[@]}"; do
    echo "  ✖ $s"
  done
fi

TOTAL=${#SERVICES[@]}
OK_COUNT=${#BUILT_OK[@]}
FAIL_COUNT=${#BUILT_FAIL[@]}

echo ""
echo "Result: $OK_COUNT/$TOTAL images built and pushed for AKS"

if [ $FAIL_COUNT -gt 0 ]; then
  echo "Status: COMPLETED WITH ERRORS"
  exit 1
else
  echo "Status: ALL OK ✔"
  exit 0
fi