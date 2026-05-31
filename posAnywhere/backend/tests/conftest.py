"""Shared pytest fixtures for the posAnywhere.io backend test suite.

Strategy:
  * Point the app at a throwaway SQLite file BEFORE importing any app module,
    so the engine/settings are built against the test database.
  * Recreate all tables and seed a minimal baseline (tenant, location, two
    zones, two available drivers) before every test for full isolation.
  * Expose a FastAPI TestClient that the HTTP/WebSocket tests drive.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# --- Critical: configure the DB path before importing the application. ------
# config.Settings caches itself on first import, so this must run first.
_TMP_DB = os.path.join(tempfile.gettempdir(), "posanywhere_test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ["CORS_ORIGINS"] = "*"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    DeliveryZone,
    Driver,
    DriverStatus,
    Location,
    Tenant,
)

# Restaurant origin used across tests (central Warsaw).
LOC_LAT, LOC_LNG = 52.2297, 21.0122


def _square(lat: float, lng: float, half: float) -> list[list[float]]:
    """Build a square polygon centred on a point (matches seed helper)."""
    return [
        [lat - half, lng - half],
        [lat - half, lng + half],
        [lat + half, lng + half],
        [lat + half, lng - half],
    ]


@pytest.fixture(autouse=True)
def fresh_db():
    """Drop + recreate all tables and seed baseline data before each test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        tenant = Tenant(name="Test Group")
        db.add(tenant)
        db.flush()

        location = Location(
            tenant_id=tenant.id,
            name="Test Kitchen",
            address="Test Street 1",
            lat=LOC_LAT,
            lng=LOC_LNG,
        )
        db.add(location)
        db.flush()

        # Inner zone added first so close addresses resolve to it.
        db.add_all(
            [
                DeliveryZone(location_id=location.id, name="Inner", polygon=_square(LOC_LAT, LOC_LNG, 0.02), fee=5.0),
                DeliveryZone(location_id=location.id, name="Outer", polygon=_square(LOC_LAT, LOC_LNG, 0.05), fee=9.0),
            ]
        )

        db.add_all(
            [
                Driver(name="Driver A", status=DriverStatus.AVAILABLE, last_lat=LOC_LAT, last_lng=LOC_LNG),
                Driver(name="Driver B", status=DriverStatus.AVAILABLE, last_lat=LOC_LAT, last_lng=LOC_LNG),
            ]
        )
        db.commit()
    finally:
        db.close()

    yield  # run the test


@pytest.fixture
def client() -> TestClient:
    """A TestClient bound to the app (also drives WebSocket endpoints)."""
    return TestClient(app)


@pytest.fixture
def location_id() -> int:
    """Convenience: the seeded location is always id 1 in a fresh DB."""
    return 1
