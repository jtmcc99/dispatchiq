"""Rule-based per-order late-risk baseline.

This is the predictive-model baseline called for in
``docs/PRD-predictive-late-risk.md`` §9: *"The baseline to beat is the existing
rule-based flag, scored the same way."*

The existing in-app rules are window- and zone-level — they live in
``mcp_server/_assess.py`` (`_classify_window_risk`, `assess_zone_coverage`).
The synthetic training set is per-order dispatch snapshots, so a faithful
apples-to-apples baseline is a *per-order projection* of those rules that uses
the same features the predictive model uses. The three pieces of the existing
rule that map onto the synthetic feature set:

1. **Capacity ratio.** `_classify_window_risk` flags as "high" or worse when
   ``required_per_hour / (available_drivers * 2.0) > 0.75``. Applied to a
   single order in ``time_remaining`` minutes:
   ``required_per_hour = 60 / time_remaining``,
   ``capacity_per_hour = (drivers + bikers) * 2.0``.

2. **Downtown without cars.** From `_coverage_recommendation`: Downtown with
   no drivers is `at_risk` because most Downtown deliveries need a car.

3. **Heavy order without a driver.** From `assess_reservation`: a heavy/large
   order assigned where no car driver is available is "block"-worthy.

This is deliberately not a learned model — it's the rule, projected onto the
per-order view, with the same coefficient and threshold the live system uses.
A predictive model that doesn't beat this on the holdout has no business
shipping.
"""

from __future__ import annotations

from typing import Mapping


# Same constant as mcp_server/_assess.THROUGHPUT_PER_DRIVER_PER_HOUR. Kept as a
# local copy rather than imported so this module stays runnable without the
# mcp_server path shim.
THROUGHPUT_PER_DRIVER_PER_HOUR: float = 2.0

# Same "high or critical" threshold as _classify_window_risk.
RATIO_FLAG_THRESHOLD: float = 0.75


def rule_based_late_flag(features: Mapping) -> bool:
    """Return True iff the existing rule-based logic would flag this order
    as at-risk at dispatch time.

    `features` is any mapping containing the synthetic-dataset fields
    (`zone`, `has_heavy_items`, `zone_drivers_at_dispatch`,
    `zone_bikers_at_dispatch`, `time_remaining_minutes_at_dispatch`).
    """
    time_remaining = float(features["time_remaining_minutes_at_dispatch"])
    drivers = int(features["zone_drivers_at_dispatch"])
    bikers = int(features["zone_bikers_at_dispatch"])
    zone = features["zone"]
    has_heavy = bool(features["has_heavy_items"])

    zone_staff = drivers + bikers

    if time_remaining <= 0 or zone_staff == 0:
        return True

    required_per_hour = 60.0 / time_remaining
    capacity_per_hour = zone_staff * THROUGHPUT_PER_DRIVER_PER_HOUR
    if required_per_hour / capacity_per_hour > RATIO_FLAG_THRESHOLD:
        return True

    if zone == "Downtown" and drivers == 0:
        return True

    if has_heavy and drivers == 0:
        return True

    return False
