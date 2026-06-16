"""Unit tests for `late_risk_features.extract_features`.

Goals:
1. A known input maps to the exact expected vector (positions and values),
   so a refactor that silently reorders or rescales a feature breaks loudly.
2. The synthetic-row path and the live-Order path produce identical vectors
   for equivalent inputs — this is the property the whole module exists to
   guarantee (training and live prediction must agree on the feature shape).

Run from the repo root:
    python -m unittest backend.tests.test_late_risk_features
Or from inside `backend/`:
    python -m unittest tests.test_late_risk_features
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Allow `from late_risk_features import ...` whether tests are run from the
# repo root or from inside `backend/`. Mirrors the bare-import pattern the
# rest of the backend uses.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from late_risk_features import (  # noqa: E402
    FEATURE_NAMES,
    PredictionContext,
    context_from_synthetic_row,
    extract_features,
)
from models import Order, OrderItem  # noqa: E402


class TestExtractFeaturesKnownVector(unittest.TestCase):
    """A known input maps to a specific, fully-enumerated vector."""

    def test_synthetic_row_produces_expected_vector(self):
        row = {
            "id": "HIST-000001",
            "zone": "Downtown",
            "delivery_window": "18:00-19:00",
            "time_of_day": "evening",
            "weather": "rain",
            "size_items": 18,
            "has_heavy_items": True,
            "distance_from_warehouse_km": 4.5,
            "zone_drivers_at_dispatch": 1,
            "zone_bikers_at_dispatch": 2,
            "time_remaining_minutes_at_dispatch": 22,
            "was_late": True,
        }
        context = context_from_synthetic_row(row)

        vector = extract_features(row, context)

        expected = [
            18.0,  # size_items
            1.0,   # has_heavy_items
            4.5,   # distance_from_warehouse_km
            1.0,   # zone_drivers_at_dispatch
            2.0,   # zone_bikers_at_dispatch
            22.0,  # time_remaining_minutes_at_dispatch
            # zone one-hot: Uptown, Midtown, Chelsea, East Village, Downtown
            0.0, 0.0, 0.0, 0.0, 1.0,
            # time_of_day one-hot: morning, lunch, afternoon, evening
            0.0, 0.0, 0.0, 1.0,
            # weather one-hot: clear, cloudy, rain, snow, heat
            0.0, 0.0, 1.0, 0.0, 0.0,
        ]
        self.assertEqual(vector, expected)
        self.assertEqual(len(vector), len(FEATURE_NAMES))


class TestLiveOrderEquivalence(unittest.TestCase):
    """The live `Order` path produces the same vector as the synthetic dict
    path for an equivalent input. This is the property the module exists to
    guarantee."""

    def test_live_order_matches_synthetic_row(self):
        synthetic_row = {
            "zone": "Chelsea",
            "size_items": 7,
            "has_heavy_items": False,
            "weather": "clear",
            "time_of_day": "lunch",
            "distance_from_warehouse_km": 1.2,
            "zone_drivers_at_dispatch": 2,
            "zone_bikers_at_dispatch": 3,
            "time_remaining_minutes_at_dispatch": 45,
        }
        context = context_from_synthetic_row(synthetic_row)

        live_order = Order(
            id="ORD-TEST",
            customer_name="Test Customer",
            items=[
                OrderItem(name=f"item-{i}", quantity=1)
                for i in range(7)
            ],
            delivery_window="12:00-13:00",
            zone="Chelsea",
            status="picked",
            total_items=7,
            has_heavy_items=False,
        )

        self.assertEqual(
            extract_features(live_order, context),
            extract_features(synthetic_row, context),
        )


class TestExtractFeaturesValidation(unittest.TestCase):
    """Bad inputs raise loudly instead of producing a silently-wrong vector."""

    def _ctx(self, **overrides) -> PredictionContext:
        base = dict(
            weather="clear",
            time_of_day="morning",
            distance_from_warehouse_km=2.0,
            zone_drivers_at_dispatch=1,
            zone_bikers_at_dispatch=2,
            time_remaining_minutes_at_dispatch=30,
        )
        base.update(overrides)
        return PredictionContext(**base)

    def test_unknown_zone_raises(self):
        row = {"zone": "Brooklyn", "size_items": 5, "has_heavy_items": False}
        with self.assertRaises(ValueError):
            extract_features(row, self._ctx())

    def test_unknown_weather_raises(self):
        row = {"zone": "Midtown", "size_items": 5, "has_heavy_items": False}
        with self.assertRaises(ValueError):
            extract_features(row, self._ctx(weather="hurricane"))


if __name__ == "__main__":
    unittest.main()
