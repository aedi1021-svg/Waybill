# Deploying Waybill

Two tracks, both free:

- **Cloud Run** — always-on public URL a customer/recruiter can click.
- **kind** — full local Kubernetes for production-ops practice.

Same container image, same app. Only the substrate differs.

## Track 1 — local Kubernetes (kind)

Full production-ops practice for $0: Helm chart, Deployment + Service, HPA
autoscaling, liveness/readiness probes, a pre-upgrade Alembic migration Job, and
a bundled Postgres StatefulSet.

Requires `docker`, `kind`, `kubectl`, `helm`.

```bash
./deploy/kind/up.sh
# app comes up at http://localhost:8080
curl -s localhost:8080/ready
open http://localhost:8080/docs
kind delete cluster --name waybill      # tear down
```

What this demonstrates:
- Helm packaging with per-environment values overrides.
- HorizontalPodAutoscaler scaling the API on CPU.
- Correct liveness vs. readiness probe split.
- Schema migrations as a Helm pre-install/pre-upgrade hook Job.
- Stateful workload (Postgres) with a PersistentVolumeClaim.

## Observability (Prometheus + Grafana)

After the kind stack is up, add monitoring:

```bash
./deploy/monitoring/up.sh
kubectl -n monitoring port-forward svc/grafana 3000:3000
# open http://localhost:3000  (admin/admin) -> Waybill dashboard
```

Generate traffic so the panels fill in:

```bash
python scripts/loadgen.py http://localhost:8080 --rate 5 --duration 300
```

The app is instrumented with `prometheus_client` and exposes `/metrics`.
Prometheus auto-discovers Waybill pods and scrapes them; Grafana is
pre-provisioned with the datasource and dashboard so it works on first start.

The dashboard tells the **product** story, not just CPU graphs:
- auto-resolve vs. escalation rate (the core signal)
- decisions by exception type
- classifier confidence p50/p95 over time (the earliest drift signal)
- request rate by path, decision latency p95

Upgrade path for a real cluster: swap this minimal Prometheus for the
`kube-prometheus-stack` Helm chart and expose the app via a `ServiceMonitor`
CRD instead of the static scrape config — the app's `/metrics` endpoint is
unchanged.

## Distributed tracing (OpenTelemetry + Tempo)

Metrics tell you *that* latency rose; traces tell you *why* one specific
exception was slow. The app emits OTLP spans for each hop — HTTP request →
`agent.handle` → `agent.classify` → `llm.generate` → `agent.persist` — so you
can open a single exception in Grafana and see exactly where the time went.

Tracing is **opt-in and a soft dependency**: with `OTEL_ENABLED=false` (the
default) the spans are no-ops and the app runs without any OpenTelemetry
packages. Turn it on by setting `config.otelEnabled: "true"` in Helm values
(pointing at Tempo), which the monitoring stack deploys:

```bash
./deploy/monitoring/up.sh        # now also deploys Tempo
helm upgrade waybill deploy/helm/waybill -f deploy/kind/values-kind.yaml \
  --set config.otelEnabled=true --set config.otelEndpoint=http://tempo.monitoring:4318
```

Then in Grafana, open the Tempo datasource and search recent traces. Each trace
shows the per-hop span breakdown, with the LLM call time called out — usually
the dominant span, which is exactly the insight you want.

Locally without Kubernetes: `pip install -r requirements-tracing.txt`, run any
OTLP collector, and `OTEL_ENABLED=true python -m waybill.cli serve`.

## Track 2 — Cloud Run (public, always-on)

Gives a public HTTPS URL, scales to zero (free when idle), runs the same image.

Prereqs: a GCP project with billing enabled, `gcloud` authenticated, and a
managed Postgres connection string. For a free database, use Neon or Supabase
(both have free Postgres tiers) — no Cloud SQL cost.

```bash
PROJECT=my-gcp-project \
REGION=us-central1 \
DATABASE_URL='postgresql+psycopg://user:pass@host/db' \
  ./deploy/cloudrun/deploy.sh
# prints the public URL when done
```

The script builds and pushes the image to Artifact Registry, stores the DB URL
as a Secret, runs migrations as a one-off Cloud Run Job, then deploys the public
service with `min-instances=0` so it costs nothing while idle.

## AWS (EKS) — later, briefly

The EKS/Terraform path is a separate, cost-managed exercise: apply, capture a
demo, `terraform destroy` the same session (~a few dollars, mostly the EKS
control plane which has no free tier). The Helm chart here deploys unchanged to
EKS by setting `postgres.enabled=false` and pointing `database.url` at RDS.
