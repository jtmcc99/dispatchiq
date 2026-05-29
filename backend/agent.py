from __future__ import annotations

"""
DispatchIQ Agent — Claude-powered operations monitor.

The agent uses tool_use to inspect order state, detect exceptions,
and generate CS notifications. It runs in a background loop.
"""

import json
import uuid
import asyncio
from datetime import datetime
from typing import Any

import anthropic

import data_store
from models import Exception_, CSNotification, Order
from risk import _parse_window, compute_risk_level  # re-export for callers

client = anthropic.Anthropic()
MODEL = "claude-sonnet-4-20250514"


# ─── Tool implementations ─────────────────────────────────────────────────────

def tool_check_window_risk(delivery_window: str) -> dict:
    orders = data_store.get_orders()
    window_orders = [o for o in orders if o.delivery_window == delivery_window]

    if not window_orders:
        return {"window": delivery_window, "total": 0, "message": "No orders in this window"}

    now = datetime.now()
    at_risk = []
    status_counts = {}

    for order in window_orders:
        risk = compute_risk_level(order, now)
        status_counts[order.status] = status_counts.get(order.status, 0) + 1
        if risk in ("yellow", "red"):
            at_risk.append({
                "order_id": order.id,
                "customer": order.customer_name,
                "status": order.status,
                "risk": risk,
                "zone": order.zone,
                "needs_driver": order.needs_driver,
                "total_items": order.total_items,
            })

    drivers = data_store.get_drivers()
    available_drivers = [d for d in drivers if d.status == "available"]

    return {
        "window": delivery_window,
        "total_orders": len(window_orders),
        "status_breakdown": status_counts,
        "at_risk_count": len(at_risk),
        "at_risk_orders": at_risk,
        "available_drivers": len(available_drivers),
        "risk_assessment": (
            "HIGH" if len(at_risk) > 3 else
            "MEDIUM" if len(at_risk) > 1 else
            "LOW"
        ),
    }


def tool_flag_missing_item(order_id: str, item_name: str) -> dict:
    order = data_store.get_order(order_id)
    if not order:
        return {"error": f"Order {order_id} not found"}

    item = next((i for i in order.items if i.name.lower() == item_name.lower()), None)
    is_core = item.is_core_item if item else False
    item_label = item.name if item else item_name

    return {
        "order_id": order_id,
        "customer": order.customer_name,
        "item": item_label,
        "is_core_item": is_core,
        "order_status": order.status,
        "severity": "high" if is_core else "low",
        "recommendation": (
            "CRITICAL: Do not dispatch. Notify CS immediately (IMMEDIATE notification). Offer substitution or cancellation."
            if is_core else
            "Create a PENDING_BATCH notification. It will be bundled with other OOS items when picking completes."
        ),
    }


def tool_check_driver_coverage(zone: str) -> dict:
    drivers = data_store.get_drivers()
    zone_drivers = [d for d in drivers if zone in d.zones]
    available = [d for d in zone_drivers if d.status == "available"]
    called_out = [d for d in zone_drivers if d.status == "called_out"]
    on_delivery = [d for d in zone_drivers if d.status == "on_delivery"]

    orders = data_store.get_orders()
    pending_in_zone = [
        o for o in orders
        if o.zone == zone and o.status not in ("delivered", "failed", "dispatched")
    ]

    return {
        "zone": zone,
        "total_drivers": len(zone_drivers),
        "available": len(available),
        "on_delivery": len(on_delivery),
        "called_out": len(called_out),
        "called_out_names": [d.name for d in called_out],
        "pending_orders_in_zone": len(pending_in_zone),
        "coverage_status": (
            "CRITICAL" if len(available) == 0 and len(pending_in_zone) > 0 else
            "AT_RISK" if len(available) < len(pending_in_zone) / 3 else
            "OK"
        ),
        "available_driver_names": [f"{d.name} ({d.type})" for d in available],
    }


