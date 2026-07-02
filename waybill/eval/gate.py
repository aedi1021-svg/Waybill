"""Eval gate.

Runs the eval harness and checks the result against committed thresholds. Exits
non-zero if any threshold is violated, which fails the CI job and blocks the
deploy. This is what makes "eval-gated deployment" real: you cannot ship a model
or prompt change that regresses the metrics that matter.

Thresholds live in eval_thresholds.json (version-controlled), so tightening the
bar is a reviewable commit, and the history of that file is a record of how the
quality bar moved over time.

Usage:
    python -m waybill.eval.gate                 # uses eval_thresholds.json
    python -m waybill.eval.gate --n 500         # bigger eval set
    python -m waybill.eval.gate --write-baseline  # record current metrics as baseline
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from waybill.eval.harness import run_eval

_THRESHOLDS_PATH = Path("eval_thresholds.json")

_DEFAULT_THRESHOLDS = {
    "classification_accuracy": 0.70,
    "escalation_recall": 0.90,
    "false_auto_resolve_rate": 0.10,
}


def _load_thresholds() -> dict:
    if _THRESHOLDS_PATH.exists():
        return json.loads(_THRESHOLDS_PATH.read_text())
    return dict(_DEFAULT_THRESHOLDS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=300)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()

    report = run_eval(n=args.n, seed=args.seed)
    thresholds = _load_thresholds()

    print("Eval report:")
    print(json.dumps(report.to_dict(), indent=2))
    print("\nThresholds:")
    print(json.dumps(thresholds, indent=2))

    if args.write_baseline:
        _THRESHOLDS_PATH.write_text(json.dumps(thresholds, indent=2) + "\n")
        print(f"\nWrote baseline thresholds to {_THRESHOLDS_PATH}")
        return 0

    passed = report.passes(thresholds)

    # Print a per-metric verdict so CI logs show exactly what failed.
    checks = [
        ("classification_accuracy", report.classification_accuracy,
         thresholds.get("classification_accuracy", 0), ">="),
        ("escalation_recall", report.escalation_recall,
         thresholds.get("escalation_recall", 0), ">="),
        ("false_auto_resolve_rate", report.false_auto_resolve_rate,
         thresholds.get("false_auto_resolve_rate", 1), "<="),
    ]
    print("\nGate:")
    for name, actual, bound, op in checks:
        ok = actual >= bound if op == ">=" else actual <= bound
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} = {actual:.3f} {op} {bound}")

    if not passed:
        print("\nEVAL GATE FAILED — deploy blocked.")
        return 1
    print("\nEval gate passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
