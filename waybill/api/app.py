"""Waybill FastAPI service.

Exposes the agent and the audit journal over HTTP:

  POST /exceptions              submit an exception, get the agent's decision
  GET  /resolutions             recent journal entries (for the dashboard)
  GET  /shipments/{id}/history  full decision history for one shipment
  POST /resolutions/{id}/override   record a human correction
  GET  /health                  liveness  (is the process up?)
  GET  /ready                   readiness (are DB + LLM reachable?)
  GET  /metrics                 Prometheus metrics (wired properly in Phase 2)

The health/ready split matters for Kubernetes: liveness failing restarts the
pod; readiness failing just pulls it out of the load balancer until deps
recover. Getting this right now means the EKS deployment in Phase 2 "just works".
"""
from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from waybill.agent.agent import WaybillAgent
from waybill.agent.llm import OllamaClient
from waybill.api import metrics
from waybill.api.schemas import (
    ActionOut,
    HealthOut,
    JournalEntryOut,
    OverrideRequest,
    ResolutionOut,
    SubmitExceptionRequest,
)
from waybill.core.models import (
    ExceptionEvent,
    ExceptionType,
    Severity,
    Shipment,
)
from waybill.db.repository import Journal
from waybill.obs.tracing import otel_enabled, setup_tracing

# Configure tracing before the app handles requests. No-op unless OTEL_ENABLED.
setup_tracing("waybill")

app = FastAPI(title="Waybill", version="0.1.0")

# Auto-instrument FastAPI so every HTTP request is a root span that the agent's
# child spans attach to — giving one connected trace per exception. Guarded so
# the app runs without the instrumentation package installed / tracing off.
if otel_enabled():
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        pass


@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    """Record request count and latency for every request. Uses the route
    template (not the raw path) as a label so per-shipment URLs don't explode
    cardinality."""
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    elapsed = time.perf_counter() - start
    metrics.REQUEST_LATENCY.labels(request.method, path).observe(elapsed)
    metrics.REQUESTS.labels(request.method, path, response.status_code).inc()
    return response


# Built once at startup. The journal + agent are cheap to hold as module state;
# in Phase 2 these get proper lifespan management and dependency injection.
_llm = OllamaClient()
_journal = Journal()
_agent = WaybillAgent(journal=_journal, model_name=_llm.model if _llm.available() else "heuristic")


@app.post("/exceptions", response_model=ResolutionOut)
def submit_exception(req: SubmitExceptionRequest) -> ResolutionOut:
    shipment = Shipment(
        tracking_number=req.tracking_number,
        carrier=req.carrier,
        origin=req.origin,
        destination=req.destination,
        value_usd=req.value_usd,
        customer=req.customer,
    )
    event = ExceptionEvent(
        shipment_id=shipment.id,
        tracking_number=req.tracking_number,
        carrier=req.carrier,
        raw_message=req.raw_message,
        true_type=ExceptionType(req.true_type) if req.true_type else None,
        true_severity=Severity(req.true_severity) if req.true_severity else None,
    )
    resolution = _agent.handle(shipment, event)
    metrics.record_decision(
        disposition=resolution.disposition.value,
        exception_type=resolution.classification.exception_type.value,
        confidence=resolution.confidence,
        latency_ms=resolution.latency_ms,
    )
    return ResolutionOut(
        resolution_id=resolution.id,
        exception_id=resolution.exception_id,
        exception_type=resolution.classification.exception_type.value,
        severity=resolution.classification.severity.value,
        confidence=resolution.confidence,
        disposition=resolution.disposition.value,
        actions=[ActionOut(kind=a.kind, summary=a.summary, payload=a.payload) for a in resolution.actions],
        latency_ms=resolution.latency_ms,
        decided_at=resolution.decided_at,
    )


@app.get("/resolutions", response_model=list[JournalEntryOut])
def recent_resolutions(limit: int = 50) -> list[JournalEntryOut]:
    rows = _journal.recent_resolutions(limit=limit)
    return [
        JournalEntryOut(
            resolution_id=r.id,
            exception_type=r.exception_type,
            severity=r.severity,
            confidence=r.confidence,
            disposition=r.disposition,
            model_name=r.model_name,
            decided_at=r.decided_at,
            human_override_type=r.human_override_type,
        )
        for r in rows
    ]


@app.get("/shipments/{shipment_id}/history", response_model=list[JournalEntryOut])
def shipment_history(shipment_id: str) -> list[JournalEntryOut]:
    rows = _journal.resolutions_for_shipment(shipment_id)
    return [
        JournalEntryOut(
            resolution_id=r.id,
            exception_type=r.exception_type,
            severity=r.severity,
            confidence=r.confidence,
            disposition=r.disposition,
            model_name=r.model_name,
            decided_at=r.decided_at,
            human_override_type=r.human_override_type,
        )
        for r in rows
    ]


@app.post("/resolutions/{resolution_id}/override")
def override(resolution_id: str, req: OverrideRequest) -> dict:
    try:
        ExceptionType(req.corrected_type)  # validate it's a real type
        _journal.record_human_override(resolution_id, req.corrected_type)
    except KeyError:
        raise HTTPException(status_code=404, detail="resolution not found")
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid exception type")
    return {"status": "recorded", "resolution_id": resolution_id}


@app.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    """Liveness: is the process itself healthy? Cheap, no external calls that
    would cause a restart loop if a dependency is briefly down."""
    return HealthOut(status="ok", llm_available=False, db_reachable=False)


@app.get("/ready", response_model=HealthOut)
def ready() -> HealthOut:
    """Readiness: are dependencies reachable? Used by the load balancer to
    decide whether to send traffic. Checks DB and LLM."""
    db_ok = _db_reachable()
    llm_ok = _llm.available()
    status = "ok" if db_ok else "degraded"
    return HealthOut(status=status, llm_available=llm_ok, db_reachable=db_ok)


@app.get("/metrics")
def prometheus_metrics() -> PlainTextResponse:
    """Prometheus exposition endpoint. Scraped by Prometheus (see the
    ServiceMonitor / scrape config in deploy/). Exposes both service-level
    metrics (request rate, latency) and domain metrics (decisions by
    disposition, confidence distribution)."""
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/drift")
def drift_status(window: int = 500) -> dict:
    """Current drift signals over the last `window` decisions. Surfaced so the
    dashboard and on-call can see model health at a glance; the scheduled
    retrain CronJob acts on the same signal."""
    try:
        from waybill.ml.drift import detect_drift

        return detect_drift(window=window).to_dict()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"drift check unavailable: {exc}")


def _db_reachable() -> bool:
    try:
        from sqlalchemy import text

        from waybill.db.engine import session_scope

        with session_scope() as s:
            s.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
