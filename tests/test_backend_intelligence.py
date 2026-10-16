"""Comprehensive test suite for FleetFusion Backend Intelligence & Security (Phase A).

Validates:
1. NIST-compliant PBKDF2-HMAC-SHA256 password hashing and constant-time verification.
2. RFC 7519 HS256 JWT encoding, decoding, signature verification, and expiration.
3. Operator authentication via POST /api/v1/auth/token and GET /api/v1/auth/me.
4. Server-side Role-Based Access Control (RBAC): 401 unauthenticated, 403 unauthorized.
5. Authorized execution for DISPATCHER and ADMIN roles.
6. Edge machine X-Bus-API-Key verification.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import settings
from backend.app.core.security import (
    authenticate_user,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
    InvalidTokenError,
    TokenExpiredError,
)
from backend.app.main import app


@pytest.fixture
def client():
    """Test client fixture."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Cryptography & Password Hashing Tests
# ---------------------------------------------------------------------------
def test_password_hashing_and_verification() -> None:
    """Verify PBKDF2-HMAC-SHA256 hashing produces distinct salts and validates correctly."""
    pwd = "SecurePassword123!"
    hash1 = hash_password(pwd)
    hash2 = hash_password(pwd)

    # Hashes must differ due to random 16-byte salt
    assert hash1 != hash2
    assert hash1.startswith("pbkdf2:sha256:100000$")

    # Correct password succeeds
    assert verify_password(pwd, hash1) is True
    assert verify_password(pwd, hash2) is True

    # Incorrect password fails
    assert verify_password("WrongPassword!", hash1) is False
    assert verify_password("", hash1) is False

    # Malformed hash string handled safely
    assert verify_password(pwd, "invalid_hash_string") is False
    assert verify_password(pwd, "pbkdf2:md5:1000$salt$key") is False


# ---------------------------------------------------------------------------
# 2. Pure Python RFC 7519 JWT Tests
# ---------------------------------------------------------------------------
def test_jwt_encoding_decoding_and_expiration() -> None:
    """Verify HS256 JWT creation, claims preservation, signature checks, and expiration."""
    data = {"sub": "operator_test", "role": "DISPATCHER", "email": "test@pmc.gov.in"}
    token = create_access_token(data, expires_delta=timedelta(minutes=15))

    payload = decode_access_token(token)
    assert payload["sub"] == "operator_test"
    assert payload["role"] == "DISPATCHER"
    assert "exp" in payload
    assert "iat" in payload

    # 1. Tampered signature fails
    parts = token.split(".")
    tampered_sig = parts[2][:-2] + "xx"
    tampered_token = f"{parts[0]}.{parts[1]}.{tampered_sig}"
    with pytest.raises(InvalidTokenError, match="cryptographic signature"):
        decode_access_token(tampered_token)

    # 2. Tampered payload fails
    tampered_payload_token = f"{parts[0]}.e30.{parts[2]}"
    with pytest.raises(InvalidTokenError):
        decode_access_token(tampered_payload_token)

    # 3. Expired token raises TokenExpiredError
    expired_token = create_access_token(data, expires_delta=timedelta(seconds=-10))
    with pytest.raises(TokenExpiredError, match="expired"):
        decode_access_token(expired_token)


# ---------------------------------------------------------------------------
# 3. Authentication Endpoints: POST /auth/token and GET /auth/me
# ---------------------------------------------------------------------------
def test_operator_token_generation_success(client: TestClient) -> None:
    """Operators can exchange valid credentials for an HS256 access token."""
    # Test all provisioned roles
    credentials_map = {
        "admin": ("admin123", "ADMIN"),
        "dispatcher": ("dispatch123", "DISPATCHER"),
        "viewer": ("view123", "VIEWER"),
    }

    for username, (password, expected_role) in credentials_map.items():
        resp = client.post(
            "/api/v1/auth/token",
            json={"username": username, "password": password},
        )
        assert resp.status_code == 200, f"Login failed for {username}: {resp.text}"
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["role"] == expected_role
        assert data["username"] == username
        assert data["expires_in"] > 0

        # Verify token is valid via GET /api/v1/auth/me
        token = data["access_token"]
        me_resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200
        me_data = me_resp.json()
        assert me_data["username"] == username
        assert me_data["role"] == expected_role
        assert me_data["is_active"] is True


def test_operator_token_generation_invalid_credentials(client: TestClient) -> None:
    """Invalid credentials return HTTP 401 Unauthorized."""
    resp = client.post(
        "/api/v1/auth/token",
        json={"username": "dispatcher", "password": "WrongPassword!"},
    )
    assert resp.status_code == 401
    assert "Incorrect username or password" in resp.json()["detail"]

    # Non-existent user
    resp_unknown = client.post(
        "/api/v1/auth/token",
        json={"username": "non_existent_user", "password": "password"},
    )
    assert resp_unknown.status_code == 401


