# Waybill

An AI agent that detects, triages, and resolves freight shipment exceptions —
the delays, customs holds, damaged goods, missing documents, and address errors
that logistics teams otherwise chase by hand. Waybill auto-resolves the routine
cases and escalates the uncertain or severe ones to a human, with every decision
recorded for audit.

Built as a production-grade DevOps/MLOps reference project: a supervised agent
wrapped in an evaluation harness, containerized, and deployable to Kubernetes on
AWS with full CI/CD and observability.

## Why this design

The agent is deliberately **narrow and supervised**. It handles the boring 90%
autonomously and escalates the weird 10% — the pattern that separates agents
that reach production from the ~77% that die in a sandbox. The confidence gate
(auto-resolve vs. escalate) is the core of the product, not an afterthought.

## Phase 1 — local core (this milestone)

```
waybill/
  core/       domain models + config
  data/       synthetic exception generator (seeded, labelled, class-balanced)
  agent/      Ollama LLM client, classifier (LLM + heuristic baseline), agent core
  tools/      scoped resolution actions (the agent's entire permission set)
  db/         Postgres persistence: SQLAlchemy models, repository (append-only
              audit journal), Alembic migrations
  eval/       eval harness: accuracy, escalation recall, false-auto-resolve rate
  cli.py      demo / eval / migrate / seed commands
```

### The audit journal

Every decision the agent makes is persisted to an append-only journal in
Postgres — four tables (`shipments`, `exceptions`, `resolutions`,
`resolution_actions`). Exceptions and resolutions are immutable: a new decision
on the same exception is a new row, never an overwrite. This is both the audit
trail (why did the agent do X?) and the training-data flywheel: the
`human_override_type` column captures operator corrections, which Phase 4's
drift detection and retraining learn from.

Schema is version-controlled with Alembic migrations, so local Postgres and (in
Phase 2) AWS RDS are provisioned identically.

### Run it

Local (Python):

```bash
pip install -r requirements.txt

# start Postgres however you like, then point DATABASE_URL at it, e.g.:
export DATABASE_URL=postgresql+psycopg://waybill:waybill@localhost:5432/waybill
alembic upgrade head                 # create the schema
python -m waybill.cli seed -n 50     # persist a batch to the journal
python -m waybill.cli demo           # watch the agent resolve exceptions
python -m waybill.cli eval -n 200    # score it against ground truth
pytest -q                            # unit tests (no DB or Ollama needed)
```

Full local stack (Postgres + Ollama + app) via Docker:

```bash
cd docker
docker compose up -d postgres ollama
docker compose run --rm waybill migrate
docker compose exec ollama ollama pull llama3.1:8b
docker compose up -d waybill            # starts the HTTP service on :8000
```

### HTTP API

Run the service locally with `python -m waybill.cli serve` (or the Docker stack
above), then:

```
POST /exceptions                    submit an exception, get the agent's decision
GET  /resolutions?limit=50          recent journal entries (dashboard feed)
GET  /shipments/{id}/history        full decision history for one shipment
POST /resolutions/{id}/override     record a human correction (training label)
GET  /health                        liveness probe (process up?)
GET  /ready                         readiness probe (DB + LLM reachable?)
GET  /metrics                       Prometheus metrics (fully wired in Phase 2)
```

Example:

```bash
curl -X POST localhost:8000/exceptions -H 'content-type: application/json' -d '{
  "tracking_number": "ABC123456789",
  "carrier": "DHL",
  "raw_message": "Shipment held by customs pending inspection",
  "origin": "Shanghai", "destination": "Hamburg"
}'
```

Interactive API docs are auto-generated at `/docs` (FastAPI/OpenAPI).

The `/health` vs `/ready` split is deliberate: liveness stays cheap so a briefly
unreachable dependency doesn't trigger a pod restart loop, while readiness pulls
the pod out of the load balancer until Postgres and the model are back. This is
exactly the contract Kubernetes expects in Phase 2.

The classifier uses Ollama when reachable and falls back to a transparent
heuristic baseline otherwise — so tests and CI never depend on a live model, and
you always have a dumb baseline to measure the LLM against.

## Roadmap

- **Phase 2** — Terraform + AWS + EKS + Helm; Prometheus & Grafana dashboards.
- **Phase 3** — GitHub Actions CI, Argo CD GitOps, MLflow registry,
  OpenTelemetry tracing (Tempo/Loki), eval-gated deploys.
