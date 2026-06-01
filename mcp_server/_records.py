"""Record-mutation + summary helpers (exceptions, CS notifications, shift summary).

Anything that writes to the data store, or aggregates state for the shift
summary, lives here. Reuses ``backend/data_store.py`` for persistence and
``backend/agent.py``'s ``compute_risk_level`` for late-order classification.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

import _path  # noqa: F401  (path shim)

import data_store
from models import CSNotification as _BackendCSNotification
from models import Exception_, Order
from risk import _parse_window, compute_risk_level

from _assess import TERMINAL_STATUSES
from _models import (
    CSNotification,
    ExceptionRecord,
    ExceptionType,
    NotFound,
    ShiftSummary,
    WindowProgress,
)


# ─── ExceptionType ↔ backend type mapping ─────────────────────────────────────

# NEW: ExceptionType (MCP-side) ↔ backend Exception_.type (limited enum).
# The backend model uses a narrower set; we map at the boundary and keep the
# richer MCP type in the response by tagging it inside the description so the
# round-trip is lossless. Severity gets the same treatment ("critical" doesn't
# exist on the backend model).
_MCP_TO_BACKEND_TYPE: dict[ExceptionType, str] = {
    ExceptionType.LATE_RISK: "late_risk",
    ExceptionType.MISSING_CORE_ITEM: "missing_item",
    ExceptionType.MISSING_MINOR_ITEM: "missing_item",
    ExceptionType.COVERAGE_GAP: "coverage_gap",
    ExceptionType.DRIVER_CALLOUT: "coverage_gap",
    ExceptionType.WINDOW_FAILED: "late_risk",
    ExceptionType.OTHER: "late_risk",
}

_BACKEND_TO_MCP_DEFAULT: dict[str, ExceptionType] = {
    "late_risk": ExceptionType.LATE_RISK,
    "missing_item": ExceptionType.MISSING_CORE_ITEM,
    "coverage_gap": ExceptionType.COVERAGE_GAP,
    "delivery_dispute": ExceptionType.OTHER,
    "driver_reservation": ExceptionType.OTHER,
}

_TYPE_TAG = "[mcp:type="
_SEVERITY_TAG = "[mcp:severity="


def _encode_details(details: str, mcp_type: ExceptionType, severity: str) -> str:
    return f"{_TYPE_TAG}{mcp_type.value}]{_SEVERITY_TAG}{severity}] {details}"


def _decode_details(raw: str) -> tuple[str, ExceptionType | None, str | None]:
    text = raw
    mcp_type: ExceptionType | None = None
    severity: str | None = None
    if text.startswith(_TYPE_TAG):
        end = text.find("]")
        if end > 0:
            try:
                mcp_type = ExceptionType(text[len(_TYPE_TAG) : end])
            except ValueError:
                mcp_type = None
            text = text[end + 1 :]
    if text.startswith(_SEVERITY_TAG):
        end = text.find("]")
        if end > 0:
            severity = text[len(_SEVERITY_TAG) : end]
            text = text[end + 1 :]
    return text.lstrip(), mcp_type, severity


def _to_exception_record(exc: Exception_) -> ExceptionRecord:
    details, mcp_type, override_severity = _decode_details(exc.description)
    return ExceptionRecord(
        exception_id=exc.id,
        order_id=exc.order_id,
        type=mcp_type or _BACKEND_TO_MCP_DEFAULT.get(exc.type, ExceptionType.OTHER),
        severity=(override_severity or exc.severity),  # type: ignore[arg-type]
        details=details,
        created_at=exc.created_at,
        # NEW: backend uses "escalated"; surface as "acknowledged" to MCP clients.
        status=("acknowledged" if exc.status == "escalated" else exc.status),  # type: ignore[arg-type]
    )


def _mcp_type_of(exc: Exception_) -> ExceptionType:
    """Recover the spec-level ExceptionType from a stored backend record."""
    _, encoded, _ = _decode_details(exc.description)
    return encoded or _BACKEND_TO_MCP_DEFAULT.get(exc.type, ExceptionType.OTHER)


def create_exception_record(
    mcp_type: ExceptionType,
    severity: str,
    details: str,
    order_id: str | None,
) -> ExceptionRecord:
    backend_severity = "high" if severity == "critical" else severity
    backend_type = _MCP_TO_BACKEND_TYPE[mcp_type]

    # NEW: dedupe at the MCP layer using the spec-level ExceptionType so
    # MISSING_CORE_ITEM and MISSING_MINOR_ITEM stay distinct even though both
    # collapse to backend "missing_item". Same-type repeats still dedupe.
    if order_id:
        for existing in data_store.get_exceptions():
            if (
                existing.order_id == order_id
                and existing.status == "open"
                and _mcp_type_of(existing) == mcp_type
            ):
                return _to_exception_record(existing)

    exc = Exception_(
        id=f"EXC-{uuid.uuid4().hex[:8].upper()}",
        type=backend_type,  # type: ignore[arg-type]
        order_id=order_id,
        severity=backend_severity,  # type: ignore[arg-type]
        description=_encode_details(details, mcp_type, severity),
        agent_recommendation="(created via MCP server)",
        status="open",
        cs_notified=False,
        created_at=datetime.now().isoformat(),
    )

    # NEW: bypass data_store.create_exception, which dedupes on the narrower
    # backend `type` and would collapse CORE/MINOR pairs on the same order.
    # The MCP-side check above already enforces the wider dedupe key.
    all_exceptions = data_store.get_exceptions()
    all_exceptions.insert(0, exc)
    data_store.save_exceptions(all_exceptions)
    return _to_exception_record(exc)


# ─── CS notification templates ────────────────────────────────────────────────

# NEW: customer-facing script templates. Caller (CS) may edit before sending.
# Note for MISSING_*_ITEM: {summary} is filled by _script_clause_for(), which
# returns a self-contained clause (with its own verb) rather than a bare noun
# phrase. The template provides only connective tissue, so the script reads
# grammatically whether we have 1, N, or 0 known item names.
_SCRIPTS: dict[ExceptionType, str] = {
    ExceptionType.MISSING_CORE_ITEM: (
        "Hi {name}, this is {company}. We're picking your order and "
        "{summary}. We didn't want to send your delivery without checking "
        "first — would you like a substitute, a refund on that item, or "
        "to cancel the order? {extra}"
    ),
    ExceptionType.MISSING_MINOR_ITEM: (
        "Hi {name}, a quick heads-up that {summary} on your order. "
        "We'll refund and send everything else as planned. {extra}"
    ),
    ExceptionType.LATE_RISK: (
        "Hi {name}, your delivery is running a little behind. {summary} "
        "We're working to get it to you as soon as possible and we'll text "
        "again with an updated ETA. {extra}"
    ),
    ExceptionType.WINDOW_FAILED: (
        "Hi {name}, we weren't able to deliver your order in the promised "
        "window. {summary} We've applied a credit and will get this out to "
        "you ASAP. {extra}"
    ),
    ExceptionType.COVERAGE_GAP: (
        "Hi {name}, we're short-staffed in your area today and your delivery "
        "may run late. {summary} {extra}"
    ),
    ExceptionType.DRIVER_CALLOUT: (
        "Hi {name}, a driver assigned to your delivery had to step away. "
        "{summary} We're reassigning now and will update you shortly. {extra}"
    ),
    ExceptionType.OTHER: (
        "Hi {name}, we wanted to reach out about your order. {summary} {extra}"
    ),
}

# NEW: spec policy — core item failure is immediate; everything else batches.
_IMMEDIATE_TYPES: set[ExceptionType] = {
    ExceptionType.MISSING_CORE_ITEM,
    ExceptionType.LATE_RISK,
    ExceptionType.WINDOW_FAILED,
}


def _summary_for(
    exc_type: ExceptionType,
    order: Order,
    additional_context: str | None = None,
) -> str:
    """Short customer-readable label used in exception_summary + shift summary.

    Always grammatical on its own. For MISSING_*_ITEM specifically:
      - if order.missing_items is populated, join the item names
      - else if additional_context was supplied, use it as the natural phrasing
      - else fall back to a singular, grammatical generic that makes clear we
        don't yet know the specific item
    """
    if exc_type in {ExceptionType.MISSING_CORE_ITEM, ExceptionType.MISSING_MINOR_ITEM}:
        if order.missing_items:
            return ", ".join(order.missing_items)
        if additional_context and additional_context.strip():
            return additional_context.strip().rstrip(".")
        return f"an item on order {order.id} is currently unavailable"
    if exc_type in {ExceptionType.LATE_RISK, ExceptionType.WINDOW_FAILED}:
        return f"Your {order.delivery_window} window in {order.zone} is affected."
    return f"Order {order.id} in {order.zone}."


def _script_clause_for(exc_type: ExceptionType, order: Order) -> str:
    """Self-contained clause inserted at {summary} in the script template.

    Carries its own verb so the surrounding template stays grammatical for
    1, N, or 0 known item names. additional_context is delivered separately
    via {extra} and is intentionally not duplicated here.
    """
    if exc_type == ExceptionType.MISSING_CORE_ITEM:
        items = order.missing_items
        if len(items) == 1:
            return f"the {items[0]} is out of stock"
        if len(items) > 1:
            return f"the following items are out of stock: {', '.join(items)}"
        return "an item on your order is currently unavailable"
    if exc_type == ExceptionType.MISSING_MINOR_ITEM:
        items = order.missing_items
        if len(items) == 1:
            return f"{items[0]} won't be available"
        if len(items) > 1:
            return f"the following items won't be available: {', '.join(items)}"
        return "an item won't be available"
    if exc_type in {ExceptionType.LATE_RISK, ExceptionType.WINDOW_FAILED}:
        return f"Your {order.delivery_window} window in {order.zone} is affected."
    return f"Order {order.id} in {order.zone}."


def build_cs_notification(
    order_id: str,
    exc_type: ExceptionType,
    additional_context: str | None,
) -> CSNotification | NotFound:
    order = data_store.get_order(order_id)
    if order is None:
        return NotFound(
            entity="order",
            id=order_id,
            message=f"Order {order_id} not found.",
        )

    summary = _summary_for(exc_type, order, additional_context)
    script_clause = _script_clause_for(exc_type, order)
    extra = additional_context.strip() if additional_context else ""
    script = (
        _SCRIPTS.get(exc_type, _SCRIPTS[ExceptionType.OTHER])
        .format(
            name=order.customer_name,
            company="DispatchIQ",
            summary=script_clause,
            extra=extra,
        )
        .strip()
    )
    send_immediately = exc_type in _IMMEDIATE_TYPES

    backend_notif = _BackendCSNotification(
        id=f"CS-{uuid.uuid4().hex[:8].upper()}",
        order_id=order.id,
        customer_name=order.customer_name,
        issue_type=exc_type.value,
        details=summary,
        customer_message=script,
        status="pending" if send_immediately else "pending_batch",
        notification_subtype="immediate" if send_immediately else "batched",
        created_at=datetime.now().isoformat(),
    )
    saved = data_store.create_cs_notification(backend_notif)
    return CSNotification(
        notification_id=saved.id,
        order_id=saved.order_id or order.id,
        customer_name=saved.customer_name or order.customer_name,
        exception_summary=summary,
        suggested_script=script,
        send_immediately=send_immediately,
        # NEW: collapse the backend's tri-state status to the spec's binary one.
        status=("handled" if saved.status == "handled" else "pending"),
        created_at=saved.created_at,
    )


def _to_mcp_cs_notification(notif: _BackendCSNotification) -> CSNotification:
    return CSNotification(
        notification_id=notif.id,
        order_id=notif.order_id or "",
        customer_name=notif.customer_name or "",
        exception_summary=notif.details,
        suggested_script=notif.customer_message,
        send_immediately=notif.notification_subtype == "immediate",
        status=("handled" if notif.status == "handled" else "pending"),
        created_at=notif.created_at,
    )


# ─── generate_shift_summary ───────────────────────────────────────────────────


def _window_label(window_id: str, zones: list[str]) -> str:
    """NEW: "10:00-11:00" + ["Uptown"] → "10am-11am Uptown" (best-effort)."""
    try:
        start_t, end_t = _parse_window(window_id)

        def _fmt(t: time) -> str:
            hour = t.hour % 12 or 12
            suffix = "am" if t.hour < 12 else "pm"
            mins = f":{t.minute:02d}" if t.minute else ""
            return f"{hour}{mins}{suffix}"

        zone_str = ", ".join(zones) if zones else ""
        return f"{_fmt(start_t)}-{_fmt(end_t)} {zone_str}".strip()
    except Exception:
        return window_id


def _shift_id_for(now: datetime) -> str:
    """NEW: stable id derived from the calendar date of `now`."""
    return f"shift-{now.date().isoformat()}"


def build_shift_summary(shift_id: str | None) -> ShiftSummary | NotFound:
    now = datetime.now()
    target_id = shift_id or _shift_id_for(now)
    # NEW: shift bounds — 8am→8pm of the date encoded in target_id. If the
    # caller passed a past shift_id, shift_end is the historical 8pm; for the
    # active shift we leave shift_end null until after 8pm today.
    if shift_id is not None:
        try:
            shift_date = date.fromisoformat(target_id.removeprefix("shift-"))
        except ValueError:
            return NotFound(
                entity="shift",
                id=shift_id,
                message=(
                    f"shift_id {shift_id!r} is not in the expected "
                    "'shift-YYYY-MM-DD' format."
                ),
            )
    else:
        shift_date = now.date()
    shift_start = datetime.combine(shift_date, time(8, 0)).isoformat()
    shift_end_dt = datetime.combine(shift_date, time(20, 0))
    is_current = shift_date == now.date()
    shift_end: str | None = (
        None if (is_current and now < shift_end_dt) else shift_end_dt.isoformat()
    )

    orders = data_store.get_orders()
    drivers = data_store.get_drivers()
    exceptions = data_store.get_exceptions()
    notifications = data_store.get_cs_notifications()

    completed = [o for o in orders if o.status == "delivered"]
    failed = [o for o in orders if o.status == "failed"]
    late = [o for o in orders if compute_risk_level(o, now) == "red"]

    windows = sorted({o.delivery_window for o in orders})
    progress: list[WindowProgress] = []
    for w in windows:
        w_orders = [o for o in orders if o.delivery_window == w]
        w_completed = [o for o in w_orders if o.status == "delivered"]
        w_late = [o for o in w_orders if compute_risk_level(o, now) == "red"]
        zones = sorted({o.zone for o in w_orders})
        if w_late and any(o.status not in ("delivered", "failed") for o in w_late):
            status = "at_risk"
        elif w_late:
            status = "missed"
        else:
            status = "on_track"
        progress.append(
            WindowProgress(
                window_id=w,
                window_label=_window_label(w, zones),
                orders_planned=len(w_orders),
                orders_completed=len(w_completed),
                orders_late=len(w_late),
                status=status,  # type: ignore[arg-type]
            )
        )

    open_exc = [_to_exception_record(e) for e in exceptions if e.status == "open"]
    unresolved_cs = [
        _to_mcp_cs_notification(n)
        for n in notifications
        if n.status in ("pending", "pending_batch")
    ]

    expected = len(drivers)
    out_count = sum(1 for d in drivers if d.status == "called_out")
    staffing = f"Expected: {expected} | Present: {expected - out_count} | Out: {out_count}"

    # NEW: top priorities = at most 5 items, critical/high exceptions first,
    # then immediate CS notifications. Mirrors what an ops manager would scan.
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    priorities: list[str] = []
    for e in sorted(open_exc, key=lambda x: severity_rank.get(x.severity, 4)):
        priorities.append(f"[{e.severity.upper()}] {e.type.value}: {e.details}"[:140])
        if len(priorities) >= 3:
            break
    for n in unresolved_cs:
        if n.send_immediately and len(priorities) < 5:
            priorities.append(
                f"[CS-IMMEDIATE] {n.customer_name} — {n.exception_summary}"[:140]
            )
    if not priorities:
        priorities.append("No critical items. Continue normal monitoring.")

    return ShiftSummary(
        shift_id=target_id,
        shift_start=shift_start,
        shift_end=shift_end,
        orders_completed=len(completed),
        orders_late=len(late),
        orders_failed=len(failed),
        window_progress=progress,
        open_exceptions=open_exc,
        unresolved_cs_items=unresolved_cs,
        staffing_summary=staffing,
        top_priorities_for_next_shift=priorities,
    )
