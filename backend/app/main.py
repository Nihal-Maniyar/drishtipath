"""FastAPI backend application for Fleet Sensing Urban Intelligence."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("urban_intelligence.backend")

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

try:
    from backend.app.core.security import (
        UserAccount,
        get_current_user,
        require_role,
        verify_bus_auth,
    )
    from backend.app.routers.auth import router as auth_router
    from backend.app.routers.advisory import router as advisory_router
    from backend.app.routers.verification import router as verification_router
    from backend.app.services.advisory_service import advisory_service
    from backend.app.services.verification_service import verification_service
    from backend.app.services.pdf_service import pdf_service
except ImportError:
    from app.core.security import (
        UserAccount,
        get_current_user,
        require_role,
        verify_bus_auth,
    )
    from app.routers.auth import router as auth_router
    from app.routers.advisory import router as advisory_router
    from app.routers.verification import router as verification_router
    from app.services.advisory_service import advisory_service
    from app.services.verification_service import verification_service
    from app.services.pdf_service import pdf_service

# Handle imports whether launched from root or backend directory
try:
    from backend.app.schemas.contracts import (
        EVENT_TYPES,
        INCIDENT_STATUSES,
        SEVERITIES,
        SOURCES,
        TICKET_STATUSES,
        Event,
        Incident,
        Observation,
        Ticket,
    )
except ImportError:
    from app.schemas.contracts import (
        EVENT_TYPES,
        INCIDENT_STATUSES,
        SEVERITIES,
        SOURCES,
        TICKET_STATUSES,
        Event,
        Incident,
        Observation,
        Ticket,
    )

# Fleet Fusion Engine
try:
    from fusion.policy import FleetFusionEngine
except ImportError:
    from ...fusion.policy import FleetFusionEngine

# Database operations
try:
    from database.connection import db_session
    from database.operations import (
        insert_event_idempotent,
        create_observation as db_create_observation,
        create_or_update_incident as db_create_or_update_incident,
        create_ticket as db_create_ticket,
        transition_ticket_status as db_transition_ticket_status,
        link_observation_to_incident as db_link_obs_to_inc,
    )
except Exception:
    db_session = None

# MQTT background consumer
try:
    from backend.app.services.mqtt_consumer import MQTTEventConsumer
except ImportError:
    try:
        from app.services.mqtt_consumer import MQTTEventConsumer
    except ImportError:
        MQTTEventConsumer = None

fusion_engine = FleetFusionEngine()


def safe_db_persist(func, *args, **kwargs):
    """Safely persist to database if connection and session are active, without failing if offline."""
    if db_session is None:
        return None
    try:
        with db_session() as session:
            return func(session, *args, **kwargs)
    except Exception as e:
        logger.debug("Database operation skipped/fallback: %s", e)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI application lifespan: start background MQTT listener if configured."""
    main_loop = asyncio.get_running_loop()

    def on_mqtt_message(payload: Dict[str, Any]):
        try:
            event = Event(**payload)
            asyncio.run_coroutine_threadsafe(ingest_event(event), main_loop)
        except Exception as err:
            logger.warning("Error processing MQTT message: %s", err)

    mqtt_consumer = None
    if MQTTEventConsumer:
        try:
            mqtt_consumer = MQTTEventConsumer(on_event_received=on_mqtt_message)
            mqtt_consumer.start()
            if mqtt_consumer.client:
                advisory_service.set_mqtt_client(mqtt_consumer.client)
        except Exception as e:
            logger.debug("MQTT consumer startup bypassed: %s", e)

    yield

    if mqtt_consumer:
        try:
            mqtt_consumer.stop()
        except Exception:
            pass


