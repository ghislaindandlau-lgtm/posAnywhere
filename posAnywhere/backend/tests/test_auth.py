"""Tests for the authentication module: register, login and identity."""

from __future__ import annotations

EMAIL = "staff@example.com"
PASSWORD = "supersecret123"


def _register(client, email=EMAIL, password=PASSWORD, **extra):
    body = {"email": email, "password": password, **extra}
    return client.post("/api/auth/register", json=body)


def _login(client, email=EMAIL, password=PASSWORD):
    return client.post(
        "/api/auth/login", data={"username": email, "password": password}
    )


def test_register_creates_user(client):
    resp = _register(client)
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == EMAIL
    assert data["role"] == "staff"
    assert data["is_active"] is True
    # The password (or its hash) must never be returned.
    assert "password" not in data
    assert "hashed_password" not in data


def test_register_rejects_short_password(client):
    resp = _register(client, password="short")
    assert resp.status_code == 422


def test_register_duplicate_email_conflicts(client):
    assert _register(client).status_code == 201
    # Same email with different casing should still collide.
    resp = _register(client, email="STAFF@example.com")
    assert resp.status_code == 409


def test_login_returns_token_and_me_works(client):
    _register(client)
    resp = _login(client)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["token_type"] == "bearer"
    token = payload["access_token"]
    assert token

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL


def test_login_wrong_password_rejected(client):
    _register(client)
    resp = _login(client, password="wrongpassword")
    assert resp.status_code == 401


def test_login_unknown_user_rejected(client):
    resp = _login(client, email="nobody@example.com")
    assert resp.status_code == 401


def test_me_requires_authentication(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_rejects_invalid_token(client):
    resp = client.get(
        "/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401
