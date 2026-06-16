"""Late-risk model SHADOW-MODE scoring service.

Shadow mode means: predictions compute and get logged, but they are never
surfaced to end users, never create an `ExceptionRecord`, and never appear on
the dashboard as a flag. The only consumers are:

  - `GET /late-risk/shadow-predictions` (backend API, for inspection / future
    frontend instrumentation)
  - `predict_late_risk_shadow` MCP tool (for operators and analysts who want
    to see what the model would say)
  - The append-only log at `backend/data/shadow_predictions.jsonl`, which is
    the data substrate for the eventual real-outcome eval (see
    `docs/late-risk-eval-and-limitations.md` for graduation criteria).

The model itself was trained on synthetic data whose labels were derived from
the same features the model sees, so the eval is methodologically circular.
That circularity is precisely why this module exists in this shape — the
predictions go to a log, not to users.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Optional

import data_store
from late_risk_features import (
    PredictionContext,
    extract_features,
)
from late_risk_model import DEFAULT_THRESHOLD, TrainedLateRiskModel
from models import Order


MODEL_VERSION = "late_risk_logreg_v1"
MODEL_PATH = Path(__file__).resolve().parent.parent / "data" / "models" / f"{MODEL_VERSION}.joblib"

SHADOW_LOG_PATH = data_store.DATA_DIR / "shadow_predictions.jsonl"


# Per-zone distance midpoints (km). Mirrors the ranges used in
# `data/generate_synthetic_order_history.py`. Single value per zone — within-
# zone variation is lost. Recorded as a caveat on every prediction.
ZONE_TO_DISTANCE_KM: dict[str, float] = {
    "Chelsea": 1.0,
    "Midtown": 1.8,
    "East Village": 2.4,
    "Uptown": 4.0,
    "Downtown": 4.5,
}

# Coarse time-of-day buckets keyed off the window start hour. Matches the
# bucketing in `data/generate_synthetic_order_history.py`.
def _time_of_day_for_window(window: str) -> str:
    try:
        start_h = int(window.split(":")[0])
    except (ValueError, IndexError):
        return "morning"
    if start_h < 12:
        return "morning"
    if start_h < 14:
        return "lunch"
    if start_h < 17:
        return "afternoon"
    return "evening"


def _parse_window_end(window: str, today: datetime) -> Optional[datetime]:
    try:
        _, end_str = window.split("-")
        end_h, end_m = map(int, end_str.split(":"))
    except (ValueError, IndexError):
        return None
    return datetime.combine(today.date(), time(end_h, end_m))


@dataclass
class ShadowPrediction:
    """One model prediction, plus everything needed to audit it later.

    Goes to the JSONL log verbatim. `context_caveats` lists the
    approximations applied when building the prediction context from live
    state — useful when the log is replayed against real outcomes to exclude
    distorted records.
    """

    predicted_at: str
    order_id: str
    model_version: str
    p_late: float
    threshold: float
    would_flag: bool
    zone: str
    delivery_window: str
    features_resolved: dict
    context_caveats: list[str] = field(default_factory=list)
    shadow_mode: bool = True

    def to_dict(self) -> dict:
        return {
            "predicted_at": self.predicted_at,
            "order_id": self.order_id,
            "model_version": self.model_version,
            "p_late": round(self.p_late, 4),
            "threshold": self.threshold,
            "would_flag": self.would_flag,
            "zone": self.zone,
            "delivery_window": self.delivery_window,
            "features_resolved": self.features_resolved,
            "context_caveats": self.context_caveats,
            "shadow_mode": self.shadow_mode,
        }


# ─── Public API ──────────────────────────────────────────────────────────────


_model_cache: TrainedLateRiskModel | None = None


def _get_model() -> TrainedLateRiskModel:
    global _model_cache
    if _model_cache is None:
        if not MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Trained model not found at {MODEL_PATH}. "
                f"Run `python backend/train_late_risk.py` first."
            )
        _model_cache = TrainedLateRiskModel.load(MODEL_PATH)
    return _model_cache


def _build_context_from_live_state(
    order: Order, now: datetime
) -> tuple[PredictionContext, list[str]]:
    """Synthesize a PredictionContext from live data_store state.

    Returns (context, caveats). Caveats are recorded on the resulting
    ShadowPrediction so circular-eval artifacts in the log can be filtered
    out later.
    """
    caveats: list[str] = []

    distance = ZONE_TO_DISTANCE_KM.get(order.zone)
    if distance is None:
        distance = 2.5
        caveats.append(
            f"distance_from_warehouse_km defaulted to 2.5 for unknown zone "
            f"{order.zone!r}"
        )
    else:
        caveats.append(
            "distance_from_warehouse_km is a per-zone midpoint, not "
            "per-address"
        )

    weather = "clear"
    caveats.append(
        "weather hardcoded to 'clear' — no weather feed wired in"
    )

    time_of_day = _time_of_day_for_window(order.delivery_window)

    drivers = data_store.get_drivers()
    zone_present = [
        d
        for d in drivers
        if order.zone in d.zones and d.status != "called_out"
    ]
    zone_drivers = sum(1 for d in zone_present if d.type == "driver")
    zone_bikers = sum(1 for d in zone_present if d.type == "biker")

    window_end = _parse_window_end(order.delivery_window, now)
    if window_end is None:
        time_remaining = 30
        caveats.append(
            f"time_remaining_minutes defaulted to 30; could not parse "
            f"delivery_window {order.delivery_window!r}"
        )
    else:
        remaining_seconds = (window_end - now).total_seconds()
        time_remaining = max(int(remaining_seconds // 60), 0)
        if order.status in ("received", "picking"):
            caveats.append(
                f"order status is '{order.status}'; time_remaining reflects "
                "window deadline, not actual dispatch time (which is in "
                "the future)"
            )

    context = PredictionContext(
        weather=weather,
        time_of_day=time_of_day,
        distance_from_warehouse_km=distance,
        zone_drivers_at_dispatch=zone_drivers,
        zone_bikers_at_dispatch=zone_bikers,
        time_remaining_minutes_at_dispatch=time_remaining,
    )
    return context, caveats


def score_order(
    order: Order, now: datetime | None = None, threshold: float = DEFAULT_THRESHOLD
) -> ShadowPrediction:
    """Score one order with the shadow-mode model. Does NOT log."""
    now = now or datetime.now()
    model = _get_model()
    context, caveats = _build_context_from_live_state(order, now)
    vector = extract_features(order, context)
    p_late = model.predict_proba(vector)

    return ShadowPrediction(
        predicted_at=now.isoformat(timespec="seconds"),
        order_id=order.id,
        model_version=MODEL_VERSION,
        p_late=p_late,
        threshold=threshold,
        would_flag=p_late >= threshold,
        zone=order.zone,
        delivery_window=order.delivery_window,
        features_resolved={
            "size_items": order.total_items,
            "has_heavy_items": order.has_heavy_items,
            "distance_from_warehouse_km": context.distance_from_warehouse_km,
            "zone_drivers_at_dispatch": context.zone_drivers_at_dispatch,
            "zone_bikers_at_dispatch": context.zone_bikers_at_dispatch,
            "time_remaining_minutes_at_dispatch": context.time_remaining_minutes_at_dispatch,
            "weather": context.weather,
            "time_of_day": context.time_of_day,
        },
        context_caveats=caveats,
    )


def log_prediction(prediction: ShadowPrediction) -> None:
    """Append one prediction to the shadow log (JSONL, append-only)."""
    SHADOW_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SHADOW_LOG_PATH.open("a") as f:
        f.write(json.dumps(prediction.to_dict()) + "\n")


def score_active_orders(
    now: datetime | None = None, threshold: float = DEFAULT_THRESHOLD
) -> list[ShadowPrediction]:
    """Score every currently-active order (not delivered/failed) and log
    each prediction. Returns the predictions in score order (highest p_late
    first) for the API caller's convenience."""
    now = now or datetime.now()
    orders = data_store.get_orders()
    active = [
        o for o in orders if o.status not in ("delivered", "failed")
    ]
    predictions = [score_order(o, now=now, threshold=threshold) for o in active]
    for p in predictions:
        log_prediction(p)
    return sorted(predictions, key=lambda p: p.p_late, reverse=True)


def score_order_by_id(
    order_id: str,
    now: datetime | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> ShadowPrediction | None:
    """Score one specific order by id. Logs the prediction. Returns None if
    the order doesn't exist."""
    order = data_store.get_order(order_id)
    if order is None:
        return None
    prediction = score_order(order, now=now, threshold=threshold)
    log_prediction(prediction)
    return prediction
