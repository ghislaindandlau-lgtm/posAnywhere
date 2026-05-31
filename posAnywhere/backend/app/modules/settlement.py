"""Settlement & reporting service.

Reconciles each driver's delivered orders into an end-of-shift cash total
(architecture §6 SETTLEMENT) and exposes a small operational reporting
summary (orders, delivered count, revenue).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Driver, Order, OrderStatus, Run, Settlement
from app.schemas import SettlementOut

router = APIRouter(prefix="/api", tags=["settlement"])
logger = logging.getLogger(__name__)


@router.post("/settlements/generate", response_model=SettlementOut)
def generate_settlement(
    driver_id: int, shift_date: date, db: Session = Depends(get_db)
) -> Settlement:
    """Compute (or refresh) a driver's cash settlement for a given shift date.

    Cash total = sum of the order totals delivered by that driver on the date.
    A real system would filter by payment method; here every delivered order
    is treated as cash collected by the courier.
    """
    driver = db.get(Driver, driver_id)
    if driver is None:
        raise HTTPException(status_code=404, detail="Driver not found")

    # Day boundaries used to bucket orders into the shift.
    day_start = datetime.combine(shift_date, time.min)
    day_end = datetime.combine(shift_date, time.max)

    delivered = (
        db.query(Order)
        .join(Run, Order.run_id == Run.id)
        .filter(Run.driver_id == driver_id)
        .filter(Order.status == OrderStatus.DELIVERED)
        .filter(Order.created_at >= day_start, Order.created_at <= day_end)
        .all()
    )

    cash_total = sum(o.total for o in delivered)
    orders_delivered = len(delivered)

    # Upsert: update an existing settlement for the day or create a new one.
    settlement = (
        db.query(Settlement)
        .filter(Settlement.driver_id == driver_id, Settlement.shift_date == shift_date)
        .first()
    )
    if settlement is None:
        settlement = Settlement(driver_id=driver_id, shift_date=shift_date)
        db.add(settlement)

    settlement.cash_total = cash_total
    settlement.orders_delivered = orders_delivered
    db.commit()
    db.refresh(settlement)
    logger.info(
        "settlement.generated driver_id=%s shift_date=%s cash_total=%.2f orders=%s",
        driver_id,
        shift_date,
        cash_total,
        orders_delivered,
    )
    return settlement


@router.get("/settlements", response_model=list[SettlementOut])
def list_settlements(db: Session = Depends(get_db)) -> list[Settlement]:
    """Return all recorded driver settlements."""
    return db.query(Settlement).order_by(Settlement.shift_date.desc()).all()


@router.get("/reports/summary")
def report_summary(location_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    """Return a lightweight operational summary for dashboards."""
    query = db.query(Order)
    if location_id is not None:
        query = query.filter(Order.location_id == location_id)

    orders = query.all()
    delivered = [o for o in orders if o.status == OrderStatus.DELIVERED]
    return {
        "total_orders": len(orders),
        "delivered": len(delivered),
        "in_progress": len(orders) - len(delivered),
        "revenue": round(sum(o.total for o in delivered), 2),
    }
