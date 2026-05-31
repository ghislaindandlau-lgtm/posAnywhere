"""Tests for request logging, correlation IDs and failure documentation."""

from __future__ import annotations

import json
import logging

from app.logging_config import ContextFilter, JsonFormatter, request_id_var, user_var


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


def test_json_formatter_emits_structured_line_with_context():
    """JSON formatter outputs one-line JSON enriched with request_id + user."""
    fmt = JsonFormatter()
    ctx = ContextFilter()
    token_r = request_id_var.set("abc123")
    token_u = user_var.set("user:7")
    try:
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        ctx.filter(record)  # emulate the handler-attached context filter
        line = fmt.format(record)
    finally:
        request_id_var.reset(token_r)
        user_var.reset(token_u)

    data = json.loads(line)
    assert data["message"] == "hello world"
    assert data["level"] == "INFO"
    assert data["logger"] == "test.logger"
    assert data["request_id"] == "abc123"
    assert data["user"] == "user:7"


class _DummyRequest:
    """Minimal stand-in exposing the `.headers` attribute used by _identify_user."""

    def __init__(self, headers: dict[str, str]):
        self.headers = headers


def test_identify_user_from_valid_bearer_token():
    """A valid JWT resolves to the 'user:<id>' identity used in log lines."""
    from app.main import _identify_user
    from app.security import create_access_token

    token = create_access_token(subject="7")
    request = _DummyRequest({"Authorization": f"Bearer {token}"})
    assert _identify_user(request) == "user:7"


def test_identify_user_anonymous_without_token():
    """Requests without a Bearer token are logged as anonymous."""
    from app.main import _identify_user

    assert _identify_user(_DummyRequest({})) == "anonymous"


def test_identify_user_invalid_token():
    """A malformed token is recorded as 'invalid-token' (not a crash)."""
    from app.main import _identify_user

    request = _DummyRequest({"Authorization": "Bearer not-a-real-token"})
    assert _identify_user(request) == "invalid-token"
