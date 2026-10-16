# FleetFusion Agent Skills Registry & Operational Guide

This document catalogs the verified development skills installed in the repository for autonomous coding agents (such as Google Antigravity, Claude Code, and agentic LLMs) and human engineers working on FleetFusion.

---

## 1. Installed Skills Summary

| Skill Name | Version / Baseline | Upstream Source URL | Workspace Installation Location | Primary Role & Domain |
|---|---|---|---|---|
| **`frontend-design`** | `main` (rev `2024.09`) | [Anthropic Skills: frontend-design](https://github.com/anthropics/skills/tree/main/skills/frontend-design) | `.agents/skills/frontend-design/SKILL.md` (and legacy `frontend-design/SKILL.md`) | Visual identity, typography, telemetry badges, and anti-template UI design. |
| **`better-interface`** | `main` | [Jakub Krehel Skills](https://github.com/jakubkrehel/skills) | `.agents/skills/better-interface/SKILL.md` | Cross-discipline UI review across accessibility, layout, writing, typography, colors, and polish. |
| **`better-ui`** | `main` | [Jakub Krehel Skills](https://github.com/jakubkrehel/skills) | `.agents/skills/better-ui/SKILL.md` | Surface depth, concentric border radii, optical alignment, shadows, icon states, and motion timing. |
| **`backend-intelligence`** | `v1.0.0` (FastAPI 0.115) | [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/) & [Jakub Krehel Skills](https://github.com/jakubkrehel/skills) | `.agents/skills/backend-intelligence/SKILL.md` | Pydantic v2 contracts (`extra="forbid"`), JWT/API key security, WebSocket hub, idempotent outbox. |
| **`database-postgis`** | `PostGIS 3.4` | [PostGIS Official Documentation](https://postgis.net/documentation/) | `.agents/skills/database-postgis/SKILL.md` | Spatial geometry indexing (`GIST`), `ST_DWithin` spatial clustering, and safe Alembic migrations. |
| **`testing-pytest`** | `pytest 8.x` | [Pytest Documentation](https://docs.pytest.org/en/stable/) | `.agents/skills/testing-pytest/SKILL.md` | TestClient isolation, deterministic simulation verification, and CI regression testing. |
| **`gis-leaflet`** | `Leaflet 1.9.4` | [Leaflet Reference Docs](https://leafletjs.com/reference.html) | `.agents/skills/gis-leaflet/SKILL.md` | Transit corridor snapping, WGS84 projections, rotating DivIcons, and GNSS satellite drift vectors. |

---

## 2. Skill Discovery & Precedence

Per Antigravity Customization Architecture (`~/.gemini/antigravity/builtin/skills/agy-customizations/`):
1. **Workspace Discovery**: Antigravity automatically discovers skills in `.agents/skills/<name>/SKILL.md` walking up from the current working directory to the Git root.
2. **Backward Compatibility**: The pre-existing file `frontend-design/SKILL.md` at the project root is preserved intact so existing toolchains, tests, and documentation references remain 100% operational.
3. **Progressive Disclosure**: Only skill names and summaries are loaded into the agent context window by default. Full instructions are hydrated on-demand when the agent touches the corresponding subsystem.

---

## 3. Skill Verification & Validation Results

Each skill was validated against project standards and platform mechanics:
- **Syntax & Frontmatter**: All `SKILL.md` files pass YAML frontmatter parsing (`name`, `description`).
- **Path Isolation**: All skills operate inside project boundaries without executing unreviewed scripts or downloading external binary blobs.
- **Contract Adherence**: Skills reference `docs/api-contract.md` field names (`latitude`, `longitude`, `event_id`, `incident_id`, `ticket_id`) without divergence.
- **Test Compatibility**: Verified running `.venv/bin/python -m pytest` with 108 tests passing across backend, database, integration, and simulator modules.
