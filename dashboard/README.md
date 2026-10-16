# Fleet Sensing Urban Intel — Real-Time GIS Operations Dashboard

Interactive GIS and civic operations dashboard for the Fleet Sensing Urban Intelligence platform in Pune, Maharashtra.

Owned by **Member 5 (GIS Dashboard)** per `docs/team-tasks/member-5-gis-dashboard.md`, `frontend-design/SKILL.md`, and `AGENTS.md`.

---

## Key Capabilities

1. **Strict Road Alignment & Map Matching**:
   - **Guaranteed Road-Centered Locations**: Sensed defect pins and bus locations are anchored strictly over the Pune road corridor polylines (`EPSG:4326 / WGS84`).
   - **Algorithmic Snapping (`snapToRoad`)**: Computes the nearest road corridor segment and snaps coordinates precisely to the road centerline.
   - **GNSS Satellite Drift Vectors**: When raw satellite GNSS coordinates differ from the snapped road location by $> 4.5\text{m}$, a red dashed vector line (`#E11D48`, `dashArray: '4, 4'`) connects the raw satellite fix to the road marker with live distance tooltip, visually proving edge-to-road alignment and map matching.
   - **Dynamic Pune Transit Corridors**: Loads RFC 7946 GeoJSON from `/api/v1/routes/geojson` for FC Road (`ROUTE-PUNE-FC`), JM Road (`ROUTE-PUNE-JM`), Karve Road (`ROUTE-PUNE-KARVE`), and Shivaji Central (`ROUTE-PUNE-SHIVAJI`) with fallback to embedded coordinates.

2. **Real-Time Bi-Directional Streaming (WebSockets + Polling Fallback)**:
   - **WebSocket Endpoint (`/ws` and `/api/v1/ws`)**: Connected clients receive instant push notifications (<50ms latency) on:
     - `EVENT_INGESTED`: New event from Actual Bus Simulator or Demo Simulator.
     - `INCIDENT_CREATED` / `INCIDENT_UPDATED`: Spatial-temporal fleet fusion updates.
     - `TICKET_CREATED` / `TICKET_STATUS_UPDATED`: Civic lifecycle updates with live drawer re-render.
     - `FLEET_UPDATED`: Real-time bus coordinates, heading, and telemetry.
     - `STATS_UPDATED`: KPI counts.
   - **Resilient Polling**: 5-second background polling fallback ensures 100% data consistency even across restrictive network proxies without resetting open map markers or user state.

3. **In-Dashboard Interactive Simulation Triggers**:
   - 1-click execution of key vertical slice demonstration scenarios directly from the dashboard:
     - `▶ 2-Bus Corroboration (FC Road)`: Simulates BUS-001 and BUS-002 corroborating a pothole at Goodluck Chowk, fusing into `INC-000001`, and generating `POT-2026-000001`.
     - `▶ Offline Burst Replay (JM Road)`: Simulates offline event buffering in SQLite/WAL and idempotent replay upon reconnection.
     - `▶ Repair Verification`: Simulates smooth asphalt detection and advances the civic ticket to `RESOLVED` with Before/After audit HUD.
     - `⚡ Reset / Seed`: Re-seeds the baseline authentic Pune dataset.

4. **Security, Sanitization & Role-Based Access Control (RBAC)**:
   - **Strict Entity Sanitization**: Custom `escapeHtml()` utility wraps all dynamic string interpolations to protect against XSS injections.
   - **Operator Role Switcher**: Topbar role toggle between `DISPATCHER` (read/write ticket management) and `VIEWER` (read-only audit mode).
   - **Pydantic v2 Contract Adherence**: `raiseTicketForIncident()` dispatches compliant `created_at` and `updated_at` ISO 8601 timestamps, preventing 422 Unprocessable Entity errors.
   - **Toast Notifications**: Interactive alert feedback for high-severity defect detections, multi-bus fleet corroborations, and ticket stage advances.

5. **Civic Ticket Lifecycle Stepper & Dual-Pane Resolution HUD**:
   - Complete 4-stage lifecycle: `REPORTED` → `ACKNOWLEDGED` → `IN_PROGRESS` → `RESOLVED`.
   - Composable Kanban filters: Search query, Defect Type, Priority, and SLA urgency.
   - Before/After dual-pane image viewer for resolved defect audit and verification.
   - Direct Google Maps navigation link (`https://www.google.com/maps/dir/?api=1&destination=lat,lng`).

---

## How to Run

### Option A: Complete Prototype Launcher (Recommended)
Run the all-in-one launcher script from the repository root:
```bash
./.venv/bin/python scripts/run_prototype.py
```
This boots FastAPI, seeds Pune corridor data, and opens `http://localhost:8000/dashboard` in your browser.

### Option B: Run Backend Directly
```bash
./.venv/bin/uvicorn backend.app.main:app --reload --port 8000
```
Navigate to:
```text
http://localhost:8000/dashboard
```

---

## How to Test

Run the automated dashboard integration and hardening test suites:
```bash
./.venv/bin/pytest tests/test_dashboard_integration.py tests/test_dashboard_hardening.py -v
```

All 23 combined tests verify:
- HTML file structure and Leaflet GIS integration.
- FastAPI delivery of `GET /dashboard`.
- Contract adherence of GeoJSON layers (`/incidents/geojson`, `/routes/geojson`).
- Fleet telemetry and platform stats API endpoints.
- Full ticket status transition lifecycle synchronization.
- Real-time WebSocket connection and message streaming.
- Simulator interactive trigger execution.
- Static evidence photo serving.
- Authentic Pune road corridor snapping and coordinate validation.
- XSS escaping and DOM interpolation safety.
- Pydantic v2 ticket payload compliance (`created_at`, `updated_at`).
- GNSS satellite drift vector threshold rendering (>4.5m).
- Role-based UI permission gating (Dispatcher vs Viewer).
- Before/After resolution verification dual HUD.
