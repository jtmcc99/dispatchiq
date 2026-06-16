"""Train and honestly evaluate the late-risk predictive model.

Per ``docs/PRD-predictive-late-risk.md`` §9, this script reports PRECISION and
RECALL **separately** — never blended accuracy — and compares the trained
model against the rule-based baseline on the same holdout split so the
"beats the rule" claim is auditable.

Usage (from anywhere):

    python backend/train_late_risk.py

Outputs:
- ``data/models/late_risk_logreg_v1.joblib`` — the trained model
- ``data/models/late_risk_logreg_v1.metadata.json`` — eval results sidecar
- A short eval summary printed to stdout
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

# Add backend/ to sys.path so this script can be run from the repo root.
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sklearn.model_selection import train_test_split  # type: ignore  # noqa: E402

from late_risk_baseline import rule_based_late_flag  # noqa: E402
from late_risk_features import FEATURE_NAMES  # noqa: E402
from late_risk_model import (  # noqa: E402
    DEFAULT_THRESHOLD,
    TrainedLateRiskModel,
    featurize_synthetic_records,
    train_logistic_regression,
    write_metadata_sidecar,
)


_REPO_ROOT = _BACKEND_DIR.parent
DATASET_PATH = _REPO_ROOT / "data" / "synthetic_order_history.json"
MODEL_DIR = _REPO_ROOT / "data" / "models"
MODEL_PATH = MODEL_DIR / "late_risk_logreg_v1.joblib"

TRAIN_HOLDOUT_SPLIT = 0.20
SPLIT_SEED = 42


# ─── Metric helpers ──────────────────────────────────────────────────────────


def _precision_recall(
    predictions: Sequence[bool], truths: Sequence[bool]
) -> tuple[float, float, dict[str, int]]:
    """Compute precision, recall, and the underlying confusion-matrix counts.

    Implemented in stdlib so the eval output doesn't depend on sklearn's
    formatting and is easy to spot-check.
    """
    tp = sum(1 for p, t in zip(predictions, truths) if p and t)
    fp = sum(1 for p, t in zip(predictions, truths) if p and not t)
    fn = sum(1 for p, t in zip(predictions, truths) if not p and t)
    tn = sum(1 for p, t in zip(predictions, truths) if not p and not t)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return precision, recall, {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


# ─── Pipeline ────────────────────────────────────────────────────────────────


def load_dataset() -> list[dict]:
    if not DATASET_PATH.exists():
        raise FileNotFoundError(
            f"Dataset not found at {DATASET_PATH}. "
            f"Run `python data/generate_synthetic_order_history.py` first."
        )
    payload = json.loads(DATASET_PATH.read_text())
    return payload["records"]


def main() -> None:
    records = load_dataset()

    train_records, holdout_records = train_test_split(
        records,
        test_size=TRAIN_HOLDOUT_SPLIT,
        random_state=SPLIT_SEED,
        stratify=[r["was_late"] for r in records],
    )

    X_train, y_train = featurize_synthetic_records(train_records)
    X_holdout, y_holdout = featurize_synthetic_records(holdout_records)

    estimator = train_logistic_regression(X_train, y_train, random_state=SPLIT_SEED)

    model_predictions = [
        bool(estimator.predict_proba([x])[0][1] >= DEFAULT_THRESHOLD)
        for x in X_holdout
    ]
    model_precision, model_recall, model_counts = _precision_recall(
        model_predictions, y_holdout
    )

    baseline_predictions = [rule_based_late_flag(r) for r in holdout_records]
    baseline_precision, baseline_recall, baseline_counts = _precision_recall(
        baseline_predictions, y_holdout
    )

    train_late_count = sum(1 for v in y_train if v)
    holdout_late_count = sum(1 for v in y_holdout if v)

    eval_metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": str(DATASET_PATH.relative_to(_REPO_ROOT)),
        "split": {
            "train_size": len(y_train),
            "train_late_count": train_late_count,
            "train_late_rate": round(train_late_count / len(y_train), 4),
            "holdout_size": len(y_holdout),
            "holdout_late_count": holdout_late_count,
            "holdout_late_rate": round(holdout_late_count / len(y_holdout), 4),
            "split_seed": SPLIT_SEED,
            "holdout_fraction": TRAIN_HOLDOUT_SPLIT,
        },
        "model": {
            "type": "LogisticRegression",
            "threshold": DEFAULT_THRESHOLD,
            "precision": round(model_precision, 4),
            "recall": round(model_recall, 4),
            "confusion": model_counts,
        },
        "rule_based_baseline": {
            "source": "backend/late_risk_baseline.py:rule_based_late_flag",
            "precision": round(baseline_precision, 4),
            "recall": round(baseline_recall, 4),
            "confusion": baseline_counts,
        },
        "lead_time_note": (
            "PRD §9 also calls for measuring lead time vs. the rule-based flag. "
            "On this synthetic dispatch-snapshot dataset both predictors operate "
            "on the same moment in time, so lead time cannot be measured here. "
            "It is a real eval dimension once the model is wired into the live "
            "pre-dispatch path."
        ),
        "feature_count": len(FEATURE_NAMES),
    }

    trained = TrainedLateRiskModel(
        estimator=estimator,
        feature_names=FEATURE_NAMES,
        metadata=eval_metadata,
    )
    trained.save(MODEL_PATH)
    sidecar_path = write_metadata_sidecar(MODEL_PATH, eval_metadata)

    _print_summary(eval_metadata, MODEL_PATH, sidecar_path)


def _print_summary(meta: dict, model_path: Path, sidecar_path: Path) -> None:
    split = meta["split"]
    model = meta["model"]
    base = meta["rule_based_baseline"]

    def fmt(p: float) -> str:
        return f"{p:.1%}" if p else "  0.0%"

    def confusion_line(c: dict) -> str:
        return f"TP={c['tp']:>3}  FP={c['fp']:>3}  FN={c['fn']:>3}  TN={c['tn']:>3}"

    print()
    print("─" * 68)
    print("  Late-risk model — holdout evaluation")
    print("─" * 68)
    print(
        f"  Split: train={split['train_size']} "
        f"({split['train_late_count']} late, {split['train_late_rate']:.1%}) | "
        f"holdout={split['holdout_size']} "
        f"({split['holdout_late_count']} late, {split['holdout_late_rate']:.1%})"
    )
    print(f"  Stratified split, seed={split['split_seed']}")
    print()
    print(f"  Predictive model (LogisticRegression, threshold={model['threshold']})")
    print(f"    Precision: {fmt(model['precision'])}")
    print(f"    Recall:    {fmt(model['recall'])}")
    print(f"    {confusion_line(model['confusion'])}")
    print()
    print("  Rule-based baseline (per-order projection of check_window_risk)")
    print(f"    Precision: {fmt(base['precision'])}")
    print(f"    Recall:    {fmt(base['recall'])}")
    print(f"    {confusion_line(base['confusion'])}")
    print()
    print("  Verdict (PRD §9: beat rule on lead time at comparable-or-better precision):")
    p_delta = model["precision"] - base["precision"]
    r_delta = model["recall"] - base["recall"]
    print(
        f"    Precision delta: {p_delta:+.1%}  |  Recall delta: {r_delta:+.1%}"
    )
    print(f"    Lead time: not measurable on snapshot data — see metadata.")
    print()
    print(f"  Wrote model:    {model_path.relative_to(_REPO_ROOT)}")
    print(f"  Wrote metadata: {sidecar_path.relative_to(_REPO_ROOT)}")
    print("─" * 68)


if __name__ == "__main__":
    main()
