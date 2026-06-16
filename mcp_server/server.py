"""DispatchIQ MCP server.

Exposes DispatchIQ's operations tools over the Model Context Protocol so any
MCP-compatible client (Claude Desktop, MCP Inspector, custom agent) can drive
the same data layer the FastAPI backend uses.

This file deliberately stays a thin wrapper layer: each `@mcp.tool()` here
should be little more than a typed signature, the canonical docstring, and a
call into a helper. All shared types live in `_models.py`; all computations
and data-store calls live in `_logic.py`.
"""

from __future__ import annotations

from typing import Literal

import _path  # noqa: F401  (splices ../backend onto sys.path)

import data_store
from mcp.server.fastmcp import FastMCP

from _assess import assess_reservation, assess_window_risks, assess_zone_coverage
from _models import (
    CSNotification,
    ExceptionRecord,
    ExceptionType,
    MissingItemAssessment,
    NotFound,
    ReservationCheck,
    ShiftSummary,
    WindowRisk,
    ZoneCoverage,
)
from _records import build_cs_notification, build_shift_summary, create_exception_record

from late_risk_shadow import score_order_by_id


mcp = FastMCP("dispatchiq")


# ─── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
def flag_missing_item(
    order_id: str, item_name: str
) -> MissingItemAssessment | NotFound:
    """Decide how to handle a missing or out-of-stock item on an order.

    Call this whenever a picker reports an item is unavailable, before
    deciding whether to send an IMMEDIATE customer notification or queue a
    batched one. The returned `severity` and `recommendation` tell you which
    path to take: core items (the customer's main protein, the centerpiece of
    the order) require immediate CS contact so the customer can substitute or
    cancel before dispatch; minor items are bundled into a single notification
    when picking completes.

    On unknown order_id this returns a NotFound result (kind="not_found",
    entity="order") rather than raising — branch on the response shape.
    """
    order = data_store.get_order(order_id)
    if order is None:
        return NotFound(
            entity="order",
            id=order_id,
            message=f"Order {order_id} not found.",
        )

    item = next(
        (i for i in order.items if i.name.lower() == item_name.lower()),
        None,
    )
    is_core = item.is_core_item if item else False
    item_label = item.name if item else item_name

    return MissingItemAssessment(
        order_id=order_id,
        customer=order.customer_name,
        item=item_label,
        is_core_item=is_core,
        order_status=order.status,
        severity="high" if is_core else "low",
        recommendation=(
            "CRITICAL: Do not dispatch. Notify CS immediately (IMMEDIATE "
            "notification). Offer substitution or cancellation."
            if is_core
            else "Create a PENDING_BATCH notification. It will be bundled "
            "with other OOS items when picking completes."
        ),
    )


@mcp.tool()
def check_window_risk(window_id: str | None = None) -> list[WindowRisk]:
    """
    Assess whether delivery windows are at risk of missing their deadline based on
    orders remaining, time left, and available drivers.

    When to call this:
    - At the start of a shift, to get a baseline read on all active windows
    - Immediately after a driver calls out or coverage changes
    - When the ops manager asks "are we going to make it?" for one or all windows
    - Before deciding whether to reassign drivers across zones

    Args:
        window_id: If provided, returns risk for only that delivery window. If
            omitted, returns risk assessments for ALL currently active windows.
            Pass a specific id only when you already know the window the user
            is asking about; otherwise leave blank for a full sweep.

    Returns a list of WindowRisk objects, one per window assessed. The
    `risk_level` reflects the math of (orders_remaining / time_remaining) against
    (available_drivers × throughput_per_driver). Use the `recommendation` field
    as the suggested action — it accounts for the affected zones and current
    coverage, so prefer relaying or building on it rather than re-deriving advice.

    Notes:
    - Returns an empty list if there are no active windows (off-shift).
    - `risk_level=critical` means the window is mathematically infeasible without
      reallocation — call check_driver_coverage next to identify reallocation
      candidates.
    """
    return assess_window_risks(window_id)


