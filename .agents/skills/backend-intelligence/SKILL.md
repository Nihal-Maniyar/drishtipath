---
name: backend-intelligence
description: Production-grade FastAPI backend patterns, strict Pydantic v2 contract enforcement, API security (JWT & API Keys), idempotent ingestion, and real-time WebSocket streaming.
sources:
  - https://fastapi.tiangolo.com/tutorial/security/
  - https://github.com/jakubkrehel/skills
  - docs/api-contract.md
---

# Backend Intelligence & API Engineering

Guidelines and operational rules for developing, modifying, and maintaining backend APIs, ingestion pipelines, and event streaming within FleetFusion.

## 1. Zero-Trust Contract Enforcement
- Always enforce Pydantic v2 models with `model_config = ConfigDict(extra="forbid")`.
- Canonical identifiers MUST strictly follow the regex patterns established in `docs/api-contract.md`:
  - Bus: `^BUS-\d{3,}$` (e.g., `BUS-001`)
  - Event: `^EVT-\d{6}$` (e.g., `EVT-000001`)
  - Observation: `^OBS-\d{6}$` (e.g., `OBS-000001`)
  - Incident: `^INC-\d{6}$` (e.g., `INC-000001`)
  - Ticket: `^[A-Z]+-\d{4}-\d{6}$` (e.g., `POT-2026-000001`)
  - Workorder: `^WO-\d{4}-\d{6}$` (e.g., `WO-2026-000001`)
- Timestamps must always be timezone-aware UTC ISO 8601 strings (`datetime.now(timezone.utc)`).

## 2. Idempotent Ingestion & Outbox Replay
- When processing `POST /events`, if `event_id` already exists:
  - If the payload is identical, return HTTP `201 Created` with `"duplicate": true` and the existing record.
  - If the payload conflicts with existing immutable fields, return HTTP `409 Conflict`.
- Never generate new random IDs during replay of offline SQLite WAL events.

## 3. Real-Time WebSocket Streaming
- Mount WebSocket streams at both `/ws` and `/api/v1/ws`.
- Broadcast state changes immediately following successful database/memory mutations for:
  - `EVENT_INGESTED`
  - `INCIDENT_CREATED` / `INCIDENT_UPDATED`
  - `TICKET_CREATED` / `TICKET_STATUS_UPDATED`
  - `FLEET_UPDATED`
- WebSocket connections must support client keep-alive pings (`"ping"` -> `"pong"`).

## 4. API Security & Role-Based Access Control (RBAC)
- Differentiate human dashboard operators from automated edge sensing machines:
  - **Human Operators**: Authenticated via JWT tokens (`HS256`/`RS256`) stored in secure, HTTP-only cookies or `Authorization: Bearer <token>`.
  - **Edge Bus Simulators**: Authenticated via API key header (`X-Bus-API-Key: bus_sec_...`).
- Guard ticket status transitions (`PATCH /api/v1/tickets/{id}/status`) to require authorized roles (`DISPATCHER` or `ADMIN`).
