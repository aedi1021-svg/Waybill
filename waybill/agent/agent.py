"""Waybill agent core.

The decision loop for a single exception:

    classify -> gate on confidence & severity -> resolve or escalate -> record

The confidence gate is the whole product. A high-confidence, non-critical
exception gets an auto-drafted resolution via the scoped tools. Anything the
agent is unsure about, or anything severe, goes to a human. This is the
"narrow, supervised, escalates the weird 10%" pattern that separates agents
that ship from agents that die in a sandbox.

Every decision is returned as a fully-populated Resolution — the audit record.
"""
from __future__ import annotations

import time

from waybill.agent.classifier import Classifier
from waybill.core.config import settings
from waybill.core.models import (
    Classification,
    Disposition,
    ExceptionEvent,
    ExceptionType,
    Resolution,
    ResolutionAction,
    Severity,
    Shipment,
)
from waybill.tools import registry
from waybill.obs.tracing import get_tracer

_tracer = get_tracer("waybill.agent")

_SEVERITY_RANK = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


class WaybillAgent:
    def __init__(
        self,
        classifier: Classifier | None = None,
        journal=None,
        model_name: str = "",
    ) -> None:
        self._classifier = classifier or Classifier()
        self._threshold = settings.escalation_threshold
        self._always_escalate = _SEVERITY_RANK[
            Severity(settings.always_escalate_severity)
        ]
        # Journal is optional: when None (tests, eval harness) the agent runs
        # without a database. When provided, every decision is persisted to the
        # append-only audit trail.
        self._journal = journal
        self._model_name = model_name

    def handle(self, shipment: Shipment, event: ExceptionEvent) -> Resolution:
        # Root span for the whole decision. Child spans below break down where
        # time goes: classification (incl. the LLM call), the decision gate, and
        # the DB write. When OTEL is disabled these spans are cheap no-ops.
        with _tracer.start_as_current_span("agent.handle") as span:
            span.set_attribute("shipment.carrier", shipment.carrier)
            span.set_attribute("exception.tracking_number", event.tracking_number)

            started = time.perf_counter()
            with _tracer.start_as_current_span("agent.classify"):
                classification = self._classifier.classify(event.raw_message)

            with _tracer.start_as_current_span("agent.decide"):
                disposition, actions, notes = self._decide(shipment, classification)

            latency_ms = int((time.perf_counter() - started) * 1000)
            resolution = Resolution(
                exception_id=event.id,
                classification=classification,
                disposition=disposition,
                actions=actions,
                confidence=classification.confidence,
                latency_ms=latency_ms,
                notes=notes,
            )

            span.set_attribute("decision.exception_type", classification.exception_type.value)
            span.set_attribute("decision.disposition", disposition.value)
            span.set_attribute("decision.confidence", classification.confidence)

            if self._journal is not None:
                with _tracer.start_as_current_span("agent.persist"):
                    self._journal.record(shipment, event, resolution, self._model_name)
            return resolution

    def _decide(
        self, shipment: Shipment, c: Classification
    ) -> tuple[Disposition, list[ResolutionAction], str]:
        # Rule 1: severe exceptions always get a human, even if confident.
        if _SEVERITY_RANK[c.severity] >= self._always_escalate:
            return (
                Disposition.ESCALATED,
                [registry.flag_for_human(f"{c.severity.value} severity")],
                "Auto-escalated on severity policy.",
            )

        # Rule 2: low confidence => escalate rather than guess.
        if c.confidence < self._threshold:
            return (
                Disposition.ESCALATED,
                [registry.flag_for_human(f"confidence {c.confidence:.2f} below threshold")],
                "Escalated on low confidence.",
            )

        # Rule 3: confident and non-severe => auto-resolve via scoped tools.
        actions = self._resolve(shipment, c)
        return (Disposition.AUTO_RESOLVED, actions, "Auto-resolved by agent.")

    def _resolve(self, shipment: Shipment, c: Classification) -> list[ResolutionAction]:
        """Map an exception type to a concrete resolution playbook."""
        t = c.exception_type
        if t is ExceptionType.MISSING_DOCS:
            return [
                registry.request_missing_document(shipment, "commercial invoice"),
                registry.draft_carrier_email(shipment, t, "Required documentation missing."),
            ]
        if t is ExceptionType.ADDRESS_ISSUE:
            return [
                registry.update_shipment_status(shipment, "address_correction_pending"),
                registry.draft_carrier_email(shipment, t, "Consignee address needs correction."),
            ]
        if t is ExceptionType.DELAY:
            return [
                registry.update_shipment_status(shipment, "delayed"),
                registry.draft_carrier_email(shipment, t, "Please confirm revised ETA."),
            ]
        if t is ExceptionType.CUSTOMS_HOLD:
            return [
                registry.draft_carrier_email(shipment, t, "Customs hold — advise required documents."),
            ]
        # Fallback for any confident-but-unhandled type: still act conservatively.
        return [registry.draft_carrier_email(shipment, t, "Exception raised, investigating.")]