@mcp.tool()
def check_driver_coverage(zone: str | None = None) -> list[ZoneCoverage] | NotFound:
    """
    Identify which delivery zones have adequate driver coverage and which are at
    risk or uncovered, accounting for driver type (biker vs. driver) and call-outs.

    When to call this:
    - Immediately after a driver call-out is reported
    - When check_window_risk returns a window at high/critical risk and you need
      to understand which zones are short-staffed
    - At shift start to verify the planned roster actually showed up
    - Before assigning a driver to an order, when you're unsure if the source
      zone can spare them

    Args:
        zone: If provided, returns coverage for only that zone. Valid zones are
            "Uptown", "Midtown", "Chelsea", "East Village", "Downtown". If
            omitted, returns coverage for all zones.

    Returns a list of ZoneCoverage objects. `coverage_status` reflects whether
    the zone has enough of the RIGHT type of driver — a zone full of bikers but
    no drivers is `at_risk` or `uncovered` if it has orders too far for bikers.
    `affected_window_ids` lets you correlate coverage gaps with the specific
    delivery windows at risk.

    Notes:
    - Downtown specifically requires drivers (not bikers) for most orders due to
      distance — bikers-only coverage in Downtown should typically read as `at_risk`.
    - The `recommendation` field already accounts for reallocation candidates
      from adjacent zones; use it as a starting point rather than re-deriving.
    - On unknown `zone` this returns a NotFound result (kind="not_found",
      entity="zone") rather than raising — branch on the response shape.
    """
    return assess_zone_coverage(zone)


@mcp.tool()
def check_driver_reservation(
    order_id: str, proposed_driver_id: str
) -> ReservationCheck | NotFound:
    """
    Decide whether assigning a specific driver to a specific order is a good idea,
    given that drivers are a finite resource and may be needed for upcoming
    large/heavy orders that bikers can't handle.

    When to call this:
    - BEFORE confirming any driver-to-order assignment
    - Specifically critical when the order is small/light AND a driver (not a
      biker) is being proposed — this is the "burning your only driver on a
      delivery a biker could do" failure mode
    - Skip this for biker-to-order assignments; the reservation logic only
      matters for the limited driver pool

    Args:
        order_id: The order being assigned.
        proposed_driver_id: The driver someone wants to assign to it.

    Returns a ReservationCheck. The `decision` field is the recommendation:
    - "approve": this assignment is fine, the order genuinely needs this driver
      or there's plenty of driver capacity remaining
    - "warn": this works but isn't ideal — a biker could handle this order and
      drivers are limited. Surface this to the user but don't block.
    - "block": this would leave insufficient drivers for known upcoming
      large/heavy orders. Recommend a biker instead.

    Notes:
    - "Heavy" weight class generally requires a driver regardless of order size.
    - The `reasoning` field explains the decision in operational terms — relay
      it to the user rather than re-stating the math.
    - On unknown order_id or proposed_driver_id this returns a NotFound result
      (kind="not_found", entity="order" or "driver") rather than raising —
      branch on the response shape.
    """
    return assess_reservation(order_id, proposed_driver_id)


@mcp.tool()
def create_exception(
    type: ExceptionType,
    severity: Literal["low", "medium", "high", "critical"],
    details: str,
    order_id: str | None = None,
) -> ExceptionRecord:
    """
    Create a formal exception record for an operational issue that needs tracking,
    follow-up, or visibility in the shift summary.

    When to call this:
    - After confirming a missing core item that will require customer contact
    - After identifying a coverage gap that won't be resolved within the current
      window
    - When a delivery window has officially failed (orders went out late or
      didn't go out at all)
    - When a driver call-out creates a downstream operational issue beyond
      what check_driver_coverage can handle automatically
    - Generally: when an issue needs to persist beyond this conversation and be
      visible to the next shift

    Do NOT call this for:
    - Informational risk assessments (use check_window_risk instead — it doesn't
      create records)
    - Reservation warnings (the warning itself is sufficient; only escalate to
      an exception if the warning is ignored and creates an actual problem)

    Args:
        type: The category of exception. Use the most specific value that fits.
        severity: How urgently this needs attention. "critical" should be rare —
            reserve it for issues that will affect customer experience within
            the current window if not handled.
        details: A 1-3 sentence description of what happened and what context
            matters. This is read by humans, so write it for an ops manager
            taking over the next shift.
        order_id: Required for order-scoped exceptions (missing items, etc).
            Omit for shift-wide exceptions (coverage gaps, driver call-outs).

    Returns the created ExceptionRecord with its assigned id and timestamp. The
    record will appear in the next generate_shift_summary call.

    Notes:
    - Calling this is a mutation — only do it when the user has confirmed action,
      not while exploring or discussing possibilities.
    """
    return create_exception_record(type, severity, details, order_id)


