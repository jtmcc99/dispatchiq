"""Read-only assessment helpers (window risk, zone coverage, reservations).

Wherever the existing in-app agent already implements the logic (see
``backend/agent.py``), we reuse it directly. Everything else — i.e. anything
the spec asks for that the live demo did not yet compute — is implemented
here and tagged with a ``# NEW:`` comment so the gap is obvious in review.
"""

from __future__ import annotations

from datetime import date, datetime

import _path  # noqa: F401  (path shim)

import data_store
from models import Order
from risk import _parse_window

from _models import NotFound, ReservationCheck, WindowRisk, ZoneCoverage

# Canonical zone list per the spec. Used for input validation and as the
# default sweep set for check_driver_coverage.
VALID_ZONES: list[str] = ["Uptown", "Midtown", "Chelsea", "East Village", "Downtown"]

# NEW: heuristic — how many deliveries one driver can complete per hour.
# Used by check_window_risk to compare required vs. available throughput.
THROUGHPUT_PER_DRIVER_PER_HOUR: float = 2.0

# Statuses that mean an order is no longer in the active queue.
TERMINAL_STATUSES: set[str] = {"delivered", "dispatched", "failed"}


# ─── check_window_risk ────────────────────────────────────────────────────────


def _window_bounds(window_id: str, today: date) -> tuple[datetime, datetime]:
    """Convert "HH:MM-HH:MM" → two ISO-able datetimes anchored to today."""
    start_t, end_t = _parse_window(window_id)  # reused from agent.py
    return datetime.combine(today, start_t), datetime.combine(today, end_t)


def _classify_window_risk(
    orders_remaining: int,
    minutes_remaining: int,
    available_drivers: int,
) -> str:
    """NEW: low/medium/high/critical from (orders/time) vs (drivers*throughput)."""
    if orders_remaining == 0:
        return "low"
    if minutes_remaining <= 0:
        return "critical"
    required = orders_remaining / (minutes_remaining / 60.0)
    capacity = max(available_drivers, 0) * THROUGHPUT_PER_DRIVER_PER_HOUR
    if capacity <= 0:
        return "critical"
    ratio = required / capacity
    if ratio > 1.0:
        return "critical"
    if ratio > 0.75:
        return "high"
    if ratio > 0.5:
        return "medium"
    return "low"


def _window_recommendation(level: str, drivers: int, zones: list[str]) -> str:
    """NEW: short, action-oriented suggestion keyed off the risk level."""
    zone_phrase = ", ".join(zones) if zones else "the affected zones"
    if level == "critical":
        return (
            f"Reallocate drivers into {zone_phrase} immediately — current "
            "capacity cannot finish the window. Call check_driver_coverage."
        )
    if level == "high":
        return (
            f"Pull a driver back early or shift one into {zone_phrase}; "
            "window is trending late."
        )
    if level == "medium":
        return f"Monitor closely. {drivers} driver(s) available across {zone_phrase}."
    return "On track. No action needed."


