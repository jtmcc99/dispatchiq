from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import data_store
from models import CSNotification
from agent import AgentMonitor, run_agent_cycle, generate_shift_summary_structured, compute_risk_level

# ─── App lifecycle ─────────────────────────────────────────────────────────────

agent_monitor = AgentMonitor(interval_seconds=60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(agent_monitor.run_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="DispatchIQ API", lifespan=lifespan)

cors_origins = os.getenv("DISPATCHIQ_CORS_ORIGINS")
allow_origins = [o.strip() for o in cors_origins.split(",")] if cors_origins else [
    "http://localhost:5173",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Request bodies ────────────────────────────────────────────────────────────

class OrderStatusUpdate(BaseModel):
    status: str
    driver_id: Optional[str] = None
    missing_items: Optional[list[str]] = None
    notes: Optional[str] = None
    items_picked: Optional[int] = None


class DriverStatusUpdate(BaseModel):
    status: str


class ExceptionUpdate(BaseModel):
    status: str


class CSNotificationUpdate(BaseModel):
    status: str


# ─── CS batching helper ────────────────────────────────────────────────────────

def _batch_pending_cs_notifications(order_id: str):
    """When an order moves to 'picked', consolidate all pending_batch OOS items into one notification."""
    notifications = data_store.get_cs_notifications()
    batch = [n for n in notifications if n.order_id == order_id and n.status == "pending_batch"]

    if not batch:
        return

    order = data_store.get_order(order_id)
    if not order:
        return

    # Collect OOS items from the staged notifications
    oos_items = []
    for n in batch:
        # item name is stored in details: "OOS during picking: <item>"
        if ": " in n.details:
            oos_items.append(n.details.split(": ", 1)[-1])
        else:
            oos_items.append(n.issue_type.replace("_", " ").title())

    items_str = ", ".join(oos_items)
    first_name = order.customer_name.split()[0]

    batched = CSNotification(
        id=f"CS-{uuid.uuid4().hex[:8].upper()}",
        order_id=order_id,
        customer_name=order.customer_name,
        issue_type="missing_items_batch",
        details=f"The following items were OOS during picking: {items_str}",
        customer_message=(
            f"Hi {first_name}, we wanted to let you know that the following items were "
            f"unavailable for your order: {items_str}. "
            f"We're sorry for any inconvenience — your order has been packed with everything else available."
        ),
        status="pending",
        notification_subtype="batched",
        created_at=datetime.now().isoformat(),
    )
    data_store.create_cs_notification(batched)

    # Remove the individual staged notifications
    remaining = [n for n in notifications if n not in batch]
    data_store.save_cs_notifications(remaining)


# ─── Orders ───────────────────────────────────────────────────────────────────

@app.get("/orders")
def list_orders(window: Optional[str] = None, zone: Optional[str] = None):
    orders = data_store.get_orders()
    now = datetime.now()

    result = []
    for o in orders:
        d = o.model_dump()
        d["risk_level"] = compute_risk_level(o, now)
        result.append(d)

    if window:
        result = [o for o in result if o["delivery_window"] == window]
    if zone:
        result = [o for o in result if o["zone"] == zone]
    return result


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    order = data_store.get_order(order_id)
    if not order:
        raise HTTPException(404, f"Order {order_id} not found")
    d = order.model_dump()
    d["risk_level"] = compute_risk_level(order, datetime.now())
    return d


@app.patch("/orders/{order_id}")
def update_order(order_id: str, update: OrderStatusUpdate):
    now_iso = datetime.now().isoformat()
    updates: dict = {"status": update.status}

    ts_map = {
        "picking": "picking_started",
        "picked": "picked",
        "dispatched": "dispatched",
        "delivered": "delivered",
    }
    if update.status in ts_map:
        updates["timestamps"] = {ts_map[update.status]: now_iso}

    if update.missing_items is not None:
        updates["missing_items"] = update.missing_items
    if update.notes is not None:
        updates["notes"] = update.notes
    if update.items_picked is not None:
        updates["items_picked"] = update.items_picked
    if update.driver_id:
        driver = data_store.get_driver(update.driver_id)
        if driver:
            updates["assigned_driver"] = driver.name

    order = data_store.update_order(order_id, updates)
    if not order:
        raise HTTPException(404, f"Order {order_id} not found")

    # When picking completes, batch all staged OOS notifications into one
    if update.status == "picked":
        _batch_pending_cs_notifications(order_id)

    return order.model_dump()


# ─── Drivers ──────────────────────────────────────────────────────────────────

@app.get("/drivers")
def list_drivers():
    return [d.model_dump() for d in data_store.get_drivers()]


@app.patch("/drivers/{driver_id}")
def update_driver(driver_id: str, update: DriverStatusUpdate):
    driver = data_store.update_driver(driver_id, {"status": update.status})
    if not driver:
        raise HTTPException(404, f"Driver {driver_id} not found")
    return driver.model_dump()


# ─── Exceptions ───────────────────────────────────────────────────────────────

@app.get("/exceptions")
def list_exceptions(status: Optional[str] = None):
    exceptions = data_store.get_exceptions()
    if status:
        exceptions = [e for e in exceptions if e.status == status]
    return [e.model_dump() for e in exceptions]


@app.patch("/exceptions/{exc_id}")
def update_exception(exc_id: str, update: ExceptionUpdate):
    updates: dict = {"status": update.status}
    if update.status == "resolved":
        updates["resolved_at"] = datetime.now().isoformat()
    exc = data_store.update_exception(exc_id, updates)
    if not exc:
        raise HTTPException(404, f"Exception {exc_id} not found")
    return exc.model_dump()


# ─── CS Notifications ─────────────────────────────────────────────────────────

@app.get("/cs-notifications")
def list_cs_notifications(status: Optional[str] = None):
    notifications = data_store.get_cs_notifications()
    if status:
        notifications = [n for n in notifications if n.status == status]
    else:
        # By default exclude pending_batch (not ready for CS yet)
        notifications = [n for n in notifications if n.status != "pending_batch"]
    return [n.model_dump() for n in notifications]


@app.patch("/cs-notifications/{notif_id}")
def update_cs_notification(notif_id: str, update: CSNotificationUpdate):
    updates: dict = {"status": update.status}
    if update.status == "handled":
        updates["handled_at"] = datetime.now().isoformat()
    notif = data_store.update_cs_notification(notif_id, updates)
    if not notif:
        raise HTTPException(404, f"Notification {notif_id} not found")
    return notif.model_dump()


# ─── Agent ────────────────────────────────────────────────────────────────────

@app.post("/agent/run")
def run_agent():
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(
            503,
            "Run Agent unavailable: backend ANTHROPIC_API_KEY is not configured.",
        )
    try:
        return run_agent_cycle()
    except Exception as e:
        raise HTTPException(500, f"Agent error: {str(e)}")


@app.get("/agent/status")
def agent_status():
    return agent_monitor.get_status()


@app.get("/agent/shift-summary")
async def shift_summary():
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(
            503,
            "Shift summary unavailable: backend ANTHROPIC_API_KEY is not configured.",
        )
    try:
        structured = await generate_shift_summary_structured()
        return {
            "structured": structured,
            "generated_at": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(500, f"Summary error: {str(e)}")


# ─── Stats ────────────────────────────────────────────────────────────────────

@app.get("/stats")
def get_stats():
    orders = data_store.get_orders()
    drivers = data_store.get_drivers()
    exceptions = data_store.get_exceptions()
    notifications = data_store.get_cs_notifications()
    now = datetime.now()

    status_counts: dict[str, int] = {}
    for o in orders:
        status_counts[o.status] = status_counts.get(o.status, 0) + 1

    windows = sorted(set(o.delivery_window for o in orders))
    window_stats = {}
    for w in windows:
        w_orders = [o for o in orders if o.delivery_window == w]
        at_risk = [o for o in w_orders if compute_risk_level(o, now) in ("yellow", "red")]
        picking_orders = [o for o in w_orders if o.status == "picking"]
        window_stats[w] = {
            "total": len(w_orders),
            "delivered": sum(1 for o in w_orders if o.status == "delivered"),
            "dispatched": sum(1 for o in w_orders if o.status == "dispatched"),
            "at_risk": len(at_risk),
            # Pick progress across all picking orders in this window
            "items_picked": sum(o.items_picked for o in picking_orders),
            "total_picking_items": sum(o.total_items for o in picking_orders),
            "picking_orders": len(picking_orders),
        }

    # Group drivers by company
    company_stats: dict[str, dict] = {}
    for d in drivers:
        co = d.company or "Unknown"
        if co not in company_stats:
            company_stats[co] = {"expected": 0, "present": 0, "called_out": 0, "drivers": []}
        company_stats[co]["expected"] += 1
        if d.status != "called_out":
            company_stats[co]["present"] += 1
        else:
            company_stats[co]["called_out"] += 1
        company_stats[co]["drivers"].append(d.model_dump())

    return {
        "total_orders": len(orders),
        "status_breakdown": status_counts,
        "window_stats": window_stats,
        "drivers": {
            "total": len(drivers),
            "available": sum(1 for d in drivers if d.status == "available"),
            "on_delivery": sum(1 for d in drivers if d.status == "on_delivery"),
            "called_out": sum(1 for d in drivers if d.status == "called_out"),
            "by_company": company_stats,
        },
        "open_exceptions": sum(1 for e in exceptions if e.status == "open"),
        "pending_notifications": sum(1 for n in notifications if n.status == "pending"),
    }


@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now().isoformat()}