def tool_check_driver_reservation() -> dict:
    """Scan all upcoming large/heavy orders and check if enough real drivers are available."""
    orders = data_store.get_orders()
    drivers = data_store.get_drivers()

    available_car_drivers = [d for d in drivers if d.status == "available" and d.type == "driver"]
    on_delivery_car_drivers = [d for d in drivers if d.status == "on_delivery" and d.type == "driver"]

    # Large orders not yet dispatched
    large_orders = [
        o for o in orders
        if o.needs_driver and o.status not in ("dispatched", "delivered", "failed")
    ]

    if not large_orders:
        return {
            "status": "OK",
            "message": "No upcoming large orders requiring dedicated drivers",
            "available_car_drivers": len(available_car_drivers),
        }

    by_window: dict[str, list] = {}
    for o in large_orders:
        by_window.setdefault(o.delivery_window, []).append({
            "order_id": o.id,
            "customer": o.customer_name,
            "zone": o.zone,
            "total_items": o.total_items,
            "has_heavy_items": o.has_heavy_items,
            "status": o.status,
        })

    warnings = []
    for window, window_large in by_window.items():
        if len(window_large) > len(available_car_drivers):
            warnings.append({
                "window": window,
                "large_orders_needing_driver": len(window_large),
                "available_car_drivers": len(available_car_drivers),
                "gap": len(window_large) - len(available_car_drivers),
                "at_risk_orders": window_large,
            })

    return {
        "status": "WARNING" if warnings else "OK",
        "total_large_orders": len(large_orders),
        "available_car_drivers": len(available_car_drivers),
        "car_drivers_on_delivery": len(on_delivery_car_drivers),
        "large_orders_by_window": by_window,
        "warnings": warnings,
        "recommendation": (
            f"Driver shortage: {len(warnings)} window(s) have more large orders than available drivers. "
            "Consider pulling car drivers back early or rebalancing loads."
            if warnings else
            "Driver supply looks adequate for upcoming large orders."
        ),
    }


def tool_create_exception(
    exc_type: str,
    severity: str,
    description: str,
    agent_recommendation: str,
    order_id: str = None,
) -> dict:
    exc = Exception_(
        id=f"EXC-{uuid.uuid4().hex[:8].upper()}",
        type=exc_type,
        order_id=order_id,
        severity=severity,
        description=description,
        agent_recommendation=agent_recommendation,
        status="open",
        cs_notified=False,
        created_at=datetime.now().isoformat(),
    )
    result = data_store.create_exception(exc)
    return {"exception_id": result.id, "created": result.id == exc.id, "duplicate": result.id != exc.id}


def tool_generate_cs_notification(
    order_id: str,
    issue_type: str,
    customer_message: str,
    details: str,
    is_immediate: bool = False,
) -> dict:
    """Create a CS notification. Set is_immediate=True for core item issues (shown right away).
    For minor OOS during picking, use is_immediate=False — it will be batched at pick completion."""
    order = data_store.get_order(order_id)
    customer_name = order.customer_name if order else "Unknown Customer"

    status = "pending" if is_immediate else "pending_batch"
    subtype = "immediate" if is_immediate else "standard"

    notif = CSNotification(
        id=f"CS-{uuid.uuid4().hex[:8].upper()}",
        order_id=order_id,
        customer_name=customer_name,
        issue_type=issue_type,
        details=details,
        customer_message=customer_message,
        status=status,
        notification_subtype=subtype,
        created_at=datetime.now().isoformat(),
    )
    result = data_store.create_cs_notification(notif)
    return {
        "notification_id": result.id,
        "status": status,
        "subtype": subtype,
        "customer": customer_name,
        "note": "Will be shown to CS immediately." if is_immediate else "Staged — will be batched when picking completes.",
    }


def tool_generate_shift_summary() -> dict:
    orders = data_store.get_orders()
    exceptions = data_store.get_exceptions()
    notifications = data_store.get_cs_notifications()

    status_counts: dict[str, int] = {}
    for o in orders:
        status_counts[o.status] = status_counts.get(o.status, 0) + 1

    open_exceptions = [e for e in exceptions if e.status == "open"]
    resolved_exceptions = [e for e in exceptions if e.status == "resolved"]
    pending_notifications = [n for n in notifications if n.status == "pending"]

    windows = sorted(set(o.delivery_window for o in orders))
    now = datetime.now()
    window_stats = {}
    for w in windows:
        w_orders = [o for o in orders if o.delivery_window == w]
        late = [o for o in w_orders if compute_risk_level(o, now) == "red"]
        window_stats[w] = {
            "total": len(w_orders),
            "delivered": sum(1 for o in w_orders if o.status == "delivered"),
            "late_or_at_risk": len(late),
        }

    return {
        "total_orders": len(orders),
        "status_breakdown": status_counts,
        "window_stats": window_stats,
        "open_exceptions": len(open_exceptions),
        "resolved_exceptions": len(resolved_exceptions),
        "pending_cs_notifications": len(pending_notifications),
    }


