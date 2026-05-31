"""Tests for the geospatial helpers and the Dispatch & Routing engine."""

from __future__ import annotations

from app.geo import haversine_km, point_in_polygon

from tests.conftest import LOC_LAT, LOC_LNG, _square


# --------------------------------------------------------------------------
# Pure geo helpers
# --------------------------------------------------------------------------
def test_haversine_zero_distance():
    """Distance between a point and itself is zero."""
    assert haversine_km(LOC_LAT, LOC_LNG, LOC_LAT, LOC_LNG) == 0.0


def test_haversine_known_distance():
    """~1 degree of latitude is ~111 km; allow a generous tolerance."""
    d = haversine_km(0.0, 0.0, 1.0, 0.0)
    assert 110 < d < 112


def test_point_in_polygon_inside_and_outside():
    """Ray-casting correctly classifies inside vs outside points."""
    square = _square(LOC_LAT, LOC_LNG, 0.02)
    assert point_in_polygon(LOC_LAT, LOC_LNG, square) is True       # centre
    assert point_in_polygon(LOC_LAT + 1, LOC_LNG, square) is False  # far north


# --------------------------------------------------------------------------
# Quote endpoint (Zone Engine + ETA Calculator)
# --------------------------------------------------------------------------
def test_quote_inner_zone(client, location_id):
    """An address near the kitchen resolves to the cheaper inner zone."""
    resp = client.post(
        "/api/dispatch/quote",
        json={"location_id": location_id, "delivery_lat": 52.231, "delivery_lng": 21.014},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deliverable"] is True
    assert body["zone_name"] == "Inner"
    assert body["delivery_fee"] == 5.0
    assert body["eta_minutes"] > 0


def test_quote_outside_all_zones(client, location_id):
    """An address far away is flagged undeliverable with no fee."""
    resp = client.post(
        "/api/dispatch/quote",
        json={"location_id": location_id, "delivery_lat": 53.5, "delivery_lng": 22.5},
    )
    body = resp.json()
    assert body["deliverable"] is False
    assert body["delivery_fee"] == 0.0


# --------------------------------------------------------------------------
# Batching + assignment
# --------------------------------------------------------------------------
def _place_order(client, location_id, lat=52.231, lng=21.014):
    """Helper: place a simple deliverable order and return its JSON."""
    return client.post(
        "/api/orders",
        json={
            "location_id": location_id,
            "channel": "phone",
            "customer_phone": "+48500000009",
            "customer_name": "Batch Test",
            "delivery_address": "Somewhere 1",
            "delivery_lat": lat,
            "delivery_lng": lng,
            "items": [{"name": "Pizza", "qty": 1, "price": 30}],
        },
    ).json()


def test_dispatch_run_assigns_driver(client, location_id):
    """A pending order is batched into a run and a driver is assigned."""
    order = _place_order(client, location_id)
    assert order["run_id"] is None

    result = client.post("/api/dispatch/run").json()
    assert result["runs_created"] == 1
    assert result["orders_assigned"] == 1

    refreshed = client.get(f"/api/orders/{order['id']}").json()
    assert refreshed["status"] == "assigned"
    assert refreshed["run_id"] is not None

    # Exactly one driver should now be on a run.
    drivers = client.get("/api/drivers").json()
    on_run = [d for d in drivers if d["status"] == "on_run"]
    assert len(on_run) == 1
