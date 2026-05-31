"""FastAPI application entry point — wires the modular monolith together.

Responsibilities:
  * Create the FastAPI app and configure CORS.
  * Initialise the database schema on startup.
  * Mount every module router (orders, dispatch, drivers, settlement, tracking).
  * Expose a couple of small catalog endpoints (locations) the UI needs.
  * Host the dispatch-map WebSocket and serve the static POS + tracking pages.

Run locally with:  uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db, init_db
from app.models import Location
from app.realtime import DISPATCH_CHANNEL, manager
from app.modules import auth, dispatch, drivers, orders, settlement, tracking

# Absolute path to the bundled static frontend (POS + tracking pages).
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup/shutdown hook — create tables before the app serves traffic."""
    init_db()
    yield


# The FastAPI application instance imported by uvicorn (app.main:app).
app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

# Allow the browser frontend to call the API from the configured origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register each domain module's router.
app.include_router(auth.router)
app.include_router(orders.router)
app.include_router(dispatch.router)
app.include_router(drivers.router)
app.include_router(settlement.router)
app.include_router(tracking.router)


# --------------------------------------------------------------------------
# Small catalog endpoints the UI needs to render the POS (locations + zones).
# --------------------------------------------------------------------------
@app.get("/api/locations", tags=["catalog"])
def list_locations(db: Session = Depends(get_db)) -> list[dict]:
    """Return locations with their delivery zones for the POS map/dropdown."""
    result = []
    for loc in db.query(Location).order_by(Location.id).all():
        result.append(
            {
                "id": loc.id,
                "name": loc.name,
                "address": loc.address,
                "lat": loc.lat,
                "lng": loc.lng,
                "zones": [
                    {"id": z.id, "name": z.name, "fee": z.fee, "polygon": z.polygon}
                    for z in loc.zones
                ],
            }
        )
    return result


@app.get("/api/health", tags=["catalog"])
def health() -> dict:
    """Simple liveness probe used by load balancers / cloud platforms."""
    return {"status": "ok", "app": settings.app_name}


# --------------------------------------------------------------------------
# Dispatch-map WebSocket: the POS subscribes here for fleet-wide live updates
# (driver positions + order status changes broadcast by other modules).
# --------------------------------------------------------------------------
@app.websocket("/api/dispatch/ws")
async def dispatch_ws(websocket: WebSocket) -> None:
    """Subscribe the POS dispatch map to the fleet-wide realtime channel."""
    await manager.connect(DISPATCH_CHANNEL, websocket)
    try:
        while True:
            await websocket.receive_text()  # inbound ignored; keep-alive only
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(DISPATCH_CHANNEL, websocket)


# --------------------------------------------------------------------------
# Static frontend: POS console at "/" and the app-free tracking page.
# --------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def pos_page() -> FileResponse:
    """Serve the staff POS + dispatch console."""
    return FileResponse(STATIC_DIR / "pos.html")


@app.get("/track", include_in_schema=False)
def tracking_page() -> FileResponse:
    """Serve the customer app-free tracking page (uses ?token=... query)."""
    return FileResponse(STATIC_DIR / "tracking.html")


# Expose remaining static assets (if any are added later) under /static.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
