"""Drift detection.

Watches the agent's live behaviour (recorded in the journal) for signs the model
has degraded, and decides whether a retrain is warranted. Three complementary
signals, because no single one is reliable on its own:

1. Confidence drift — the mean classifier confidence over recent decisions drops
   below a floor. The earliest, cheapest signal: the model is getting unsure.
2. Escalation-rate drift — the share of decisions being escalated climbs above a
   ceiling. Behavioural proxy for "the model can't handle the current traffic."
3. Human-override rate — of decisions a human reviewed, how many they corrected.
   The ground-truth signal: operators are actively disagreeing with the agent.

Each signal is compared against a configured bound; any breach flags drift. The
override rate is weighted most heavily because it's the only one grounded in
real human labels rather than the model's own self-report.

This reads from the journal, so it works on real production decisions — the
append-only audit log built in the DB layer is exactly what makes this possible.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import func, select

from waybill.db.engine import session_scope
from waybill.db.tables import ResolutionRow


@dataclass
class DriftReport:
    n_recent: int
    mean_confidence: float
    escalation_rate: float
    override_rate: float
    n_reviewed: int
    drift_detected: bool
    reasons: list[str]

    def to_dict(self) -> dict:
        return {
            "n_recent": self.n_recent,
            "mean_confidence": round(self.mean_confidence, 3),
            "escalation_rate": round(self.escalation_rate, 3),
            "override_rate": round(self.override_rate, 3),
            "n_reviewed": self.n_reviewed,
            "drift_detected": self.drift_detected,
            "reasons": self.reasons,
        }


@dataclass(frozen=True)
class DriftThresholds:
    min_mean_confidence: float = float(os.getenv("DRIFT_MIN_CONFIDENCE", "0.70"))
    max_escalation_rate: float = float(os.getenv("DRIFT_MAX_ESCALATION_RATE", "0.45"))
    max_override_rate: float = float(os.getenv("DRIFT_MAX_OVERRIDE_RATE", "0.15"))
    # Don't cry drift on tiny samples.
    min_sample: int = int(os.getenv("DRIFT_MIN_SAMPLE", "50"))


def detect_drift(window: int = 500, thresholds: DriftThresholds | None = None) -> DriftReport:
    """Analyse the most recent `window` decisions for drift."""
    th = thresholds or DriftThresholds()

    with session_scope() as s:
        rows = s.scalars(
            select(ResolutionRow).order_by(ResolutionRow.decided_at.desc()).limit(window)
        ).all()
        n = len(rows)
        if n == 0:
            return DriftReport(0, 0.0, 0.0, 0.0, 0, False, ["no data"])

        mean_conf = sum(r.confidence for r in rows) / n
        escalated = sum(1 for r in rows if r.disposition == "escalated")
        escalation_rate = escalated / n

        reviewed = [r for r in rows if r.reviewed_at is not None]
        overridden = sum(1 for r in reviewed if r.human_override_type is not None)
        override_rate = (overridden / len(reviewed)) if reviewed else 0.0

    reasons: list[str] = []
    if n < th.min_sample:
        return DriftReport(n, mean_conf, escalation_rate, override_rate,
                           len(reviewed), False, [f"sample {n} < {th.min_sample}"])

    if mean_conf < th.min_mean_confidence:
        reasons.append(f"mean confidence {mean_conf:.2f} < {th.min_mean_confidence}")
    if escalation_rate > th.max_escalation_rate:
        reasons.append(f"escalation rate {escalation_rate:.2f} > {th.max_escalation_rate}")
    if reviewed and override_rate > th.max_override_rate:
        reasons.append(f"override rate {override_rate:.2f} > {th.max_override_rate}")

    return DriftReport(
        n_recent=n,
        mean_confidence=mean_conf,
        escalation_rate=escalation_rate,
        override_rate=override_rate,
        n_reviewed=len(reviewed),
        drift_detected=bool(reasons),
        reasons=reasons or ["within bounds"],
    )