app = FastAPI(
    title="Fleet Sensing Urban Intelligence - Backend",
    version="1.0.0",
    description="FastAPI service for contract-valid event ingestion, observations, incidents, civic ticket lifecycle, and GIS layers.",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Static Evidence Mounting
# ---------------------------------------------------------------------------
evidence_dir = Path(__file__).resolve().parent.parent.parent / "runtime" / "evidence"
evidence_dir.mkdir(parents=True, exist_ok=True)
app.mount("/runtime/evidence", StaticFiles(directory=str(evidence_dir)), name="evidence")



# ---------------------------------------------------------------------------
# Real-Time WebSocket Connection Manager
# ---------------------------------------------------------------------------
class ConnectionManager:
    """Manages active dashboard WebSocket subscriptions and broadcasts."""

    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                if connection in self.active_connections:
                    self.active_connections.remove(connection)


ws_manager = ConnectionManager()

# ---------------------------------------------------------------------------
# Prototype in-memory storage
# ---------------------------------------------------------------------------
events_by_id: dict[str, dict] = {}
observations_by_id: dict[str, dict] = {}
incidents_by_id: dict[str, dict] = {}
tickets_by_id: dict[str, dict] = {}
buses_by_id: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Authentic Pune Transit Corridors (GIS reference)
# ---------------------------------------------------------------------------
PUNE_CORRIDORS: List[Dict[str, Any]] = [
    {
        "route_id": "ROUTE-PUNE-FC",
        "name": "FC Road Corridor",
        "description": "Fergusson College Road from Deccan Gymkhana to Agriculture College",
        "color": "#1C6E8C",
        "waypoints": [
            {"name": "Deccan Gymkhana Bus Stop", "lat": 18.5158, "lng": 73.8418},
            {"name": "Goodluck Chowk (Pothole Demo Hotspot)", "lat": 18.5196, "lng": 73.8436},
            {"name": "Fergusson College Main Gate", "lat": 18.5235, "lng": 73.8415},
            {"name": "Dnyaneshwar Paduka Chowk", "lat": 18.5278, "lng": 73.8424},
            {"name": "Agriculture College / Shivajinagar", "lat": 18.5320, "lng": 73.8450},
        ],
    },
    {
        "route_id": "ROUTE-PUNE-JM",
        "name": "JM Road Corridor",
        "description": "Jangali Maharaj Road north-bound transit corridor",
        "color": "#2E7D53",
        "waypoints": [
            {"name": "Balgandharva Rangmandir", "lat": 18.5222, "lng": 73.8493},
            {"name": "Sambhaji Park Crossing", "lat": 18.5255, "lng": 73.8500},
            {"name": "Modern High School", "lat": 18.5280, "lng": 73.8510},
            {"name": "Sancheti Hospital Chowk", "lat": 18.5325, "lng": 73.8525},
        ],
    },
    {
        "route_id": "ROUTE-PUNE-KARVE",
        "name": "Karve Road Corridor",
        "description": "Karve Road arterial corridor towards Kothrud",
        "color": "#CC7A2E",
        "waypoints": [
            {"name": "Deccan Corner / Lakdi Pul", "lat": 18.5135, "lng": 73.8385},
            {"name": "Garware College", "lat": 18.5110, "lng": 73.8325},
            {"name": "Nal Stop (Tunnel/Offline Test Zone)", "lat": 18.5085, "lng": 73.8268},
            {"name": "Paud Phata", "lat": 18.5060, "lng": 73.8210},
        ],
    },
    {
        "route_id": "ROUTE-PUNE-SHIVAJI",
        "name": "Swargate to PMC Central Corridor",
        "description": "Shivaji Road crossing central Pune historical core to PMC HQ",
        "color": "#5B3FA8",
        "waypoints": [
            {"name": "Swargate Bus Station", "lat": 18.5018, "lng": 73.8580},
            {"name": "Dagdusheth Ganpati", "lat": 18.5165, "lng": 73.8562},
            {"name": "Pune Municipal Corporation (PMC)", "lat": 18.5218, "lng": 73.8565},
            {"name": "Shivajinagar Railway Station", "lat": 18.5328, "lng": 73.8550},
        ],
    },
]


def init_default_buses() -> None:
    """Initialize registered buses for the Pune transit fleet."""
    default_fleet = [
        {"bus_id": "BUS-001", "route_id": "ROUTE-PUNE-FC", "route_name": "FC Road Corridor", "status": "ONLINE", "battery": 87, "uptime": "6h 20m", "latitude": 18.5196, "longitude": 73.8436, "last_event": "EVT-000001"},
        {"bus_id": "BUS-002", "route_id": "ROUTE-PUNE-FC", "route_name": "FC Road Corridor", "status": "ONLINE", "battery": 74, "uptime": "5h 10m", "latitude": 18.5236, "longitude": 73.8412, "last_event": "EVT-000002"},
        {"bus_id": "BUS-003", "route_id": "ROUTE-PUNE-KARVE", "route_name": "Karve Road Corridor", "status": "ONLINE", "battery": 91, "uptime": "4h 45m", "latitude": 18.5085, "longitude": 73.8268, "last_event": "EVT-000003"},
        {"bus_id": "BUS-004", "route_id": "ROUTE-PUNE-SHIVAJI", "route_name": "Swargate to PMC", "status": "ONLINE", "battery": 65, "uptime": "3h 20m", "latitude": 18.5218, "longitude": 73.8565, "last_event": "EVT-000004"},
        {"bus_id": "BUS-005", "route_id": "ROUTE-PUNE-FC", "route_name": "FC Road Corridor", "status": "OFFLINE", "battery": 15, "uptime": "—", "latitude": 18.5158, "longitude": 73.8418, "last_event": "EVT-000010"},
        {"bus_id": "BUS-006", "route_id": "ROUTE-PUNE-JM", "route_name": "JM Road Corridor", "status": "ONLINE", "battery": 82, "uptime": "2h 55m", "latitude": 18.5280, "longitude": 73.8510, "last_event": "EVT-000011"},
    ]
    for b in default_fleet:
        if b["bus_id"] not in buses_by_id:
            buses_by_id[b["bus_id"]] = b


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def iso_utc(value: datetime) -> str:
    """Return ISO 8601 UTC with a trailing Z."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def event_to_storage(event: Event) -> dict:
    data = event.model_dump(mode="json")
    data["timestamp"] = iso_utc(event.timestamp)
    return data


def create_observation_from_event(event: Event, incoming_timestamp: str) -> dict:
    """Derive a normalized immutable Observation record from an ingested Event."""
    seq = event.event_id.split("-")[-1]
    obs_id = f"OBS-{seq}"
    return {
        "observation_id": obs_id,
        "event_id": event.event_id,
        "bus_id": event.bus_id,
        "event_type": event.event_type,
        "timestamp": incoming_timestamp,
        "latitude": event.latitude,
        "longitude": event.longitude,
        "confidence": event.confidence,
        "severity": event.severity,
        "evidence_image": event.evidence_image,
        "road_aligned_latitude": event.road_aligned_latitude,
        "road_aligned_longitude": event.road_aligned_longitude,
    }


def seed_demo_data_in_memory() -> dict:
    """Populate authentic Pune transit demo dataset into memory."""
    init_default_buses()

    # Initial demo events
    demo_events = [
        {
            "event_id": "EVT-000001",
            "bus_id": "BUS-001",
            "event_type": "POTHOLE",
            "timestamp": "2026-09-22T08:30:00Z",
            "latitude": 18.519616,
            "longitude": 73.843587,
            "road_aligned_latitude": 18.519600,
            "road_aligned_longitude": 73.843600,
            "heading_degrees": 92.4,
            "route_id": "ROUTE-PUNE-FC",
            "confidence": 0.94,
            "severity": "HIGH",
            "evidence_image": "runtime/evidence/EVT-000001.jpg",
            "source": "actual_bus_simulator",
            "connectivity_state": "ONLINE",
        },
        {
            "event_id": "EVT-000002",
            "bus_id": "BUS-002",
            "event_type": "POTHOLE",
            "timestamp": "2026-09-22T08:34:20Z",
            "latitude": 18.519587,
            "longitude": 73.843620,
            "road_aligned_latitude": 18.519600,
            "road_aligned_longitude": 73.843600,
            "heading_degrees": 91.8,
            "route_id": "ROUTE-PUNE-FC",
            "confidence": 0.92,
            "severity": "HIGH",
            "evidence_image": "runtime/evidence/EVT-000002.jpg",
            "source": "data_demo_simulator",
            "connectivity_state": "ONLINE",
        },
        {
            "event_id": "EVT-000003",
            "bus_id": "BUS-003",
            "event_type": "GARBAGE",
            "timestamp": "2026-09-22T08:45:00Z",
            "latitude": 18.508512,
            "longitude": 73.826815,
            "road_aligned_latitude": 18.508500,
            "road_aligned_longitude": 73.826800,
            "heading_degrees": 268.0,
            "route_id": "ROUTE-PUNE-KARVE",
            "confidence": 0.88,
            "severity": "MEDIUM",
            "evidence_image": "runtime/evidence/EVT-000003.jpg",
            "source": "data_demo_simulator",
            "connectivity_state": "ONLINE",
        },
        {
            "event_id": "EVT-000004",
            "bus_id": "BUS-004",
            "event_type": "TRAFFIC_OBSTRUCTION",
            "timestamp": "2026-09-22T09:10:00Z",
            "latitude": 18.525510,
            "longitude": 73.850020,
            "road_aligned_latitude": 18.525500,
            "road_aligned_longitude": 73.850000,
            "heading_degrees": 12.5,
            "route_id": "ROUTE-PUNE-JM",
            "confidence": 0.91,
            "severity": "HIGH",
            "evidence_image": "runtime/evidence/EVT-000004.jpg",
            "source": "data_demo_simulator",
            "connectivity_state": "ONLINE",
        },
        {
            "event_id": "EVT-000005",
            "bus_id": "BUS-002",
            "event_type": "PEDESTRIAN_RISK",
            "timestamp": "2026-09-22T09:25:00Z",
            "latitude": 18.521820,
            "longitude": 73.856530,
            "road_aligned_latitude": 18.521800,
            "road_aligned_longitude": 73.856500,
            "heading_degrees": 180.0,
            "route_id": "ROUTE-PUNE-SHIVAJI",
            "confidence": 0.85,
            "severity": "MEDIUM",
            "evidence_image": "runtime/evidence/EVT-000005.jpg",
            "source": "data_demo_simulator",
            "connectivity_state": "ONLINE",
        },
    ]

    for ev in demo_events:
        events_by_id[ev["event_id"]] = ev
        obs = {
            "observation_id": f"OBS-{ev['event_id'].split('-')[-1]}",
            "event_id": ev["event_id"],
            "bus_id": ev["bus_id"],
            "event_type": ev["event_type"],
            "timestamp": ev["timestamp"],
            "latitude": ev["latitude"],
            "longitude": ev["longitude"],
            "confidence": ev["confidence"],
            "severity": ev["severity"],
            "evidence_image": ev["evidence_image"],
            "road_aligned_latitude": ev["road_aligned_latitude"],
            "road_aligned_longitude": ev["road_aligned_longitude"],
        }
        observations_by_id[obs["observation_id"]] = obs

    # Verified & pending incidents
    demo_incidents = [
        {
            "incident_id": "INC-000001",
            "event_id": "EVT-000001",
            "evidence_image": "runtime/evidence/EVT-000001.jpg",
            "event_type": "POTHOLE",
            "status": "VERIFIED",
            "latitude": 18.5196,
            "longitude": 73.8436,
            "observation_count": 2,
            "bus_count": 2,
            "confidence": 0.94,
            "severity": "HIGH",
            "department": "MUNICIPAL_CORPORATION",
            "first_observed_at": "2026-09-22T08:30:00Z",
            "last_observed_at": "2026-09-22T08:34:20Z",
            "locality": "Goodluck Chowk, FC Road",
            "description": "Severe road defect on left lane causing bus suspension shocks and deceleration.",
            "buses": ["BUS-001", "BUS-002"],
            "ticket_id": "POT-2026-000001",
        },
        {
            "incident_id": "INC-000002",
            "event_id": "EVT-000003",
            "evidence_image": "runtime/evidence/EVT-000003.jpg",
            "event_type": "GARBAGE",
            "status": "VERIFIED",
            "latitude": 18.5085,
            "longitude": 73.8268,
            "observation_count": 1,
            "bus_count": 1,
            "confidence": 0.88,
            "severity": "MEDIUM",
            "department": "SANITATION",
            "first_observed_at": "2026-09-22T08:45:00Z",
            "last_observed_at": "2026-09-22T08:45:00Z",
            "locality": "Nal Stop, Karve Road",
            "description": "Overflowing civic waste accumulated near transit underpass.",
            "buses": ["BUS-003"],
            "ticket_id": "POT-2026-000002",
        },
        {
            "incident_id": "INC-000003",
            "event_id": "EVT-000004",
            "evidence_image": "runtime/evidence/EVT-000004.jpg",
            "event_type": "TRAFFIC_OBSTRUCTION",
            "status": "PENDING",
            "latitude": 18.5255,
            "longitude": 73.8500,
            "observation_count": 1,
            "bus_count": 1,
            "confidence": 0.91,
            "severity": "HIGH",
            "department": "TRAFFIC_POLICE",
            "first_observed_at": "2026-09-22T09:10:00Z",
            "last_observed_at": "2026-09-22T09:10:00Z",
            "locality": "Sambhaji Park, JM Road",
            "description": "Unattended construction debris blocking bus transit corridor lane.",
            "buses": ["BUS-004"],
            "ticket_id": None,
        },
        {
            "incident_id": "INC-000004",
            "event_id": "EVT-000005",
            "evidence_image": "runtime/evidence/EVT-000005.jpg",
            "event_type": "PEDESTRIAN_RISK",
            "status": "VERIFIED",
            "latitude": 18.5218,
            "longitude": 73.8565,
            "observation_count": 1,
            "bus_count": 1,
            "confidence": 0.85,
            "severity": "MEDIUM",
            "department": "TRAFFIC_POLICE",
            "first_observed_at": "2026-09-22T09:25:00Z",
            "last_observed_at": "2026-09-22T09:25:00Z",
            "locality": "PMC Building, Shivaji Road",
            "description": "Damaged pedestrian guardrail causing commuters to spill onto carriageway.",
            "buses": ["BUS-002"],
            "ticket_id": "POT-2026-000003",
        },
    ]

    for inc in demo_incidents:
        incidents_by_id[inc["incident_id"]] = inc

    # Civic tickets
    demo_tickets = [
        {
            "ticket_id": "POT-2026-000001",
            "incident_id": "INC-000001",
            "workorder_id": "WO-2026-000001",
            "event_type": "POTHOLE",
            "confidence": 0.94,
            "latitude": 18.5196,
            "longitude": 73.8436,
            "evidence_image": "runtime/evidence/EVT-000001.jpg",
            "google_maps_url": "https://www.google.com/maps/dir/?api=1&destination=18.5196,73.8436",
            "estimated_repair_sla_hours": 48,
            "status": "IN_PROGRESS",
            "department": "Road Maintenance Department",
            "priority": "HIGH",
            "created_at": "2026-09-22T08:35:00Z",
            "updated_at": "2026-09-22T09:00:00Z",
            "timeline": [
                {"label": "Reported", "done": True, "time": "22 Sep, 08:35 AM"},
                {"label": "Acknowledged", "done": True, "time": "22 Sep, 08:45 AM"},
                {"label": "In Progress", "done": True, "time": "22 Sep, 09:00 AM"},
                {"label": "Resolved", "done": False, "time": "Pending"},
            ],
        },
        {
            "ticket_id": "POT-2026-000002",
            "incident_id": "INC-000002",
            "workorder_id": "WO-2026-000002",
            "event_type": "GARBAGE",
            "confidence": 0.88,
            "latitude": 18.5085,
            "longitude": 73.8268,
            "evidence_image": "runtime/evidence/EVT-000003.jpg",
            "google_maps_url": "https://www.google.com/maps/dir/?api=1&destination=18.5085,73.8268",
            "estimated_repair_sla_hours": 24,
            "status": "ACKNOWLEDGED",
            "department": "Sanitation Department",
            "priority": "MEDIUM",
            "created_at": "2026-09-22T08:50:00Z",
            "updated_at": "2026-09-22T08:55:00Z",
            "timeline": [
                {"label": "Reported", "done": True, "time": "22 Sep, 08:50 AM"},
                {"label": "Acknowledged", "done": True, "time": "22 Sep, 08:55 AM"},
                {"label": "In Progress", "done": False, "time": "Pending"},
                {"label": "Resolved", "done": False, "time": "Pending"},
            ],
        },
        {
            "ticket_id": "POT-2026-000003",
            "incident_id": "INC-000004",
            "workorder_id": "WO-2026-000003",
            "event_type": "PEDESTRIAN_RISK",
            "confidence": 0.85,
            "latitude": 18.5218,
            "longitude": 73.8565,
            "evidence_image": "runtime/evidence/EVT-000005.jpg",
            "google_maps_url": "https://www.google.com/maps/dir/?api=1&destination=18.5218,73.8565",
            "estimated_repair_sla_hours": 72,
            "status": "REPORTED",
            "department": "Traffic Police",
            "priority": "MEDIUM",
            "created_at": "2026-09-22T09:30:00Z",
            "updated_at": "2026-09-22T09:30:00Z",
            "timeline": [
                {"label": "Reported", "done": True, "time": "22 Sep, 09:30 AM"},
                {"label": "Acknowledged", "done": False, "time": "Pending"},
                {"label": "In Progress", "done": False, "time": "Pending"},
                {"label": "Resolved", "done": False, "time": "Pending"},
            ],
        },
    ]

    for tkt in demo_tickets:
        tickets_by_id[tkt["ticket_id"]] = tkt

    return {
        "message": "Demo data seeded successfully",
        "events_count": len(events_by_id),
        "observations_count": len(observations_by_id),
        "incidents_count": len(incidents_by_id),
        "tickets_count": len(tickets_by_id),
        "buses_count": len(buses_by_id),
    }


# Initialize registered fleet buses
init_default_buses()


# ---------------------------------------------------------------------------
# Router for API endpoints (mounted at both "/" and "/api/v1")
# ---------------------------------------------------------------------------

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(advisory_router)
api_router.include_router(verification_router)


# ---------------------------------------------------------------------------
# Health / Metadata
# ---------------------------------------------------------------------------

@api_router.get("/", tags=["Health"])
@api_router.get("/health", tags=["Health"])
def health_check():
    return {
        "service": "Member 3 Backend",
        "status": "running",
        "contract_version": "v1",
        "dashboard_url": "/dashboard",
        "timestamp": iso_utc(datetime.now(timezone.utc)),
    }


@api_router.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"], include_in_schema=False)
def serve_dashboard():
    """Serve the single-page GIS Dashboard."""
    possible_paths = [
        Path(__file__).resolve().parent.parent.parent / "dashboard" / "index.html",
        Path.cwd() / "dashboard" / "index.html",
    ]
    for path in possible_paths:
        if path.is_file():
            return HTMLResponse(content=path.read_text(encoding="utf-8"))
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard index.html not found")


# ---------------------------------------------------------------------------
# Platform Statistics & Fleet Telemetry
# ---------------------------------------------------------------------------

@api_router.get("/stats", tags=["Platform"])
def get_platform_stats():
    """Return summary KPI analytics for the GIS dashboard."""
    init_default_buses()
    total_incidents = len(incidents_by_id)
    verified_incidents = sum(1 for i in incidents_by_id.values() if i.get("status") == "VERIFIED")
    open_tickets = sum(1 for t in tickets_by_id.values() if t.get("status") != "RESOLVED")
    resolved_tickets = sum(1 for t in tickets_by_id.values() if t.get("status") == "RESOLVED")
    online_fleet = sum(1 for b in buses_by_id.values() if b.get("status") == "ONLINE")

    return {
        "total_events": len(events_by_id),
        "total_observations": len(observations_by_id),
        "total_incidents": total_incidents,
        "verified_incidents": verified_incidents,
        "open_tickets": open_tickets,
        "resolved_tickets": resolved_tickets,
        "fleet_total": len(buses_by_id),
        "fleet_online": online_fleet,
        "timestamp": iso_utc(datetime.now(timezone.utc)),
    }


@api_router.get("/fleet", tags=["Fleet"])
def list_fleet():
    """Return active fleet buses with telemetry, routes, and edge AI status."""
    init_default_buses()
    return {
        "count": len(buses_by_id),
        "fleet": list(buses_by_id.values()),
    }


@api_router.get("/fleet/{bus_id}", tags=["Fleet"])
def get_bus_telemetry(bus_id: str):
    init_default_buses()
    bus = buses_by_id.get(bus_id)
    if bus is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bus not found")
    return bus


@api_router.post("/fleet/telemetry", tags=["Fleet"])
async def receive_fleet_telemetry(payload: Dict[str, Any]):
    """Accept real-time telemetry from running bus simulators and broadcast to GIS dashboard."""
    bus_id = payload.get("bus_id")
    if not bus_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing bus_id")

    init_default_buses()
    if bus_id in buses_by_id:
        buses_by_id[bus_id].update({
            "latitude": payload.get("latitude", buses_by_id[bus_id].get("latitude")),
            "longitude": payload.get("longitude", buses_by_id[bus_id].get("longitude")),
            "status": payload.get("connectivity_state", "ONLINE"),
            "route_id": payload.get("route_id", buses_by_id[bus_id].get("route_id")),
            "speed_kmh": payload.get("speed_kmh", 35.0),
            "heading_degrees": payload.get("heading_degrees", 0.0),
        })
    else:
        buses_by_id[bus_id] = {
            "bus_id": bus_id,
            "route_id": payload.get("route_id", "ROUTE-PUNE-FC"),
            "route_name": "FC Road Corridor",
            "status": payload.get("connectivity_state", "ONLINE"),
            "battery": 90,
            "uptime": "Live",
            "latitude": payload.get("latitude", 18.5196),
            "longitude": payload.get("longitude", 73.8436),
            "speed_kmh": payload.get("speed_kmh", 35.0),
            "heading_degrees": payload.get("heading_degrees", 0.0),
        }

    await ws_manager.broadcast({
        "type": "FLEET_UPDATED",
        "data": list(buses_by_id.values()),
    })
    return {"status": "ok", "bus": buses_by_id[bus_id]}


# ---------------------------------------------------------------------------
# GIS GeoJSON APIs
# ---------------------------------------------------------------------------

@api_router.get("/incidents/geojson", tags=["GIS"])
def get_incidents_geojson():
    """Return an RFC 7946 GeoJSON FeatureCollection of all incidents."""
    features = []
    for inc in incidents_by_id.values():
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(inc["longitude"]), float(inc["latitude"])],
            },
            "properties": {
                "incident_id": inc["incident_id"],
                "event_type": inc["event_type"],
                "status": inc["status"],
                "severity": inc.get("severity", "MEDIUM"),
                "confidence": inc.get("confidence", 0.9),
                "observation_count": inc.get("observation_count", 1),
                "department": inc.get("department", "MUNICIPAL_CORPORATION"),
                "locality": inc.get("locality", "Pune Transit Corridor"),
                "ticket_id": inc.get("ticket_id"),
                "event_id": inc.get("event_id"),
                "evidence_image": inc.get("evidence_image"),
                "description": inc.get("description", ""),
                "first_observed_at": inc.get("first_observed_at"),
                "last_observed_at": inc.get("last_observed_at"),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
    }


@api_router.get("/routes/geojson", tags=["GIS"])
def get_routes_geojson():
    """Return an RFC 7946 GeoJSON FeatureCollection of Pune road corridors."""
    features = []
    for route in PUNE_CORRIDORS:
        coords = [[wp["lng"], wp["lat"]] for wp in route["waypoints"]]
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords,
            },
            "properties": {
                "route_id": route["route_id"],
                "name": route["name"],
                "description": route["description"],
                "color": route["color"],
                "waypoints": route["waypoints"],
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
    }


@api_router.post("/demo/seed", tags=["Platform"])
def seed_demo_endpoint():
    """Seed or reset the working demonstration dataset."""
    result = seed_demo_data_in_memory()
    return result


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Simulator Interactive Trigger Endpoint
# ---------------------------------------------------------------------------

@api_router.post("/simulators/trigger", tags=["Platform"])
async def trigger_simulator_scenario(
    payload: Optional[Dict[str, Any]] = None,
    current_user: UserAccount = Depends(require_role(["ADMIN", "DISPATCHER"])),
):
    """Trigger an interactive demonstration scenario and broadcast live updates."""
    scenario = (payload or {}).get("scenario", "dual_bus")

    if scenario == "dual_bus":
        now = iso_utc(datetime.now(timezone.utc))
        # 1. BUS-001 detects high-severity pothole at Goodluck Chowk
        evt1 = {
            "event_id": "EVT-000001",
            "bus_id": "BUS-001",
            "event_type": "POTHOLE",
            "timestamp": now,
            "latitude": 18.519616,
            "longitude": 73.843587,
            "road_aligned_latitude": 18.519600,
            "road_aligned_longitude": 73.843600,
            "heading_degrees": 92.4,
            "route_id": "ROUTE-PUNE-FC",
            "confidence": 0.94,
            "severity": "HIGH",
            "evidence_image": "runtime/evidence/EVT-000001.jpg",
            "source": "actual_bus_simulator",
            "connectivity_state": "ONLINE",
        }
        events_by_id[evt1["event_id"]] = evt1
        observations_by_id["OBS-000001"] = {
            "observation_id": "OBS-000001",
            "event_id": "EVT-000001",
            "bus_id": "BUS-001",
            "event_type": "POTHOLE",
            "timestamp": evt1["timestamp"],
            "latitude": evt1["latitude"],
            "longitude": evt1["longitude"],
            "confidence": evt1["confidence"],
            "severity": evt1["severity"],
            "evidence_image": evt1["evidence_image"],
            "road_aligned_latitude": evt1["road_aligned_latitude"],
            "road_aligned_longitude": evt1["road_aligned_longitude"],
        }
        init_default_buses()
        buses_by_id["BUS-001"]["latitude"] = evt1["latitude"]
        buses_by_id["BUS-001"]["longitude"] = evt1["longitude"]
        buses_by_id["BUS-001"]["last_event"] = evt1["event_id"]
        await ws_manager.broadcast({"type": "EVENT_INGESTED", "data": evt1, "bus": buses_by_id["BUS-001"]})

        # 2. BUS-002 independent corroboration at same road point
        evt2 = {
            "event_id": "EVT-000002",
            "bus_id": "BUS-002",
            "event_type": "POTHOLE",
            "timestamp": now,
            "latitude": 18.519587,
            "longitude": 73.843620,
            "road_aligned_latitude": 18.519600,
            "road_aligned_longitude": 73.843600,
            "heading_degrees": 91.8,
            "route_id": "ROUTE-PUNE-FC",
            "confidence": 0.92,
            "severity": "HIGH",
            "evidence_image": "runtime/evidence/EVT-000002.jpg",
            "source": "data_demo_simulator",
            "connectivity_state": "ONLINE",
        }
        events_by_id[evt2["event_id"]] = evt2
        observations_by_id["OBS-000002"] = {
            "observation_id": "OBS-000002",
            "event_id": "EVT-000002",
            "bus_id": "BUS-002",
            "event_type": "POTHOLE",
            "timestamp": evt2["timestamp"],
            "latitude": evt2["latitude"],
            "longitude": evt2["longitude"],
            "confidence": evt2["confidence"],
            "severity": evt2["severity"],
            "evidence_image": evt2["evidence_image"],
            "road_aligned_latitude": evt2["road_aligned_latitude"],
            "road_aligned_longitude": evt2["road_aligned_longitude"],
        }
        buses_by_id["BUS-002"]["latitude"] = evt2["latitude"]
        buses_by_id["BUS-002"]["longitude"] = evt2["longitude"]
        buses_by_id["BUS-002"]["last_event"] = evt2["event_id"]
        await ws_manager.broadcast({"type": "EVENT_INGESTED", "data": evt2, "bus": buses_by_id["BUS-002"]})

        # 3. Fleet fusion verified incident
        inc = {
            "incident_id": "INC-000001",
            "event_type": "POTHOLE",
            "status": "VERIFIED",
            "latitude": 18.519600,
            "longitude": 73.843600,
            "observation_count": 2,
            "bus_count": 2,
            "confidence": 0.95,
            "severity": "HIGH",
            "department": "MUNICIPAL_CORPORATION",
            "first_observed_at": evt1["timestamp"],
            "last_observed_at": evt2["timestamp"],
            "locality": "Goodluck Chowk, FC Road",
            "description": "Corroborated severe pothole on FC Road verified by Fleet Fusion.",
            "buses": ["BUS-001", "BUS-002"],
            "ticket_id": "POT-2026-000001",
        }
        incidents_by_id[inc["incident_id"]] = inc
        await ws_manager.broadcast({"type": "INCIDENT_UPDATED", "data": inc})

        # 4. Civic ticket
        tkt = {
            "ticket_id": "POT-2026-000001",
            "incident_id": "INC-000001",
            "workorder_id": "WO-2026-000001",
            "event_type": "POTHOLE",
            "confidence": 0.95,
            "latitude": 18.519600,
            "longitude": 73.843600,
            "evidence_image": "runtime/evidence/EVT-000001.jpg",
            "google_maps_url": "https://www.google.com/maps/dir/?api=1&destination=18.519600,73.843600",
            "estimated_repair_sla_hours": 48,
            "status": "REPORTED",
            "locality": "Goodluck Chowk, FC Road",
            "created_at": evt2["timestamp"],
            "updated_at": evt2["timestamp"],
            "timeline": [
                {"label": "Reported", "done": True, "time": evt2["timestamp"]},
                {"label": "Acknowledged", "done": False, "time": "Pending"},
                {"label": "In Progress", "done": False, "time": "Pending"},
                {"label": "Resolved", "done": False, "time": "Pending"},
            ],
        }
        tickets_by_id[tkt["ticket_id"]] = tkt
        await ws_manager.broadcast({"type": "TICKET_CREATED", "data": tkt})

        # 5. Downstream hazard advisory for corridor
        adv = advisory_service.evaluate_and_publish(
            incident=inc,
            fleet=buses_by_id,
            source_bus_id="BUS-001",
            route_id="ROUTE-PUNE-FC",
        )
        if adv:
            await ws_manager.broadcast({"type": "ADVISORY_ISSUED", "data": adv.model_dump()})

        await ws_manager.broadcast({"type": "STATS_UPDATED", "data": get_platform_stats()})

        return {"message": "Dual-bus scenario executed successfully", "incident_id": "INC-000001", "ticket_id": "POT-2026-000001"}

    elif scenario == "offline_replay":
        now = iso_utc(datetime.now(timezone.utc))
        init_default_buses()
        buses_by_id["BUS-003"]["status"] = "OFFLINE"
        await ws_manager.broadcast({"type": "FLEET_UPDATED", "data": list(buses_by_id.values())})

        evt_offline = {
            "event_id": "EVT-000010",
            "bus_id": "BUS-003",
            "event_type": "GARBAGE",
            "timestamp": now,
            "latitude": 18.508520,
            "longitude": 73.826830,
            "road_aligned_latitude": 18.508500,
            "road_aligned_longitude": 73.826800,
            "heading_degrees": 240.0,
            "route_id": "ROUTE-PUNE-KARVE",
            "confidence": 0.88,
            "severity": "MEDIUM",
            "evidence_image": "runtime/evidence/EVT-000003.jpg",
            "source": "actual_bus_simulator",
            "connectivity_state": "ONLINE",
        }
        events_by_id[evt_offline["event_id"]] = evt_offline
        buses_by_id["BUS-003"]["status"] = "ONLINE"
        buses_by_id["BUS-003"]["last_event"] = "EVT-000010"
        await ws_manager.broadcast({"type": "EVENT_INGESTED", "data": evt_offline, "bus": buses_by_id["BUS-003"]})
        await ws_manager.broadcast({"type": "FLEET_UPDATED", "data": list(buses_by_id.values())})
        await ws_manager.broadcast({"type": "STATS_UPDATED", "data": get_platform_stats()})

        return {"message": "Offline replay scenario executed successfully", "event_id": "EVT-000010"}

    elif scenario == "resolution":
        now = iso_utc(datetime.now(timezone.utc))
        tkt = tickets_by_id.get("POT-2026-000001")
        if tkt:
            tkt["status"] = "RESOLVED"
            tkt["updated_at"] = now
            for step in tkt.get("timeline", []):
                step["done"] = True
                if step.get("time") == "Pending":
                    step["time"] = now
            verification_service.schedule_watch(tkt)
            await ws_manager.broadcast({"type": "TICKET_STATUS_UPDATED", "data": tkt})

        inc = incidents_by_id.get("INC-000001")
        if inc:
            inc["status"] = "RESOLVED"
            inc["last_observed_at"] = now
            await ws_manager.broadcast({"type": "INCIDENT_UPDATED", "data": inc})

        await ws_manager.broadcast({"type": "STATS_UPDATED", "data": get_platform_stats()})
        return {"message": "Resolution scenario executed successfully", "ticket_id": "POT-2026-000001"}

    elif scenario == "seed":
        seed_result = seed_demo_data_in_memory()
        await ws_manager.broadcast({
            "type": "INITIAL_STATE",
            "data": {
                "stats": get_platform_stats(),
                "fleet": list(buses_by_id.values()),
                "incidents": list(incidents_by_id.values()),
                "tickets": list(tickets_by_id.values()),
                "events": list(events_by_id.values())[-15:],
            },
        })
        return seed_result

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown scenario: {scenario}")


# ---------------------------------------------------------------------------
# Event Ingestion
# ---------------------------------------------------------------------------

@api_router.post("/events", status_code=status.HTTP_201_CREATED, tags=["Events"])
async def ingest_event(event: Event):
    """
    Validate and idempotently ingest one contract-valid event.

    Replaying an existing event_id does not create a second record.
    The stored event and associated observation are immutable for this prototype.
    """
    incoming = event_to_storage(event)

    if event.event_id in events_by_id:
        existing = events_by_id[event.event_id]

        if existing != incoming:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"event_id {event.event_id} already exists with a different payload"
                ),
            )

        return {
            "message": "Duplicate event ignored (idempotent replay)",
            "event_id": event.event_id,
            "duplicate": True,
            "event": existing,
        }

    events_by_id[event.event_id] = incoming

    # Record normalized observation record
    obs = create_observation_from_event(event, incoming["timestamp"])
    observations_by_id[obs["observation_id"]] = obs

    # Closed-loop automated repair verification check
    updated_watch, affected_ticket = verification_service.process_observation(
        obs, tickets_by_id
    )
    if affected_ticket:
        if affected_ticket["status"] == "REPORTED":
            # Failed repair reopened!
            safe_db_persist(
                db_transition_ticket_status,
                ticket_id=affected_ticket["ticket_id"],
                new_status="REPORTED",
                changed_by="system_verifier",
                notes=updated_watch.notes if updated_watch else "FAILED_REPAIR",
            )
            await ws_manager.broadcast({
                "type": "TICKET_REOPENED",
                "data": affected_ticket,
                "watch": updated_watch.model_dump() if updated_watch else None,
            })
        elif affected_ticket.get("verification_status") == "VERIFIED_RESOLVED":
            # Successfully verified repair
            safe_db_persist(
                db_transition_ticket_status,
                ticket_id=affected_ticket["ticket_id"],
                new_status="RESOLVED",
                changed_by="system_verifier",
                notes=updated_watch.notes if updated_watch else "VERIFIED_RESOLVED",
            )
            await ws_manager.broadcast({
                "type": "TICKET_VERIFIED",
                "data": affected_ticket,
                "watch": updated_watch.model_dump() if updated_watch else None,
            })

    # Database persistence (idempotent event + observation)
    safe_db_persist(insert_event_idempotent, incoming)
    try:
        ts_clean = incoming["timestamp"].replace("Z", "+00:00")
        ts_dt = datetime.fromisoformat(ts_clean)
        safe_db_persist(
            db_create_observation,
            observation_id=obs["observation_id"],
            event_id=obs["event_id"],
            bus_id=obs["bus_id"],
            event_type=obs["event_type"],
            timestamp=ts_dt,
            latitude=obs["latitude"],
            longitude=obs["longitude"],
            confidence=obs["confidence"],
            evidence_image=obs["evidence_image"],
            road_aligned_latitude=obs.get("road_aligned_latitude"),
            road_aligned_longitude=obs.get("road_aligned_longitude"),
            severity=obs.get("severity"),
            heading_degrees=incoming.get("heading_degrees"),
        )
    except Exception as e:
        logger.debug("Failed observation DB persist: %s", e)

    # Automatic Fleet Fusion clustering & ticket derivation
    fused_incident, created_ticket = fusion_engine.process_observation(
        obs, incidents_by_id, tickets_by_id
    )

    if fused_incident:
        try:
            first_dt = datetime.fromisoformat(fused_incident["first_observed_at"].replace("Z", "+00:00"))
            last_dt = datetime.fromisoformat(fused_incident["last_observed_at"].replace("Z", "+00:00"))
            safe_db_persist(
                db_create_or_update_incident,
                incident_id=fused_incident["incident_id"],
                event_type=fused_incident["event_type"],
                latitude=fused_incident["latitude"],
                longitude=fused_incident["longitude"],
                confidence=fused_incident["confidence"],
                severity=fused_incident["severity"],
                department=fused_incident["department"],
                first_observed_at=first_dt,
                last_observed_at=last_dt,
                observation_count=fused_incident["observation_count"],
                bus_count=fused_incident["bus_count"],
                status=fused_incident["status"],
            )
            safe_db_persist(db_link_obs_to_inc, fused_incident["incident_id"], obs["observation_id"])
        except Exception as e:
            logger.debug("Incident DB persist error: %s", e)

        await ws_manager.broadcast({
            "type": "INCIDENT_UPDATED" if fused_incident.get("observation_count", 1) > 1 else "INCIDENT_CREATED",
            "data": fused_incident,
        })

    if created_ticket:
        try:
            safe_db_persist(
                db_create_ticket,
                ticket_id=created_ticket["ticket_id"],
                incident_id=created_ticket["incident_id"],
                event_type=created_ticket["event_type"],
                department=fused_incident.get("department", "MUNICIPAL_CORPORATION") if fused_incident else "MUNICIPAL_CORPORATION",
                google_maps_url=created_ticket.get("google_maps_url"),
                workorder_id=created_ticket.get("workorder_id"),
                estimated_repair_sla_hours=created_ticket.get("estimated_repair_sla_hours", 48),
                status=created_ticket.get("status", "REPORTED"),
            )
        except Exception as e:
            logger.debug("Ticket DB persist error: %s", e)

        await ws_manager.broadcast({
            "type": "TICKET_CREATED",
            "data": created_ticket,
        })

    # Update bus telemetry if registered
    init_default_buses()
    if event.bus_id in buses_by_id:
        buses_by_id[event.bus_id]["last_event"] = event.event_id
        buses_by_id[event.bus_id]["latitude"] = event.latitude
        buses_by_id[event.bus_id]["longitude"] = event.longitude
        buses_by_id[event.bus_id]["status"] = event.connectivity_state or "ONLINE"
    else:
        buses_by_id[event.bus_id] = {
            "bus_id": event.bus_id,
            "route_id": getattr(event, "route_id", "ROUTE-PUNE-FC"),
            "route_name": "Active Transit Route",
            "status": event.connectivity_state or "ONLINE",
            "battery": 85,
            "uptime": "Live",
            "latitude": event.latitude,
            "longitude": event.longitude,
            "last_event": event.event_id,
        }

    # Evaluate and publish downstream hazard advisory if qualified
    if fused_incident:
        try:
            adv = advisory_service.evaluate_and_publish(
                incident=fused_incident,
                fleet=buses_by_id,
                source_bus_id=event.bus_id,
                route_id=getattr(event, "route_id", None) or fused_incident.get("route_id"),
            )
            if adv:
                await ws_manager.broadcast({
                    "type": "ADVISORY_ISSUED",
                    "data": adv.model_dump(),
                })
        except Exception as e:
            logger.debug("Advisory evaluation skipped/error: %s", e)

    # Broadcast event and stats to all active WebSocket clients in realtime
    await ws_manager.broadcast({
        "type": "EVENT_INGESTED",
        "data": incoming,
        "bus": buses_by_id.get(event.bus_id),
        "stats": get_platform_stats(),
    })

    return {
        "message": "Event accepted",
        "event_id": event.event_id,
        "duplicate": False,
        "event": incoming,
    }


@api_router.get("/events", tags=["Events"])
def list_events():
    return {
        "count": len(events_by_id),
        "events": list(events_by_id.values()),
    }


@api_router.get("/events/{event_id}", tags=["Events"])
def get_event(event_id: str):
    event = events_by_id.get(event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event


# ---------------------------------------------------------------------------
# Observation read API
# ---------------------------------------------------------------------------

@api_router.get("/observations", tags=["Observations"])
def list_observations():
    return {
        "count": len(observations_by_id),
        "observations": list(observations_by_id.values()),
    }


@api_router.get("/observations/{observation_id}", tags=["Observations"])
def get_observation(observation_id: str):
    observation = observations_by_id.get(observation_id)
    if observation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observation not found")
    return observation


# ---------------------------------------------------------------------------
# Incident API
# ---------------------------------------------------------------------------

@api_router.post("/incidents", status_code=status.HTTP_201_CREATED, tags=["Incidents"])
async def create_incident(incident: Incident):
    data = incident.model_dump(mode="json")
    data["first_observed_at"] = iso_utc(incident.first_observed_at)
    data["last_observed_at"] = iso_utc(incident.last_observed_at)

    if incident.incident_id in incidents_by_id:
        existing = incidents_by_id[incident.incident_id]
        if existing.get("event_type") != data.get("event_type"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"incident_id {incident.incident_id} already exists "
                    f"with conflicting event_type {existing.get('event_type')}"
                ),
            )
        is_exact_match = all(existing.get(k) == data.get(k) for k in data.keys())
        existing.update(data)
        safe_db_persist(
            db_create_or_update_incident,
            incident_id=incident.incident_id,
            event_type=data["event_type"],
            latitude=data["latitude"],
            longitude=data["longitude"],
            confidence=data["confidence"],
            severity=data["severity"],
            department=data["department"],
            first_observed_at=incident.first_observed_at,
            last_observed_at=incident.last_observed_at,
            observation_count=data.get("observation_count", 1),
            bus_count=data.get("bus_count", 1),
            status=data["status"],
        )
        await ws_manager.broadcast({
            "type": "INCIDENT_UPDATED",
            "data": existing,
            "stats": get_platform_stats(),
        })
        return {
            "message": "Duplicate incident ignored" if is_exact_match else "Incident updated",
            "incident": existing,
            "duplicate": True if is_exact_match else False,
        }

    incidents_by_id[incident.incident_id] = data

    await ws_manager.broadcast({
        "type": "INCIDENT_CREATED",
        "data": data,
        "stats": get_platform_stats(),
    })

    return {
        "message": "Incident accepted",
        "incident": data,
        "duplicate": False,
    }


@api_router.get("/incidents", tags=["Incidents"])
def list_incidents():
    return {
        "count": len(incidents_by_id),
        "incidents": list(incidents_by_id.values()),
    }


@api_router.get("/incidents/{incident_id}", tags=["Incidents"])
def get_incident(incident_id: str):
    incident = incidents_by_id.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return incident


# ---------------------------------------------------------------------------
# Ticket API
# ---------------------------------------------------------------------------

@api_router.post("/tickets", status_code=status.HTTP_201_CREATED, tags=["Tickets"])
async def create_ticket(
    ticket: Ticket,
    current_user: UserAccount = Depends(require_role(["DISPATCHER", "ADMIN"])),
):
    data = ticket.model_dump(mode="json")
    data["created_at"] = iso_utc(ticket.created_at)
    data["updated_at"] = iso_utc(ticket.updated_at)

    if ticket.ticket_id in tickets_by_id:
        existing = tickets_by_id[ticket.ticket_id]
        if existing.get("event_type") != data.get("event_type"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"ticket_id {ticket.ticket_id} already exists "
                    "with conflicting event_type"
                ),
            )
        is_exact_match = all(existing.get(k) == data.get(k) for k in data.keys())
        existing.update(data)
        return {
            "message": "Duplicate ticket ignored" if is_exact_match else "Ticket updated",
            "ticket": existing,
            "duplicate": True if is_exact_match else False,
        }

    # Initialize timeline stepper
    data["timeline"] = [
        {"label": "Reported", "done": True, "time": iso_utc(ticket.created_at)},
        {"label": "Acknowledged", "done": False, "time": "Pending"},
        {"label": "In Progress", "done": False, "time": "Pending"},
        {"label": "Resolved", "done": False, "time": "Pending"},
    ]

    tickets_by_id[ticket.ticket_id] = data

    # Link ticket to incident if found
    if ticket.incident_id in incidents_by_id:
        incidents_by_id[ticket.incident_id]["ticket_id"] = ticket.ticket_id

    await ws_manager.broadcast({
        "type": "TICKET_CREATED",
        "data": data,
        "stats": get_platform_stats(),
    })

    return {
        "message": "Ticket accepted",
        "ticket": data,
        "duplicate": False,
    }


@api_router.get("/tickets", tags=["Tickets"])
def list_tickets():
    return {
        "count": len(tickets_by_id),
        "tickets": list(tickets_by_id.values()),
    }


@api_router.get("/tickets/{ticket_id}", tags=["Tickets"])
def get_ticket(ticket_id: str):
    ticket = tickets_by_id.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    return ticket


@api_router.patch("/tickets/{ticket_id}/status", tags=["Tickets"])
async def update_ticket_status(
    ticket_id: str,
    new_status: str,
    current_user: UserAccount = Depends(require_role(["DISPATCHER", "ADMIN"])),
):
    if new_status not in TICKET_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid ticket status. Allowed: "
                + ", ".join(sorted(TICKET_STATUSES))
            ),
        )

    ticket = tickets_by_id.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    ticket["status"] = new_status
    now_iso = iso_utc(datetime.now(timezone.utc))
    ticket["updated_at"] = now_iso

    # Update timeline if present
    if "timeline" in ticket and isinstance(ticket["timeline"], list):
        status_map = {
            "REPORTED": 0,
            "ACKNOWLEDGED": 1,
            "IN_PROGRESS": 2,
            "RESOLVED": 3,
        }
        target_idx = status_map.get(new_status, 0)
        for i, step in enumerate(ticket["timeline"]):
            if i <= target_idx:
                step["done"] = True
                if step.get("time") == "Pending":
                    step["time"] = now_iso

    safe_db_persist(
        db_transition_ticket_status,
        ticket_id=ticket_id,
        new_status=new_status,
        changed_by=current_user.username,
    )

    # Schedule automated repair verification watch
    if new_status == "RESOLVED":
        verification_service.schedule_watch(ticket)

    await ws_manager.broadcast({
        "type": "TICKET_STATUS_UPDATED",
        "data": ticket,
        "stats": get_platform_stats(),
    })

    return {
        "message": "Ticket status updated",
        "ticket": ticket,
    }


@api_router.get("/tickets/{ticket_id}/map", tags=["Tickets"])
def get_ticket_map(ticket_id: str):
    ticket = tickets_by_id.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    latitude = ticket["latitude"]
    longitude = ticket["longitude"]
    google_maps_url = (
        "https://www.google.com/maps/dir/?api=1"
        f"&destination={latitude},{longitude}"
    )

    return {
        "ticket_id": ticket_id,
        "latitude": latitude,
        "longitude": longitude,
        "google_maps_url": google_maps_url,
    }


@api_router.get("/tickets/{ticket_id}/workorder.pdf", tags=["Tickets"])
def get_ticket_workorder_pdf(
    ticket_id: str,
    current_user: UserAccount = Depends(require_role(["DISPATCHER", "ADMIN"])),
):
    """Generate and return a standardized contractor work-order PDF with map QR code and evidence."""
    ticket = tickets_by_id.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    pdf_bytes = pdf_service.generate_pdf(ticket)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="workorder_{ticket_id}.pdf"',
            "Content-Type": "application/pdf",
        },
    )


# Mount routes at root and with /api/v1 prefix
app.include_router(api_router)
app.include_router(api_router, prefix="/api/v1")


# ---------------------------------------------------------------------------
# Real-Time WebSocket Endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws")
@app.websocket("/api/v1/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time bi-directional streaming endpoint for the GIS Dashboard."""
    await ws_manager.connect(websocket)
    try:
        init_default_buses()
        await websocket.send_json({
            "type": "INITIAL_STATE",
            "data": {
                "stats": get_platform_stats(),
                "fleet": list(buses_by_id.values()),
                "incidents": list(incidents_by_id.values()),
                "tickets": list(tickets_by_id.values()),
                "events": list(events_by_id.values())[-15:],
            },
        })
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
