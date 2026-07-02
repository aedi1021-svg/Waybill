"""Model-backed classifier.

Loads the trained classifier from the MLflow registry (the model currently in
"Staging") and uses it to classify exceptions, producing confidence from the
model's predicted probability. Falls back to the heuristic classifier if no
registered model is available, so the agent always works.

This is the serving side of the registry story: CI/training promotes a version
to Staging; the app loads whatever is in Staging. Swapping the production model
is a registry stage transition, not a code deploy.
"""
from __future__ import annotations

import os

from waybill.agent.classifier import Classifier
from waybill.core.models import Classification, ExceptionType
from waybill.ml.model import severity_for

_MODEL_NAME = "waybill-exception-classifier"


class ModelClassifier(Classifier):
    """Classifier that serves a trained model from the MLflow registry."""

    def __init__(self, stage: str = "Staging") -> None:
        self._pipeline = None
        self._fallback = Classifier()  # heuristic/LLM fallback
        try:
            import mlflow

            mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "file:./mlruns"))
            self._pipeline = mlflow.sklearn.load_model(f"models:/{_MODEL_NAME}/{stage}")
        except Exception:
            # No registry / no model yet: fall back cleanly.
            self._pipeline = None

    def classify(self, message: str) -> Classification:
        if self._pipeline is None:
            return self._fallback.classify(message)

        proba = self._pipeline.predict_proba([message])[0]
        classes = list(self._pipeline.classes_)
        best_idx = max(range(len(proba)), key=lambda i: proba[i])
        etype = ExceptionType(classes[best_idx])
        confidence = float(proba[best_idx])
        return Classification(
            exception_type=etype,
            severity=severity_for(etype),
            confidence=confidence,
            rationale="trained model (mlflow registry)",
        )
