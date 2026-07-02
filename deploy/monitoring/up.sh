#!/usr/bin/env bash
# Deploy Prometheus + Grafana into the kind cluster and provision the Waybill
# dashboard. Run after deploy/kind/up.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MON="$ROOT/deploy/monitoring"

echo "==> Creating monitoring namespace"
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

echo "==> Creating Grafana dashboard ConfigMap from JSON"
kubectl -n monitoring create configmap grafana-dashboards \
  --from-file="$MON/grafana-dashboards/waybill.json" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Applying Prometheus (RBAC + deployment)"
kubectl apply -f "$MON/prometheus-rbac.yaml"
kubectl apply -f "$MON/prometheus.yaml"

echo "==> Applying Tempo (tracing backend)"
kubectl apply -f "$MON/tempo.yaml"
kubectl -n monitoring rollout status deploy/tempo --timeout=120s

echo "==> Applying Grafana"
kubectl apply -f "$MON/grafana.yaml"

echo "==> Waiting for rollouts"
kubectl -n monitoring rollout status deploy/prometheus --timeout=120s
kubectl -n monitoring rollout status deploy/grafana --timeout=120s

echo "==> Done."
echo "    Grafana:    kubectl -n monitoring port-forward svc/grafana 3000:3000  -> http://localhost:3000 (admin/admin)"
echo "    Prometheus: kubectl -n monitoring port-forward svc/prometheus 9090:9090"
echo "    Generate traffic so the dashboard fills in:"
echo "      python $ROOT/scripts/loadgen.py http://localhost:8080"
