"""Dispatch & Routing service — posAnywhere.io's core differentiator.

Implements the components from the C4 Level-3 diagram (architecture §4):

  * Zone Engine      -> resolve_zone():      address -> delivery zone + fee
  * ETA Calculator   -> calculate_eta():     prep time + travel time
  * Route Batcher    -> dispatch_pending():  groups pending orders into runs
  * Driver Assigner  -> dispatch_pending():  assigns runs to available drivers

The "Maps Adapter" is represented locally by app.geo (haversine distance +
point-in-polygon), avoiding any external Maps API dependency for local runs.
"""

from __future__ import annotations

from collections import defaultdict
from math import ceil

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.domain import publish_order_update, record_status
from app.geo import haversine_km, point_in_polygon
from app.models import (
    DeliveryZone,
    Driver,
    DriverStatus,
    Location,
    Order,
    OrderStatus,
    Run,
)
from app.schemas import DispatchResult, QuoteRequest, QuoteResponse

router = APIRouter(prefix="/api/dispatch", tags=["dispatch"])


# --------------------------------------------------------------------------
# Zone Engine — resolve a destination to its delivery zone + fee.
# --------------------------------------------------------------------------
def resolve_zone(db: Session, location_id: int, lat: float, lng: float) -> DeliveryZone | None:
    """Return the first zone of a location whose polygon contains the point.

    Zones are checked in id order; a richer engine could pick the cheapest or
    smallest containing zone. Returns None if the address is undeliverable.
    """
    zones = db.query(DeliveryZone).filter(DeliveryZone.location_id == location_id).all()
    for zone in zones:
        if point_in_polygon(lat, lng, zone.polygon):
            return zone
    return None


# --------------------------------------------------------------------------
# ETA Calculator — kitchen prep time plus straight-line travel time.
# --------------------------------------------------------------------------
def calculate_eta(location: Location, lat: float, lng: float) -> int:
    """Estimate total minutes until delivery for a destination.

    ETA = configured prep time + travel time, where travel time derives from
    the straight-line distance and the configured average courier speed.
    """
    distance_km = haversine_km(location.lat, location.lng, lat, lng)
    travel_minutes = (distance_km / settings.average_driver_speed_kmh) * 60
    return settings.default_prep_minutes + ceil(travel_minutes)


def quote_order(db: Session, location_id: int, lat: float, lng: float) -> QuoteResponse:
    """Produce a fee + ETA quote for a destination (used by order intake)."""
    location = db.get(Location, location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Location not found")

    zone = resolve_zone(db, location_id, lat, lng)
    eta = calculate_eta(location, lat, lng)
    return QuoteResponse(
        zone_id=zone.id if zone else None,
        zone_name=zone.name if zone else None,
        delivery_fee=zone.fee if zone else 0.0,
        eta_minutes=eta,
        deliverable=zone is not None,
    )


# --------------------------------------------------------------------------
# Route Batcher + Driver Assigner — turn pending orders into driver runs.
# --------------------------------------------------------------------------
async def dispatch_pending(db: Session) -> DispatchResult:
    """Batch unassigned orders into runs and assign them to free drivers.

    Strategy (intentionally simple but realistic):
      1. Collect orders that are ACCEPTED/PREPARING and not yet on a run.
      2. Group them by delivery zone so one driver covers nearby drops.
      3. For each group, take an AVAILABLE driver, create a Run, attach the
         orders, flip their status to ASSIGNED and the driver to ON_RUN.
    Runs stop being created once drivers run out.
    """
    # 1) Pending, unassigned orders.
    pending = (
        db.query(Order)
        .filter(Order.run_id.is_(None))
        .filter(Order.status.in_([OrderStatus.ACCEPTED, OrderStatus.PREPARING]))
        .order_by(Order.created_at)
        .all()
    )

    # 2) Group by zone (None zone => undeliverable, skipped).
    groups: dict[int, list[Order]] = defaultdict(list)
    for order in pending:
        if order.zone_id is not None:
            groups[order.zone_id].append(order)

    # Available drivers, consumed one run at a time.
    available = (
        db.query(Driver).filter(Driver.status == DriverStatus.AVAILABLE).all()
    )

    runs_created = 0
    orders_assigned = 0
    assigned_orders: list[Order] = []

    for _zone_id, zone_orders in groups.items():
        if not available:
            break  # No more free drivers; remaining orders wait for next pass.

        driver = available.pop(0)
        run = Run(driver_id=driver.id)
        db.add(run)
        db.flush()  # Assign run.id before linking orders.

        for order in zone_orders:
            order.run_id = run.id
            record_status(db, order, OrderStatus.ASSIGNED)
            orders_assigned += 1
            assigned_orders.append(order)

        driver.status = DriverStatus.ON_RUN
        runs_created += 1

    db.commit()

    # Notify tracking pages + the dispatch map for every newly assigned order.
    for order in assigned_orders:
        db.refresh(order)
        await publish_order_update(order)

    return DispatchResult(
        runs_created=runs_created,
        orders_assigned=orders_assigned,
        message=f"Created {runs_created} run(s), assigned {orders_assigned} order(s).",
    )


# --------------------------------------------------------------------------
# HTTP endpoints
# --------------------------------------------------------------------------
@router.post("/quote", response_model=QuoteResponse)
def post_quote(payload: QuoteRequest, db: Session = Depends(get_db)) -> QuoteResponse:
    """Return a delivery fee + ETA quote for a candidate address."""
    return quote_order(db, payload.location_id, payload.delivery_lat, payload.delivery_lng)


@router.post("/run", response_model=DispatchResult)
async def post_run(db: Session = Depends(get_db)) -> DispatchResult:
    """Trigger a dispatch pass that batches & assigns all pending orders."""
    return await dispatch_pending(db)
