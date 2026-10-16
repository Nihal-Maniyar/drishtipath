# API and Event Contract (v1)

This document locks shared names for the first prototype. Do not substitute local variants such as `lat`, `gps_lat`, or `latitude_value`.

## Naming rules

- IDs are strings with one shared display format: `BUS-001`, `EVT-000001`, `OBS-000001`, `INC-000001`, `POT-YYYY-XXXXXX`, and `WO-YYYY-XXXXXX`.
- The corresponding field names are `bus_id`, `event_id`, `observation_id`, `incident_id`, `ticket_id`, and `workorder_id`.
- Timestamps use ISO 8601 UTC, for example `2026-09-20T10:30:00Z`.
- Coordinates use WGS84 decimal degrees: `latitude`, `longitude`.
- `confidence` is a number from `0.0` through `1.0`; severity is a separate derived field.
- `evidence_image` is a storage URI/path, not embedded image bytes.
- Initial candidate `event_type` values are `POTHOLE`, `GARBAGE`, `TRAFFIC_OBSTRUCTION`, and `PEDESTRIAN_RISK`. Add new values through a contract change.
- `source` identifies `actual_bus_simulator`, `data_demo_simulator`, or a future real edge device.
- Original GNSS coordinates and road-aligned coordinates are separate facts. The latter may be produced by map matching.

## Event — edge/simulator output

```json
{
  "event_id": "EVT-000001",
  "bus_id": "BUS-001",
  "event_type": "POTHOLE",
  "timestamp": "2026-09-20T10:30:00Z",
  "latitude": 18.5204,
  "longitude": 73.8567,
  "road_aligned_latitude": 18.5203,
  "road_aligned_longitude": 73.8568,
  "heading_degrees": 92.4,
  "route_id": "ROUTE-A",
  "confidence": 0.91,
  "severity": "HIGH",
  "evidence_image": "runtime/evidence/EVT-000001.jpg",
  "source": "data_demo_simulator",
  "connectivity_state": "ONLINE"
}
```

Required v1 fields: `event_id`, `bus_id`, `event_type`, `timestamp`, `latitude`, `longitude`, `confidence`, `evidence_image`, and `source`. `severity`, route, heading, road-aligned coordinates, and connectivity metadata are optional at ingestion but must retain their meaning when present. `ONLINE`/`OFFLINE` describes the edge state at event creation, not whether the server received it immediately.

## Observation — stored normalized event

An observation is the persisted representation of one received event. It retains the source event identity and must not overwrite evidence or coordinates.

```json
{
  "observation_id": "OBS-000001",
  "event_id": "EVT-000001",
  "bus_id": "BUS-001",
  "event_type": "POTHOLE",
  "timestamp": "2026-09-20T10:30:00Z",
  "latitude": 18.5204,
  "longitude": 73.8567,
  "confidence": 0.91,
  "severity": "HIGH",
  "evidence_image": "runtime/evidence/EVT-000001.jpg",
  "road_aligned_latitude": 18.5203,
  "road_aligned_longitude": 73.8568
}
```

## Incident — fleet-fusion output

```json
{
  "incident_id": "INC-000001",
  "event_type": "POTHOLE",
  "status": "CANDIDATE",
  "latitude": 18.5203,
  "longitude": 73.8568,
  "observation_count": 2,
  "bus_count": 2,
  "confidence": 0.93,
  "severity": "HIGH",
  "department": "MUNICIPAL_CORPORATION",
  "first_observed_at": "2026-09-20T10:30:00Z",
  "last_observed_at": "2026-09-20T10:35:00Z"
}
```

Allowed initial incident statuses: `CANDIDATE`, `VERIFIED`, `REJECTED`, `RESOLUTION_CANDIDATE`, `RESOLVED`.

## Ticket — civic lifecycle record