# ─── Tool registry ─────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "check_window_risk",
        "description": "Calculate whether orders in a delivery window are at risk of being late.",
        "input_schema": {
            "type": "object",
            "properties": {
                "delivery_window": {"type": "string", "description": "e.g. '11:00-12:00'"}
            },
            "required": ["delivery_window"],
        },
    },
    {
        "name": "flag_missing_item",
        "description": "Evaluate criticality of a missing item. Core items need IMMEDIATE CS notification. Minor items get batched.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "item_name": {"type": "string"},
            },
            "required": ["order_id", "item_name"],
        },
    },
    {
        "name": "check_driver_coverage",
        "description": "Check driver availability and coverage gaps for a delivery zone.",
        "input_schema": {
            "type": "object",
            "properties": {"zone": {"type": "string"}},
            "required": ["zone"],
        },
    },
    {
        "name": "check_driver_reservation",
        "description": "Scan all upcoming large/heavy orders (needs_driver=True) and verify enough car drivers are reserved. Warns if driver supply is insufficient for large orders in upcoming windows.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "create_exception",
        "description": "Create an exception record for an operational issue.",
        "input_schema": {
            "type": "object",
            "properties": {
                "exc_type": {
                    "type": "string",
                    "enum": ["late_risk", "missing_item", "coverage_gap", "delivery_dispute", "driver_reservation"],
                },
                "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                "description": {"type": "string"},
                "agent_recommendation": {"type": "string"},
                "order_id": {"type": "string"},
            },
            "required": ["exc_type", "severity", "description", "agent_recommendation"],
        },
    },
    {
        "name": "generate_cs_notification",
        "description": (
            "Create a CS queue notification. "
            "RULES: (1) Core item missing → is_immediate=True, notify before dispatch. "
            "(2) Minor OOS during picking → is_immediate=False, will be batched at pick completion. "
            "(3) Late delivery / dispute → is_immediate=True."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "issue_type": {"type": "string"},
                "customer_message": {"type": "string"},
                "details": {"type": "string"},
                "is_immediate": {
                    "type": "boolean",
                    "description": "True for core item OOS, late delivery, disputes. False for minor OOS (will be batched).",
                },
            },
            "required": ["order_id", "issue_type", "customer_message", "details", "is_immediate"],
        },
    },
    {
        "name": "generate_shift_summary",
        "description": "Compile current shift statistics.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


def dispatch_tool(tool_name: str, tool_input: dict) -> Any:
    if tool_name == "check_window_risk":
        return tool_check_window_risk(tool_input["delivery_window"])
    elif tool_name == "flag_missing_item":
        return tool_flag_missing_item(tool_input["order_id"], tool_input["item_name"])
    elif tool_name == "check_driver_coverage":
        return tool_check_driver_coverage(tool_input["zone"])
    elif tool_name == "check_driver_reservation":
        return tool_check_driver_reservation()
    elif tool_name == "create_exception":
        return tool_create_exception(**tool_input)
    elif tool_name == "generate_cs_notification":
        return tool_generate_cs_notification(**tool_input)
    elif tool_name == "generate_shift_summary":
        return tool_generate_shift_summary()
    else:
        return {"error": f"Unknown tool: {tool_name}"}


# ─── Monitoring loop ──────────────────────────────────────────────────────────

def build_monitoring_prompt() -> str:
    orders = data_store.get_orders()
    drivers = data_store.get_drivers()
    now = datetime.now()

    active_orders = [o for o in orders if o.status not in ("delivered", "failed")]
    windows = sorted(set(o.delivery_window for o in active_orders))
    orders_with_missing = [o for o in orders if o.missing_items and o.status not in ("delivered", "failed")]
    called_out = [d for d in drivers if d.status == "called_out"]
    active_zones = sorted(set(o.zone for o in active_orders))
    large_orders = [o for o in active_orders if o.needs_driver]

    return f"""You are DispatchIQ, an agentic operations monitor for a last-mile delivery company.
Current time: {now.strftime('%I:%M %p')} on {now.strftime('%A, %B %d, %Y')}

Your job this cycle:
1. Check every active delivery window for late-risk orders
2. Flag orders with missing items — core items get IMMEDIATE CS notification, minor OOS gets batched
3. Check driver coverage for all active zones
4. Run check_driver_reservation to see if large orders have enough car drivers
5. Create exceptions for issues found — skip if already open for the same order/type
6. Do NOT create duplicate exceptions

ACTIVE WINDOWS: {', '.join(windows) if windows else 'None'}
ORDERS WITH MISSING ITEMS: {json.dumps([{'id': o.id, 'customer': o.customer_name, 'missing': o.missing_items, 'status': o.status} for o in orders_with_missing])}
CALLED-OUT DRIVERS: {', '.join(d.name for d in called_out) if called_out else 'None'}
ACTIVE ZONES: {', '.join(active_zones) if active_zones else 'None'}
LARGE ORDERS NEEDING DRIVERS: {json.dumps([{'id': o.id, 'window': o.delivery_window, 'items': o.total_items, 'heavy': o.has_heavy_items} for o in large_orders])}

After analysis, give a concise ops summary."""


def run_agent_cycle() -> dict:
    prompt = build_monitoring_prompt()
    messages = [{"role": "user", "content": prompt}]
    exceptions_before = len(data_store.get_exceptions())
    notifications_before = len([n for n in data_store.get_cs_notifications() if n.status != "pending_batch"])

    final_text = ""
    max_iterations = 20

    for _ in range(max_iterations):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            tools=TOOLS,
            messages=messages,
        )

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        text_blocks = [b for b in response.content if b.type == "text"]
        if text_blocks:
            final_text = text_blocks[-1].text

        if response.stop_reason == "end_turn" or not tool_uses:
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for tu in tool_uses:
            result = dispatch_tool(tu.name, tu.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(result),
            })
        messages.append({"role": "user", "content": tool_results})

    exceptions_after = len(data_store.get_exceptions())
    notifications_after = len([n for n in data_store.get_cs_notifications() if n.status != "pending_batch"])

    return {
        "status": "completed",
        "exceptions_detected": exceptions_after - exceptions_before,
        "notifications_created": notifications_after - notifications_before,
        "summary": final_text or "Agent cycle completed.",
        "timestamp": datetime.now().isoformat(),
    }


