"""Tests for request logging, correlation IDs and failure documentation."""

from __future__ import annotations

import logging


def test_every_response_has_correlation_id(client):
    """The middleware tags every response with an X-Request-ID header."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID")


def test_successful_request_is_logged(client, caplog):
    """Each request produces an audit log line naming the method + path."""
    with caplog.at_level(logging.INFO, logger="posanywhere.request"):
        client.get("/api/health")
    assert any(
        "/api/health" in rec.getMessage() and rec.levelno == logging.INFO
        for rec in caplog.records
    )


def test_http_failure_is_documented(client, caplog):
    """A 404 (raised HTTPException) is logged as a warning with its detail."""
    with caplog.at_level(logging.WARNING, logger="posanywhere.request"):
        resp = client.get("/api/orders/999999")
    assert resp.status_code == 404
    assert any("http_error" in rec.getMessage() for rec in caplog.records)


def test_failed_login_is_documented(client, caplog):
    """A bad-credentials login is logged as a security warning."""
    client.post(
        "/api/auth/register",
        json={"email": "log@example.com", "password": "supersecret123"},
    )
    with caplog.at_level(logging.WARNING, logger="app.modules.auth"):
        resp = client.post(
            "/api/auth/login",
            data={"username": "log@example.com", "password": "wrongpassword"},
        )
    assert resp.status_code == 401
    assert any("auth.login.failed" in rec.getMessage() for rec in caplog.records)


def test_order_creation_is_logged(client, caplog, location_id):
    """Placing an order emits an order.created audit line."""
    body = {
        "location_id": location_id,
        "channel": "phone",
        "customer_phone": "+48500000111",
        "delivery_lat": 52.2300,
        "delivery_lng": 21.0150,
        "items": [{"name": "Pizza", "qty": 1, "price": 30.0}],
    }
    with caplog.at_level(logging.INFO, logger="app.domain"):
        with caplog.at_level(logging.INFO, logger="app.modules.orders"):
            resp = client.post("/api/orders", json=body)
    assert resp.status_code == 201
    assert any("order.created" in rec.getMessage() for rec in caplog.records)
    assert any("order.status_change" in rec.getMessage() for rec in caplog.records)