def assess_window_risks(window_id: str | None, now: datetime | None = None) -> list[WindowRisk]:
    now = now or datetime.now()
    orders = data_store.get_orders()
    active_orders = [o for o in orders if o.status not in TERMINAL_STATUSES]
    drivers = data_store.get_drivers()
    available_drivers_total = sum(1 for d in drivers if d.status == "available")

    windows = [window_id] if window_id else sorted({o.delivery_window for o in active_orders})

    out: list[WindowRisk] = []
    for w in windows:
        try:
            start, end = _window_bounds(w, now.date())
        except Exception:
            continue
        window_orders = [o for o in active_orders if o.delivery_window == w]
        zones = sorted({o.zone for o in window_orders})
        minutes_remaining = max(int((end - now).total_seconds() // 60), 0)
        level = _classify_window_risk(
            len(window_orders), minutes_remaining, available_drivers_total
        )
        out.append(
            WindowRisk(
                window_id=w,
                window_start=start.isoformat(),
                window_end=end.isoformat(),
                orders_remaining=len(window_orders),
                time_remaining_minutes=minutes_remaining,
                available_drivers=available_drivers_total,
                risk_level=level,  # type: ignore[arg-type]
                affected_zones=zones,
                recommendation=_window_recommendation(level, available_drivers_total, zones),
            )
        )
    return out


# ─── check_driver_coverage ────────────────────────────────────────────────────


def _coverage_recommendation(
    zone: str, status: str, driver_count: int, biker_count: int, pending: int
) -> str:
    """NEW: zone-aware suggestion. Downtown is a special case (needs cars)."""
    if status == "covered":
        return f"{zone} is covered — no action needed."
    if zone == "Downtown" and driver_count == 0 and pending > 0:
        return (
            "Downtown has bikers only; most deliveries here need a car. "
            "Pull a car driver from Midtown or Chelsea."
        )
    if status == "uncovered":
        return (
            f"{zone} has no available staff for {pending} pending order(s). "
            "Reassign from an adjacent covered zone immediately."
        )
    return (
        f"{zone} is at risk: {driver_count} driver(s) + {biker_count} biker(s) "
        f"against {pending} pending order(s). Consider pulling 1 extra."
    )


def assess_zone_coverage(zone: str | None) -> list[ZoneCoverage] | NotFound:
    if zone is not None and zone not in VALID_ZONES:
        return NotFound(
            entity="zone",
            id=zone,
            message=(
                f"Unknown zone {zone!r}. Valid zones: {', '.join(VALID_ZONES)}."
            ),
        )
    zones = [zone] if zone else VALID_ZONES
    drivers = data_store.get_drivers()
    orders = data_store.get_orders()

    out: list[ZoneCoverage] = []
    for z in zones:
        zone_drivers = [d for d in drivers if z in d.zones]
        out_drivers = [d for d in zone_drivers if d.status == "called_out"]
        present = [d for d in zone_drivers if d.status != "called_out"]
        bikers = [d for d in present if d.type == "biker"]
        cars = [d for d in present if d.type == "driver"]

        pending = [
            o for o in orders if o.zone == z and o.status not in TERMINAL_STATUSES
        ]
        windows = sorted({o.delivery_window for o in pending})

        # NEW: account for biker vs. driver mix and the Downtown special case
        # (agent.py used a different OK/AT_RISK/CRITICAL scale keyed only on
        # `available`, which the spec doesn't match).
        downtown_cars_required = z == "Downtown" and len(cars) == 0 and pending
        if len(present) == 0 and pending:
            status: str = "uncovered"
        elif downtown_cars_required:
            status = "at_risk"
        elif pending and len(present) < max(1, len(pending) // 3):
            status = "at_risk"
        else:
            status = "covered"

        out.append(
            ZoneCoverage(
                zone=z,
                expected_drivers=len(zone_drivers),
                present_drivers=len(present),
                out_drivers=len(out_drivers),
                biker_count=len(bikers),
                driver_count=len(cars),
                coverage_status=status,  # type: ignore[arg-type]
                affected_window_ids=windows,
                recommendation=_coverage_recommendation(
                    z, status, len(cars), len(bikers), len(pending)
                ),
            )
        )
    return out


# ─── check_driver_reservation ─────────────────────────────────────────────────


def _weight_class(order: Order) -> str:
    """NEW: derive light/medium/heavy from item flags + total_items."""
    if order.has_heavy_items:
        return "heavy"
    if order.total_items > 8:
        return "medium"
    return "light"


def assess_reservation(
    order_id: str, proposed_driver_id: str
) -> ReservationCheck | NotFound:
    """NEW per-order/per-driver decision. agent.py only had a coarse
    "scan all large orders" version; the spec requires a focused check."""
    order = data_store.get_order(order_id)
    if order is None:
        return NotFound(
            entity="order",
            id=order_id,
            message=f"Order {order_id} not found.",
        )
    driver = data_store.get_driver(proposed_driver_id)
    if driver is None:
        return NotFound(
            entity="driver",
            id=proposed_driver_id,
            message=f"Driver {proposed_driver_id} not found.",
        )

    weight = _weight_class(order)
    biker_eligible = not order.needs_driver  # mirrors backend `needs_driver`

    drivers = data_store.get_drivers()
    available_cars = [
        d for d in drivers if d.status == "available" and d.type == "driver"
    ]
    will_consume_car = driver.type == "driver"
    remaining_cars = max(
        len(available_cars) - (1 if will_consume_car and driver in available_cars else 0),
        0,
    )

    orders = data_store.get_orders()
    upcoming_large = [
        o
        for o in orders
        if o.id != order_id
        and o.needs_driver
        and o.status not in TERMINAL_STATUSES
    ]

    if driver.type == "biker":
        if order.needs_driver:
            decision = "block"
            reasoning = (
                f"{driver.name} is a biker but {order.id} requires a car "
                f"(weight={weight}, items={order.total_items}). Assign a car driver."
            )
        else:
            decision = "approve"
            reasoning = (
                f"{driver.name} (biker) is appropriate for {order.id} "
                f"(weight={weight}, items={order.total_items})."
            )
    else:  # driver.type == "driver"
        if remaining_cars < len(upcoming_large):
            decision = "block"
            reasoning = (
                f"Assigning {driver.name} would leave {remaining_cars} car driver(s) "
                f"for {len(upcoming_large)} upcoming large order(s). Use a biker if eligible."
            )
        elif biker_eligible and weight != "heavy":
            decision = "warn"
            reasoning = (
                f"{order.id} is biker-eligible (weight={weight}, items={order.total_items}). "
                f"Using {driver.name} (car) is fine but wastes a scarce resource — "
                f"prefer a biker if one is available in {order.zone}."
            )
        else:
            decision = "approve"
            reasoning = (
                f"{order.id} genuinely needs a car (weight={weight}, items={order.total_items}). "
                f"{driver.name} is the right call."
            )

    return ReservationCheck(
        order_id=order.id,
        proposed_driver_id=driver.id,
        order_size=order.total_items,
        order_weight_class=weight,  # type: ignore[arg-type]
        biker_eligible=biker_eligible,
        drivers_remaining_after_assignment=remaining_cars,
        upcoming_large_orders_in_queue=len(upcoming_large),
        decision=decision,  # type: ignore[arg-type]
        reasoning=reasoning,
    )
