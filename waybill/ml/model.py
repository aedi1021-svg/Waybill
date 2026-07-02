"""Trainable exception classifier.

A real, trainable model (TF-IDF + logistic regression) over the carrier message
text, giving MLflow something genuine to track, version, and promote — as
opposed to the fixed Ollama model or the heuristic baseline.

Why this model choice: the classification task (short text -> one of 7 labels)
is exactly what a linear model over TF-IDF features does well, it trains in
seconds on CPU, and it produces calibrated-enough probabilities to drive the
confidence gate. It's deliberately simple: the point of the project is the MLOps
lifecycle around the model, not squeezing out the last accuracy point.

The trained artifact is a single sklearn Pipeline, so it serializes cleanly and
MLflow can log/version/serve it as one object.
"""
from __future__ import annotations

from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from waybill.core.models import ExceptionType, Severity
from waybill.data.generator import SyntheticGenerator


@dataclass
class TrainingResult:
    pipeline: Pipeline
    accuracy: float
    n_train: int
    n_test: int


def _build_pipeline() -> Pipeline:
    return Pipeline(
        steps=[
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=1, lowercase=True)),
            ("clf", LogisticRegression(max_iter=1000, C=4.0)),
        ]
    )


def _make_split(n: int, seed: int) -> tuple[list[str], list[str], list[str], list[str]]:
    """Generate labelled data and split into train/test. Labels are the
    exception type strings (the model's target)."""
    gen = SyntheticGenerator(seed)
    data = gen.dataset(n)
    texts = [e.raw_message for _, e in data]
    labels = [e.true_type.value for _, e in data]
    cut = int(0.8 * n)
    return texts[:cut], texts[cut:], labels[:cut], labels[cut:]


def train(n: int = 1200, seed: int = 13) -> TrainingResult:
    x_train, x_test, y_train, y_test = _make_split(n, seed)
    pipe = _build_pipeline()
    pipe.fit(x_train, y_train)
    accuracy = pipe.score(x_test, y_test)
    return TrainingResult(
        pipeline=pipe,
        accuracy=float(accuracy),
        n_train=len(x_train),
        n_test=len(x_test),
    )


# --- Severity heuristic (kept simple; type is the learned part) ---
_SEVERITY_BY_TYPE = {
    ExceptionType.DELAY: Severity.MEDIUM,
    ExceptionType.CUSTOMS_HOLD: Severity.HIGH,
    ExceptionType.DAMAGED_GOODS: Severity.HIGH,
    ExceptionType.MISSING_DOCS: Severity.MEDIUM,
    ExceptionType.ADDRESS_ISSUE: Severity.MEDIUM,
    ExceptionType.LOST: Severity.CRITICAL,
    ExceptionType.UNKNOWN: Severity.MEDIUM,
}


def severity_for(etype: ExceptionType) -> Severity:
    return _SEVERITY_BY_TYPE.get(etype, Severity.MEDIUM)
