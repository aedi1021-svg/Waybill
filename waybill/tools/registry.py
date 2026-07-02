"""Resolution tools.

The narrow, explicitly-scoped set of actions the agent is allowed to take. In
production these would hit carrier APIs and an email service; here they're
deterministic mocks that produce structured action payloads. Keeping them behind
a small registry mirrors real agent tool-use and sets up Phase 2, where each tool
becomes a real integration with its own IAM permissions.

The key production idea: the agent never has ambient authority. It can only do
what's in this registry, and each tool returns a describable action rather than
performing an irreversible side effect silently.
"""
from __future__ import annotations

from waybill.core.models import ExceptionType, ResolutionAction, Shipment


def draft_carrier_email(shipment: Shipment, etype: ExceptionType, detail: str) -> ResolutionAction:
    subject = f"[{shipment.tracking_number}] {etype.value.replace('_', ' ').title()} — action required"
    body = (
        f"Hello {shipment.carrier} team,\n\n"
        f"We've flagged an exception on shipment {shipment.tracking_number} "
        f"({shipment.origin} -> {shipment.destination}).\n"
        f"Issue: {detail}\n\n"
        f"Please advise on resolution and updated ETA.\n\nThanks,\nWaybill Ops"
    )
    return ResolutionAction(
        kind="draft_carrier_email",
        summary=f"Drafted carrier email for {etype.value}",
        payload={"to": shipment.carrier, "subject": subject, "body": body},
    )


def update_shipment_status(shipment: Shipment, new_status: str) -> ResolutionAction:
    return ResolutionAction(
        kind="update_shipment_status",
        summary=f"Set status to '{new_status}'",
        payload={"shipment_id": shipment.id, "from": shipment.status, "to": new_status},
    )


def request_missing_document(shipment: Shipment, doc: str) -> ResolutionAction:
    return ResolutionAction(
        kind="request_missing_document",
        summary=f"Requested missing document: {doc}",
        payload={"shipment_id": shipment.id, "document": doc},
    )


def flag_for_human(reason: str) -> ResolutionAction:
    return ResolutionAction(
        kind="flag_for_human",
        summary="Escalated to human operator",
        payload={"reason": reason},
    )


# Registry = the agent's complete permission set. Nothing outside this is callable.
REGISTRY = {
    "draft_carrier_email": draft_carrier_email,
    "update_shipment_status": update_shipment_status,
    "request_missing_document": request_missing_document,
    "flag_for_human": flag_for_human,
}
