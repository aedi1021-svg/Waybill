"""Repository layer.

The only module that knows how to turn domain models (waybill.core.models) into
database rows and back. The agent and API depend on this interface, never on
SQLAlchemy directly — so persistence stays swappable and the domain logic stays
clean. Writes are append-only for exceptions/resolutions/actions; shipments are
upserted because their current status legitimately evolves.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from waybill.core.models import ExceptionEvent, Resolution, Shipment
from waybill.db.engine import session_scope
from waybill.db.tables import (
    ExceptionRow,
    ResolutionActionRow,
    ResolutionRow,
    ShipmentRow,
)


class Journal:
    """High-level persistence API for the exception -> resolution audit trail."""

    def record(
        self,
        shipment: Shipment,
        event: ExceptionEvent,
        resolution: Resolution,
        model_name: str = "",
    ) -> None:
        """Persist one full handled exception in a single transaction.

        Upserts the shipment (state may have changed), then appends the
        immutable exception, resolution, and action rows.
        """
        with session_scope() as s:
            self._upsert_shipment(s, shipment)
            self._insert_exception(s, event)
            self._insert_resolution(s, resolution, model_name)

    # --- writes ---

    def _upsert_shipment(self, s: Session, shp: Shipment) -> None:
        row = s.get(ShipmentRow, shp.id)
        if row is None:
            s.add(
                ShipmentRow(
                    id=shp.id,
                    tracking_number=shp.tracking_number,
                    carrier=shp.carrier,
                    origin=shp.origin,
                    destination=shp.destination,
                    status=shp.status,
                    value_usd=shp.value_usd,
                    customer=shp.customer,
                )
            )
        else:
            row.status = shp.status  # only mutable field we track here

    def _insert_exception(self, s: Session, e: ExceptionEvent) -> None:
        if s.get(ExceptionRow, e.id) is not None:
            return  # already recorded; exceptions are immutable
        s.add(
            ExceptionRow(
                id=e.id,
                shipment_id=e.shipment_id,
                tracking_number=e.tracking_number,
                carrier=e.carrier,
                raw_message=e.raw_message,
                received_at=e.received_at,
                true_type=e.true_type.value if e.true_type else None,
                true_severity=e.true_severity.value if e.true_severity else None,
            )
        )

    def _insert_resolution(self, s: Session, r: Resolution, model_name: str) -> None:
        s.add(
            ResolutionRow(
                id=r.id,
                exception_id=r.exception_id,
                exception_type=r.classification.exception_type.value,
                severity=r.classification.severity.value,
                confidence=r.confidence,
                disposition=r.disposition.value,
                rationale=r.classification.rationale,
                notes=r.notes,
                latency_ms=r.latency_ms,
                model_name=model_name,
                decided_at=r.decided_at,
            )
        )
        for a in r.actions:
            s.add(
                ResolutionActionRow(
                    resolution_id=r.id,
                    kind=a.kind,
                    summary=a.summary,
                    payload=a.payload,
                )
            )

    # --- reads (used by the API and, later, drift tooling) ---

    def recent_resolutions(self, limit: int = 50) -> list[ResolutionRow]:
        with session_scope() as s:
            rows = s.scalars(
                select(ResolutionRow).order_by(ResolutionRow.decided_at.desc()).limit(limit)
            ).all()
            # Detach so callers can read after the session closes.
            for r in rows:
                s.expunge(r)
            return list(rows)

    def resolutions_for_shipment(self, shipment_id: str) -> list[ResolutionRow]:
        with session_scope() as s:
            rows = s.scalars(
                select(ResolutionRow)
                .join(ExceptionRow, ResolutionRow.exception_id == ExceptionRow.id)
                .where(ExceptionRow.shipment_id == shipment_id)
                .order_by(ResolutionRow.decided_at.desc())
            ).all()
            for r in rows:
                s.expunge(r)
            return list(rows)

    def record_human_override(self, resolution_id: str, corrected_type: str) -> None:
        """Operator corrects the agent. This is the label the retrain loop uses."""
        with session_scope() as s:
            row = s.get(ResolutionRow, resolution_id)
            if row is None:
                raise KeyError(resolution_id)
            row.human_override_type = corrected_type
            row.reviewed_at = datetime.now(timezone.utc)
