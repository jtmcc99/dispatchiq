"""Generate a synthetic, labeled order-history dataset for predictive late-risk training.

⚠️  SYNTHETIC DATA. This script produces ENTIRELY FABRICATED order records — not
real deliveries, not anonymized real deliveries, not derived from any production
system. It exists so the predictive late-risk model described in
docs/PRD-predictive-late-risk.md has *something* to train and evaluate against
during prototyping. Any conclusions drawn from a model trained on this data tell
you about this script's labeling rules, not about real DispatchIQ operations.

Determinism: a fixed seed (42) means re-running this script produces the same
dataset, byte-for-byte. Change the seed only if you want a different sample.

The labeling rule (function `_late_probability` below) is intentionally
transparent: lateness probability rises with order size, distance from
warehouse, bad weather, low remaining time at dispatch, and zone understaffing.
A small amount of noise is added so labels aren't perfectly separable — the
model has to actually learn the pattern, and we get realistic false-positive
and false-negative behavior on a holdout.
"""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path


# ─── Configuration ────────────────────────────────────────────────────────────

SEED = 42
N_RECORDS = 500
OUTPUT_PATH = Path(__file__).parent / "synthetic_order_history.json"

ZONES = ["Uptown", "Midtown", "Chelsea", "East Village", "Downtown"]

# Rough per-zone distance ranges from a notional Chelsea-area warehouse, in km.
# These are made-up but ordinally plausible for a single-warehouse Manhattan op.
ZONE_DISTANCE_KM = {
    "Chelsea": (0.4, 1.6),
    "Midtown": (1.0, 2.6),
    "East Village": (1.5, 3.2),
    "Uptown": (3.0, 5.0),
    "Downtown": (3.4, 5.6),
}

# Delivery windows mirror the live demo data: hourly slots through the day.
DELIVERY_WINDOWS = [
    ("10:00-11:00", "morning"),
    ("11:00-12:00", "morning"),
    ("12:00-13:00", "lunch"),
    ("13:00-14:00", "lunch"),
    ("14:00-15:00", "afternoon"),
    ("15:00-16:00", "afternoon"),
    ("16:00-17:00", "afternoon"),
    ("17:00-18:00", "evening"),
    ("18:00-19:00", "evening"),
    ("19:00-20:00", "evening"),
]

# Weather distribution roughly matches a temperate-NYC year, weighted toward
# clear days but with enough rain/snow that the model sees the bad-weather signal.
WEATHER_OPTIONS: list[tuple[str, float]] = [
    ("clear", 0.55),
    ("cloudy", 0.20),
    ("rain", 0.18),
    ("snow", 0.05),
    ("heat", 0.02),
]


# ─── Label generation ────────────────────────────────────────────────────────


def _late_probability(record: dict) -> float:
    """Transparent scoring function used to assign late/not-late labels.

    The point isn't to be a realistic ops model — it's to encode a coherent set
    of "what makes an order more likely to be late" rules so a downstream
    classifier has signal to learn. Coefficients are illustrative.
    """
    score = -3.3  # baseline pulls most orders toward "not late"

    score += 0.05 * record["size_items"]
    if record["has_heavy_items"]:
        score += 0.35
    score += 0.22 * record["distance_from_warehouse_km"]

    zone_staff = (
        record["zone_drivers_at_dispatch"] + record["zone_bikers_at_dispatch"]
    )
    if zone_staff <= 1:
        score += 0.85
    elif zone_staff == 2:
        score += 0.30

    if (
        record["zone"] == "Downtown"
        and record["zone_drivers_at_dispatch"] == 0
    ):
        score += 0.55

    if record["has_heavy_items"] and record["zone_drivers_at_dispatch"] == 0:
        score += 0.45

    weather = record["weather"]
    if weather == "rain":
        score += 0.40
    elif weather == "snow":
        score += 0.85
    elif weather == "heat":
        score += 0.15

    tr = record["time_remaining_minutes_at_dispatch"]
    if tr < 20:
        score += 0.65
    elif tr < 35:
        score += 0.25

    if record["time_of_day"] in ("lunch", "evening"):
        score += 0.20

    return 1.0 / (1.0 + math.exp(-score))


# ─── Record synthesis ────────────────────────────────────────────────────────