```json
{
  "ticket_id": "POT-2026-000001",
  "incident_id": "INC-000001",
  "workorder_id": "WO-2026-000001",
  "event_type": "POTHOLE",
  "confidence": 0.93,
  "latitude": 18.5203,
  "longitude": 73.8568,
  "evidence_image": "runtime/evidence/EVT-000001.jpg",
  "google_maps_url": "https://www.google.com/maps/dir/?api=1&destination=18.5203,73.8568",
  "estimated_repair_sla_hours": 48,
  "status": "REPORTED",
  "created_at": "2026-09-20T10:36:00Z",
  "updated_at": "2026-09-20T10:36:00Z"
}
```

The official ticket statuses are `REPORTED`, `ACKNOWLEDGED`, `IN_PROGRESS`, and `RESOLVED`; do not create alternate lifecycle status names. Format: `POT-YYYY-XXXXXX`, with a six-digit sequence. Department routing is derived from event type and remains reviewable by an authority user. `workorder_id` is assigned after acknowledgement when a prototype work-order handoff is created.

## Severity

Severity is derived from event type, confidence, persistence, location/context, and corroborating observations. The prototype may use `LOW`, `MEDIUM`, and `HIGH`; the scoring formula is a future implementation contract and must not be inferred from `confidence` alone.

Publish v1 events to `beyonders/events/v1`. Retain no secrets in MQTT payloads. An offline edge event is stored in SQLite/WAL and later published with the same `event_id`; ingestion must be idempotent.

## Public HTTP API Endpoints

The backend exposes these v1 endpoints (available at root and `/api/v1` prefix):

| Method | Path | Request Body | Success Code | Description |
|---|---|---|---|---|
| `GET` | `/health` / `/` | None | `200 OK` | Service health and contract version |
| `POST` | `/events` | `Event` JSON | `201 Created` | Idempotent event ingestion; persists event and creates normalized `Observation` |
| `GET` | `/events` | None | `200 OK` | List all ingested events |
| `GET` | `/events/{event_id}` | None | `200 OK` | Retrieve single event (`404` if not found) |
| `GET` | `/observations` | None | `200 OK` | List all normalized observations |
| `GET` | `/observations/{observation_id}` | None | `200 OK` | Retrieve single observation (`404` if not found) |
| `POST` | `/incidents` | `Incident` JSON | `201 Created` | Register or update verified/candidate incident |
| `GET` | `/incidents` | None | `200 OK` | List all incidents |
| `GET` | `/incidents/{incident_id}` | None | `200 OK` | Retrieve incident by ID (`404` if not found) |
| `POST` | `/tickets` | `Ticket` JSON | `201 Created` | Create civic ticket (`POT-YYYY-XXXXXX`) |
| `GET` | `/tickets` | None | `200 OK` | List all tickets |
| `GET` | `/tickets/{ticket_id}` | None | `200 OK` | Retrieve ticket by ID (`404` if not found) |
| `PATCH` | `/tickets/{ticket_id}/status` | Query `new_status` | `200 OK` | Transition ticket lifecycle status (`400` if invalid) |
| `GET` | `/tickets/{ticket_id}/map` | None | `200 OK` | Get Google Maps navigation link and coordinates |
| `GET` | `/tickets/{ticket_id}/workorder.pdf` | None | `200 OK` | Download standardized PMC contractor work-order PDF with QR navigation |
| `GET` | `/advisories` | None | `200 OK` | List active downstream hazard advisories |
| `GET` | `/advisories/{advisory_id}` | None | `200 OK` | Retrieve single advisory (`404` if not found) |
| `GET` | `/advisories/bus/{bus_id}` | None | `200 OK` | Retrieve active hazard advisories applicable to specific bus |
| `POST` | `/advisories/evaluate` | `AdvisoryEvaluationRequest` | `201 Created` | Evaluate incident and issue downstream hazard advisory |
| `GET` | `/verification/watches` | None | `200 OK` | List all scheduled and historical repair verification watches |
| `GET` | `/tickets/{ticket_id}/verification` | None | `200 OK` | Retrieve verification watch state for a specific ticket |
| `POST` | `/verification/evaluate-expired` | None | `200 OK` | Evaluate and transition lapsed verification watches |
| `POST` | `/tickets/{ticket_id}/verify-smooth` | `SmoothObservationSimulateRequest` | `200 OK` | Simulate smooth road pass over repaired ticket |
| `GET` | `/analytics/heatmap` | Query bbox and filters | `200 OK` | Bounded spatial density aggregation for Leaflet GIS heatmap rendering |

