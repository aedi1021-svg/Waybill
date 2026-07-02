"""Exception classifier.

Turns a raw carrier message into a typed, severity-scored Classification with a
confidence. Two paths:

- LLM path (Ollama): prompts the model for structured JSON.
- Heuristic fallback: keyword rules. Runs when the LLM is unavailable, and also
  serves as a cheap sanity baseline the eval harness can compare the LLM against.

The heuristic being a real baseline (not just a stub) is deliberate: in MLOps you
always want a dumb baseline to know whether the expensive model is actually
earning its keep.
"""
from __future__ import annotations

import re

from waybill.agent.llm import LLMUnavailable, OllamaClient
from waybill.core.models import Classification, ExceptionType, Severity

_SYSTEM = (
    "You are a logistics exception classifier. Given a carrier message about a "
    "freight shipment, identify the exception type and severity. Respond ONLY "
    "with JSON."
)

_PROMPT = """Classify this freight exception message.

Message: "{message}"

Valid exception_type values: delay, customs_hold, damaged_goods, missing_docs,
address_issue, lost, unknown.
Valid severity values: low, medium, high, critical.

If the message is vague or you are not confident, use "unknown" and a low
confidence. Return JSON with keys: exception_type, severity, confidence
(0.0-1.0), rationale (one short sentence).
"""

# Keyword -> type rules for the fallback. Order matters (first hit wins).
_RULES: list[tuple[re.Pattern, ExceptionType]] = [
    (re.compile(r"customs|clearance|import hold|duties", re.I), ExceptionType.CUSTOMS_HOLD),
    (re.compile(r"damage|crushed|leak|compromised|broken", re.I), ExceptionType.DAMAGED_GOODS),
    (re.compile(r"invoice|certificate|bill of lading|document|paperwork", re.I), ExceptionType.MISSING_DOCS),
    (re.compile(r"address|consignee|recipient .*found|unit number", re.I), ExceptionType.ADDRESS_ISSUE),
    (re.compile(r"lost|missing|no scan|unable to locate|tracing", re.I), ExceptionType.LOST),
    (re.compile(r"delay|behind schedule|congestion|miss.*window|weather", re.I), ExceptionType.DELAY),
]

_SEVERITY_KEYWORDS = {
    Severity.CRITICAL: re.compile(r"lost|critical|leak|total loss", re.I),
    Severity.HIGH: re.compile(r"damage|customs|missing|inspection", re.I),
    Severity.MEDIUM: re.compile(r"delay|address|document", re.I),
}


class Classifier:
    def __init__(self, llm: OllamaClient | None = None) -> None:
        self._llm = llm or OllamaClient()

    def classify(self, message: str) -> Classification:
        try:
            return self._classify_llm(message)
        except (LLMUnavailable, KeyError, ValueError):
            return self._classify_heuristic(message)

    def _classify_llm(self, message: str) -> Classification:
        data = self._llm.generate_json(
            _PROMPT.format(message=message), system=_SYSTEM
        )
        return Classification(
            exception_type=ExceptionType(data["exception_type"]),
            severity=Severity(data["severity"]),
            confidence=float(data["confidence"]),
            rationale=str(data.get("rationale", "")),
        )

    def _classify_heuristic(self, message: str) -> Classification:
        etype = ExceptionType.UNKNOWN
        for pattern, mapped in _RULES:
            if pattern.search(message):
                etype = mapped
                break

        severity = Severity.LOW
        for sev, pattern in _SEVERITY_KEYWORDS.items():
            if pattern.search(message):
                severity = sev
                break

        # Unknown type => low confidence, which routes to escalation downstream.
        confidence = 0.55 if etype is ExceptionType.UNKNOWN else 0.8
        return Classification(
            exception_type=etype,
            severity=severity,
            confidence=confidence,
            rationale="heuristic keyword match",
        )
