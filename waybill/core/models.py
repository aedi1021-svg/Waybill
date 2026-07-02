"""Core domain models for Waybill.

These are the shared vocabulary every other module speaks: what a shipment is,
what kinds of exceptions occur, and what the agent decides to do about them.
Kept deliberately small and dependency-light so both the agent and the eval
harness import from one source of truth.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class ExceptionType(str, Enum):
    """The kinds of things that go wrong with a shipment.

    This enum is the classifier's label space and the agent's routing key.
    Keep it stable — the eval set, the classifier, and the resolution
    playbooks are all keyed off these exact values.
    """

    DELAY = "delay"
    CUSTOMS_HOLD = "customs_hold"
    DAMAGED_GOODS = "damaged_goods"
    MISSING_DOCS = "missing_docs"
    ADDRESS_ISSUE = "address_issue"
    LOST = "lost"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Disposition(str, Enum):
    """What the agent decided to do with an exception."""

    AUTO_RESOLVED = "auto_resolved"      # agent handled it end to end
    ESCALATED = "escalated"              # low confidence -> human
    NEEDS_INFO = "needs_info"            # blocked on missing information


class Shipment(BaseModel):
    id: str = Field(default_factory=lambda: _id("shp"))
    tracking_number: str
    carrier: str
    origin: str
    destination: str
    status: str = "in_transit"
    eta: Optional[datetime] = None
    value_usd: float = 0.0
    customer: str = ""


class ExceptionEvent(BaseModel):
    """A raw problem signal as it arrives from a carrier.

    `raw_message` is the unstructured text the classifier reads. `true_type`
    is only populated in synthetic data (it's the ground-truth label the eval
    harness scores against); in production it would be absent.
    """

    id: str = Field(default_factory=lambda: _id("exc"))
    shipment_id: str
    tracking_number: str
    carrier: str
    raw_message: str
    received_at: datetime = Field(default_factory=_now)
    true_type: Optional[ExceptionType] = None      # ground truth, synthetic only
    true_severity: Optional[Severity] = None


class Classification(BaseModel):
    exception_type: ExceptionType
    severity: Severity
    confidence: float                                # 0..1
    rationale: str = ""


class ResolutionAction(BaseModel):
    """A concrete action the agent proposes or takes."""

    kind: str                                        # e.g. "draft_carrier_email"
    summary: str
    payload: dict = Field(default_factory=dict)


class Resolution(BaseModel):
    """The agent's full decision for one exception."""

    id: str = Field(default_factory=lambda: _id("res"))
    exception_id: str
    classification: Classification
    disposition: Disposition
    actions: list[ResolutionAction] = Field(default_factory=list)
    confidence: float
    decided_at: datetime = Field(default_factory=_now)
    latency_ms: int = 0
    notes: str = ""
