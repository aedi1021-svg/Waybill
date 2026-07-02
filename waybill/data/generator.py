"""Synthetic data generator for freight exceptions.

Produces realistic-looking shipments and the messy carrier messages that signal
an exception. Every generated event carries its ground-truth type and severity,
which the eval harness uses to score the classifier and agent. The messages are
deliberately noisy and templated with variation so the classifier has to do real
work rather than match a fixed string.

Design notes for the portfolio story:
- Seeded RNG => reproducible datasets (important for eval stability).
- Class balance is configurable so you can stress rare exception types.
- Some messages are intentionally ambiguous to exercise the escalation path.
"""
from __future__ import annotations

import random
from datetime import timedelta
from typing import Iterable

from waybill.core.models import (
    ExceptionEvent,
    ExceptionType,
    Severity,
    Shipment,
    _now,
)

_CARRIERS = ["Maersk", "DHL", "FedEx", "UPS", "DBSchenker", "Kuehne+Nagel"]
_CITIES = [
    "Shanghai", "Rotterdam", "Los Angeles", "Hamburg", "Singapore",
    "Sydney", "Dubai", "Newark", "Felixstowe", "Busan",
]
_CUSTOMERS = ["Acme Retail", "Northwind", "Globex", "Initech", "Umbrella Foods"]

# Message templates per exception type. {tn} = tracking number, {c} = carrier.
# Multiple templates per type + word-level variation forces the classifier to
# generalize rather than memorize.
_TEMPLATES: dict[ExceptionType, list[str]] = {
    ExceptionType.DELAY: [
        "Shipment {tn} delayed due to weather at transit hub. New ETA pending.",
        "Heads up: {tn} is running behind schedule, congestion at port.",
        "{c} notice: consignment {tn} will miss its scheduled departure window.",
    ],
    ExceptionType.CUSTOMS_HOLD: [
        "{tn} held by customs pending inspection. Awaiting clearance.",
        "Customs authority has flagged shipment {tn} for documentation review.",
        "{c}: import hold placed on {tn}, duties/paperwork query raised.",
    ],
    ExceptionType.DAMAGED_GOODS: [
        "Driver reports visible damage to cartons on {tn} at delivery scan.",
        "{tn}: pallet compromised, product leakage observed during handling.",
        "Damage exception logged for {tn} — crushed packaging, contents affected.",
    ],
    ExceptionType.MISSING_DOCS: [
        "Cannot proceed with {tn}: commercial invoice missing from packet.",
        "{c} flag: {tn} lacks required certificate of origin.",
        "Bill of lading not attached to {tn}, shipment cannot be released.",
    ],
    ExceptionType.ADDRESS_ISSUE: [
        "Delivery failed for {tn}: address incomplete, no unit number.",
        "{tn} returned to depot — recipient address not found.",
        "{c}: consignee address for {tn} appears invalid, needs correction.",
    ],
    ExceptionType.LOST: [
        "No scan events for {tn} in 6 days, possible loss in transit.",
        "{c} unable to locate consignment {tn}, tracing initiated.",
        "{tn} reported missing after transfer between facilities.",
    ],
    # Ambiguous messages -> should trigger low confidence / escalation.
    ExceptionType.UNKNOWN: [
        "{tn}: exception raised, reason code 99, see carrier portal.",
        "Something went wrong with {tn}, {c} did not specify.",
        "Manual review requested for {tn}, details unclear.",
    ],
}

_SEVERITY_BIAS: dict[ExceptionType, list[Severity]] = {
    ExceptionType.DELAY: [Severity.LOW, Severity.MEDIUM],
    ExceptionType.CUSTOMS_HOLD: [Severity.MEDIUM, Severity.HIGH],
    ExceptionType.DAMAGED_GOODS: [Severity.HIGH, Severity.CRITICAL],
    ExceptionType.MISSING_DOCS: [Severity.MEDIUM, Severity.HIGH],
    ExceptionType.ADDRESS_ISSUE: [Severity.LOW, Severity.MEDIUM],
    ExceptionType.LOST: [Severity.HIGH, Severity.CRITICAL],
    ExceptionType.UNKNOWN: [Severity.MEDIUM, Severity.HIGH],
}


class SyntheticGenerator:
    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)

    def _shipment(self) -> Shipment:
        origin, dest = self._rng.sample(_CITIES, 2)
        return Shipment(
            tracking_number=self._tracking_number(),
            carrier=self._rng.choice(_CARRIERS),
            origin=origin,
            destination=dest,
            value_usd=round(self._rng.uniform(500, 90_000), 2),
            customer=self._rng.choice(_CUSTOMERS),
            eta=_now() + timedelta(days=self._rng.randint(2, 21)),
        )

    def _tracking_number(self) -> str:
        letters = "".join(self._rng.choices("ABCDEFGHJKLMNPQRSTUVWXYZ", k=3))
        digits = "".join(self._rng.choices("0123456789", k=9))
        return f"{letters}{digits}"

    def exception(self, etype: ExceptionType | None = None) -> tuple[Shipment, ExceptionEvent]:
        shp = self._shipment()
        etype = etype or self._rng.choice(list(ExceptionType))
        template = self._rng.choice(_TEMPLATES[etype])
        message = template.format(tn=shp.tracking_number, c=shp.carrier)
        severity = self._rng.choice(_SEVERITY_BIAS[etype])
        event = ExceptionEvent(
            shipment_id=shp.id,
            tracking_number=shp.tracking_number,
            carrier=shp.carrier,
            raw_message=message,
            true_type=etype,
            true_severity=severity,
        )
        return shp, event

    def dataset(self, n: int, balanced: bool = True) -> list[tuple[Shipment, ExceptionEvent]]:
        """Generate n (shipment, exception) pairs.

        balanced=True cycles evenly through exception types so the eval set
        isn't dominated by whatever the RNG favours.
        """
        out: list[tuple[Shipment, ExceptionEvent]] = []
        types = list(ExceptionType)
        for i in range(n):
            etype = types[i % len(types)] if balanced else None
            out.append(self.exception(etype))
        self._rng.shuffle(out)
        return out


def iter_dataset(n: int, seed: int = 42) -> Iterable[tuple[Shipment, ExceptionEvent]]:
    yield from SyntheticGenerator(seed).dataset(n)
