# Parallel Development Handoff & Environment Blueprint

This document specifies the verified operational baseline and development environment configuration for parallel development by two developers working on separate machines.

---

## 1. Verified Repository Topology & Remotes

- **Primary Upstream Remote (`origin`)**: `https://github.com/Nihal-Maniyar/drishtipath.git`
  - Remote default branch: `origin/main` (commit `bc4e317`)
  - Integration branch: `origin/develop` (commit `0034616`)
- **Secondary Remote (`fleet-sensing`)**: `https://github.com/Nihal-Maniyar/fleet-sensing-urban-intel.git`
- **Active Base Branch**: `develop`
  - Status: Up to date with `origin/develop` (`0034616`)
  - Local commit tip: `0034616 Merge pull request #18 from Nihal-Maniyar/feature/fleet-integration-fixes`

---

## 2. Developer Branch Assignments & Ownership Boundaries

Two isolated feature branches have been created from the verified `develop` baseline commit (`0034616`):

```
                                  develop (0034616)
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 ▼                                               ▼
   feature/dashboard-hardening                     feature/backend-intelligence
   (Developer 1 — Frontend & GIS)                  (Developer 2 — Backend & Fusion)
```

### Developer 1: Frontend & GIS Specialist
- **Dedicated Branch**: `feature/dashboard-hardening`
- **Owned Scope**:
  - `dashboard/` (Single-page GIS Command Center, `index.html`, and CSS tokens).
  - Leaflet GIS integration: road corridor polyline rendering (`EPSG:4326`), algorithmic snapping (`snapToRoadCorridor`), and satellite drift vector visualization.
  - Interactive simulator scenario triggers in UI (`▶ Dual-Bus`, `▶ Offline Replay`, `▶ Post-Repair`).
  - Evidence HUD display, Ticket Kanban drag-and-drop board, and client WebSocket reconnection logic.
- **Rules**: Must NOT modify `backend/app/`, `database/`, or `fusion/` without coordinating with Developer 2.

### Developer 2: Backend & Intelligence Specialist
- **Dedicated Branch**: `feature/backend-intelligence`
- **Owned Scope**:
  - `backend/app/` (FastAPI endpoints, routers, WebSocket broadcast hub, and Pydantic schemas).
  - `database/` (PostGIS spatial schemas, SQLAlchemy models, and Alembic migrations).
  - `fusion/` (Spatial-temporal corridor clustering and corroboration logic).
  - `bus-simulator/` & `data-demo-simulator/` (Edge AI pipelines, IMU coupling, NavIC GPS simulation, and SQLite WAL outboxes).
- **Rules**: Must NOT alter API field names or JSON contracts without first updating `docs/api-contract.md` and obtaining approval from Developer 1.

---

## 3. Safe Git Workflow & Step-by-Step Commands

### Starting Work on Your Assigned Track
```bash
# Developer 1:
git checkout feature/dashboard-hardening
git pull origin develop --ff-only

# Developer 2:
git checkout feature/backend-intelligence
git pull origin develop --ff-only
```

### Making Commits
Stage only your relevant subsystem files. Never stage secrets or generated assets:
```bash
# Verify status before staging
git status

# Stage specific files only
git add dashboard/index.html

# Conventional commit message
git commit -m "feat(gis): add satellite drift vector visualization to map matching"
```

### Preparing & Submitting a Pull Request
```bash
# 1. Fetch latest integration changes
git fetch origin develop

# 2. Rebase feature branch on fresh develop
git rebase origin/develop

# 3. Run regression tests (must pass 100%)
.venv/bin/python -m pytest

# 4. Push feature branch to remote
git push origin feature/dashboard-hardening   # or feature/backend-intelligence

# 5. Open a Pull Request targeting 'develop'
# NEVER target 'main' directly from a feature branch!
```

---

## 4. Integration Gates & Verification Checklist

Before any pull request can be merged into `develop`:
1. **Contract Integrity**: No modification to `docs/api-contract.md` field names without documented cross-team agreement.
2. **Automated Test Gate**: All 88 baseline unit and integration tests (plus 20 bus-simulator tests) must pass cleanly:
   ```bash
   .venv/bin/python -m pytest
   PYTHONPATH=bus-simulator .venv/bin/python -m pytest bus-simulator/tests/
   ```
3. **Hygiene Verification**: No `.env`, `runtime/evidence/*.jpg`, `*.db`, `*.zip`, or model weights are staged.
4. **Peer Review**: Each PR must receive an approving review from the counterpart developer track (Developer 1 reviews Developer 2, and vice versa).
5. **Release Promotion**: Only the repository owner promotes tested releases from `develop` into `main` via fast-forward or squash release PR.
