"""Order service — multi-channel order intake and lifecycle management.

Covers steps 1-4 and 9-11 of the order-lifecycle sequence (architecture §5):
create an order, resolve its zone/fee/ETA via the dispatch engine, accept it,
advance it through the kitchen, and finally mark it delivered (which frees the
driver's capacity).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.domain import publish_order_update, record_status
from app.modules.dispatch import calculate_eta, resolve_zone
from app.models import (
    Customer,
    DriverStatus,
    Location,
    Order,
    OrderItem,
    OrderStatus,
)
from app.schemas import OrderCreate, OrderOut

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _get_or_create_customer(db: Session, payload: OrderCreate) -> Customer:
    """Find a customer by phone (Caller-ID style) or create a new record."""
    customer = db.query(Customer).filter(Customer.phone == payload.customer_phone).first()
    if customer is None:
        customer = Customer(phone=payload.customer_phone)
        db.add(customer)

    # Always refresh the latest contact/address details from this order.
    if payload.customer_name:
        customer.name = payload.customer_name
    if payload.delivery_address:
        customer.address = payload.delivery_address
    customer.lat = payload.delivery_lat
    customer.lng = payload.delivery_lng
    db.flush()  # Ensure customer.id is available before linking the order.
    return customer


@router.post("", response_model=OrderOut, status_code=201)
async def create_order(payload: OrderCreate, db: Session = Depends(get_db)) -> Order:
    """Place a new order from any channel, pricing delivery and computing ETA."""
    location = db.get(Location, payload.location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Location not found")
    if not payload.items:
        raise HTTPException(status_code=400, detail="Order must contain at least one item")

    customer = _get_or_create_customer(db, payload)

    # Sum the line items to get the goods subtotal.
    items_total = sum(item.qty * item.price for item in payload.items)

    # Ask the dispatch engine for the delivery zone, fee and ETA.
    zone = resolve_zone(db, location.id, payload.delivery_lat, payload.delivery_lng)
    delivery_fee = zone.fee if zone else 0.0
    eta = calculate_eta(location, payload.delivery_lat, payload.delivery_lng)

    order = Order(
        location_id=location.id,
        customer_id=customer.id,
        channel=payload.channel,
        status=OrderStatus.NEW,
        items_total=items_total,
        delivery_fee=delivery_fee,
        total=items_total + delivery_fee,
        zone_id=zone.id if zone else None,
        eta_minutes=eta,
        # A random, unguessable token powers the app-free tracking link.
        tracking_token=secrets.token_urlsafe(12),
    )
    db.add(order)
    db.flush()

    # Persist the line items.
    for item in payload.items:
        db.add(OrderItem(order_id=order.id, name=item.name, qty=item.qty, price=item.price))

    # Record the initial NEW event, then auto-accept (kitchen received it).
    record_status(db, order, OrderStatus.NEW)
    record_status(db, order, OrderStatus.ACCEPTED)
    db.commit()
    db.refresh(order)

    await publish_order_update(order)
    return order


@router.get("", response_model=list[OrderOut])
def list_orders(
    location_id: int | None = None,
    status: OrderStatus | None = None,
    db: Session = Depends(get_db),
) -> list[Order]:
    """List orders, optionally filtered by location and/or status (POS board)."""
    query = db.query(Order)
    if location_id is not None:
        query = query.filter(Order.location_id == location_id)
    if status is not None:
        query = query.filter(Order.status == status)
    return query.order_by(Order.created_at.desc()).all()


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: Session = Depends(get_db)) -> Order:
    """Fetch a single order by its internal id."""
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.post("/{order_id}/status", response_model=OrderOut)
async def update_status(
    order_id: int, status: OrderStatus, db: Session = Depends(get_db)
) -> Order:
    """Advance an order to an explicit status (e.g. PREPARING, ON_THE_WAY)."""
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    record_status(db, order, status)
    db.commit()
    db.refresh(order)

    await publish_order_update(order)
    return order


@router.post("/{order_id}/deliver", response_model=OrderOut)
async def deliver_order(order_id: int, db: Session = Depends(get_db)) -> Order:
    """Mark an order DELIVERED and free the driver if their run is finished.

    Implements steps 9-11 of the sequence diagram: payment captured on
    delivery and driver capacity released once all run orders are done.
    """
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    record_status(db, order, OrderStatus.DELIVERED)
    db.flush()

    # If every order on the run is delivered, complete the run and free driver.
    run = order.run
    if run is not None:
        remaining = [o for o in run.orders if o.status != OrderStatus.DELIVERED]
        if not remaining:
            run.completed_at = datetime.now(timezone.utc)
            run.driver.status = DriverStatus.AVAILABLE

    db.commit()
    db.refresh(order)

    await publish_order_update(order)
    return order
