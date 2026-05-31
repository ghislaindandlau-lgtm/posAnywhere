"""Seed script — populates the database with demo data for local exploration.

Creates one tenant, one location (central Warsaw), two delivery zones, a few
drivers and a couple of sample orders so the POS and tracking pages have
something to show immediately.

Run with:  python -m app.seed
This is idempotent-ish: it wipes existing demo rows first so it can be re-run.
"""

from __future__ import annotations

import secrets

from app.database import SessionLocal, init_db
from app.domain import record_status
from app.modules.dispatch import resolve_zone
from app.models import (
    Customer,
    DeliveryZone,
    Driver,
    DriverStatus,
    Location,
    Order,
    OrderChannel,
    OrderItem,
    OrderStatus,
    Run,
    Settlement,
    StatusEvent,
    Tenant,
)

# Central Warsaw coordinates used as the restaurant origin.
LOC_LAT, LOC_LNG = 52.2297, 21.0122


def _square(center_lat: float, center_lng: float, half: float) -> list[list[float]]:
    """Return a square polygon (list of [lat, lng]) centred on a point."""
    return [
        [center_lat - half, center_lng - half],
        [center_lat - half, center_lng + half],
        [center_lat + half, center_lng + half],
        [center_lat + half, center_lng - half],
    ]


def reset_tables(db) -> None:
    """Delete all rows so the seed can be run repeatedly from a clean slate."""
    for model in (Settlement, StatusEvent, OrderItem, Order, Run, DeliveryZone, Customer, Driver, Location, Tenant):
        db.query(model).delete()
    db.commit()


def seed() -> None:
    """Create demo data and print the resulting tracking links."""
    init_db()
    db = SessionLocal()
    try:
        reset_tables(db)

        # Tenant + location.
        tenant = Tenant(name="Pizza Roma Group")
        db.add(tenant)
        db.flush()

        location = Location(
            tenant_id=tenant.id,
            name="Pizza Roma — Centrum",
            address="ul. Marszalkowska 1, Warszawa",
            lat=LOC_LAT,
            lng=LOC_LNG,
        )
        db.add(location)
        db.flush()

        # Two zones: a small inner zone (cheaper) and a larger outer zone.
        # Inner is added first so point-in-polygon matches it for close addresses.
        db.add_all(
            [
                DeliveryZone(location_id=location.id, name="Inner City", polygon=_square(LOC_LAT, LOC_LNG, 0.02), fee=5.0),
                DeliveryZone(location_id=location.id, name="Greater City", polygon=_square(LOC_LAT, LOC_LNG, 0.05), fee=9.0),
            ]
        )

        # A small driver fleet, all clocked in and ready.
        drivers = [
            Driver(name="Anna Nowak", status=DriverStatus.AVAILABLE, last_lat=LOC_LAT, last_lng=LOC_LNG),
            Driver(name="Piotr Kowalski", status=DriverStatus.AVAILABLE, last_lat=LOC_LAT, last_lng=LOC_LNG),
        ]
        db.add_all(drivers)
        db.flush()

        # Two sample orders at nearby addresses (inside the inner zone).
        sample = [
            ("+48500111222", "Jan Customer", "ul. Krucza 5", 52.2270, 21.0150, [("Margherita", 1, 32.0), ("Cola", 2, 7.0)]),
            ("+48500333444", "Ewa Klient", "ul. Nowy Swiat 20", 52.2350, 21.0180, [("Pepperoni", 1, 39.0)]),
        ]
        tokens = []
        for phone, name, addr, lat, lng, items in sample:
            customer = Customer(phone=phone, name=name, address=addr, lat=lat, lng=lng)
            db.add(customer)
            db.flush()

            items_total = sum(q * p for _n, q, p in items)
            # Resolve the zone so the sample orders are dispatchable in the demo.
            zone = resolve_zone(db, location.id, lat, lng)
            fee = zone.fee if zone else 0.0
            order = Order(
                location_id=location.id,
                customer_id=customer.id,
                channel=OrderChannel.PHONE,
                status=OrderStatus.NEW,
                items_total=items_total,
                delivery_fee=fee,
                total=items_total + fee,
                zone_id=zone.id if zone else None,
                eta_minutes=25,
                tracking_token=secrets.token_urlsafe(12),
            )
            db.add(order)
            db.flush()
            for n, q, p in items:
                db.add(OrderItem(order_id=order.id, name=n, qty=q, price=p))
            record_status(db, order, OrderStatus.NEW)
            record_status(db, order, OrderStatus.ACCEPTED)
            tokens.append(order.tracking_token)

        db.commit()

        print("Seed complete.")
        print(f"Location id = {location.id}")
        print("Customer tracking links (open while server runs):")
        for t in tokens:
            print(f"  http://localhost:8000/track?token={t}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