- **Phase 4** — Istio canary rollouts, KEDA queue autoscaling, Feast feature
  store, drift detection with automated retraining.

## CI/CD (eval-gated)

`.github/workflows/ci.yml` runs on every push/PR:

1. `lint` — ruff.
2. `test` — pytest.
3. `eval-gate` — runs the eval harness and checks it against the committed
   thresholds in `eval_thresholds.json`. **If the agent regresses, this job
   fails and the image is never built.** This is the core MLOps guardrail: you
   cannot ship a model or prompt change that drops classification accuracy,
   misses escalations, or raises the false-auto-resolve rate.
4. `build` — builds and pushes the container image to GHCR, only if all of the
   above pass.

`.github/workflows/deploy-cloudrun.yml` then deploys to Cloud Run on `main`,
but only after CI (eval gate included) succeeds — so "merge to main" means
"eval-passed change goes live."

`deploy/argocd/application.yaml` represents the GitOps track for the Kubernetes
deployment: Argo CD watches the repo and continuously syncs the cluster to the
Helm chart, self-healing any drift.

Move the quality bar by editing `eval_thresholds.json` — a reviewable commit
whose history records how the bar rose over time.

## Model registry & lifecycle (MLflow)

The classifier has three interchangeable backends: the Ollama LLM, a heuristic
baseline, and a **trained** model (TF-IDF + logistic regression over the carrier
message text). The trained model is what the registry governs.

```bash
pip install -r requirements.txt -r requirements-ml.txt
python -m waybill.cli train        # train -> log to MLflow -> eval-gated promotion
mlflow ui                          # browse experiments + registry at :5000
```

The lifecycle, all in `waybill/ml/train_register.py`:

1. Train on synthetic labelled data.
2. Log the run (params, metrics, model artifact) to MLflow.
3. Register the model as a new version.
4. Evaluate against the committed `eval_thresholds.json`.
5. **Promote to "Staging" only if it clears the bar.** A model that fails is
   versioned for the record but never promoted, so serving can't pick it up.

Serving (`waybill/ml/serving.py`) loads whatever is in "Staging" from the
registry, deriving the agent's confidence from the model's predicted
probability. Swapping the production model is a registry stage transition, not a
code deploy.

The CI pipeline runs this same train-and-gate step (`train-model` job), so a
model regression blocks the build exactly like a failing eval or test.

## Drift detection & automated retraining (the closed loop)

The system improves itself, safely. Every agent decision lands in the journal;
every human override becomes a ground-truth label. On a schedule, a CronJob
checks for drift and retrains only if warranted — and the retrained model must
still clear the eval gate to be served.

```bash
python -m waybill.cli drift              # inspect current drift signals
python -m waybill.cli retrain            # retrain only if drifted
python -m waybill.cli retrain --force    # retrain regardless
```

Drift detection (`waybill/ml/drift.py`) watches three signals over recent
decisions:
- mean classifier confidence dropping below a floor (earliest signal),
- escalation rate climbing above a ceiling (behavioural proxy),
- human-override rate — the ground-truth signal, weighted most heavily because
  it's real operator disagreement, not the model's self-report.

Retraining (`waybill/ml/retrain.py`) folds the human-corrected labels from the
journal into the training set (upweighted ×5, since real corrections are the
most valuable examples), retrains, and promotes through the same eval gate. The
loop is self-improving but never self-degrading: a retrained model that fails
the gate is logged but never served.

Automation: the `waybill-retrain` CronJob (Helm, `retrain.enabled=true`) runs
the drift-check-and-retrain daily. The `/drift` API endpoint surfaces the live
signals for dashboards and on-call.

This closes the loop that the whole architecture was built for: the append-only
journal is the training data, the human-override endpoint captures the labels,
drift detection decides when to act, and the eval gate + registry ensure only a
better model ever reaches production.

## Key metrics the eval harness tracks

- `classification_accuracy` — did it identify the exception type?
- `escalation_recall` — of cases that *should* reach a human, how many did?
  (The safety metric — a missed escalation is the expensive error.)
- `false_auto_resolve_rate` — how often did it confidently act on a wrong call?
  (A confident wrong action is worse than an escalation.)
- latency mean / p95.
