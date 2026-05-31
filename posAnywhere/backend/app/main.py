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

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.database import get_db, init_db
from app.logging_config import configure_logging, request_id_var, user_var
from app.models import Location
from app.realtime import DISPATCH_CHANNEL, manager
from app.modules import auth, dispatch, drivers, orders, settlement, tracking

# Configure application-wide logging before anything else emits log records.
configure_logging()
logger = logging.getLogger("posanywhere.request")

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


# --------------------------------------------------------------------------
# Request logging + centralised failure documentation.
# Every HTTP request gets a correlation id and an audit log line; every
# failure (HTTP error, validation error, or unhandled exception) is logged.
# --------------------------------------------------------------------------
def _identify_user(request: Request) -> str:
    """Best-effort: read the user id from a Bearer token without enforcing auth.

    Returns "user:<id>" for a valid token, "invalid-token" when a token is
    present but cannot be decoded, or "anonymous" otherwise. No DB lookup is
    performed, so this adds negligible per-request overhead.
    """
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return "anonymous"
    token = authorization[len("Bearer "):]
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return "invalid-token"
    subject = payload.get("sub")
    return f"user:{subject}" if subject else "anonymous"


def _bind_log_context(request: Request) -> str:
    """Bind request id + user onto the logging context vars; return the id."""
    request_id = getattr(request.state, "request_id", None) or (
        request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    )
    request.state.request_id = request_id
    request_id_var.set(request_id)
    user_var.set(getattr(request.state, "user", None) or _identify_user(request))
    return request_id


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Tag every request with a correlation id + user and log an audit line."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    user = _identify_user(request)
    request.state.request_id = request_id
    request.state.user = user
    request_id_var.set(request_id)
    user_var.set(user)
    start = time.perf_counter()

    response = await call_next(request)

    duration_ms = (time.perf_counter() - start) * 1000
    client = request.client.host if request.client else "-"
    logger.info(
        "%s %s -> %s (%.1f ms) client=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        client,
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Document raised HTTP errors (e.g. 401/404/409) with their reason."""
    request_id = _bind_log_context(request)
    logger.warning(
        "http_error %s %s status=%s detail=%s",
        request.method,
        request.url.path,
        exc.status_code,
        exc.detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={"X-Request-ID": request_id},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Document request validation failures (422) with the offending fields."""
    request_id = _bind_log_context(request)
    logger.warning(
        "validation_error %s %s errors=%s",
        request.method,
        request.url.path,
        exc.errors(),
    )
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors())},
        headers={"X-Request-ID": request_id},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Document unexpected failures with a full stack trace and return 500."""
    request_id = _bind_log_context(request)
    logger.exception(
        "unhandled_error %s %s",
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
        headers={"X-Request-ID": request_id},
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
    client = websocket.client.host if websocket.client else "-"
    logger.info("ws.connect channel=dispatch client=%s", client)
    try:
        while True:
            await websocket.receive_text()  # inbound ignored; keep-alive only
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(DISPATCH_CHANNEL, websocket)
        logger.info("ws.disconnect channel=dispatch client=%s", client)


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
