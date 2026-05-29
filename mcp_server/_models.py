"""Pydantic response models for the DispatchIQ MCP server.

All MCP tool return types are defined here so `server.py` can stay a thin
wrapper layer around the FastMCP `@mcp.tool()` decorator. Field descriptions
match the spec the agent operates against; tweaking these will change the
schema the MCP client sees.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ─── Shared error envelope ────────────────────────────────────────────────────


class NotFound(BaseModel):
    """Structured "not found" result returned by lookup tools instead of
    raising. Discriminator: ``kind="not_found"`` — clients should branch on
    this field when consuming union return types.

    Fields:
        kind: Always ``"not_found"``. Acts as the discriminator for clients
            consuming Union return types.
        entity: Which kind of id failed to resolve (order, driver, zone, shift).
        id: The unresolved id, echoed back verbatim so the caller can quote it.
        message: Human-readable reason, safe to relay to the user.
    """

    kind: Literal["not_found"] = "not_found"
    entity: Literal["order", "driver", "zone", "shift"]
    id: str
    message: str


# ─── flag_missing_item ────────────────────────────────────────────────────────


class MissingItemAssessment(BaseModel):
    """Verdict for a single out-of-stock or missing line item on an order."""

    order_id: str
    customer: str = Field(description="Customer name on the order.")
    item: str = Field(
        description=(
            "Canonical item name as it appears on the order, or the caller-"
            "provided name if no matching line item was found."
        )
    )
    is_core_item: bool = Field(
        description=(
            "True if the item is flagged as a core item on the order. Core "
            "items require immediate CS notification before dispatch; non-core "
            "items are batched at pick completion."
        )
    )
    order_status: str = Field(
        description="Current order status (received, picking, picked, ...)."
    )
    severity: str = Field(description='"high" for core items, "low" otherwise.')
    recommendation: str = Field(
        description="Concrete next step for the agent or CS team."
    )


# ─── check_window_risk ────────────────────────────────────────────────────────


class WindowRisk(BaseModel):
    window_id: str
    window_start: str  # ISO timestamp
    window_end: str
    orders_remaining: int
    time_remaining_minutes: int
    available_drivers: int
    risk_level: Literal["low", "medium", "high", "critical"]
    affected_zones: list[str]
    recommendation: str


# ─── check_driver_coverage ────────────────────────────────────────────────────


class ZoneCoverage(BaseModel):
    zone: str
    expected_drivers: int
    present_drivers: int
    out_drivers: int
    biker_count: int
    driver_count: int
    coverage_status: Literal["covered", "at_risk", "uncovered"]
    affected_window_ids: list[str]
    recommendation: str


# ─── check_driver_reservation ─────────────────────────────────────────────────


class ReservationCheck(BaseModel):
    order_id: str
    proposed_driver_id: str
    order_size: int  # item count
    order_weight_class: Literal["light", "medium", "heavy"]
    biker_eligible: bool  # could a biker handle this order?
    drivers_remaining_after_assignment: int
    upcoming_large_orders_in_queue: int
    decision: Literal["approve", "warn", "block"]
    reasoning: str


# ─── Exceptions + CS ──────────────────────────────────────────────────────────


class ExceptionType(str, Enum):
    LATE_RISK = "late_risk"
    MISSING_CORE_ITEM = "missing_core_item"
    MISSING_MINOR_ITEM = "missing_minor_item"
    COVERAGE_GAP = "coverage_gap"
    DRIVER_CALLOUT = "driver_callout"
    WINDOW_FAILED = "window_failed"
    OTHER = "other"


class ExceptionRecord(BaseModel):
    exception_id: str
    order_id: str | None  # not all exceptions are order-scoped
    type: ExceptionType
    severity: Literal["low", "medium", "high", "critical"]
    details: str
    created_at: str  # ISO timestamp
    status: Literal["open", "acknowledged", "resolved"]


class CSNotification(BaseModel):
    notification_id: str
    order_id: str
    customer_name: str
    exception_summary: str
    suggested_script: str
    # core item failure = immediate, others = batch at pick complete
    send_immediately: bool
    status: Literal["pending", "handled"]
    created_at: str


# ─── generate_shift_summary ───────────────────────────────────────────────────


class WindowProgress(BaseModel):
    window_id: str
    window_label: str  # e.g., "10am-12pm Uptown"
    orders_planned: int
    orders_completed: int
    orders_late: int
    status: Literal["on_track", "at_risk", "missed"]


class ShiftSummary(BaseModel):
    shift_id: str
    shift_start: str
    shift_end: str | None  # null if mid-shift
    orders_completed: int
    orders_late: int
    orders_failed: int
    window_progress: list[WindowProgress]
    open_exceptions: list[ExceptionRecord]
    unresolved_cs_items: list[CSNotification]
    staffing_summary: str  # "Expected: 12 | Present: 11 | Out: 1"
    top_priorities_for_next_shift: list[str]
