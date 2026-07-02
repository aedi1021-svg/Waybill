"""Tests for the agent decision logic.

These run without Ollama (they exercise the heuristic path and the confidence
gate), so they're CI-safe from day one.
"""
from __future__ import annotations

from waybill.agent.agent import WaybillAgent
from waybill.agent.classifier import Classifier
from waybill.core.models import (
    Classification,
    Disposition,
    ExceptionEvent,
    ExceptionType,
    Severity,
    Shipment,
)


class StubClassifier(Classifier):
    """Lets tests inject a fixed classification to probe the gate."""

    def __init__(self, classification: Classification) -> None:
        self._fixed = classification

    def classify(self, message: str) -> Classification:  # type: ignore[override]
        return self._fixed


def _shipment() -> Shipment:
    return Shipment(tracking_number="ABC123456789", carrier="DHL",
                    origin="Shanghai", destination="Hamburg")


def _event() -> ExceptionEvent:
    return ExceptionEvent(shipment_id="shp_x", tracking_number="ABC123456789",
                          carrier="DHL", raw_message="test")


def test_low_confidence_escalates():
    c = Classification(exception_type=ExceptionType.DELAY, severity=Severity.LOW,
                       confidence=0.40)
    agent = WaybillAgent(classifier=StubClassifier(c))
    res = agent.handle(_shipment(), _event())
    assert res.disposition is Disposition.ESCALATED


def test_critical_severity_always_escalates_even_if_confident():
    c = Classification(exception_type=ExceptionType.LOST, severity=Severity.CRITICAL,
                       confidence=0.99)
    agent = WaybillAgent(classifier=StubClassifier(c))
    res = agent.handle(_shipment(), _event())
    assert res.disposition is Disposition.ESCALATED


def test_confident_non_severe_auto_resolves():
    c = Classification(exception_type=ExceptionType.DELAY, severity=Severity.LOW,
                       confidence=0.92)
    agent = WaybillAgent(classifier=StubClassifier(c))
    res = agent.handle(_shipment(), _event())
    assert res.disposition is Disposition.AUTO_RESOLVED
    assert any(a.kind == "draft_carrier_email" for a in res.actions)


def test_heuristic_classifier_detects_customs():
    # Real heuristic path, no Ollama needed.
    c = Classifier(llm=_NoLLM())
    result = c.classify("Shipment held by customs pending inspection")
    assert result.exception_type is ExceptionType.CUSTOMS_HOLD


class _NoLLM:
    """LLM double that is always unavailable, forcing the heuristic path."""

    def generate_json(self, *a, **k):
        from waybill.agent.llm import LLMUnavailable
        raise LLMUnavailable("disabled in test")
