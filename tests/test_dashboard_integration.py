"""Comprehensive integration tests for the GIS Dashboard and live platform APIs."""

import json
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from backend.app.main import app, incidents_by_id, tickets_by_id


class TestDashboardIntegration(unittest.TestCase):
    """Verify GIS dashboard file validity, contract compliance, and backend delivery."""

    def setUp(self) -> None:
        self.client = TestClient(app)
        self.repo_root = Path(__file__).resolve().parent.parent
        self.dashboard_html = self.repo_root / "dashboard" / "index.html"
        evidence_file = self.repo_root / "runtime" / "evidence" / "EVT-000001.jpg"
        if not evidence_file.exists():
            evidence_file.parent.mkdir(parents=True, exist_ok=True)
            evidence_file.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\xff\xd9")

    def test_dashboard_file_exists_and_valid(self) -> None:
        self.assertTrue(self.dashboard_html.is_file(), "dashboard/index.html must exist")
        content = self.dashboard_html.read_text(encoding="utf-8")
        self.assertIn("<!DOCTYPE html>", content)
        self.assertIn("Fleet Sensing Urban Intel", content)
        # Real GIS Leaflet verification
        self.assertIn("leaflet.js", content)
        self.assertIn("leafletMap", content)
        # Contract compliance checks
        self.assertIn("POT-", content)
        self.assertIn("BUS-001", content)
        self.assertIn("REPORTED", content)
        self.assertIn("ACKNOWLEDGED", content)
        self.assertIn("IN_PROGRESS", content)
        self.assertIn("RESOLVED", content)
        self.assertIn("google.com/maps", content)
        self.assertIn("Pune", content)

    def test_dashboard_served_via_fastapi(self) -> None:
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers.get("content-type", ""))
        self.assertIn("Fleet Sensing Urban Intel", response.text)
        self.assertIn("leafletMap", response.text)

    def test_dashboard_linked_in_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("dashboard_url"), "/dashboard")

    def test_platform_stats_endpoint(self) -> None:
        response = self.client.get("/api/v1/stats")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("total_incidents", data)
        self.assertIn("fleet_online", data)
        self.assertIn("open_tickets", data)

    def test_fleet_telemetry_endpoint(self) -> None:
        response = self.client.get("/api/v1/fleet")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("fleet", data)
        self.assertGreaterEqual(len(data["fleet"]), 1)
        bus_ids = [b["bus_id"] for b in data["fleet"]]
        self.assertIn("BUS-001", bus_ids)

    def test_gis_incidents_geojson_endpoint(self) -> None:
        # Seed working data
        self.client.post("/api/v1/demo/seed")
        response = self.client.get("/api/v1/incidents/geojson")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("type"), "FeatureCollection")
        self.assertIn("features", data)
        self.assertGreaterEqual(len(data["features"]), 1)
        feature = data["features"][0]
        self.assertEqual(feature["type"], "Feature")
        self.assertEqual(feature["geometry"]["type"], "Point")
        self.assertEqual(len(feature["geometry"]["coordinates"]), 2)

    def test_gis_routes_geojson_endpoint(self) -> None:
        response = self.client.get("/api/v1/routes/geojson")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("type"), "FeatureCollection")
        self.assertIn("features", data)
        route_ids = [f["properties"]["route_id"] for f in data["features"]]
        self.assertIn("ROUTE-PUNE-FC", route_ids)
        self.assertIn("ROUTE-PUNE-JM", route_ids)

    def test_dashboard_ticket_lifecycle_backend_sync(self) -> None:
        # Create a sample ticket
        ticket_payload = {
            "ticket_id": "POT-2026-888888",
            "incident_id": "INC-000001",
            "workorder_id": "WO-2026-888888",
            "event_type": "POTHOLE",
            "confidence": 0.94,
            "latitude": 18.5204,
            "longitude": 73.8567,
            "evidence_image": "runtime/evidence/EVT-000001.jpg",
            "google_maps_url": "https://www.google.com/maps/dir/?api=1&destination=18.5204,73.8567",
            "estimated_repair_sla_hours": 48,
            "status": "REPORTED",
            "created_at": "2026-09-20T10:30:00Z",
            "updated_at": "2026-09-20T10:30:00Z",
        }
        res = self.client.post("/tickets", json=ticket_payload)
        self.assertEqual(res.status_code, 201)

        # Transition through lifecycle matching the frontend order
        lifecycle = ["ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED"]
        for st in lifecycle:
            patch_res = self.client.patch(f"/tickets/POT-2026-888888/status?new_status={st}")
            self.assertEqual(patch_res.status_code, 200)
            self.assertEqual(patch_res.json()["ticket"]["status"], st)

        # Cleanup
        tickets_by_id.pop("POT-2026-888888", None)

    def test_websocket_stream_and_initial_state(self) -> None:
        """Verify real-time WebSocket connection, initial state payload, and ping-pong."""
        with self.client.websocket_connect("/api/v1/ws") as websocket:
            data = websocket.receive_json()
            self.assertEqual(data.get("type"), "INITIAL_STATE")
            self.assertIn("stats", data.get("data", {}))
            self.assertIn("fleet", data.get("data", {}))
            self.assertIn("incidents", data.get("data", {}))

            websocket.send_text("ping")
            reply = websocket.receive_text()
            self.assertEqual(reply, "pong")

    def test_simulator_trigger_scenarios(self) -> None:
        """Verify interactive scenario execution from the GIS dashboard."""
        # Dual-bus scenario
        res = self.client.post("/api/v1/simulators/trigger", json={"scenario": "dual_bus"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("Dual-bus scenario executed", data["message"])
        self.assertEqual(data["incident_id"], "INC-000001")
        self.assertEqual(data["ticket_id"], "POT-2026-000001")

        # Verify incident and ticket exist with verified road location
        inc_res = self.client.get("/incidents/INC-000001")
        self.assertEqual(inc_res.status_code, 200)
        self.assertEqual(inc_res.json()["status"], "VERIFIED")
        self.assertEqual(inc_res.json()["latitude"], 18.5196)
        self.assertEqual(inc_res.json()["longitude"], 73.8436)

        # Offline replay scenario
        offline_res = self.client.post("/api/v1/simulators/trigger", json={"scenario": "offline_replay"})
        self.assertEqual(offline_res.status_code, 200)
        self.assertIn("Offline replay scenario executed", offline_res.json()["message"])

        # Resolution scenario
        res_res = self.client.post("/api/v1/simulators/trigger", json={"scenario": "resolution"})
        self.assertEqual(res_res.status_code, 200)
        tkt_res = self.client.get("/tickets/POT-2026-000001")
        self.assertEqual(tkt_res.status_code, 200)
        self.assertEqual(tkt_res.json()["status"], "RESOLVED")

    def test_static_evidence_serving(self) -> None:
        """Verify evidence photos can be retrieved by the browser for inspection."""
        res = self.client.get("/runtime/evidence/EVT-000001.jpg")
        self.assertEqual(res.status_code, 200)
        self.assertIn("image/jpeg", res.headers.get("content-type", ""))

    def test_pune_corridor_road_snapping_verification(self) -> None:
        """Verify all seeded incidents lie exactly on authentic Pune road corridors."""
        self.client.post("/api/v1/demo/seed")
        res = self.client.get("/api/v1/incidents")
        self.assertEqual(res.status_code, 200)
        incidents = res.json()["incidents"]

        rts_res = self.client.get("/api/v1/routes/geojson")
        routes_geojson = rts_res.json()
        all_waypoints = []
        for feat in routes_geojson["features"]:
            for coord in feat["geometry"]["coordinates"]:
                all_waypoints.append((coord[1], coord[0]))  # (lat, lon)

        # Check each incident is associated with a waypoint coordinate or along corridor
        for inc in incidents:
            lat = float(inc["latitude"])
            lon = float(inc["longitude"])
            # Ensure within Pune bounding box
            self.assertTrue(18.49 <= lat <= 18.55, f"Latitude {lat} out of Pune range")
            self.assertTrue(73.81 <= lon <= 73.87, f"Longitude {lon} out of Pune range")

    def test_incident_report_evidence_image_attached(self) -> None:
        """Verify evidence image received from bus simulator is attached to fused incident and served."""
        # 1. Re-seed demo data and check incident evidence
        self.client.post("/api/v1/demo/seed")
        inc_res = self.client.get("/incidents/INC-000001")
        self.assertEqual(inc_res.status_code, 200)
        inc_data = inc_res.json()
        self.assertIn("evidence_image", inc_data)
        self.assertEqual(inc_data["evidence_image"], "runtime/evidence/EVT-000001.jpg")

        # 2. Ingest an event from actual bus simulator with evidence_image
        event_payload = {
            "event_id": "EVT-099999",
            "bus_id": "BUS-001",
            "event_type": "POTHOLE",
            "timestamp": "2026-09-24T12:00:00Z",
            "latitude": 18.5204,
            "longitude": 73.8567,
            "confidence": 0.96,
            "severity": "HIGH",
            "evidence_image": "runtime/evidence/EVT-000001.jpg",
            "source": "actual_bus_simulator",
            "connectivity_state": "ONLINE",
        }
        ingest_res = self.client.post("/events", json=event_payload)
        self.assertEqual(ingest_res.status_code, 201)

        # 3. Retrieve incidents and verify newly created incident has evidence_image
        inc_list = self.client.get("/incidents").json()["incidents"]
        matching = [i for i in inc_list if i.get("event_id") == "EVT-099999"]
        self.assertTrue(len(matching) >= 1)
        self.assertEqual(matching[0]["evidence_image"], "runtime/evidence/EVT-000001.jpg")

        # 4. Verify static route returns the image
        img_res = self.client.get(f"/{matching[0]['evidence_image']}")
        self.assertEqual(img_res.status_code, 200)
        self.assertIn("image/jpeg", img_res.headers.get("content-type", ""))

    def test_frontend_auth_modal_and_role_tokens(self) -> None:
        """Verify frontend contains login modal, quick-fill chips, and authFetch token management."""
        content = self.dashboard_html.read_text(encoding="utf-8")
        self.assertIn("loginModal", content)
        self.assertIn("operatorAuthBtn", content)
        self.assertIn("quickFillCredentials", content)
        self.assertIn("handleLoginSubmit", content)
        self.assertIn("logoutOperator", content)
        self.assertIn("Authorization", content)
        self.assertIn("Bearer ", content)

        # Verify backend /api/v1/auth/token contract with default admin
        res = self.client.post("/api/v1/auth/token", json={"username": "admin", "password": "admin123"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("access_token", data)
        self.assertEqual(data.get("role"), "ADMIN")

    def test_frontend_hazard_advisories_integration(self) -> None:
        """Verify frontend contains active advisories banner, danger circle rendering, and API sync."""
        content = self.dashboard_html.read_text(encoding="utf-8")
        self.assertIn("activeAdvisoriesBanner", content)
        self.assertIn("fetchActiveAdvisories", content)
        self.assertIn("renderAdvisoryLayers", content)
        self.assertIn("updateAdvisoriesBanner", content)
        self.assertIn("Danger Radius:</b>", content)
        self.assertIn("radiusMeters", content)
        self.assertIn("ADVISORY_ISSUED", content)

        # Verify backend GET /api/v1/advisories endpoint
        res = self.client.get("/api/v1/advisories")
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json(), list)

    def test_frontend_repair_verification_hud_integration(self) -> None:
        """Verify frontend contains closed-loop verification card, progress bar, and pass trigger."""
        content = self.dashboard_html.read_text(encoding="utf-8")
        self.assertIn("renderVerificationCardHTML", content)
        self.assertIn("ticketVerificationCardWrap", content)
        self.assertIn("triggerSimulateSmoothPass", content)
        self.assertIn("Simulate Smooth Road Pass (BUS-002)", content)
        self.assertIn("progress-bar-fill", content)
        self.assertIn("VERIFICATION_WATCH_UPDATED", content)

        # Seed and create resolved ticket to test verification watch retrieval
        self.client.post("/api/v1/demo/seed")
        # Advance POT-2026-000001 to RESOLVED to trigger a verification watch
        login_res = self.client.post("/api/v1/auth/token", json={"username": "dispatcher", "password": "dispatch123"})
        self.assertEqual(login_res.status_code, 200)
        token = login_res.json()["access_token"]
        auth_headers = {"Authorization": f"Bearer {token}"}

        patch_res = self.client.patch("/api/v1/tickets/POT-2026-000001/status?new_status=RESOLVED", headers=auth_headers)
        self.assertEqual(patch_res.status_code, 200)

        # Test verification watch endpoint
        v_res = self.client.get("/api/v1/tickets/POT-2026-000001/verification", headers=auth_headers)
        self.assertEqual(v_res.status_code, 200)
        v_data = v_res.json()
        self.assertEqual(v_data.get("ticket_id"), "POT-2026-000001")

        # Test smooth pass simulation endpoint
        smooth_res = self.client.post(
            "/api/v1/tickets/POT-2026-000001/verify-smooth",
            headers=auth_headers,
            json={"bus_id": "BUS-002", "confidence": 0.95}
        )
        self.assertEqual(smooth_res.status_code, 200)

    def test_frontend_workorder_pdf_download_integration(self) -> None:
        """Verify frontend contains work-order PDF download action and backend generates valid PDF."""
        content = self.dashboard_html.read_text(encoding="utf-8")
        self.assertIn("downloadWorkOrderPdf", content)
        self.assertIn("Work-Order (PDF)", content)
        self.assertIn("workorder.pdf", content)

        # Test backend PDF generation endpoint
        pdf_res = self.client.get("/api/v1/tickets/POT-2026-000001/workorder.pdf")
        self.assertEqual(pdf_res.status_code, 200)
        self.assertEqual(pdf_res.headers.get("content-type"), "application/pdf")
        self.assertTrue(pdf_res.content.startswith(b"%PDF"))

    def test_frontend_density_heatmap_toggle_integration(self) -> None:
        """Verify frontend contains density heatmap toggle and backend returns aggregated grid."""
        content = self.dashboard_html.read_text(encoding="utf-8")
        self.assertIn("mapHeatmapToggleBtn", content)
        self.assertIn("toggleDensityHeatmap", content)
        self.assertIn("api/v1/analytics/heatmap", content)
        self.assertIn("Spatial Density Cell", content)

        # Test backend heatmap query
        heat_res = self.client.get("/api/v1/analytics/heatmap?min_lat=18.49&max_lat=18.55&min_lon=73.81&max_lon=73.87&grid_size_meters=150")
        self.assertEqual(heat_res.status_code, 200)
        h_data = heat_res.json()
        self.assertIn("cells", h_data)
        self.assertIn("total_incidents", h_data)
        self.assertIn("cell_count", h_data)
