"""SQLAlchemy ORM models — the operational data model.

These tables are a direct implementation of the ER diagram in
architecture.md §6 (Tenant, Location, Customer, Order, OrderItem,
DeliveryZone, Driver, Run, Settlement, StatusEvent).

Each class maps to one database table; relationships express the
cardinalities documented in the architecture (1-to-many, etc.).
"""

from __future__ import annotations

import enum
from datetime import datetime, date

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# --------------------------------------------------------------------------
# Enumerations — fixed vocabularies used across the domain.
# --------------------------------------------------------------------------
class OrderChannel(str, enum.Enum):
    """Where an order originated (architecture §1 multi-channel intake)."""

    DINE_IN = "dine_in"
    PHONE = "phone"          # via Caller ID
    PORTAL = "portal"        # third-party delivery portal
    ONLINE_STORE = "online_store"


class OrderStatus(str, enum.Enum):
    """Lifecycle states from the sequence diagram (architecture §5)."""

    NEW = "new"
    ACCEPTED = "accepted"
    PREPARING = "preparing"
    ASSIGNED = "assigned"      # batched into a run and given to a driver
    ON_THE_WAY = "on_the_way"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class DriverStatus(str, enum.Enum):
    """Availability of a courier, read by the Driver Assigner."""

    OFFLINE = "offline"
    AVAILABLE = "available"
    ON_RUN = "on_run"


# --------------------------------------------------------------------------
# Core tenancy hierarchy: Tenant -> Location -> (Zones, Orders).
# --------------------------------------------------------------------------
class Tenant(Base):
    """A restaurant brand/chain (top of the multi-tenant hierarchy)."""

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # One tenant owns many physical locations.
    locations: Mapped[list[Location]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class Location(Base):
    """A physical restaurant/branch belonging to a tenant."""

    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    # The branch coordinates are the origin point for travel-time/ETA maths.
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="locations")
    zones: Mapped[list[DeliveryZone]] = relationship(back_populates="location", cascade="all, delete-orphan")
    orders: Mapped[list[Order]] = relationship(back_populates="location")


class DeliveryZone(Base):
    """A geographic delivery area with an associated delivery fee.

    The area is stored as a polygon: a JSON list of [lat, lng] vertices.
    A production system would use PostGIS geometry (architecture §8 A6);
    here a plain point-in-polygon test keeps the app dependency-free.
    """

    __tablename__ = "delivery_zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # polygon = [[lat, lng], [lat, lng], ...] describing the zone boundary.
    polygon: Mapped[list] = mapped_column(JSON, nullable=False)
    fee: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    location: Mapped[Location] = relationship(back_populates="zones")


# --------------------------------------------------------------------------
# Customers and their orders.
# --------------------------------------------------------------------------
class Customer(Base):
    """An end customer, keyed by phone for Caller-ID lookups."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=True)
    address: Mapped[str] = mapped_column(String(255), nullable=True)
    lat: Mapped[float] = mapped_column(Float, nullable=True)
    lng: Mapped[float] = mapped_column(Float, nullable=True)

    orders: Mapped[list[Order]] = relationship(back_populates="customer")


class Order(Base):
    """A customer order — the central aggregate of the platform."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)

    channel: Mapped[OrderChannel] = mapped_column(Enum(OrderChannel), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), nullable=False, default=OrderStatus.NEW)

    # Money + logistics fields resolved by the dispatch engine.
    items_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    delivery_fee: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("delivery_zones.id"), nullable=True)
    eta_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Public, hard-to-guess token used by the app-free tracking page.
    tracking_token: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    location: Mapped[Location] = relationship(back_populates="orders")
    customer: Mapped[Customer] = relationship(back_populates="orders")
    run: Mapped[Run | None] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(back_populates="order", cascade="all, delete-orphan")
    status_events: Mapped[list[StatusEvent]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="StatusEvent.ts"
    )


class OrderItem(Base):
    """A single line item within an order."""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    order: Mapped[Order] = relationship(back_populates="items")


class StatusEvent(Base):
    """An append-only audit record of every order status transition."""

    __tablename__ = "status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    order: Mapped[Order] = relationship(back_populates="status_events")


# --------------------------------------------------------------------------
# Fleet: drivers, delivery runs and end-of-shift settlement.
# --------------------------------------------------------------------------
class Driver(Base):
    """A delivery courier and their current live state."""

    __tablename__ = "drivers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[DriverStatus] = mapped_column(Enum(DriverStatus), nullable=False, default=DriverStatus.OFFLINE)
    # Last known GPS position (updated by the Driver App over WebSocket).
    last_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_lng: Mapped[float | None] = mapped_column(Float, nullable=True)

    runs: Mapped[list[Run]] = relationship(back_populates="driver")
    settlements: Mapped[list[Settlement]] = relationship(back_populates="driver")


class Run(Base):
    """A batch of one or more orders delivered together by one driver."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    driver: Mapped[Driver] = relationship(back_populates="runs")
    orders: Mapped[list[Order]] = relationship(back_populates="run")


class Settlement(Base):
    """End-of-shift cash reconciliation for a driver (architecture §6)."""

    __tablename__ = "settlements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"), nullable=False)
    shift_date: Mapped[date] = mapped_column(Date, nullable=False)
    cash_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    orders_delivered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    driver: Mapped[Driver] = relationship(back_populates="settlements")
