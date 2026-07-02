"""Eval harness.

Runs the agent over a labelled synthetic dataset and computes the metrics that
matter for a supervised agent:

- classification_accuracy: did it identify the exception type correctly?
- escalation_recall: of the cases that SHOULD go to a human (critical severity
  or genuinely ambiguous), how many did it actually escalate? This is the safety
  metric — missing an escalation is the expensive error.
- false_auto_resolve_rate: how often did it confidently auto-resolve something
  it got wrong? This is the metric a hiring manager will care about, because in
  a real system a confident wrong action is worse than an escalation.
- mean_latency_ms, p95_latency_ms: performance.

The harness returns a structured EvalReport that Phase 3 will (a) print in CI and
(b) gate deploys on — you don't ship a model version that regresses these.
"""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field

from waybill.agent.agent import WaybillAgent
from waybill.core.models import Disposition, ExceptionType, Severity
from waybill.data.generator import SyntheticGenerator


@dataclass
class EvalReport:
    n: int
    classification_accuracy: float
    escalation_recall: float
    false_auto_resolve_rate: float
    mean_latency_ms: float
    p95_latency_ms: float
    by_type_accuracy: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def passes(self, thresholds: dict[str, float]) -> bool:
        """CI gate: every threshold must be satisfied.

        Thresholds are expressed as minimums except false_auto_resolve_rate,
        which is a maximum.
        """
        ok = True
        ok &= self.classification_accuracy >= thresholds.get("classification_accuracy", 0)
        ok &= self.escalation_recall >= thresholds.get("escalation_recall", 0)
        ok &= self.false_auto_resolve_rate <= thresholds.get("false_auto_resolve_rate", 1)
        return ok


def _should_escalate(true_type: ExceptionType, true_severity: Severity) -> bool:
    return true_severity is Severity.CRITICAL or true_type is ExceptionType.UNKNOWN


def run_eval(n: int = 200, seed: int = 7) -> EvalReport:
    gen = SyntheticGenerator(seed)
    dataset = gen.dataset(n)
    agent = WaybillAgent()

    correct = 0
    latencies: list[int] = []
    should_escalate = 0
    did_escalate_when_should = 0
    false_auto_resolve = 0

    per_type_total: dict[str, int] = {}
    per_type_correct: dict[str, int] = {}

    for shipment, event in dataset:
        res = agent.handle(shipment, event)
        latencies.append(res.latency_ms)

        true_type = event.true_type
        pred_type = res.classification.exception_type
        is_correct = pred_type is true_type
        correct += int(is_correct)

        key = true_type.value
        per_type_total[key] = per_type_total.get(key, 0) + 1
        per_type_correct[key] = per_type_correct.get(key, 0) + int(is_correct)

        needs_human = _should_escalate(true_type, event.true_severity)
        if needs_human:
            should_escalate += 1
            if res.disposition is Disposition.ESCALATED:
                did_escalate_when_should += 1

        # False auto-resolve: agent confidently acted, but got the type wrong.
        if res.disposition is Disposition.AUTO_RESOLVED and not is_correct:
            false_auto_resolve += 1

    n_auto = sum(1 for _ in latencies)  # total handled
    by_type = {
        k: per_type_correct[k] / per_type_total[k] for k in per_type_total
    }
    return EvalReport(
        n=n,
        classification_accuracy=correct / n,
        escalation_recall=(did_escalate_when_should / should_escalate) if should_escalate else 1.0,
        false_auto_resolve_rate=false_auto_resolve / n_auto if n_auto else 0.0,
        mean_latency_ms=statistics.mean(latencies) if latencies else 0.0,
        p95_latency_ms=(sorted(latencies)[int(0.95 * len(latencies)) - 1] if latencies else 0.0),
        by_type_accuracy={k: round(v, 3) for k, v in by_type.items()},
    )
