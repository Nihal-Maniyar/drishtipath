"""Router for Urban Analytics, Spatial Heatmaps, and Aggregations.

Exposes REST endpoints for querying GIS spatial density heatmaps with bounded
corridor viewports and configurable grid resolutions.
"""

from __future__ import annotations

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, status

from starlette import status as http_status

from backend.app.schemas.heatmap import (
    BoundingBox,
    SpatialHeatmapResponse,
)
from backend.app.services.heatmap_service import heatmap_service

logger = logging.getLogger("urban_intelligence.analytics")

router = APIRouter(prefix="/analytics", tags=["Analytics"])

# Default Pune Transit Corridor bounding box
DEFAULT_MIN_LAT = 18.4400
DEFAULT_MAX_LAT = 18.6200
DEFAULT_MIN_LON = 73.7500
DEFAULT_MAX_LON = 73.9500


@router.get("/heatmap", response_model=SpatialHeatmapResponse)
def get_spatial_heatmap(
    min_lat: Optional[float] = Query(None, description="Southern bounding box latitude in WGS84 degrees"),
    max_lat: Optional[float] = Query(None, description="Northern bounding box latitude in WGS84 degrees"),
    min_lon: Optional[float] = Query(None, description="Western bounding box longitude in WGS84 degrees"),
    max_lon: Optional[float] = Query(None, description="Eastern bounding box longitude in WGS84 degrees"),
    grid_size_meters: float = Query(
        100.0,
        ge=10.0,
        le=5000.0,
        description="Spatial aggregation cell size in meters (10.0m - 5000.0m)",
    ),
    event_type: Optional[str] = Query(None, description="Filter by defect type: POTHOLE, GARBAGE, etc."),
    severity: Optional[str] = Query(None, description="Filter by defect severity: HIGH, MEDIUM, LOW"),
    incident_status: Optional[str] = Query(
        None,
        alias="status",
        description="Filter by incident status: CANDIDATE, VERIFIED, RESOLVED",
    ),
    target: str = Query("incidents", description="Aggregation target: 'incidents' or 'observations'"),
):
    """Retrieve spatial density heatmap cells for the specified geographic viewport.

    Aggregates defects into deterministic metric grid cells and calculates normalized
    intensity weights (0.0 to 1.0) optimized for Leaflet GIS canvas rendering without
    returning overwhelming raw point data.
    """
    # 1. Apply defaults if any bound coordinate is omitted
    eff_min_lat = min_lat if min_lat is not None else DEFAULT_MIN_LAT
    eff_max_lat = max_lat if max_lat is not None else DEFAULT_MAX_LAT
    eff_min_lon = min_lon if min_lon is not None else DEFAULT_MIN_LON
    eff_max_lon = max_lon if max_lon is not None else DEFAULT_MAX_LON

    # 2. Validate geographic bounding box
    try:
        bbox = BoundingBox(
            min_latitude=eff_min_lat,
            max_latitude=eff_max_lat,
            min_longitude=eff_min_lon,
            max_longitude=eff_max_lon,
        )
    except Exception as err:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid bounding box parameters: {err}",
        )

    # 3. Retrieve in-memory and database incidents/observations
    try:
        from backend.app.main import incidents_by_id, observations_by_id
    except ImportError:
        try:
            from app.main import incidents_by_id, observations_by_id
        except ImportError:
            incidents_by_id = {}
            observations_by_id = {}

    session = None
    try:
        from database.connection import SessionLocal

        session = SessionLocal()
    except Exception:
        session = None

    try:
        result = heatmap_service.get_spatial_heatmap(
            bounding_box=bbox,
            grid_size_meters=grid_size_meters,
            event_type=event_type,
            severity=severity,
            status=incident_status,
            session=session,
            in_memory_incidents=incidents_by_id,
            in_memory_observations=observations_by_id,
            target=target,
        )
        return result
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