def _weighted_choice(rng: random.Random, options: list[tuple[str, float]]) -> str:
    r = rng.random()
    cumulative = 0.0
    for value, weight in options:
        cumulative += weight
        if r <= cumulative:
            return value
    return options[-1][0]


def _make_record(rng: random.Random, idx: int) -> dict:
    zone = rng.choice(ZONES)
    window, time_of_day = rng.choice(DELIVERY_WINDOWS)
    weather = _weighted_choice(rng, WEATHER_OPTIONS)

    size_items = rng.randint(1, 24)
    has_heavy_items = rng.random() < 0.18

    lo, hi = ZONE_DISTANCE_KM[zone]
    distance_km = round(rng.uniform(lo, hi), 2)

    if zone in ("Uptown", "Downtown"):
        drivers = rng.choices([0, 1, 2, 3], weights=[0.20, 0.35, 0.30, 0.15])[0]
        bikers = rng.choices([0, 1, 2, 3], weights=[0.15, 0.30, 0.35, 0.20])[0]
    else:
        drivers = rng.choices([0, 1, 2, 3], weights=[0.10, 0.30, 0.35, 0.25])[0]
        bikers = rng.choices([1, 2, 3, 4], weights=[0.20, 0.35, 0.30, 0.15])[0]

    time_remaining = rng.choice([10, 15, 20, 25, 30, 35, 40, 45, 50, 55])

    record = {
        "id": f"HIST-{idx:06d}",
        "zone": zone,
        "delivery_window": window,
        "time_of_day": time_of_day,
        "weather": weather,
        "size_items": size_items,
        "has_heavy_items": has_heavy_items,
        "distance_from_warehouse_km": distance_km,
        "zone_drivers_at_dispatch": drivers,
        "zone_bikers_at_dispatch": bikers,
        "time_remaining_minutes_at_dispatch": time_remaining,
    }

    p_late = _late_probability(record)
    record["was_late"] = rng.random() < p_late

    return record


# ─── Main ────────────────────────────────────────────────────────────────────


FEATURE_SCHEMA: dict[str, str] = {
    "id": "Synthetic order id, format HIST-NNNNNN.",
    "zone": "Destination zone. One of Uptown, Midtown, Chelsea, East Village, Downtown.",
    "delivery_window": "Committed delivery window, e.g. '14:00-15:00'.",
    "time_of_day": "Coarse bucket: morning, lunch, afternoon, evening.",
    "weather": "Weather at dispatch: clear, cloudy, rain, snow, heat.",
    "size_items": "Total item count on the order (1-24).",
    "has_heavy_items": "True if the order contains at least one heavy item (forces driver assignment).",
    "distance_from_warehouse_km": "Approximate distance from the warehouse, in km.",
    "zone_drivers_at_dispatch": "Number of car-drivers available in the destination zone at dispatch.",
    "zone_bikers_at_dispatch": "Number of bikers available in the destination zone at dispatch.",
    "time_remaining_minutes_at_dispatch": "Minutes left in the delivery window when the order was dispatched.",
    "was_late": "LABEL. True if the order missed its committed delivery window.",
}


def main() -> None:
    rng = random.Random(SEED)
    records = [_make_record(rng, i + 1) for i in range(N_RECORDS)]

    late_count = sum(1 for r in records if r["was_late"])
    payload = {
        "_synthetic": True,
        "_warning": (
            "DO NOT USE FOR ANALYSIS OF REAL OPERATIONS. Every record in this "
            "file is fabricated by data/generate_synthetic_order_history.py "
            "using a fixed seed. Labels are produced by a transparent scoring "
            "rule, not observed outcomes."
        ),
        "_description": (
            "Synthetic labeled order-history dataset for prototyping the "
            "predictive late-risk model described in "
            "docs/PRD-predictive-late-risk.md. Each record encodes the features "
            "listed in PRD §5 plus a binary `was_late` label."
        ),
        "_generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "_generator": "data/generate_synthetic_order_history.py",
        "_seed": SEED,
        "_record_count": len(records),
        "_late_count": late_count,
        "_late_rate": round(late_count / len(records), 3),
        "feature_schema": FEATURE_SCHEMA,
        "records": records,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(
        f"Wrote {len(records)} records to {OUTPUT_PATH} "
        f"({late_count} late, rate={late_count / len(records):.1%})"
    )


if __name__ == "__main__":
    main()
