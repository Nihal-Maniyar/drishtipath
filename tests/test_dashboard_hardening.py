"""Unit and regression tests for Dashboard Hardening & GIS Operations.

Tests security escaping, Pydantic v2 ticket payload compliance, role-based controls,
GNSS drift vectoring, simulation triggers, and resolution audit HUD.
"""

from datetime import datetime, timezone
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from backend.app.main import app


class TestDashboardHardening(unittest.TestCase):
    """Regression test suite for frontend dashboard hardening."""

    def setUp(self) -> None:
        self.client = TestClient(app)
        self.repo_root = Path(__file__).resolve().parent.parent
        self.dashboard_html_path = self.repo_root / "dashboard" / "index.html"
        self.assertTrue(self.dashboard_html_path.is_file(), "dashboard/index.html must exist")
        self.html = self.dashboard_html_path.read_text(encoding="utf-8")

    def test_xss_sanitization_utility_present(self) -> None:
        """Verify strict HTML entity sanitizer is present and used across templates."""
        self.assertIn("function escapeHtml(str)", self.html)
        self.assertIn("&amp;", self.html)
        self.assertIn("&lt;", self.html)
        self.assertIn("&gt;", self.html)
        self.assertIn("&quot;", self.html)
        self.assertIn("&#39;", self.html)
        # Verify usage in interpolation
        self.assertIn("escapeHtml(i.incident_id)", self.html)
        self.assertIn("escapeHtml(b.bus_id)", self.html)
        self.assertIn("escapeHtml(t.ticket_id)", self.html)

    def test_ticket_payload_includes_pydantic_required_timestamps(self) -> None:
        """Verify raiseTicketForIncident includes created_at and updated_at ISO strings."""
        self.assertIn("created_at: now", self.html)
        self.assertIn("updated_at: now", self.html)

        # Test backend creation with frontend-equivalent payload to ensure no 422
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload = {
            "ticket_id": "POT-2026-999001",
            "incident_id": "INC-000001",
            "workorder_id": "WO-2026-999001",
            "event_type": "POTHOLE",
            "confidence": 0.94,
            "latitude": 18.5204,
            "longitude": 73.8436,
            "evidence_image": "runtime/evidence/EVT-000001.jpg",
            "google_maps_url": "https://www.google.com/maps?q=18.5204,73.8436",
            "estimated_repair_sla_hours": 48,
            "status": "REPORTED",
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        res = self.client.post("/api/v1/tickets", json=payload)
        self.assertEqual(res.status_code, 201, f"POST /api/v1/tickets must succeed with 201: {res.text}")
        ticket = res.json().get("ticket", {})
        self.assertEqual(ticket.get("ticket_id"), "POT-2026-999001")
        self.assertEqual(ticket.get("status"), "REPORTED")

    def test_gnss_satellite_drift_vector_rendering(self) -> None:
        """Verify GNSS satellite drift calculation and threshold vector rendering."""
        self.assertIn("function haversineMeters", self.html)
        self.assertIn("driftM > 4.5", self.html)
        self.assertIn("GNSS Drift:", self.html)
        self.assertIn("dashArray: '4, 4'", self.html)
        self.assertIn("state.driftLayers", self.html)

    def test_simulation_controls_toolbar(self) -> None:
        """Verify interactive simulation toolbar and scenarios."""
        self.assertIn("sim-toolbar", self.html)
        self.assertIn("triggerSimulatorScenario", self.html)
        self.assertIn("dual_bus", self.html)
        self.assertIn("offline_replay", self.html)
        self.assertIn("resolution", self.html)
        self.assertIn("seed", self.html)

        # Test backend trigger endpoint for each scenario
        for sc in ["dual_bus", "offline_replay", "resolution", "seed"]:
            res = self.client.post("/api/v1/simulators/trigger", json={"scenario": sc})
            self.assertEqual(res.status_code, 200, f"Trigger scenario '{sc}' failed: {res.text}")

    def test_dynamic_geojson_route_loader(self) -> None:
        """Verify dynamic corridor loading from GET /api/v1/routes/geojson."""
        self.assertIn("loadCorridorsFromGeoJSON", self.html)
        self.assertIn("renderCorridorPolylines", self.html)
        self.assertIn("/api/v1/routes/geojson", self.html)
        self.assertIn("PUNE_CORRIDORS_GEO", self.html)

        # Verify backend endpoint contract
        res = self.client.get("/api/v1/routes/geojson")
        self.assertEqual(res.status_code, 200)
        fc = res.json()
        self.assertEqual(fc.get("type"), "FeatureCollection")
        self.assertGreaterEqual(len(fc.get("features", [])), 4)

    def test_corridor_focus_controls(self) -> None:
        """Verify interactive corridor focus buttons and bounds fitting."""
        self.assertIn("focusCorridor", self.html)
        self.assertIn("ROUTE-PUNE-FC", self.html)
        self.assertIn("ROUTE-PUNE-JM", self.html)
        self.assertIn("ROUTE-PUNE-KARVE", self.html)
        self.assertIn("ROUTE-PUNE-SHIVAJI", self.html)
        self.assertIn("focusCorridor('FC')", self.html)
        self.assertIn("focusCorridor('ALL')", self.html)

    def test_role_based_security_ui(self) -> None:
        """Verify dispatcher vs viewer role switching and permission gates."""
        self.assertIn("userRoleSelect", self.html)
        self.assertIn("setUserRole", self.html)
        self.assertIn("DISPATCHER", self.html)
        self.assertIn("VIEWER", self.html)
        self.assertIn("canMutate", self.html)
        self.assertIn("authFetch", self.html)

    def test_toast_notification_system(self) -> None:
        """Verify toast notification container, styles, and trigger functions."""
        self.assertIn("toastContainer", self.html)
        self.assertIn("showToast", self.html)
        self.assertIn(".toast-container", self.html)
        self.assertIn(".toast", self.html)

    def test_dual_view_resolution_verification_hud(self) -> None:
        """Verify before/after dual-pane resolution verification audit HUD."""
        self.assertIn("Resolution Audit & Verification", self.html)
        self.assertIn("BEFORE (DEFECT DETECTED)", self.html)
        self.assertIn("AFTER (REPAIR VERIFIED)", self.html)
        self.assertIn("VERIFIED SMOOTH ASPHALT", self.html)

    def test_pwa_web_app_manifest_and_meta(self) -> None:
        """Verify data-URI PWA manifest, service worker registration, and mobile metas."""
        self.assertIn('rel="manifest"', self.html)
        self.assertIn("apple-mobile-web-app-capable", self.html)
        self.assertIn("navigator.serviceWorker.register", self.html)

    def test_kanban_composable_filters(self) -> None:
        """Verify composable ticket filters (search, event type, priority, SLA urgency)."""
        self.assertIn("tktFilterSearch", self.html)
        self.assertIn("tktFilterType", self.html)
        self.assertIn("tktFilterSev", self.html)
        self.assertIn("tktFilterSLA", self.html)
        self.assertIn("updateTicketFilter", self.html)
        self.assertIn("resetTicketFilters", self.html)
        self.assertIn("getFilteredTickets", self.html)
        self.assertIn("renderKanbanColumnsHTML", self.html)
        self.assertIn("URGENT", self.html)
        self.assertIn("estimated_repair_sla_hours || 48) <= 24", self.html)

    def test_operator_rbac_mutation_guards(self) -> None:
        """Verify explicit client-side RBAC guards across all state mutation entrypoints."""
        # 1. raiseTicketForIncident guard
        self.assertIn("Dispatching civic tickets requires Dispatcher or Admin role", self.html)
        # 2. advanceTicketStatus guard
        self.assertIn("Advancing ticket lifecycle requires Dispatcher or Admin role", self.html)
        # 3. moveTicketToStatus guard
        self.assertIn("Modifying ticket status requires Dispatcher or Admin role", self.html)
        # 4. Incident drawer button role restriction
        self.assertIn("Raise Ticket (Dispatcher Only)", self.html)
        self.assertIn("Requires Dispatcher or Admin role", self.html)
        # 5. Ticket drawer role restriction
        self.assertIn("Read-Only (Viewer)", self.html)
        # 6. Role switch drawer reconciliation
        self.assertIn("openIncidentDrawer(state.selectedIncidentId)", self.html)
        # 7. Kanban drag-and-drop gating
        self.assertIn('draggable="${canMutate ? \'true\' : \'false\'}"', self.html)

    def test_contract_status_alignment(self) -> None:
        """Verify contract statuses match backend schemas."""
        self.assertIn("CANDIDATE", self.html)
        self.assertIn("VERIFIED", self.html)
        self.assertIn("RESOLUTION_CANDIDATE", self.html)
        self.assertIn("RESOLVED", self.html)
        self.assertIn("REJECTED", self.html)

    def test_inline_javascript_syntax_valid(self) -> None:
        """Verify inline dashboard JavaScript is syntactically valid via node or balance check."""
        import re
        import shutil
        import subprocess

        m = re.search(r"<script>(.*?)</script>", self.html, re.DOTALL)
        self.assertIsNotNone(m, "Inline script must be present in dashboard/index.html")
        script_code = m.group(1)

        self.assertEqual(script_code.count("{"), script_code.count("}"), "Braces must balance")
        self.assertEqual(script_code.count("("), script_code.count(")"), "Parentheses must balance")

        node_bin = shutil.which("node")
        if node_bin:
            import tempfile
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tmp:
                tmp.write(script_code)
                tmp_path = tmp.name
            try:
                proc = subprocess.run([node_bin, "--check", tmp_path], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, f"Node syntax check failed: {proc.stderr}")
            finally:
                Path(tmp_path).unlink(missing_ok=True)

