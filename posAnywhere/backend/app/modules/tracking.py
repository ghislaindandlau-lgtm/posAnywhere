"""Tracking service — the app-free customer order tracking experience.

Exposes a public, token-addressed view of an order plus a WebSocket that
streams live status + driver-position updates (architecture §8 A4: app-free
tracking via a light web page + WSS). No authentication is required beyond
possession of the unguessable tracking token.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.domain import build_tracking_payload
from app.models import Order
from app.realtime import manager, order_channel
from app.schemas import StatusEventOut, TrackingView

router = APIRouter(prefix="/api/tracking", tags=["tracking"])


def _load_order_by_token(db: Session, token: str) -> Order:
    """Fetch an order by its public tracking token or raise 404."""
    order = db.query(Order).filter(Order.tracking_token == token).first()
    if order is None:
        raise HTTPException(status_code=404, detail="Tracking link not found")
    return order


@router.get("/{token}", response_model=TrackingView)
def get_tracking(token: str, db: Session = Depends(get_db)) -> TrackingView:
    """Return the current tracking snapshot for an order (initial page load)."""
    order = _load_order_by_token(db, token)
    payload = build_tracking_payload(order)
    return TrackingView(
        **payload,
        history=[StatusEventOut(status=e.status, ts=e.ts) for e in order.status_events],
    )


@router.websocket("/{token}/ws")
async def tracking_ws(websocket: WebSocket, token: str) -> None:
    """Stream live updates for one order to the customer's tracking page."""
    # Validate the token before subscribing so bad links fail fast.
    db = SessionLocal()
    try:
        order = db.query(Order).filter(Order.tracking_token == token).first()
        if order is None:
            await websocket.accept()
            await websocket.close(code=4404)
            return
        initial = build_tracking_payload(order)
    finally:
        db.close()

    channel = order_channel(token)
    await manager.connect(channel, websocket)
    try:
        # Push the current state immediately so the page renders without delay.
        await websocket.send_json(initial)
        # Keep the socket open; updates arrive via manager.broadcast elsewhere.
        while True:
            await websocket.receive_text()  # ignore inbound; keeps connection alive
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(channel, websocket)