async def generate_shift_summary_structured() -> dict:
    """Ask Claude to return a structured JSON shift briefing."""
    stats = tool_generate_shift_summary()
    exceptions = data_store.get_exceptions()
    notifications = data_store.get_cs_notifications()

    open_exc = [e for e in exceptions if e.status == "open"]
    pending_notifs = [n for n in notifications if n.status == "pending"]

    prompt = f"""You are DispatchIQ. Generate an end-of-shift briefing as a JSON object.

Current shift data:
{json.dumps(stats, indent=2)}

Open exceptions ({len(open_exc)}):
{json.dumps([{{'type': e.type, 'severity': e.severity, 'description': e.description}} for e in open_exc], indent=2)}

Pending CS notifications ({len(pending_notifs)}):
{json.dumps([{{'order_id': n.order_id, 'issue': n.issue_type}} for n in pending_notifs], indent=2)}

Return ONLY valid JSON with this exact structure:
{{
  "handoff_status": "clean" | "issues" | "critical",
  "critical_issues": [
    {{"title": "short title", "detail": "one sentence detail", "action": "what next shift must do"}}
  ],
  "next_priorities": [
    "Action item 1 — concrete and specific",
    "Action item 2 — concrete and specific"
  ],
  "operational_notes": "1-2 sentences of context for next shift manager"
}}

critical_issues should only include genuinely urgent items (open high-severity exceptions, unhandled core item CS notifications).
If nothing critical, return empty array."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        return {
            "handoff_status": "issues",
            "critical_issues": [],
            "next_priorities": ["Review open exceptions", "Clear pending CS notifications"],
            "operational_notes": text[:500],
        }


# ─── Background monitor ────────────────────────────────────────────────────────

class AgentMonitor:
    def __init__(self, interval_seconds: int = 60):
        self.interval = interval_seconds
        self.last_result: dict = {}
        self.running = False

    async def run_loop(self):
        self.running = True
        while self.running:
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, run_agent_cycle)
                self.last_result = result
            except Exception as e:
                self.last_result = {
                    "status": "error",
                    "error": str(e),
                    "timestamp": datetime.now().isoformat(),
                }
            await asyncio.sleep(self.interval)

    def get_status(self) -> dict:
        return self.last_result
