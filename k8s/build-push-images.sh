#!/bin/bash
set -uo pipefail

REGISTRY="ghcr.io/tejesvani/fraud-detection-platform"
TAG=${1:-latest}

echo "=== Building and pushing images to $REGISTRY ==="
echo "Tag: $TAG"
echo ""

# Ensure logged in to GHCR
if ! docker info 2>/dev/null | grep -q "ghcr.io"; then
  echo "NOTE: Make sure you are logged in to GHCR:"
  echo "  echo \$GITHUB_TOKEN | docker login ghcr.io -u <your-github-username> --password-stdin"
  echo ""
fi

SERVICES=(
  "transaction-streamer:producer/Dockerfile"
  "risk-processor:processor/Dockerfile"
  "alert-service:consumer/Dockerfile.alert"
  "persistence-service:consumer/Dockerfile.persistence"
  "validation-service:data_quality/Dockerfile"
  "frontend:frontend/Dockerfile"
  "dbt:dbt/Dockerfile"
  "reconciliation:reconciliation/Dockerfile"
)

# Tracking arrays
BUILT_OK=()
BUILT_FAIL=()
PUSH_OK=()
PUSH_FAIL=()   # entries are "SERVICE:IMAGE"

# ─────────────────────────────────────────────
# Helper: push one image (and its :latest tag)
# Returns 0 on success, 1 on failure
# ─────────────────────────────────────────────
push_image() {
  local SERVICE="$1"
  local IMAGE="$2"

  echo "  Pushing $IMAGE..."
  if ! docker push "$IMAGE"; then
    return 1
  fi

  if [ "$TAG" != "latest" ]; then
    local LATEST_IMAGE="$REGISTRY/$SERVICE:latest"
    docker tag "$IMAGE" "$LATEST_IMAGE"
    echo "  Pushing $LATEST_IMAGE..."
    if ! docker push "$LATEST_IMAGE"; then
      return 1
    fi
  fi

  return 0
}

# ─────────────────────────────────────────────
# Phase 1: Build + first push attempt
# ─────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Phase 1: Build & Push"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

for entry in "${SERVICES[@]}"; do
  SERVICE="${entry%%:*}"
  DOCKERFILE="${entry##*:}"
  IMAGE="$REGISTRY/$SERVICE:$TAG"

  echo ""
  echo "▶ $SERVICE"

  # Build
  echo "  Building from $DOCKERFILE..."
  if [ "$SERVICE" = "dbt" ]; then
    BUILD_CMD="docker build -t \"$IMAGE\" -f \"$DOCKERFILE\" dbt/"
  else
    BUILD_CMD="docker build -t \"$IMAGE\" -f \"$DOCKERFILE\" ."
  fi

  if ! eval "$BUILD_CMD"; then
    echo "  ✖ Build FAILED: $SERVICE"
    BUILT_FAIL+=("$SERVICE")
    continue   # skip push if build failed
  fi

  BUILT_OK+=("$SERVICE")
  echo "  ✔ Build OK"

  # Push
  if push_image "$SERVICE" "$IMAGE"; then
    PUSH_OK+=("$SERVICE")
    echo "  ✔ Push OK"
  else
    echo "  ✖ Push FAILED: $SERVICE (will retry)"
    PUSH_FAIL+=("$SERVICE:$IMAGE")
  fi
done

# ─────────────────────────────────────────────
# Phase 2: Retry failed pushes (once)
# ─────────────────────────────────────────────
if [ ${#PUSH_FAIL[@]} -gt 0 ]; then
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Phase 2: Retrying ${#PUSH_FAIL[@]} failed push(es)"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  STILL_FAILED=()

  for entry in "${PUSH_FAIL[@]}"; do
    SERVICE="${entry%%:*}"
    IMAGE="${entry##*:}"

    echo ""
    echo "↺ Retrying push: $SERVICE"

    if push_image "$SERVICE" "$IMAGE"; then
      PUSH_OK+=("$SERVICE")
      echo "  ✔ Retry OK"
    else
      STILL_FAILED+=("$SERVICE")
      echo "  ✖ Retry FAILED: $SERVICE"
    fi
  done

  PUSH_FAIL=("${STILL_FAILED[@]+"${STILL_FAILED[@]}"}")
fi

# ─────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Summary"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
echo "Builds"
if [ ${#BUILT_OK[@]} -gt 0 ]; then
  for s in "${BUILT_OK[@]}"; do echo "  ✔ $s"; done
fi
if [ ${#BUILT_FAIL[@]} -gt 0 ]; then
  for s in "${BUILT_FAIL[@]}"; do echo "  ✖ $s  ← build failed"; done
fi

echo ""
echo "Pushes"
if [ ${#PUSH_OK[@]} -gt 0 ]; then
  for s in "${PUSH_OK[@]}"; do echo "  ✔ $s"; done
fi
if [ ${#PUSH_FAIL[@]} -gt 0 ]; then
  for s in "${PUSH_FAIL[@]}"; do echo "  ✖ $s  ← push failed after retry"; done
fi

TOTAL=${#SERVICES[@]}
PUSH_OK_COUNT=${#PUSH_OK[@]}
PUSH_FAIL_COUNT=${#PUSH_FAIL[@]}
BUILD_FAIL_COUNT=${#BUILT_FAIL[@]}

echo ""
echo "Result: $PUSH_OK_COUNT/$TOTAL pushed successfully"

if [ $BUILD_FAIL_COUNT -gt 0 ] || [ $PUSH_FAIL_COUNT -gt 0 ]; then
  echo "Status: COMPLETED WITH ERRORS"
  exit 1
else
  echo "Status: ALL OK ✔"
  exit 0
fi