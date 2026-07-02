"""OpenTelemetry tracing setup — with a graceful no-op fallback.

Design contract: the app must run whether or not OpenTelemetry is installed and
whether or not tracing is enabled. Tracing is opt-in via OTEL_ENABLED. When it's
off (or the packages aren't present), get_tracer() returns a no-op tracer whose
spans are cheap context managers that do nothing. This keeps OTel a soft
dependency: unit tests and the lightweight Cloud Run deploy don't need it.

Why tracing on top of metrics: metrics tell you *that* p95 decision latency rose;
a trace tells you *why* this particular exception took 4 seconds — the LLM call
was slow, or the DB write blocked. For an agent that chains classify -> LLM ->
decide -> persist, per-hop timing is the difference between guessing and knowing.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

_INITIALIZED = False


def otel_enabled() -> bool:
    return os.getenv("OTEL_ENABLED", "false").lower() in {"1", "true", "yes"}


class _NoOpSpan:
    def set_attribute(self, *_a, **_k) -> None:
        pass


class _NoOpTracer:
    """Returned when tracing is disabled or OTel isn't installed. Its spans are
    context managers that do nothing, so instrumentation call sites stay clean
    and identical regardless of whether tracing is active."""

    @contextmanager
    def start_as_current_span(self, *_a, **_k):
        yield _NoOpSpan()


_NOOP = _NoOpTracer()


def setup_tracing(service_name: str = "waybill") -> None:
    """Idempotently configure the global tracer provider. No-op unless
    OTEL_ENABLED is set. Imports OpenTelemetry lazily so the package is only
    required when tracing is actually turned on."""
    global _INITIALIZED
    if _INITIALIZED or not otel_enabled():
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        _INITIALIZED = True
    except Exception:
        # OTel not installed or misconfigured: stay in no-op mode rather than
        # crashing the app. Tracing is never allowed to take the service down.
        pass


def get_tracer(name: str = "waybill"):
    """Return a real tracer if tracing is enabled and OTel is importable,
    otherwise a no-op tracer with the same interface."""
    if not otel_enabled():
        return _NOOP
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except Exception:
        return _NOOP
