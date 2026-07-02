"""SQLAlchemy table definitions.

Four tables, deliberately shaped as an append-only audit log:

  shipments          current state (this one CAN be updated as status evolves)
  exceptions         immutable — the raw problem exactly as it arrived
  resolutions        immutable — one row per agent decision
  resolution_actions immutable — the concrete actions attached to a resolution

Nothing in exceptions / resolutions / resolution_actions is ever updated or
deleted. History is the product: it's the audit trail, and later the training
data for drift detection and retraining. JSON columns hold the flexible bits
(action payloads) without needing a migration every time an action shape changes.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from waybill.db.engine import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ShipmentRow(Base):
    __tablename__ = "shipments"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tracking_number: Mapped[str] = mapped_column(String(32), index=True)
    carrier: Mapped[str] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(64))
    destination: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(48), default="in_transit")
    value_usd: Mapped[float] = mapped_column(Float, default=0.0)
    customer: Mapped[str] = mapped_column(String(128), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    exceptions: Mapped[list["ExceptionRow"]] = relationship(back_populates="shipment")


class ExceptionRow(Base):
    """Immutable: the raw exception as received. Never updated."""

    __tablename__ = "exceptions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("shipments.id"), index=True)
    tracking_number: Mapped[str] = mapped_column(String(32), index=True)
    carrier: Mapped[str] = mapped_column(String(64))
    raw_message: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Ground-truth labels — populated for synthetic data, used by the eval/
    # drift tooling. Absent in real production traffic.
    true_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    true_severity: Mapped[str | None] = mapped_column(String(16), nullable=True)

    shipment: Mapped["ShipmentRow"] = relationship(back_populates="exceptions")
    resolutions: Mapped[list["ResolutionRow"]] = relationship(back_populates="exception")


class ResolutionRow(Base):
    """Immutable: one agent decision. Append-only."""

    __tablename__ = "resolutions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    exception_id: Mapped[str] = mapped_column(ForeignKey("exceptions.id"), index=True)

    exception_type: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    disposition: Mapped[str] = mapped_column(String(24), index=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    # Model provenance — which classifier/model produced this decision. Critical
    # for drift analysis later ("did accuracy drop after we changed the model?").
    model_name: Mapped[str] = mapped_column(String(64), default="")
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    # Human override, filled in later if an operator corrects the agent. This is
    # the label the retraining loop learns from. Null = not yet reviewed.
    human_override_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    exception: Mapped["ExceptionRow"] = relationship(back_populates="resolutions")
    actions: Mapped[list["ResolutionActionRow"]] = relationship(back_populates="resolution")


class ResolutionActionRow(Base):
    """Immutable: a concrete action taken as part of a resolution."""

    __tablename__ = "resolution_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resolution_id: Mapped[str] = mapped_column(ForeignKey("resolutions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(48))
    summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    resolution: Mapped["ResolutionRow"] = relationship(back_populates="actions")
