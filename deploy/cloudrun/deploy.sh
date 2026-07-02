#!/usr/bin/env bash
# Deploy Waybill to Google Cloud Run as an always-on public service.
#
# Prereqs (one-time):
#   - a GCP project with billing enabled (Cloud Run has a generous free tier;
#     scale-to-zero means you pay ~nothing when idle)
#   - gcloud CLI authenticated:  gcloud auth login && gcloud config set project <id>
#   - a managed Postgres. Cheapest free-ish options: Neon or Supabase (both have
#     free Postgres tiers) — grab their connection string. Or Cloud SQL (not free).
#
# Usage:
#   PROJECT=my-proj REGION=us-central1 DATABASE_URL='postgresql+psycopg://...' \
#     ./deploy/cloudrun/deploy.sh
set -euo pipefail

: "${PROJECT:?set PROJECT to your GCP project id}"
: "${REGION:=us-central1}"
: "${DATABASE_URL:?set DATABASE_URL to your managed Postgres connection string}"

REPO=waybill
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/waybill:latest"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "==> Ensuring Artifact Registry repo exists"
gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO" --repository-format=docker --location "$REGION"

echo "==> Building and pushing image (linux/amd64)"
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
docker build --platform linux/amd64 -t "$IMAGE" -f "$ROOT/docker/Dockerfile" "$ROOT"
docker push "$IMAGE"

echo "==> Storing DATABASE_URL as a secret"
echo -n "$DATABASE_URL" | gcloud secrets create waybill-db-url --data-file=- 2>/dev/null || \
  echo -n "$DATABASE_URL" | gcloud secrets versions add waybill-db-url --data-file=-

echo "==> Running migrations as a one-off Cloud Run job"
gcloud run jobs deploy waybill-migrate \
  --image "$IMAGE" --region "$REGION" \
  --set-secrets "DATABASE_URL=waybill-db-url:latest" \
  --args="migrate" --execute-now --wait

echo "==> Deploying the public service"
gcloud run deploy waybill \
  --image "$IMAGE" --region "$REGION" \
  --platform managed --allow-unauthenticated \
  --port 8000 --args="serve,--host,0.0.0.0,--port,8000" \
  --set-env-vars "ESCALATION_THRESHOLD=0.75" \
  --set-secrets "DATABASE_URL=waybill-db-url:latest" \
  --min-instances 0 --max-instances 4 --memory 512Mi

echo "==> Public URL:"
gcloud run services describe waybill --region "$REGION" --format 'value(status.url)'
