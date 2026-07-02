"""DHL tracking poller — the bridge from real shipments to the agent.

This is the piece that turns a real tracking number into an exception the agent
can triage. It:

  1. Calls DHL's Shipment Tracking - Unified API for a tracking number.
  2. Inspects the returned status + event history for anomaly signals
     (delayed, customs hold, failed delivery, no movement, exception flags).
  3. If something looks wrong, synthesises an exception message and hands it to
     the agent — exactly the same ExceptionEvent shape the agent already handles.

Honest scope:
  - This is READ-ONLY. It reads DHL's public tracking status. It cannot act on
    DHL's systems (that needs a business account), so the agent's "resolution"
    is still a drafted action, not a real change to the shipment.
  - The DHL API is free but needs a key (register at developer.dhl.com — 250
    calls/day testing quota). Set DHL_API_KEY. Without it, mock mode returns a
    canned "delayed" response so you can see the full flow with no key.
  - Anomaly detection here is heuristic keyword/status matching on the tracking
    status, not ML — the ML is downstream, in the agent's classifier.

Usage:
    DHL_API_KEY=... python -m waybill.integrations.dhl_poller <tracking_number>
    python -m waybill.integrations.dhl_poller MOCK        # no key, canned data
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional

import httpx

from waybill.core.models import ExceptionEvent, Shipment

_DHL_URL = "https://api-eu.dhl.com/track/shipments"

# Status/description keywords that signal an anomaly worth triaging. If a
# tracking status contains any of these, we raise an exception for the agent.
_ANOMALY_SIGNALS = [
    "delay", "delayed", "customs", "held", "hold", "exception",
    "failed", "return", "undeliverable", "address", "damaged", "lost",
    "not delivered", "attempted",
]


@dataclass
class TrackingResult:
    tracking_number: str
    status: str
    description: str
    anomaly: bool
    raw: dict


class DHLPoller:
    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or os.getenv("DHL_API_KEY", "")

    def poll(self, tracking_number: str) -> TrackingResult:
        """Fetch tracking status for one number. Falls back to mock data if no
        key is set or the tracking_number is the literal 'MOCK'."""
        if tracking_number == "MOCK" or not self.api_key:
            return self._mock(tracking_number)

        headers = {"DHL-API-Key": self.api_key}
        params = {"trackingNumber": tracking_number}
        try:
            r = httpx.get(_DHL_URL, headers=headers, params=params, timeout=15.0)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            # Network/auth/not-found: surface as a result rather than crashing.
            return TrackingResult(
                tracking_number=tracking_number,
                status="lookup_failed",
                description=f"Could not fetch tracking: {exc}",
                anomaly=False,
                raw={"error": str(exc)},
            )
        return self._parse(tracking_number, data)

    def _parse(self, tn: str, data: dict) -> TrackingResult:
        shipments = data.get("shipments", [])
        if not shipments:
            return TrackingResult(tn, "not_found", "No shipment found", False, data)
        shp = shipments[0]
        status_obj = shp.get("status", {})
        status = str(status_obj.get("statusCode", status_obj.get("status", "unknown")))
        description = str(status_obj.get("description", ""))
        # Look across the status description and recent events for anomaly words.
        haystack = (status + " " + description).lower()
        events = shp.get("events", [])
        for ev in events[:5]:
            haystack += " " + str(ev.get("description", "")).lower()
        anomaly = any(sig in haystack for sig in _ANOMALY_SIGNALS)
        return TrackingResult(tn, status, description or status, anomaly, data)

    def _mock(self, tn: str) -> TrackingResult:
        """Canned 'delayed' shipment so the full flow is visible without a key."""
        return TrackingResult(
            tracking_number=tn if tn != "MOCK" else "MOCK123456789",
            status="transit",
            description="Delay in transit — shipment held at sort facility, awaiting customs clearance",
            anomaly=True,
            raw={"mock": True},
        )


def to_exception(result: TrackingResult) -> tuple[Shipment, ExceptionEvent]:
    """Convert an anomalous tracking result into the (shipment, exception) pair
    the agent already knows how to handle. The tracking description becomes the
    raw_message the classifier reads."""
    shipment = Shipment(
        tracking_number=result.tracking_number,
        carrier="DHL",
        origin="",
        destination="",
        status=result.status,
    )
    event = ExceptionEvent(
        shipment_id=shipment.id,
        tracking_number=result.tracking_number,
        carrier="DHL",
        raw_message=result.description,
    )
    return shipment, event


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m waybill.integrations.dhl_poller <tracking_number|MOCK>")
        return 2
    tn = sys.argv[1]

    poller = DHLPoller()
    result = poller.poll(tn)
    key_note = "live DHL API" if poller.api_key and tn != "MOCK" else "MOCK mode (no API key)"
    print(f"[{key_note}] tracking {result.tracking_number}")
    print(f"  status: {result.status}")
    print(f"  description: {result.description}")
    print(f"  anomaly detected: {result.anomaly}")

    if not result.anomaly:
        print("  -> no anomaly; nothing to triage.")
        return 0

    # Anomaly found -> hand it to the agent.
    from waybill.agent.agent import WaybillAgent

    shipment, event = to_exception(result)
    agent = WaybillAgent()
    resolution = agent.handle(shipment, event)
    print("  -> anomaly handed to agent:")
    print(f"     classified as: {resolution.classification.exception_type.value} "
          f"({resolution.classification.severity.value}, "
          f"conf={resolution.confidence:.2f})")
    print(f"     disposition: {resolution.disposition.value}")
    for a in resolution.actions:
        print(f"     action: {a.kind} — {a.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
