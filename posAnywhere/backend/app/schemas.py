"""Pydantic schemas — the API's request/response contracts.

These models validate incoming JSON and shape outgoing JSON. They are kept
separate from the SQLAlchemy ORM models so the database structure and the
public API can evolve independently.
"""

from __future__ import annotations

from datetime import datetime, date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import DriverStatus, OrderChannel, OrderStatus, UserRole


# Shared config so response schemas can be built directly from ORM objects.
ORM = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------
# Order creation (request) schemas
# --------------------------------------------------------------------------
class OrderItemIn(BaseModel):
    """A single line item supplied when placing an order."""

    name: str
    qty: int = Field(default=1, ge=1)
    price: float = Field(ge=0)


class OrderCreate(BaseModel):
    """Payload accepted by POST /api/orders to place a new order."""

    location_id: int
    channel: OrderChannel
    customer_phone: str
    customer_name: str | None = None
    delivery_address: str | None = None
    # Destination coordinates used for zone resolution + ETA.
    delivery_lat: float
    delivery_lng: float
    items: list[OrderItemIn]


# --------------------------------------------------------------------------
# Order output schemas
# --------------------------------------------------------------------------
class OrderItemOut(BaseModel):
    model_config = ORM
    id: int
    name: str
    qty: int
    price: float


class StatusEventOut(BaseModel):
    model_config = ORM
    status: OrderStatus
    ts: datetime


class OrderOut(BaseModel):
    """Full order representation returned by the API."""

    model_config = ORM
    id: int
    location_id: int
    customer_id: int
    run_id: int | None
    channel: OrderChannel
    status: OrderStatus
    items_total: float
    delivery_fee: float
    total: float
    zone_id: int | None
    eta_minutes: int | None
    tracking_token: str
    created_at: datetime
    items: list[OrderItemOut] = []
    # Append-only audit trail of every status transition (US-1.6).
    status_events: list[StatusEventOut] = []


# --------------------------------------------------------------------------
# Dispatch schemas
# --------------------------------------------------------------------------
class QuoteRequest(BaseModel):
    """Ask the dispatch engine for a delivery fee + ETA without saving."""

    location_id: int
    delivery_lat: float
    delivery_lng: float


class QuoteResponse(BaseModel):
    """Result of a zone/fee/ETA quote."""

    zone_id: int | None
    zone_name: str | None
    delivery_fee: float
    eta_minutes: int
    deliverable: bool  # False if the address falls outside all zones


class DispatchResult(BaseModel):
    """Outcome of batching pending orders into runs and assigning drivers."""

    runs_created: int
    orders_assigned: int
    message: str


# --------------------------------------------------------------------------
# Driver schemas
# --------------------------------------------------------------------------
class DriverOut(BaseModel):
    model_config = ORM
    id: int
    name: str
    status: DriverStatus
    last_lat: float | None
    last_lng: float | None


class DriverLocationIn(BaseModel):
    """GPS ping sent by the Driver App (HTTP fallback for the WebSocket)."""

    lat: float
    lng: float


# --------------------------------------------------------------------------
# Settlement / reporting schemas
# --------------------------------------------------------------------------
class SettlementOut(BaseModel):
    model_config = ORM
    id: int
    driver_id: int
    shift_date: date
    cash_total: float
    orders_delivered: int


class TrackingView(BaseModel):
    """Minimal, customer-safe data shown on the app-free tracking page."""

    order_id: int
    status: OrderStatus
    eta_minutes: int | None
    restaurant_name: str
    restaurant_lat: float
    restaurant_lng: float
    driver_name: str | None
    driver_lat: float | None
    driver_lng: float | None
    history: list[StatusEventOut] = []


# --------------------------------------------------------------------------
# Authentication schemas
# --------------------------------------------------------------------------
class UserCreate(BaseModel):
    """Registration payload for POST /api/auth/register."""

    email: str
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None
    role: UserRole = UserRole.STAFF

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or "." not in value.split("@")[-1]:
            raise ValueError("Invalid email address")
        return value


class UserOut(BaseModel):
    """Public, safe representation of a user (never includes the password)."""

    model_config = ORM
    id: int
    email: str
    full_name: str | None
    role: UserRole
    is_active: bool


class Token(BaseModel):
    """JWT access token returned by POST /api/auth/login."""

    access_token: str
    token_type: str = "bearer"
