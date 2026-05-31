"""Shared domain helpers used by several modules.

Placed in its own file so both the Order and Dispatch modules can reuse them
without importing each other (which would create a circular import).
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import Order, OrderStatus, StatusEvent
from app.realtime import DISPATCH_CHANNEL, manager, order_channel

logger = logging.getLogger(__name__)


def record_status(db: Session, order: Order, status: OrderStatus) -> None:
    """Transition an order to a new status and append an audit event.

    Note: this does NOT commit — the caller controls the transaction boundary
    so several changes can be committed atomically.
    """
    order.status = status
    db.add(StatusEvent(order_id=order.id, status=status))
    logger.info("order.status_change order_id=%s status=%s", order.id, status.value)


def build_tracking_payload(order: Order) -> dict:
    """Build the customer-safe realtime payload for an order.

    Only exposes what the public tracking page needs (no totals/PII beyond
    the driver's first name and live position).
    """
    driver = order.run.driver if order.run else None
    return {
        "order_id": order.id,
        "status": order.status.value,
        "eta_minutes": order.eta_minutes,
        "restaurant_name": order.location.name,
        "restaurant_lat": order.location.lat,
        "restaurant_lng": order.location.lng,
        "driver_name": driver.name if driver else None,
        "driver_lat": driver.last_lat if driver else None,
        "driver_lng": driver.last_lng if driver else None,
    }


async def publish_order_update(order: Order) -> None:
    """Push an order update to its tracking channel and the dispatch map."""
    payload = build_tracking_payload(order)
    logger.debug("order.broadcast order_id=%s status=%s", order.id, order.status.value)
    # Customer tracking page (subscribed by tracking token).
    await manager.broadcast(order_channel(order.tracking_token), payload)
    # Staff dispatch map (fleet-wide channel) gets an order-scoped event too.
    await manager.broadcast(DISPATCH_CHANNEL, {"type": "order_update", "order": payload})