# ---------------------------------------------------------------------------
# 4. Server-Side Role-Based Access Control (RBAC) Enforcement
# ---------------------------------------------------------------------------
def test_rbac_status_mutation_gating(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that VIEWER role is forbidden from mutating ticket status, while DISPATCHER succeeds."""
    # Setup test ticket
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    client.post(
        "/tickets",
        json={
            "ticket_id": "POT-2026-888001",
            "incident_id": "INC-000001",
            "workorder_id": "WO-2026-888001",
            "event_type": "POTHOLE",
            "confidence": 0.94,
            "latitude": 18.5204,
            "longitude": 73.8436,
            "evidence_image": "runtime/evidence/EVT-000001.jpg",
            "google_maps_url": "https://www.google.com/maps",
            "estimated_repair_sla_hours": 48,
            "status": "REPORTED",
            "created_at": now,
            "updated_at": now,
        },
    )

    # 1. Viewer token -> Must return 403 Forbidden
    viewer_token = create_access_token({"sub": "viewer", "role": "VIEWER"})
    res_viewer = client.patch(
        "/api/v1/tickets/POT-2026-888001/status?new_status=ACKNOWLEDGED",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert res_viewer.status_code == 403
    assert "Access forbidden" in res_viewer.json()["detail"]
    assert "Current role is VIEWER" in res_viewer.json()["detail"]

    # 2. Dispatcher token -> Must return 200 OK
    dispatcher_token = create_access_token({"sub": "dispatcher", "role": "DISPATCHER"})
    res_dispatcher = client.patch(
        "/api/v1/tickets/POT-2026-888001/status?new_status=ACKNOWLEDGED",
        headers={"Authorization": f"Bearer {dispatcher_token}"},
    )
    assert res_dispatcher.status_code == 200
    assert res_dispatcher.json()["ticket"]["status"] == "ACKNOWLEDGED"

    # 3. Unauthenticated request when AUTH_ENFORCED is active -> Must return 401
    monkeypatch.setattr(settings, "AUTH_ENFORCED", True)
    res_unauth = client.patch(
        "/api/v1/tickets/POT-2026-888001/status?new_status=IN_PROGRESS",
    )
    assert res_unauth.status_code == 401
    assert "Authentication required" in res_unauth.json()["detail"]


def test_rbac_simulator_trigger_gating(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify simulator triggers are guarded to require ADMIN or DISPATCHER role."""
    # 1. Viewer token -> 403 Forbidden
    viewer_token = create_access_token({"sub": "viewer", "role": "VIEWER"})
    resp_viewer = client.post(
        "/api/v1/simulators/trigger",
        json={"scenario": "dual_bus"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp_viewer.status_code == 403

    # 2. Admin token -> 200 OK
    admin_token = create_access_token({"sub": "admin", "role": "ADMIN"})
    resp_admin = client.post(
        "/api/v1/simulators/trigger",
        json={"scenario": "dual_bus"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp_admin.status_code == 200
    assert "Dual-bus scenario executed successfully" in resp_admin.json()["message"]

    # 3. Unauthenticated when AUTH_ENFORCED is active -> 401
    monkeypatch.setattr(settings, "AUTH_ENFORCED", True)
    resp_unauth = client.post(
        "/api/v1/simulators/trigger",
        json={"scenario": "dual_bus"},
    )
    assert resp_unauth.status_code == 401


def test_expired_or_invalid_bearer_token_rejected(client: TestClient) -> None:
    """Requests with expired or invalid tokens return 401 with appropriate headers."""
    # Expired token
    expired = create_access_token({"sub": "admin", "role": "ADMIN"}, expires_delta=timedelta(seconds=-1))
    resp_expired = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {expired}"},
    )
    assert resp_expired.status_code == 401
    assert "Session expired" in resp_expired.json()["detail"]

    # Invalid token string
    resp_invalid = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not_a_valid_jwt_string"},
    )
    assert resp_invalid.status_code == 401
    assert "Invalid credentials token" in resp_invalid.json()["detail"]


def test_rbac_ticket_creation_gating(client: TestClient) -> None:
    """Verify that VIEWER role is blocked from creating tickets, while DISPATCHER succeeds."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {
        "ticket_id": "POT-2026-777001",
        "incident_id": "INC-000001",
        "workorder_id": "WO-2026-777001",
        "event_type": "POTHOLE",
        "confidence": 0.94,
        "latitude": 18.5204,
        "longitude": 73.8436,
        "evidence_image": "runtime/evidence/EVT-000001.jpg",
        "google_maps_url": "https://www.google.com/maps",
        "estimated_repair_sla_hours": 48,
        "status": "REPORTED",
        "created_at": now,
        "updated_at": now,
    }

    # 1. Viewer token -> 403 Forbidden
    viewer_token = create_access_token({"sub": "viewer", "role": "VIEWER"})
    res_viewer = client.post(
        "/api/v1/tickets",
        json=payload,
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert res_viewer.status_code == 403

    # 2. Dispatcher token -> 201 Created
    dispatcher_token = create_access_token({"sub": "dispatcher", "role": "DISPATCHER"})
    res_dispatcher = client.post(
        "/api/v1/tickets",
        json=payload,
        headers={"Authorization": f"Bearer {dispatcher_token}"},
    )
    assert res_dispatcher.status_code == 201
    assert res_dispatcher.json()["ticket"]["ticket_id"] == "POT-2026-777001"


def test_bus_api_key_verification() -> None:
    """Verify edge sensing machine API key validation."""
    from backend.app.core.security import verify_bus_auth
    import asyncio

    # Valid key
    assert asyncio.run(verify_bus_auth(x_bus_api_key=settings.BUS_API_KEY)) is True

    # Invalid key
    assert asyncio.run(verify_bus_auth(x_bus_api_key="invalid_bus_key")) is False
    assert asyncio.run(verify_bus_auth(x_bus_api_key=None)) is False
