"""Driver service — fleet state and live GPS ingestion.

Provides driver CRUD-lite endpoints plus the WebSocket the Driver App uses to
stream GPS pings. Each ping updates the driver's last known position and is
fanned out to the dispatch map and to every active order on the driver's run
(so the customer tracking page sees the courier move in real time).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.domain import build_tracking_payload
from app.models import Driver, DriverStatus, Order, OrderStatus, Run
from app.realtime import DISPATCH_CHANNEL, manager, order_channel
from app.schemas import DriverLocationIn, DriverOut

router = APIRouter(prefix="/api/drivers", tags=["drivers"])


@router.get("", response_model=list[DriverOut])
def list_drivers(db: Session = Depends(get_db)) -> list[Driver]:
    """Return all drivers and their current status/position (POS map)."""
    return db.query(Driver).order_by(Driver.id).all()


@router.post("/{driver_id}/status", response_model=DriverOut)
def set_driver_status(
    driver_id: int, status: DriverStatus, db: Session = Depends(get_db)
) -> Driver:
    """Set a driver's availability (e.g. clock in -> AVAILABLE)."""
    driver = db.get(Driver, driver_id)
    if driver is None:
        raise HTTPException(status_code=404, detail="Driver not found")
    driver.status = status
    db.commit()
    db.refresh(driver)
    return driver


async def _broadcast_driver_position(driver: Driver, db: Session) -> None:
    """Fan a driver's new position out to the dispatch map and tracking pages."""
    # Dispatch map: one lightweight position event for the whole fleet view.
    await manager.broadcast(
        DISPATCH_CHANNEL,
        {
            "type": "driver_position",
            "driver_id": driver.id,
            "name": driver.name,
            "lat": driver.last_lat,
            "lng": driver.last_lng,
        },
    )

    # Customer tracking: update every undelivered order on this driver's runs.
    active_orders = (
        db.query(Order)
        .join(Run, Order.run_id == Run.id)
        .filter(Run.driver_id == driver.id)
        .filter(Order.status.in_([OrderStatus.ASSIGNED, OrderStatus.ON_THE_WAY]))
        .all()
    )
    for order in active_orders:
        await manager.broadcast(order_channel(order.tracking_token), build_tracking_payload(order))


@router.post("/{driver_id}/location", response_model=DriverOut)
async def post_location(
    driver_id: int, payload: DriverLocationIn, db: Session = Depends(get_db)
) -> Driver:
    """HTTP fallback for a GPS ping (the WebSocket below is preferred)."""
    driver = db.get(Driver, driver_id)
    if driver is None:
        raise HTTPException(status_code=404, detail="Driver not found")
    driver.last_lat = payload.lat
    driver.last_lng = payload.lng
    db.commit()
    db.refresh(driver)
    await _broadcast_driver_position(driver, db)
    return driver


@router.websocket("/{driver_id}/gps")
async def driver_gps(websocket: WebSocket, driver_id: int) -> None:
    """WebSocket endpoint the Driver App streams `{lat, lng}` pings to.

    A dedicated DB session is used because WebSocket handlers live outside the
    normal request/response dependency lifecycle.
    """
    # The dispatch channel also receives these, but the socket itself does not
    # need to be subscribed; we only read pings and broadcast outward.
    await websocket.accept()
    db = SessionLocal()
    try:
        driver = db.get(Driver, driver_id)
        if driver is None:
            await websocket.close(code=4404)  # custom: driver not found
            return

        while True:
            # Expect JSON like {"lat": 52.23, "lng": 21.01}.
            data = await websocket.receive_json()
            driver.last_lat = float(data["lat"])
            driver.last_lng = float(data["lng"])
            db.commit()
            await _broadcast_driver_position(driver, db)
    except WebSocketDisconnect:
        # Normal client disconnect — nothing to clean up beyond the session.
        pass
    finally:
        db.close()
