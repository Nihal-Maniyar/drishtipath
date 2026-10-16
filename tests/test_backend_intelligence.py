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
import json
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


# ---------------------------------------------------------------------------
# Phase B: Downstream Bus Hazard Advisories Tests
# ---------------------------------------------------------------------------

def test_hazard_advisory_contract_validation() -> None:
    """Verify HazardAdvisory schema validation and regex identifier enforcement."""
    from backend.app.schemas.advisory import HazardAdvisory
    import pydantic

    now = datetime.now(timezone.utc)
    now_str = now.isoformat().replace("+00:00", "Z")
    exp_str = (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    valid_payload = {
        "advisory_id": "ADV-2026-000001",
        "incident_id": "INC-000001",
        "source_bus_id": "BUS-001",
        "route_id": "ROUTE-PUNE-FC",
        "latitude": 18.5196,
        "longitude": 73.8436,
        "road_aligned_latitude": 18.5196,
        "road_aligned_longitude": 73.8436,
        "severity": "HIGH",
        "event_type": "POTHOLE",
        "issued_at": now_str,
        "expires_at": exp_str,
        "corridor_window_meters": 500.0,
        "target_buses": ["BUS-002"],
        "description": "Severe pothole hazard ahead",
    }

    adv = HazardAdvisory.model_validate(valid_payload)
    assert adv.advisory_id == "ADV-2026-000001"
    assert adv.is_expired() is False

    # Invalid advisory ID format
    invalid_id = dict(valid_payload, advisory_id="INVALID-001")
    with pytest.raises(pydantic.ValidationError):
        HazardAdvisory.model_validate(invalid_id)

    # Invalid incident ID format
    invalid_inc = dict(valid_payload, incident_id="INCIDENT-1")
    with pytest.raises(pydantic.ValidationError):
        HazardAdvisory.model_validate(invalid_inc)

    # Expired advisory
    past_str = (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    expired_adv = HazardAdvisory.model_validate(dict(valid_payload, expires_at=past_str))
    assert expired_adv.is_expired() is True


def test_bearing_and_heading_approach_calculation() -> None:
    """Verify forward bearing and heading angle divergence calculations."""
    from backend.app.services.advisory_service import (
        compute_bearing,
        haversine_distance_meters,
        is_bus_approaching,
    )

    # Point A to Point B directly East (same latitude, increasing longitude)
    lat1, lon1 = 18.5000, 73.8000
    lat2, lon2 = 18.5000, 73.8100  # East of point 1
    dist = haversine_distance_meters(lat1, lon1, lat2, lon2)
    assert dist > 1000.0  # Approx 1.05 km

    bearing = compute_bearing(lat1, lon1, lat2, lon2)
    assert 85.0 <= bearing <= 95.0  # Close to 90 degrees (East)

    # Bus heading East (90 deg) approaching hazard to the East -> True
    assert is_bus_approaching(lat1, lon1, bus_heading=90.0, hazard_lat=lat2, hazard_lon=lon2) is True
    # Bus heading North-East (45 deg) -> True (within 90 deg divergence)
    assert is_bus_approaching(lat1, lon1, bus_heading=45.0, hazard_lat=lat2, hazard_lon=lon2) is True
    # Bus heading West (270 deg) moving away from hazard -> False
    assert is_bus_approaching(lat1, lon1, bus_heading=270.0, hazard_lat=lat2, hazard_lon=lon2) is False
    # Bus heading undefined (None) -> Fallback True
    assert is_bus_approaching(lat1, lon1, bus_heading=None, hazard_lat=lat2, hazard_lon=lon2) is True


def test_corridor_qualification_downstream_window() -> None:
    """Verify that only downstream buses within 500m on the same route qualify."""
    from backend.app.services.advisory_service import advisory_service

    hazard_lat, hazard_lon = 18.5196, 73.8436  # Goodluck Chowk
    route_id = "ROUTE-PUNE-FC"

    test_fleet = {
        # 1. Reporting bus -> must be excluded
        "BUS-001": {
            "bus_id": "BUS-001",
            "route_id": route_id,
            "latitude": 18.5196,
            "longitude": 73.8436,
            "heading_degrees": 90.0,
            "status": "ONLINE",
        },
        # 2. Downstream bus approaching, ~250m away on same route -> QUALIFIED
        "BUS-002": {
            "bus_id": "BUS-002",
            "route_id": route_id,
            "latitude": 18.5175,  # South of hazard
            "longitude": 73.8436,
            "heading_degrees": 0.0,  # Heading North towards hazard
            "status": "ONLINE",
        },
        # 3. Far bus, ~1.2 km away on same route (> 500m) -> EXCLUDED
        "BUS-003": {
            "bus_id": "BUS-003",
            "route_id": route_id,
            "latitude": 18.5320,
            "longitude": 73.8450,
            "heading_degrees": 180.0,
            "status": "ONLINE",
        },
        # 4. Bus on DIFFERENT route, 200m away -> EXCLUDED
        "BUS-004": {
            "bus_id": "BUS-004",
            "route_id": "ROUTE-PUNE-KARVE",
            "latitude": 18.5180,
            "longitude": 73.8436,
            "heading_degrees": 0.0,
            "status": "ONLINE",
        },
        # 5. Bus on same route and 200m away, but OFFLINE -> EXCLUDED
        "BUS-005": {
            "bus_id": "BUS-005",
            "route_id": route_id,
            "latitude": 18.5180,
            "longitude": 73.8436,
            "heading_degrees": 0.0,
            "status": "OFFLINE",
        },
        # 6. Bus 200m away on same route, but heading away (180 deg) -> EXCLUDED
        "BUS-006": {
            "bus_id": "BUS-006",
            "route_id": route_id,
            "latitude": 18.5175,  # South of hazard
            "longitude": 73.8436,
            "heading_degrees": 180.0,  # Moving South away from hazard
            "status": "ONLINE",
        },
    }

    qualified = advisory_service.qualify_downstream_buses(
        hazard_lat=hazard_lat,
        hazard_lon=hazard_lon,
        route_id=route_id,
        fleet=test_fleet,
        source_bus_id="BUS-001",
        corridor_window_meters=500.0,
    )

    assert qualified == ["BUS-002"]


def test_advisory_publishing_and_rest_endpoints(client: TestClient) -> None:
    """Verify advisory creation, mock MQTT dispatch, and REST retrieval endpoints."""
    from backend.app.services.advisory_service import advisory_service

    # Setup mock MQTT publisher
    published_messages = []

    class MockMQTTClient:
        def publish(self, topic: str, payload: str, qos: int = 1):
            published_messages.append({"topic": topic, "payload": json.loads(payload), "qos": qos})

    advisory_service.set_mqtt_client(MockMQTTClient())

    # Create severe pothole incident in main dictionary
    from backend.app.main import buses_by_id, incidents_by_id

    test_inc = {
        "incident_id": "INC-000888",
        "event_type": "POTHOLE",
        "severity": "HIGH",
        "status": "VERIFIED",
        "confidence": 0.96,
        "latitude": 18.5196,
        "longitude": 73.8436,
        "route_id": "ROUTE-PUNE-FC",
        "buses": ["BUS-001"],
        "description": "Severe pothole at Goodluck Chowk",
    }
    incidents_by_id["INC-000888"] = test_inc

    # Evaluate and issue advisory
    adv = advisory_service.evaluate_and_publish(
        incident=test_inc,
        fleet=buses_by_id,
        source_bus_id="BUS-001",
        route_id="ROUTE-PUNE-FC",
        corridor_window_meters=500.0,
    )

    assert adv is not None
    assert adv.incident_id == "INC-000888"
    assert adv.source_bus_id == "BUS-001"
    assert adv.severity == "HIGH"

    # Verify MQTT delivery on both global and route-specific topics
    topics = [m["topic"] for m in published_messages]
    assert "beyonders/advisories/v1" in topics
    assert "beyonders/advisories/v1/ROUTE-PUNE-FC" in topics
    assert all(m["qos"] == 1 for m in published_messages)

    # 1. Query GET /api/v1/advisories
    resp_list = client.get("/api/v1/advisories")
    assert resp_list.status_code == 200
    adv_list = resp_list.json()
    assert any(a["advisory_id"] == adv.advisory_id for a in adv_list)

    # 2. Query GET /api/v1/advisories/{id}
    resp_single = client.get(f"/api/v1/advisories/{adv.advisory_id}")
    assert resp_single.status_code == 200
    assert resp_single.json()["advisory_id"] == adv.advisory_id

    # 3. Query POST /api/v1/advisories/evaluate with dispatcher token
    dispatcher_token = create_access_token({"sub": "dispatcher", "role": "DISPATCHER"})
    resp_eval = client.post(
        "/api/v1/advisories/evaluate",
        json={"incident_id": "INC-000888", "route_id": "ROUTE-PUNE-FC", "corridor_window_meters": 500.0, "ttl_seconds": 1800},
        headers={"Authorization": f"Bearer {dispatcher_token}"},
    )
    assert resp_eval.status_code == 201
    assert resp_eval.json()["incident_id"] == "INC-000888"


def test_advisory_subscriber_idempotency_and_stale_filtering() -> None:
    """Verify that subscriber idempotently deduplicates QoS 1 re-deliveries and rejects stale advisories."""
    from backend.app.services.advisory_service import AdvisorySubscriber

    subscriber = AdvisorySubscriber(bus_id="BUS-002", route_id="ROUTE-PUNE-FC")

    now = datetime.now(timezone.utc)
    now_str = now.isoformat().replace("+00:00", "Z")
    future_exp = (now + timedelta(minutes=45)).isoformat().replace("+00:00", "Z")

    valid_payload = {
        "advisory_id": "ADV-2026-999001",
        "incident_id": "INC-000001",
        "source_bus_id": "BUS-001",
        "route_id": "ROUTE-PUNE-FC",
        "latitude": 18.5196,
        "longitude": 73.8436,
        "severity": "HIGH",
        "event_type": "POTHOLE",
        "issued_at": now_str,
        "expires_at": future_exp,
        "corridor_window_meters": 500.0,
        "target_buses": ["BUS-002"],
    }

    # 1. First reception -> Accepted
    res1 = subscriber.process_message_payload(valid_payload)
    assert res1 is not None
    assert res1.advisory_id == "ADV-2026-999001"
    assert len(subscriber.received_advisories) == 1

    # 2. QoS 1 Duplicate re-delivery of the exact same message -> Deduplicated (returns None)
    res2 = subscriber.process_message_payload(valid_payload)
    assert res2 is None
    assert len(subscriber.received_advisories) == 1  # Still 1, no duplicate stored

    # 3. Stale / Expired message -> Dropped (returns None)
    past_exp = (now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    stale_payload = dict(valid_payload, advisory_id="ADV-2026-999002", expires_at=past_exp)
    res_stale = subscriber.process_message_payload(stale_payload)
    assert res_stale is None
    assert "ADV-2026-999002" not in subscriber.seen_advisories

    # 4. Irrelevant route message -> Dropped
    diff_route = dict(valid_payload, advisory_id="ADV-2026-999003", route_id="ROUTE-PUNE-SHIVAJI")
    res_route = subscriber.process_message_payload(diff_route)
    assert res_route is None


# ---------------------------------------------------------------------------
# Phase C: Closed-Loop Automated Repair Verification Tests
# ---------------------------------------------------------------------------

def test_verification_watch_scheduling_on_resolved_ticket(client: TestClient) -> None:
    """Verify that transitioning a ticket to RESOLVED automatically schedules a verification watch."""
    from backend.app.main import tickets_by_id
    from backend.app.services.verification_service import verification_service

    verification_service.reset_for_tests()

    # Create a test ticket
    t_id = "POT-2026-990001"
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    tickets_by_id[t_id] = {
        "ticket_id": t_id,
        "incident_id": "INC-000001",
        "event_type": "POTHOLE",
        "latitude": 18.5196,
        "longitude": 73.8436,
        "status": "IN_PROGRESS",
        "created_at": now_iso,
        "updated_at": now_iso,
        "timeline": [
            {"label": "Reported", "done": True, "time": now_iso},
            {"label": "Acknowledged", "done": True, "time": now_iso},
            {"label": "In Progress", "done": True, "time": now_iso},
            {"label": "Resolved", "done": False, "time": "Pending"},
        ],
    }

    # Transition ticket to RESOLVED with DISPATCHER credentials
    token = create_access_token({"sub": "dispatcher", "role": "DISPATCHER"})
    patch_resp = client.patch(
        f"/api/v1/tickets/{t_id}/status?new_status=RESOLVED",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["ticket"]["status"] == "RESOLVED"

    # Verify that a verification watch was scheduled and can be queried
    v_resp = client.get(f"/api/v1/tickets/{t_id}/verification")
    assert v_resp.status_code == 200
    watch_data = v_resp.json()
    assert watch_data["ticket_id"] == t_id
    assert watch_data["state"] == "WATCHING"
    assert watch_data["required_smooth_observations"] == 2
    assert watch_data["smooth_observations_count"] == 0


def test_smooth_surface_corroboration_advances_to_verified_resolved(client: TestClient) -> None:
    """Verify that independent smooth surface observations advance the ticket to VERIFIED_RESOLVED."""
    from backend.app.main import tickets_by_id
    from backend.app.services.verification_service import verification_service

    verification_service.reset_for_tests()

    t_id = "POT-2026-990002"
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    tkt = {
        "ticket_id": t_id,
        "incident_id": "INC-000002",
        "event_type": "POTHOLE",
        "latitude": 18.5196,
        "longitude": 73.8436,
        "status": "RESOLVED",
        "created_at": now_iso,
        "updated_at": now_iso,
        "timeline": [
            {"label": "Reported", "done": True, "time": now_iso},
            {"label": "Resolved", "done": True, "time": now_iso},
        ],
    }
    tickets_by_id[t_id] = tkt
    verification_service.schedule_watch(tkt)

    # 1. First smooth observation by BUS-002 (simulated via POST endpoint)
    token = create_access_token({"sub": "admin", "role": "ADMIN"})
    res1 = client.post(
        f"/api/v1/tickets/{t_id}/verify-smooth",
        json={"bus_id": "BUS-002", "confidence": 0.88},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res1.status_code == 200
    w1 = res1.json()
    assert w1["state"] == "WATCHING"
    assert w1["smooth_observations_count"] == 1
    assert "BUS-002" in w1["smooth_buses"]

    # 2. Second independent smooth observation by BUS-003 -> Must reach threshold and advance to VERIFIED_RESOLVED
    res2 = client.post(
        f"/api/v1/tickets/{t_id}/verify-smooth",
        json={"bus_id": "BUS-003", "confidence": 0.90},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res2.status_code == 200
    w2 = res2.json()
    assert w2["state"] == "VERIFIED_RESOLVED"
    assert w2["smooth_observations_count"] == 2
    assert "BUS-003" in w2["smooth_buses"]

    # Check updated ticket state
    assert tickets_by_id[t_id]["verification_status"] == "VERIFIED_RESOLVED"
    assert any(step["label"] == "Verified Resolved" for step in tickets_by_id[t_id]["timeline"])


def test_persistent_defect_reopens_ticket_with_failed_repair() -> None:
    """Verify that subsequent defect observation within 25m marks FAILED_REPAIR and reopens ticket to REPORTED."""
    from backend.app.main import tickets_by_id
    from backend.app.services.verification_service import verification_service

    verification_service.reset_for_tests()

    t_id = "POT-2026-990003"
    t_dt = datetime.now(timezone.utc)
    t_iso = t_dt.isoformat().replace("+00:00", "Z")
    tkt = {
        "ticket_id": t_id,
        "incident_id": "INC-000003",
        "event_type": "POTHOLE",
        "latitude": 18.519600,
        "longitude": 73.843600,
        "status": "RESOLVED",
        "created_at": t_iso,
        "updated_at": t_iso,
        "timeline": [
            {"label": "Reported", "done": True, "time": t_iso},
            {"label": "Resolved", "done": True, "time": t_iso},
        ],
    }
    tickets_by_id[t_id] = tkt
    watch = verification_service.schedule_watch(tkt)

    # Observation occurs AFTER resolution at the same spot (10 meters away)
    obs_time = (t_dt + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
    defect_obs = {
        "observation_id": "OBS-DEFECT-001",
        "event_id": "EVT-DEFECT-001",
        "bus_id": "BUS-004",
        "event_type": "POTHOLE",
        "timestamp": obs_time,
        "latitude": 18.519650,  # ~6m distance
        "longitude": 73.843600,
        "confidence": 0.94,
    }

    updated_watch, affected_ticket = verification_service.process_observation(defect_obs, tickets_by_id)

    assert updated_watch is not None
    assert updated_watch.state == "FAILED_REPAIR"
    assert affected_ticket is not None
    # Ticket must be reopened to REPORTED!
    assert affected_ticket["status"] == "REPORTED"
    assert affected_ticket["verification_status"] == "FAILED_REPAIR"
    assert any("Failed Repair Reopened" in step.get("label", "") for step in affected_ticket["timeline"])


def test_temporal_guard_older_events_cannot_overwrite_newer_state() -> None:
    """Verify that replaying older historical events cannot overwrite a newer resolved ticket state."""
    from backend.app.main import tickets_by_id
    from backend.app.services.verification_service import verification_service

    verification_service.reset_for_tests()

    t_id = "POT-2026-990004"
    t_dt = datetime.now(timezone.utc)
    t_iso = t_dt.isoformat().replace("+00:00", "Z")

    tkt = {
        "ticket_id": t_id,
        "incident_id": "INC-000004",
        "event_type": "POTHOLE",
        "latitude": 18.5196,
        "longitude": 73.8436,
        "status": "RESOLVED",
        "created_at": t_iso,
        "updated_at": t_iso,
        "timeline": [{"label": "Resolved", "done": True, "time": t_iso}],
    }
    tickets_by_id[t_id] = tkt
    watch = verification_service.schedule_watch(tkt)

    # Historical event replayed from BEFORE the ticket was resolved
    old_time = (t_dt - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    stale_obs = {
        "observation_id": "OBS-STALE-REPLAY-001",
        "event_id": "EVT-STALE-REPLAY-001",
        "bus_id": "BUS-005",
        "event_type": "POTHOLE",
        "timestamp": old_time,
        "latitude": 18.5196,
        "longitude": 73.8436,
        "confidence": 0.95,
    }

    # Processing stale observation must be ignored
    updated_watch, affected_ticket = verification_service.process_observation(stale_obs, tickets_by_id)
    assert updated_watch is None
    assert affected_ticket is None

    # Ticket remains in RESOLVED / WATCHING state
    assert tickets_by_id[t_id]["status"] == "RESOLVED"
    assert watch.state == "WATCHING"


def test_expired_watch_transitions_to_expired_unverified() -> None:
    """Verify that a watch where no bus passes during the 48-hour window transitions to EXPIRED_UNVERIFIED."""
    from backend.app.main import tickets_by_id
    from backend.app.services.verification_service import verification_service

    verification_service.reset_for_tests()

    t_id = "POT-2026-990005"
    now = datetime.now(timezone.utc)
    t_iso = now.isoformat().replace("+00:00", "Z")

    tkt = {
        "ticket_id": t_id,
        "incident_id": "INC-000005",
        "event_type": "POTHOLE",
        "latitude": 18.5196,
        "longitude": 73.8436,
        "status": "RESOLVED",
        "created_at": t_iso,
        "updated_at": t_iso,
    }
    tickets_by_id[t_id] = tkt
    watch = verification_service.schedule_watch(tkt, window_hours=48)

    # Simulate evaluation 49 hours later
    simulated_future = now + timedelta(hours=49)
    expired = verification_service.evaluate_expired_watches(tickets_by_id, current_time=simulated_future)

    assert any(w.watch_id == watch.watch_id for w in expired)
    assert watch.state == "EXPIRED_UNVERIFIED"
    assert tickets_by_id[t_id]["verification_status"] == "EXPIRED_UNVERIFIED"


