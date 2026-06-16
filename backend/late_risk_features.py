"""Feature extraction for the predictive late-risk model.

A single pure function — `extract_features` — produces the numeric feature
vector for one (order, dispatch-time-context) pair. The same function is used
at training time (over rows from `data/synthetic_order_history.json`) and at
live prediction time, so the model can never see a different feature shape
between the two.

Design notes:
- Pure: no I/O, no globals, no time dependence. Same inputs → same vector.
- Accepts either a live `Order` model OR a flat dict (e.g., a synthetic row
  or a Pydantic dump). Field names follow the synthetic dataset
  (`size_items`, `has_heavy_items`), but `Order.total_items` is accepted as
  an alias for `size_items` so live code doesn't have to pre-translate.
- Returns `list[float]`, not numpy. Zero new dependencies; the model layer
  can convert if it wants to.
- The output positions correspond exactly to `FEATURE_NAMES`.

See `docs/PRD-predictive-late-risk.md` §5 for the feature list and rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ─── Schema ──────────────────────────────────────────────────────────────────

ZONES: tuple[str, ...] = (
    "Uptown",
    "Midtown",
    "Chelsea",
    "East Village",
    "Downtown",
)
TIMES_OF_DAY: tuple[str, ...] = ("morning", "lunch", "afternoon", "evening")
WEATHER_CONDITIONS: tuple[str, ...] = (
    "clear",
    "cloudy",
    "rain",
    "snow",
    "heat",
)

NUMERIC_FEATURES: tuple[str, ...] = (
    "size_items",
    "has_heavy_items",
    "distance_from_warehouse_km",
    "zone_drivers_at_dispatch",
    "zone_bikers_at_dispatch",
    "time_remaining_minutes_at_dispatch",
)

FEATURE_NAMES: tuple[str, ...] = (
    *NUMERIC_FEATURES,
    *(f"zone={z}" for z in ZONES),
    *(f"time_of_day={t}" for t in TIMES_OF_DAY),
    *(f"weather={w}" for w in WEATHER_CONDITIONS),
)


# ─── Context type ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PredictionContext:
    """The non-order facts the model needs at dispatch time.

    All fields are required — the model is not designed to silently impute
    missing context. Construct from a live snapshot (live path) or from the
    matching fields on a synthetic-dataset row (training path).
    """

    weather: str
    time_of_day: str
    distance_from_warehouse_km: float
    zone_drivers_at_dispatch: int
    zone_bikers_at_dispatch: int
    time_remaining_minutes_at_dispatch: int


# ─── Feature extraction ──────────────────────────────────────────────────────


def _read(record: Any, *names: str) -> Any:
    """Pull a field from either a dict or an attribute-bearing object.

    Accepts multiple name aliases (the first that exists wins) so the same
    extraction works against a synthetic row (`size_items`) and a live
    `Order` (`total_items`) without a separate adapter layer.
    """
    for name in names:
        if isinstance(record, dict):
            if name in record:
                return record[name]
        elif hasattr(record, name):
            return getattr(record, name)
    raise KeyError(
        f"None of {names} present on {type(record).__name__}: {record!r}"
    )


def _one_hot(value: str, allowed: tuple[str, ...], field: str) -> list[float]:
    if value not in allowed:
        raise ValueError(
            f"{field}={value!r} not in allowed values {allowed}"
        )
    return [1.0 if v == value else 0.0 for v in allowed]


def extract_features(order: Any, context: PredictionContext) -> list[float]:
    """Return the numeric feature vector for one (order, context) pair.

    The vector positions correspond exactly to `FEATURE_NAMES`. `order` must
    expose `zone`, a size field (`size_items` or `total_items`), and
    `has_heavy_items` — either as dict keys or as attributes.
    """
    zone = _read(order, "zone")
    if zone not in ZONES:
        raise ValueError(f"Unknown zone {zone!r}; expected one of {ZONES}")

    size_items = int(_read(order, "size_items", "total_items"))
    has_heavy = bool(_read(order, "has_heavy_items"))

    return [
        float(size_items),
        1.0 if has_heavy else 0.0,
        float(context.distance_from_warehouse_km),
        float(context.zone_drivers_at_dispatch),
        float(context.zone_bikers_at_dispatch),
        float(context.time_remaining_minutes_at_dispatch),
        *_one_hot(zone, ZONES, "zone"),
        *_one_hot(context.time_of_day, TIMES_OF_DAY, "time_of_day"),
        *_one_hot(context.weather, WEATHER_CONDITIONS, "weather"),
    ]


def context_from_synthetic_row(row: dict) -> PredictionContext:
    """Convenience: build a PredictionContext from a synthetic-dataset row.

    Lets training code call `extract_features(row, context_from_synthetic_row(row))`
    without having to know which fields belong to the order vs. the context.
    """
    return PredictionContext(
        weather=row["weather"],
        time_of_day=row["time_of_day"],
        distance_from_warehouse_km=float(row["distance_from_warehouse_km"]),
        zone_drivers_at_dispatch=int(row["zone_drivers_at_dispatch"]),
        zone_bikers_at_dispatch=int(row["zone_bikers_at_dispatch"]),
        time_remaining_minutes_at_dispatch=int(
            row["time_remaining_minutes_at_dispatch"]
        ),
    )
