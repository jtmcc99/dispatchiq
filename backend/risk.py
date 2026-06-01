"""Pure risk-classification helpers used by the DispatchIQ agent.

These were originally inlined in ``agent.py`` but are also needed by the
sibling ``mcp_server`` package, which can't import ``agent.py`` directly
because that pulls in ``anthropic`` (a backend-only dependency). Extracting
them here keeps the behavior identical while letting both packages share it.
"""

from __future__ import annotations

from datetime import datetime, time

from models import Order


def _parse_window(window: str) -> tuple[time, time]:
    start_str, end_str = window.split("-")
    start_h, start_m = map(int, start_str.split(":"))
    end_h, end_m = map(int, end_str.split(":"))
    return time(start_h, start_m), time(end_h, end_m)


def compute_risk_level(order: Order, now: datetime) -> str:
    if order.status in ("delivered", "failed"):
        return "green"
    if order.status == "dispatched":
        return "green"

    try:
        start_t, end_t = _parse_window(order.delivery_window)
    except Exception:
        return "green"

    start = datetime.combine(now.date(), start_t)
    end = datetime.combine(now.date(), end_t)

    minutes_into_window = (now - start).total_seconds() / 60
    minutes_to_end = (end - now).total_seconds() / 60

    if now < start:
        return "green"

    if now >= end and order.status not in ("dispatched", "delivered"):
        return "red"
    if minutes_into_window > 30 and order.status in ("received", "picking"):
        return "red"
    if minutes_into_window > 15 and order.status in ("received", "picking"):
        return "yellow"
    if minutes_to_end < 20 and order.status in ("received", "picking", "picked"):
        return "yellow"

    return "green"
