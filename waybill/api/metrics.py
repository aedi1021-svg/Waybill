"""Prometheus metrics.

Defines the app's instruments and a helper to record a decision. Two categories
of metric matter here:

- Standard service health: request count, request latency. Every production
  service needs these.
- Domain metrics: decisions by disposition, decisions by exception type,
  classifier confidence distribution, agent decision latency. These are what
  make the Grafana dashboard tell the *product* story — "how many exceptions
  are we auto-resolving vs escalating right now" — not just CPU graphs.

The domain metrics are also the early-warning system for model drift: if the
escalation rate suddenly climbs, the classifier's confidence has dropped, and
that shows up here before it shows up anywhere else.
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

# --- Service-level ---
REQUESTS = Counter(
    "waybill_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "waybill_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path"],
)

# --- Domain-level (the product story) ---
DECISIONS = Counter(
    "waybill_decisions_total",
    "Agent decisions",
    ["disposition", "exception_type"],
)
CONFIDENCE = Histogram(
    "waybill_decision_confidence",
    "Classifier confidence distribution",
    buckets=[0.1, 0.25, 0.5, 0.65, 0.75, 0.85, 0.95, 1.0],
)
DECISION_LATENCY = Histogram(
    "waybill_decision_duration_seconds",
    "Agent decision latency (classify + decide)",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)


def record_decision(disposition: str, exception_type: str, confidence: float, latency_ms: int) -> None:
    DECISIONS.labels(disposition=disposition, exception_type=exception_type).inc()
    CONFIDENCE.observe(confidence)
    DECISION_LATENCY.observe(latency_ms / 1000.0)