@mcp.tool()
def generate_cs_notification(
    order_id: str,
    exception_type: ExceptionType,
    additional_context: str | None = None,
) -> CSNotification | NotFound:
    """
    Generate a draft customer service notification — including suggested
    customer-facing script — for an issue affecting a specific order.

    When to call this:
    - After create_exception, when the exception affects a customer's order and
      they need to be informed
    - Specifically for: missing core items (immediate), missing minor items
      (batched at pick complete), late deliveries, substitutions
    - When the ops manager explicitly asks "what should we tell the customer?"

    Do NOT call this for:
    - Operational exceptions that don't affect a specific customer
      (coverage gaps, driver call-outs without downstream impact)
    - Issues that have been resolved before the customer would notice

    Args:
        order_id: The customer's order.
        exception_type: The type of issue affecting the order (used to set tone,
            timing, and script template).
        additional_context: Optional extra context that should inform the
            script — e.g., the specific substitution being offered, or a
            customer-specific preference noted in the order.

    Returns a CSNotification with a draft `suggested_script` that CS can use
    or adapt when contacting the customer. The `send_immediately` field reflects
    DispatchIQ's policy: core item failures get an immediate notification;
    minor items are batched and sent when picking completes.

    Notes:
    - The script is a suggestion. CS may modify it. Do not represent it as a
      committed message to the customer.
    - For batched minor-item notifications on the same order, this tool returns
      a single bundled notification rather than one per item.
    - The `suggested_script` is deliberately customer-facing and does not
      surface internal severity labels — severity tone is conveyed by phrasing,
      not by the literal word "critical." Internal severity is preserved on
      the underlying ExceptionRecord and visible in generate_shift_summary.
    - On unknown order_id this returns a NotFound result (kind="not_found",
      entity="order") rather than raising — branch on the response shape.
    """
    return build_cs_notification(order_id, exception_type, additional_context)


@mcp.tool()
def generate_shift_summary(shift_id: str | None = None) -> ShiftSummary | NotFound:
    """
    Produce a structured end-of-shift (or mid-shift) briefing covering completion
    metrics, delivery window progress, open exceptions, and unresolved CS items.

    When to call this:
    - At end-of-shift handoff, when the outgoing manager needs to brief the
      incoming one
    - Mid-shift, when an ops manager asks "what's the current state?"
    - When an incoming manager joins partway through a shift and needs to
      understand what they're inheriting
    - As the final step of a multi-tool investigation, to confirm the current
      state after several changes

    Args:
        shift_id: If provided, returns the summary for that specific shift. If
            omitted, returns the summary for the currently active shift. Pass
            a specific id only when the user is asking about a past shift.

    Returns a ShiftSummary structured for the incoming manager to read in under
    30 seconds. `top_priorities_for_next_shift` is the most actionable field —
    it surfaces the 3-5 items the next manager should focus on first, derived
    from open exceptions and unresolved CS items.

    Notes:
    - `shift_end` is null for mid-shift calls; treat its absence as "shift in
      progress."
    - Open exceptions and unresolved CS items embed the full records, not just
      ids — the caller doesn't need a second tool call to see them.
    - This tool is read-only; calling it does not close out the shift or
      resolve any exceptions.
    - On a malformed `shift_id` (anything that doesn't parse as
      "shift-YYYY-MM-DD") this returns a NotFound result (kind="not_found",
      entity="shift") rather than raising — branch on the response shape.
    - Known limitation: when called with a historical `shift_id`, the
      returned `shift_start` and `shift_end` correctly reflect that past
      date, but order metrics (orders_completed, orders_late, window_progress,
      open_exceptions, unresolved_cs_items) are computed against the CURRENT
      JSON state, not historically scoped to that shift. Acceptable for the
      live handoff use case this tool is designed for; flagged for future
      work if historical analytics is needed.
    """
    return build_shift_summary(shift_id)


@mcp.tool()
def predict_late_risk_shadow(order_id: str) -> dict | NotFound:
    """⚠️ SHADOW MODE — do not surface to end users or auto-act on the result.

    Returns the late-risk model's prediction for one order, plus the resolved
    feature inputs and the approximations applied when building them from
    live state. This tool exists so operators and analysts can inspect what
    the model would say. It is intentionally NOT wired into the dashboard,
    the agent's exception-creation path, or any user-facing flag.

    Why shadow only: the model was trained on synthetic data whose labels
    were derived from the same features the model sees, so the holdout eval
    is methodologically circular and cannot establish real-world predictive
    validity. See `docs/late-risk-eval-and-limitations.md` for the full
    rationale and the criteria for leaving shadow mode.

    The prediction is also appended to `backend/data/shadow_predictions.jsonl`
    so the log can be replayed against real outcomes later.

    Args:
        order_id: The order to score.

    Returns a dict with: `p_late` (probability), `would_flag` (boolean at
    threshold 0.5), `features_resolved`, `context_caveats`, plus a
    `shadow_mode: true` marker. On unknown order_id returns a NotFound
    result rather than raising.
    """
    prediction = score_order_by_id(order_id)
    if prediction is None:
        return NotFound(
            entity="order",
            id=order_id,
            message=f"Order {order_id} not found.",
        )
    return prediction.to_dict()


if __name__ == "__main__":
    mcp.run()
