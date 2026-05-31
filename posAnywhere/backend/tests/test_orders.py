"""Tests for the Order service: intake, pricing, lifecycle and delivery."""

from __future__ import annotations


def _order_payload(location_id, lat=52.231, lng=21.014):
    """Build a valid order-creation payload."""
    return {
        "location_id": location_id,
        "channel": "phone",
        "customer_phone": "+48500123123",
        "customer_name": "Jan Test",
        "delivery_address": "Test 5",
        "delivery_lat": lat,
        "delivery_lng": lng,
        "items": [{"name": "Margherita", "qty": 2, "price": 30.0}],
    }


def test_create_order_prices_and_accepts(client, location_id):
    """Order intake sums items, adds the zone fee and auto-accepts."""
    resp = client.post("/api/orders", json=_order_payload(location_id))
    assert resp.status_code == 201
    body = resp.json()
    assert body["items_total"] == 60.0       # 2 x 30
    assert body["delivery_fee"] == 5.0        # inner zone
    assert body["total"] == 65.0
    assert body["status"] == "accepted"
    assert body["tracking_token"]             # token issued for tracking


def test_create_order_requires_items(client, location_id):
    """An order with no items is rejected with HTTP 400."""
    payload = _order_payload(location_id)
    payload["items"] = []
    resp = client.post("/api/orders", json=payload)
    assert resp.status_code == 400


def test_create_order_unknown_location(client):
    """Placing an order against a missing location returns HTTP 404."""
    resp = client.post("/api/orders", json=_order_payload(location_id=999))
    assert resp.status_code == 404


def test_status_transition_records_history(client, location_id):
    """Advancing status is reflected on the order and in tracking history."""
    order = client.post("/api/orders", json=_order_payload(location_id)).json()
    client.post(f"/api/orders/{order['id']}/status?status=preparing")

    refreshed = client.get(f"/api/orders/{order['id']}").json()
    assert refreshed["status"] == "preparing"

    tracking = client.get(f"/api/tracking/{order['tracking_token']}").json()
    statuses = [h["status"] for h in tracking["history"]]
    assert "preparing" in statuses

    # US-1.6: the order endpoint itself returns items + the event history.
    assert refreshed["items"], "order should include its line items"
    event_statuses = [e["status"] for e in refreshed["status_events"]]
    assert "preparing" in event_statuses
    assert "accepted" in event_statuses


def test_deliver_frees_driver(client, location_id):
    """Delivering the only order on a run completes it and frees the driver."""
    order = client.post("/api/orders", json=_order_payload(location_id)).json()
    client.post("/api/dispatch/run")  # assign a driver

    # Deliver the order.
    delivered = client.post(f"/api/orders/{order['id']}/deliver").json()
    assert delivered["status"] == "delivered"

    # All drivers should be available again (none stuck on a run).
    drivers = client.get("/api/drivers").json()
    assert all(d["status"] != "on_run" for d in drivers)
