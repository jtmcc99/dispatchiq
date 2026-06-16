"""Train, save, load, and predict with the late-risk logistic regression model.

Thin wrapper over scikit-learn so callers don't need to know the model class or
the feature pipeline. The class deliberately stores `FEATURE_NAMES` alongside
the trained estimator inside the persisted artifact — if the feature schema
drifts, `load()` raises rather than silently scoring with a misaligned vector.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
from sklearn.linear_model import LogisticRegression  # type: ignore

from late_risk_features import (
    FEATURE_NAMES,
    PredictionContext,
    context_from_synthetic_row,
    extract_features,
)


# Default prediction threshold. 0.5 is the standard cutoff; the training
# script reports metrics at this threshold. Callers that want a different
# precision/recall tradeoff can pass an override to `predict`.
DEFAULT_THRESHOLD: float = 0.5


@dataclass
class TrainedLateRiskModel:
    """Estimator + the feature schema it was trained against."""

    estimator: LogisticRegression
    feature_names: tuple[str, ...]
    metadata: dict = field(default_factory=dict)

    def predict_proba(self, features_vector: list[float]) -> float:
        """Return the model's probability that this order will be late."""
        if len(features_vector) != len(self.feature_names):
            raise ValueError(
                f"Feature vector length {len(features_vector)} does not match "
                f"trained schema length {len(self.feature_names)}."
            )
        return float(self.estimator.predict_proba([features_vector])[0][1])

    def predict(
        self, features_vector: list[float], threshold: float = DEFAULT_THRESHOLD
    ) -> bool:
        return self.predict_proba(features_vector) >= threshold

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "estimator": self.estimator,
                "feature_names": list(self.feature_names),
                "metadata": self.metadata,
            },
            path,
        )

    @classmethod
    def load(cls, path: Path) -> "TrainedLateRiskModel":
        payload = joblib.load(path)
        feature_names = tuple(payload["feature_names"])
        if feature_names != FEATURE_NAMES:
            raise RuntimeError(
                f"Model at {path} was trained with a different feature schema "
                f"than the current code defines. Retrain before using."
            )
        return cls(
            estimator=payload["estimator"],
            feature_names=feature_names,
            metadata=payload.get("metadata", {}),
        )


def train_logistic_regression(
    X: list[list[float]], y: list[bool], random_state: int = 42
) -> LogisticRegression:
    """Train logistic regression with sensible defaults for this dataset.

    `max_iter=1000` because the small training set with one-hot encodings can
    take a while to converge at the default 100 iterations. No `class_weight`
    override — the PRD's precision-first stance is compatible with letting the
    default behavior under-weight the minority class.
    """
    model = LogisticRegression(max_iter=1000, random_state=random_state)
    model.fit(X, y)
    return model


def featurize_synthetic_records(
    records: list[dict],
) -> tuple[list[list[float]], list[bool]]:
    """Map a list of synthetic-dataset rows to (X, y) for sklearn.

    The same `extract_features` function is used at live prediction time, so
    training and inference cannot disagree on feature shape.
    """
    X: list[list[float]] = []
    y: list[bool] = []
    for row in records:
        context = context_from_synthetic_row(row)
        X.append(extract_features(row, context))
        y.append(bool(row["was_late"]))
    return X, y


def write_metadata_sidecar(model_path: Path, metadata: dict) -> Path:
    """Write a human-readable JSON sidecar next to the joblib artifact."""
    sidecar_path = model_path.with_suffix(".metadata.json")
    sidecar_path.write_text(json.dumps(metadata, indent=2) + "\n")
    return sidecar_path


__all__ = [
    "DEFAULT_THRESHOLD",
    "TrainedLateRiskModel",
    "featurize_synthetic_records",
    "train_logistic_regression",
    "write_metadata_sidecar",
    "FEATURE_NAMES",
]


# Silence the noisy un-used-import lint without exporting the names we don't
# want as part of the public surface.
_ = (PredictionContext, Any)
