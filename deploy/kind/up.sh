#!/usr/bin/env bash
# Stand up the full Waybill stack on a local kind cluster. Idempotent-ish:
# safe to re-run. Requires: docker, kind, kubectl, helm.
set -euo pipefail

CLUSTER=waybill
IMAGE=waybill:latest
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "==> Creating kind cluster (if absent)"
if ! kind get clusters | grep -q "^${CLUSTER}$"; then
  kind create cluster --name "$CLUSTER" --config "$ROOT/deploy/kind/kind-config.yaml"
fi

echo "==> Building app image for linux/amd64"
docker build --platform linux/amd64 -t "$IMAGE" -f "$ROOT/docker/Dockerfile" "$ROOT"

echo "==> Loading image into kind"
kind load docker-image "$IMAGE" --name "$CLUSTER"

echo "==> Installing/upgrading Waybill via Helm"
helm upgrade --install waybill "$ROOT/deploy/helm/waybill" \
  -f "$ROOT/deploy/kind/values-kind.yaml" \
  --wait --timeout 180s

echo "==> Done. The app is reachable at http://localhost:8080"
echo "    Try:  curl -s localhost:8080/ready | jq"
echo "    Docs: http://localhost:8080/docs"
echo "    Tear down with: kind delete cluster --name ${CLUSTER}"
