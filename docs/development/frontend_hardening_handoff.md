# Developer 1 to Developer 2 Handoff & Frontend Hardening Specification

**Branch**: `feature/dashboard-hardening`
**Tracking Remote**: `origin/feature/dashboard-hardening`
**Base Commit**: `0034616` (`origin/develop`)
**Target Integration Branch**: `develop`
**Author**: Developer 1 (Frontend, Dashboard & GIS)
**Counterpart**: Developer 2 (Backend, PostGIS & Intelligence)

---

## 1. Overview of Changes

Developer 1 has hardened the Pune GIS Command Center dashboard (`dashboard/index.html`), verified all Leaflet GIS operations, aligned contract schemas, added role-based security gating, and introduced an automated regression test suite (`tests/test_dashboard_hardening.py`).

### Files Modified & Created
| File | Action | Scope & Purpose |
|------|--------|-----------------|
| `dashboard/index.html` | Modified | Core single-page GIS dashboard: strict XSS entity sanitization, dynamic GeoJSON corridor loading, satellite drift vectors (>4.5m), interactive simulator controls, role-based UI gating (`DISPATCHER` vs `VIEWER`), Before/After resolution HUD, composable Kanban filters, and toast notifications. |
| `dashboard/README.md` | Modified | Documented GIS map-matching capabilities, simulation triggers, RBAC operator modes, and testing instructions. |
| `tests/test_dashboard_hardening.py` | Created | 14 automated unit and regression tests validating HTML escaping, Pydantic v2 timestamp compliance, drift vector calculation, simulator trigger endpoints, RBAC permission guards, and inline JS syntax. |
| `docs/development/frontend_hardening_handoff.md` | Created | Technical handoff, contract assumptions, and integration checklist for Developer 2. |

---

## 2. API Contract Alignments & Assumptions

All frontend changes adhere strictly to `docs/api-contract.md`. No breaking changes were introduced.

### Endpoints Consumed by Dashboard
1. **`GET /api/v1/routes/geojson`**:
   - RFC 7946 FeatureCollection of Pune road corridors (`ROUTE-PUNE-FC`, `ROUTE-PUNE-JM`, `ROUTE-PUNE-KARVE`, `ROUTE-PUNE-SHIVAJI`).
   - Dynamic route loading with transparent fallback to static coordinates if offline.
2. **`GET /api/v1/incidents/geojson`**:
   - Live defect clusters. Dashboard supports canonical statuses: `CANDIDATE`, `VERIFIED`, `RESOLUTION_CANDIDATE`, `RESOLVED`, `REJECTED`.
3. **`GET /api/v1/tickets` & `POST /api/v1/tickets`**:
   - Ingestion payload strictly includes required ISO 8601 UTC timestamps: `created_at` and `updated_at`.
   - Generates compliant IDs: `POT-YYYY-XXXXXX` and `WO-YYYY-XXXXXX`.
4. **`PATCH /api/v1/tickets/{ticket_id}/status?new_status={status}`**:
   - Supports 4-stage lifecycle: `REPORTED` → `ACKNOWLEDGED` → `IN_PROGRESS` → `RESOLVED`.
5. **`POST /api/v1/simulators/trigger`**:
   - Request body: `{"scenario": "<scenario_name>"}`.
   - Handled scenarios: `dual_bus`, `offline_replay`, `resolution`, `seed`.
6. **`WebSocket /ws` & `/api/v1/ws`**:
   - Handled message types: `EVENT_INGESTED`, `INCIDENT_CREATED`, `INCIDENT_UPDATED`, `TICKET_CREATED`, `TICKET_STATUS_UPDATED`, `FLEET_UPDATED`, `STATS_UPDATED`.
   - On `TICKET_STATUS_UPDATED`, open ticket drawers re-render in place without closing or resetting scroll position.

---

## 3. Security & Operator RBAC Implementation

- **Strict Entity Sanitization**: Custom `escapeHtml(str)` encodes `&, <, >, ", '` across all DOM interpolations (ticket IDs, bus names, incident descriptions, coordinates).
- **Client-Side Role Gating**:
  - `DISPATCHER` / `ADMIN`: Unrestricted card dragging, status advancement, ticket creation, and simulator triggers.
  - `VIEWER`: Read-only GIS map; drag-and-drop disabled (`draggable="false"`), action buttons disabled with informative tooltips (`title="Requires Dispatcher or Admin role"`), and mutation functions blocked with toast alerts (`"Permission Denied"`).
- **`authFetch` Interceptor**: Intercepts HTTP 401/403 responses and fires immediate feedback toasts for restricted actions.

---

## 4. Test Coverage & Verification Results

### Automated Test Suites
Run all tests via:
```bash
./.venv/bin/python -m pytest tests/test_dashboard_integration.py tests/test_dashboard_hardening.py -v
```

| Test File | Tests | Status | Scope Tested |
|-----------|-------|--------|--------------|
| `tests/test_dashboard_hardening.py` | 14 | **PASSED** | XSS sanitization, Pydantic v2 timestamp compliance, GNSS satellite drift threshold rendering (>4.5m), simulator toolbar triggers, GeoJSON route loader, corridor focus bounds, RBAC operator mutation guards, toast notifications, dual-pane resolution HUD, PWA manifest, contract status alignment, and inline JavaScript syntax (`node --check`). |
| `tests/test_dashboard_integration.py` | 13 | **PASSED** | Static HTML delivery, Leaflet GIS integration, GeoJSON endpoints, ticket lifecycle sync, WebSocket connection, and evidence image serving. |
| **Combined Regression Suite** | **102** | **PASSED** | Full repository test suite (root, backend, demo simulator, and bus simulator). Zero regressions. |

---

## 5. Verification Checklist for Developer 2 Review

Before merging `feature/dashboard-hardening` into `develop`:
1. [x] Working tree clean; no uncommitted changes.
2. [x] Zero `.env`, credentials, runtime SQLite databases, or evidence files committed.
3. [x] All 27 dashboard tests and all 102 platform regression tests pass cleanly.
4. [x] Remote branch is pushed and up to date (`git push origin feature/dashboard-hardening`).
5. [x] `main` and `develop` branches remain completely untouched.
