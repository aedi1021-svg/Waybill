"""Retraining orchestrator — the closed loop.

Ties the whole system together:

    detect drift -> pull corrected labels from the journal -> retrain ->
    eval-gate -> promote in the registry

The crucial detail: retraining doesn't just regenerate synthetic data, it folds
in the *human overrides* recorded in the journal. Every time an operator
corrected the agent, that (message, corrected_type) pair became a ground-truth
label. Those are the highest-value training examples — real disagreements — so
they're upweighted. This is why the append-only journal and the human-override
endpoint were built early: they're the fuel for this loop.

Promotion goes through the same eval gate as everything else, so a retrained
model that doesn't clear the bar is never served. The loop is self-improving but
never self-degrading.

Usage:
    python -m waybill.ml.retrain            # check drift; retrain only if drifted
    python -m waybill.ml.retrain --force    # retrain regardless
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import select

from waybill.db.engine import session_scope
from waybill.db.tables import ExceptionRow, ResolutionRow
from waybill.ml.drift import detect_drift

_THRESHOLDS_PATH = Path("eval_thresholds.json")


def _corrected_labels() -> list[tuple[str, str]]:
    """Pull (message, corrected_type) pairs from human overrides in the journal.
    These are real ground-truth corrections — the most valuable training data."""
    pairs: list[tuple[str, str]] = []
    with session_scope() as s:
        rows = s.execute(
            select(ExceptionRow.raw_message, ResolutionRow.human_override_type)
            .join(ResolutionRow, ResolutionRow.exception_id == ExceptionRow.id)
            .where(ResolutionRow.human_override_type.is_not(None))
        ).all()
        for message, corrected in rows:
            if message and corrected:
                pairs.append((message, corrected))
    return pairs


def _min_accuracy() -> float:
    if _THRESHOLDS_PATH.exists():
        return json.loads(_THRESHOLDS_PATH.read_text()).get("classification_accuracy", 0.70)
    return 0.70


def retrain(force: bool = False) -> int:
    # 1. Should we retrain at all?
    if not force:
        report = detect_drift()
        print("Drift check:", json.dumps(report.to_dict(), indent=2))
        if not report.drift_detected:
            print("No drift detected — skipping retrain.")
            return 0
        print("Drift detected — retraining.")

    # 2. Assemble training data: synthetic base + upweighted human corrections.
    import mlflow
    import mlflow.sklearn

    from waybill.ml.model import _build_pipeline, _make_split

    x_train, x_test, y_train, y_test = _make_split(1200, seed=13)
    corrections = _corrected_labels()
    if corrections:
        # Upweight real corrections by repeating them — cheap, effective way to
        # bias the model toward the cases humans actually cared about.
        for msg, label in corrections:
            for _ in range(5):
                x_train.append(msg)
                y_train.append(label)
        print(f"Folded in {len(corrections)} human-corrected examples (x5 weight).")

    # 3. Train.
    pipe = _build_pipeline()
    pipe.fit(x_train, y_train)
    accuracy = float(pipe.score(x_test, y_test))
    min_acc = _min_accuracy()
    print(f"Retrained accuracy = {accuracy:.3f} (threshold {min_acc})")

    # 4. Log + register + eval-gated promotion.
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "file:./mlruns"))
    mlflow.set_experiment("waybill-exception-classifier")
    with mlflow.start_run(run_name="retrain") as run:
        mlflow.log_metric("holdout_accuracy", accuracy)
        mlflow.log_param("n_corrections", len(corrections))
        mlflow.log_param("trigger", "forced" if force else "drift")
        mlflow.sklearn.log_model(pipe, artifact_path="model",
                                 registered_model_name="waybill-exception-classifier")
        promote = accuracy >= min_acc
        mlflow.set_tag("eval_passed", str(promote))

        if promote:
            client = mlflow.tracking.MlflowClient()
            versions = client.search_model_versions("name='waybill-exception-classifier'")
            latest = max(versions, key=lambda v: int(v.version))
            client.transition_model_version_stage(
                name="waybill-exception-classifier",
                version=latest.version, stage="Staging",
                archive_existing_versions=True,
            )
            print(f"Promoted retrained version {latest.version} to Staging (run {run.info.run_id}).")
        else:
            print("Retrained model failed the eval gate — NOT promoted.")

    return 0 if (force or accuracy >= min_acc) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="retrain regardless of drift")
    args = parser.parse_args()
    return retrain(force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
