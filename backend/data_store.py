from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional
from models import Order, Driver, Exception_, CSNotification

_SEED_DIR = Path(__file__).parent / "data"

# On Vercel (and other read-only filesystems) the bundled seed data is not
# writable. Mirror it into /tmp on first use so the demo can mutate state
# within a single serverless container lifetime.
if os.getenv("VERCEL") or os.getenv("DISPATCHIQ_WRITABLE_DATA_DIR"):
    DATA_DIR = Path(
        os.getenv("DISPATCHIQ_WRITABLE_DATA_DIR", "/tmp/dispatchiq_data")
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _SEED_DIR.exists():
        for _seed_file in _SEED_DIR.glob("*.json"):
            _dest = DATA_DIR / _seed_file.name
            if not _dest.exists():
                shutil.copy2(_seed_file, _dest)
else:
    DATA_DIR = _SEED_DIR
    DATA_DIR.mkdir(exist_ok=True)

ORDERS_FILE = DATA_DIR / "orders.json"
DRIVERS_FILE = DATA_DIR / "drivers.json"
EXCEPTIONS_FILE = DATA_DIR / "exceptions.json"
CS_NOTIFICATIONS_FILE = DATA_DIR / "cs_notifications.json"


def _read_json(path: Path, default=None) -> list | dict:
    if default is None:
        default = []
    if not path.exists():
        return default
    with open(path) as f:
        return json.load(f)


def _write_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ─── Orders ───────────────────────────────────────────────────────────────────

def get_orders() -> list[Order]:
    raw = _read_json(ORDERS_FILE)
    return [Order(**o) for o in raw]


def get_order(order_id: str) -> Optional[Order]:
    orders = get_orders()
    return next((o for o in orders if o.id == order_id), None)


def save_orders(orders: list[Order]):
    _write_json(ORDERS_FILE, [o.model_dump() for o in orders])


def update_order(order_id: str, updates: dict) -> Optional[Order]:
    orders = get_orders()
    for i, order in enumerate(orders):
        if order.id == order_id:
            data = order.model_dump()
            # Handle nested timestamps
            if "timestamps" in updates:
                data["timestamps"].update(updates.pop("timestamps"))
            data.update(updates)
            orders[i] = Order(**data)
            save_orders(orders)
            return orders[i]
    return None


def upsert_order(order: Order):
    orders = get_orders()
    for i, o in enumerate(orders):
        if o.id == order.id:
            orders[i] = order
            save_orders(orders)
            return
    orders.append(order)
    save_orders(orders)


# ─── Drivers ──────────────────────────────────────────────────────────────────

def get_drivers() -> list[Driver]:
    raw = _read_json(DRIVERS_FILE)
    return [Driver(**d) for d in raw]


def get_driver(driver_id: str) -> Optional[Driver]:
    drivers = get_drivers()
    return next((d for d in drivers if d.id == driver_id), None)


def save_drivers(drivers: list[Driver]):
    _write_json(DRIVERS_FILE, [d.model_dump() for d in drivers])


def update_driver(driver_id: str, updates: dict) -> Optional[Driver]:
    drivers = get_drivers()
    for i, driver in enumerate(drivers):
        if driver.id == driver_id:
            data = driver.model_dump()
            data.update(updates)
            drivers[i] = Driver(**data)
            save_drivers(drivers)
            return drivers[i]
    return None


# ─── Exceptions ───────────────────────────────────────────────────────────────

def get_exceptions() -> list[Exception_]:
    raw = _read_json(EXCEPTIONS_FILE)
    return [Exception_(**e) for e in raw]


def save_exceptions(exceptions: list[Exception_]):
    _write_json(EXCEPTIONS_FILE, [e.model_dump() for e in exceptions])


def create_exception(exc: Exception_):
    exceptions = get_exceptions()
    # Avoid duplicate exceptions for same order/type that are still open
    if exc.order_id:
        duplicate = next(
            (e for e in exceptions
             if e.order_id == exc.order_id
             and e.type == exc.type
             and e.status == "open"),
            None
        )
        if duplicate:
            return duplicate
    exceptions.insert(0, exc)
    save_exceptions(exceptions)
    return exc


def update_exception(exc_id: str, updates: dict) -> Optional[Exception_]:
    exceptions = get_exceptions()
    for i, exc in enumerate(exceptions):
        if exc.id == exc_id:
            data = exc.model_dump()
            data.update(updates)
            exceptions[i] = Exception_(**data)
            save_exceptions(exceptions)
            return exceptions[i]
    return None


# ─── CS Notifications ─────────────────────────────────────────────────────────

def get_cs_notifications() -> list[CSNotification]:
    raw = _read_json(CS_NOTIFICATIONS_FILE)
    return [CSNotification(**n) for n in raw]


def save_cs_notifications(notifications: list[CSNotification]):
    _write_json(CS_NOTIFICATIONS_FILE, [n.model_dump() for n in notifications])


def create_cs_notification(notif: CSNotification):
    notifications = get_cs_notifications()
    # Avoid duplicate notifications for same order/issue type that are pending
    if notif.order_id:
        duplicate = next(
            (n for n in notifications
             if n.order_id == notif.order_id
             and n.issue_type == notif.issue_type
             and n.status == "pending"),
            None
        )
        if duplicate:
            return duplicate
    notifications.insert(0, notif)
    save_cs_notifications(notifications)
    return notif


def update_cs_notification(notif_id: str, updates: dict) -> Optional[CSNotification]:
    notifications = get_cs_notifications()
    for i, notif in enumerate(notifications):
        if notif.id == notif_id:
            data = notif.model_dump()
            data.update(updates)
            notifications[i] = CSNotification(**data)
            save_cs_notifications(notifications)
            return notifications[i]
    return None
