"""Tests for settlement/reporting and the app-free tracking endpoints."""

from __future__ import annotations

from datetime import date


def _place_and_deliver(client, location_id):
    """Place an order, dispatch it and mark it delivered; return its JSON."""
    order = client.post(
        "/api/orders",
        json={
            "location_id": location_id,
            "channel": "phone",
            "customer_phone": "+48500777888",
            "customer_name": "Settle Test",
            "delivery_address": "Cash 1",
            "delivery_lat": 52.231,
            "delivery_lng": 21.014,
            "items": [{"name": "Pizza", "qty": 1, "price": 40.0}],
        },
    ).json()
    client.post("/api/dispatch/run")
    client.post(f"/api/orders/{order['id']}/deliver")
    return order


# --------------------------------------------------------------------------
# Settlement + reporting
# --------------------------------------------------------------------------
def test_settlement_totals_delivered_cash(client, location_id):
    """Settlement sums the totals of a driver's delivered orders for the day."""
    _place_and_deliver(client, location_id)

    # Find which driver ended up on the run.
    drivers = client.get("/api/drivers").json()
    # After delivery the driver is available again, so settle whichever has runs;
    # driver id 1 takes the first run in the batching strategy.
    driver_id = 1
    today = date.today().isoformat()

    resp = client.post(f"/api/settlements/generate?driver_id={driver_id}&shift_date={today}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["orders_delivered"] == 1
    assert body["cash_total"] == 45.0  # 40 items + 5 delivery fee


def test_report_summary_counts(client, location_id):
    """The ops summary reflects delivered orders and revenue."""
    _place_and_deliver(client, location_id)
    summary = client.get("/api/reports/summary").json()
    assert summary["total_orders"] == 1
    assert summary["delivered"] == 1
    assert summary["revenue"] == 45.0


# --------------------------------------------------------------------------
# Tracking (REST snapshot + live WebSocket)
# --------------------------------------------------------------------------
def test_tracking_snapshot(client, location_id):
    """The public tracking endpoint returns a customer-safe snapshot."""
    order = client.post(
        "/api/orders",
        json={
            "location_id": location_id,
            "channel": "online_store",
            "customer_phone": "+48500111000",
            "customer_name": "Track Test",
            "delivery_address": "Track 9",
            "delivery_lat": 52.231,
            "delivery_lng": 21.014,
            "items": [{"name": "Pizza", "qty": 1, "price": 30.0}],
        },
    ).json()

    view = client.get(f"/api/tracking/{order['tracking_token']}").json()
    assert view["order_id"] == order["id"]
    assert view["restaurant_name"] == "Test Kitchen"
    assert view["status"] == "accepted"


def test_tracking_unknown_token_404(client):
    """An unknown tracking token returns HTTP 404."""
    assert client.get("/api/tracking/does-not-exist").status_code == 404


def test_tracking_websocket_sends_initial_snapshot(client, location_id):
    """Connecting to the tracking WS immediately pushes the current state."""
    order = client.post(
        "/api/orders",
        json={
            "location_id": location_id,
            "channel": "phone",
            "customer_phone": "+48500222333",
            "customer_name": "WS Test",
            "delivery_address": "WS 1",
            "delivery_lat": 52.231,
            "delivery_lng": 21.014,
            "items": [{"name": "Pizza", "qty": 1, "price": 30.0}],
        },
    ).json()

    token = order["tracking_token"]
    with client.websocket_connect(f"/api/tracking/{token}/ws") as ws:
        snapshot = ws.receive_json()
        assert snapshot["order_id"] == order["id"]
        assert snapshot["status"] == "accepted"
