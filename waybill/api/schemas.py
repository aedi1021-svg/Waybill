"""API request/response schemas.

Kept separate from the core domain models so the HTTP contract can evolve
independently of the internal models. These are what the outside world sees:
the queue consumer, the dashboard, and any client posting exceptions.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SubmitExceptionRequest(BaseModel):
    """An incoming exception to be handled. Mirrors what a carrier webhook or
    the queue consumer would post. Shipment fields are inline so a caller can
    submit a brand-new shipment and its exception in one request."""

    tracking_number: str
    carrier: str
    raw_message: str
    origin: str = ""
    destination: str = ""
    value_usd: float = 0.0
    customer: str = ""
    # Optional ground-truth labels (synthetic/testing only).
    true_type: Optional[str] = None
    true_severity: Optional[str] = None


class ActionOut(BaseModel):
    kind: str
    summary: str
    payload: dict = Field(default_factory=dict)


class ResolutionOut(BaseModel):
    """The agent's decision, returned to the caller and stored in the journal."""

    resolution_id: str
    exception_id: str
    exception_type: str
    severity: str
    confidence: float
    disposition: str
    actions: list[ActionOut]
    latency_ms: int
    decided_at: datetime


class JournalEntryOut(BaseModel):
    resolution_id: str
    exception_type: str
    severity: str
    confidence: float
    disposition: str
    model_name: str
    decided_at: datetime
    human_override_type: Optional[str] = None


class HealthOut(BaseModel):
    status: str
    llm_available: bool
    db_reachable: bool


class OverrideRequest(BaseModel):
    corrected_type: str