## Hazard Advisory — downstream bus warning

When a severe defect (e.g. high-severity pothole) qualifies, an advisory is published via MQTT (`beyonders/advisories/v1` and `beyonders/advisories/v1/{route_id}`) to warn downstream fleet vehicles approaching within a 500-meter corridor window. Format: `ADV-YYYY-XXXXXX`.

```json
{
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
  "issued_at": "2026-09-22T08:35:00Z",
  "expires_at": "2026-09-22T09:35:00Z",
  "corridor_window_meters": 500.0,
  "target_buses": ["BUS-002"],
  "description": "Hazard Advisory: Severe pothole on corridor ROUTE-PUNE-FC."
}
```

## Repair Verification Watch — closed-loop ticket verification

When a civic ticket transitions to `RESOLVED`, an automated verification watch is scheduled (window: 48 hours, spatial threshold: 25 meters). Subsequent independent bus observations evaluate repair efficacy:
- **`VERIFIED_RESOLVED`**: Corroborated smooth surface observations (2 independent bus passes or 1 high-confidence pass >= 0.95).
- **`FAILED_REPAIR`**: Persistent defect signatures reopen the ticket back to `REPORTED` with complete audit tracking.
- **`EXPIRED_UNVERIFIED`**: Window lapses without bus passage, flagging the ticket for authority manual inspection.
Format: `VW-YYYY-XXXXXX`.

```json
{
  "watch_id": "VW-2026-000001",
  "ticket_id": "POT-2026-000001",
  "scheduled_at": "2026-09-22T08:35:00Z",
  "expires_at": "2026-09-24T08:35:00Z",
  "state": "WATCHING",
  "latitude": 18.5196,
  "longitude": 73.8436,
  "required_smooth_observations": 2,
  "smooth_observations_count": 1,
  "defect_observations_count": 0,
  "smooth_buses": ["BUS-002"],
  "defect_buses": []
}
```

## Spatial Density Heatmap — GIS aggregation

The spatial density aggregation endpoint `GET /api/v1/analytics/heatmap` aggregates defects within a bounded viewport into metric spatial grid cells. It supports aggregating both fused `incidents` (default) and raw `observations` via the `target` parameter (`target=incidents` or `target=observations`). This prevents returning excessive raw point coordinates to the GIS dashboard, while providing normalized intensity weights (from `0.0` through `1.0`) for Leaflet heatmap canvas rendering.

```json
{
  "cell_count": 3,
  "total_incidents": 4,
  "max_cell_count": 2,
  "grid_size_meters": 100.0,
  "bounding_box": {
    "min_latitude": 18.5000,
    "max_latitude": 18.5400,
    "min_longitude": 73.8200,
    "max_longitude": 73.8800
  },
  "aggregation_mode": "in_memory_spatial_binning",
  "cells": [
    {
      "cell_id": "CELL-18.5196-73.8436",
      "latitude": 18.5196,
      "longitude": 73.8436,
      "point_count": 2,
      "normalized_weight": 1.0,
      "dominant_event_type": "POTHOLE",
      "dominant_severity": "HIGH",
      "incident_ids": ["INC-000001", "INC-000003"]
    }
  ]
}
```

### Idempotency & Conflict Rules
- Re-submitting an existing `event_id`, `incident_id`, or `ticket_id` with an identical payload returns `201 Created` with `"duplicate": true`.
- Submitting an existing ID with a modified or conflicting payload returns `409 Conflict`.
- Malformed payloads or invalid contract values return `422 Unprocessable Entity`.
- Subscribers process MQTT QoS 1 duplicate deliveries idempotently using stable `advisory_id` keys.
- Older event observations (historical replay) do not overwrite newer ticket resolution state.


