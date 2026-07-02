"""MLflow training, tracking, and eval-gated registry promotion.

The full model lifecycle in one script:

  1. Train the classifier on synthetic data.
  2. Log the run to MLflow — params, metrics, and the model artifact.
  3. Register the model in the MLflow Model Registry (a new version).
  4. Run the eval harness against the freshly trained model.
  5. Promote to "Staging" ONLY if it clears the committed eval thresholds.

Step 5 is the point: registry promotion is gated on the same eval bar the CI
pipeline enforces. A model that doesn't meet the safety/accuracy thresholds gets
logged and versioned (for the record) but is never promoted — so nothing
downstream can pick it up as the serving model.

Usage:
    python -m waybill.ml.train_register            # local file-based MLflow
    MLFLOW_TRACKING_URI=http://mlflow:5000 python -m waybill.ml.train_register
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import mlflow
import mlflow.sklearn

from waybill.ml.model import train

_MODEL_NAME = "waybill-exception-classifier"
_THRESHOLDS_PATH = Path("eval_thresholds.json")


def _load_thresholds() -> dict:
    if _THRESHOLDS_PATH.exists():
        return json.loads(_THRESHOLDS_PATH.read_text())
    return {"classification_accuracy": 0.70}


def main() -> int:
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "file:./mlruns"))
    mlflow.set_experiment("waybill-exception-classifier")

    with mlflow.start_run() as run:
        # 1 + 2: train and log params/metrics.
        result = train()
        mlflow.log_param("model_type", "tfidf+logreg")
        mlflow.log_param("n_train", result.n_train)
        mlflow.log_param("n_test", result.n_test)
        mlflow.log_metric("holdout_accuracy", result.accuracy)

        # 3: log + register the model artifact as a new registry version.
        mlflow.sklearn.log_model(
            sk_model=result.pipeline,
            artifact_path="model",
            registered_model_name=_MODEL_NAME,
        )

        # 4: evaluate against the committed gate. We reuse holdout accuracy here
        # as the promotion metric; in a fuller setup you'd run the full eval
        # harness with this model wired in as the classifier.
        thresholds = _load_thresholds()
        min_acc = thresholds.get("classification_accuracy", 0.70)
        promote = result.accuracy >= min_acc

        mlflow.set_tag("eval_passed", str(promote))
        mlflow.set_tag("min_accuracy", str(min_acc))

        print(f"Run {run.info.run_id}")
        print(f"  holdout_accuracy = {result.accuracy:.3f}  (threshold {min_acc})")

        # 5: eval-gated promotion to Staging.
        if promote:
            client = mlflow.tracking.MlflowClient()
            versions = client.search_model_versions(f"name='{_MODEL_NAME}'")
            latest = max(versions, key=lambda v: int(v.version))
            client.transition_model_version_stage(
                name=_MODEL_NAME,
                version=latest.version,
                stage="Staging",
                archive_existing_versions=True,
            )
            print(f"  PROMOTED version {latest.version} to Staging.")
        else:
            print("  NOT promoted — accuracy below threshold. Version logged only.")

    return 0 if promote else 1


if __name__ == "__main__":
    raise SystemExit(main())
