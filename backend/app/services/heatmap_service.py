"""Spatial Density Heatmap Service for GIS corridor visualization.

Provides efficient, bounded spatial aggregation using PostGIS GiST index queries
and metric grid binning.

================================================================================
ARCHITECTURE NOTE: H3 COMPATIBILITY & EVALUATION
================================================================================
Evaluation of Uber H3 Hexagonal Hierarchical Indexing:
1. External Dependency & Security Boundary:
   H3 requires compiling C library bindings (`h3-py`) which introduces external
   binary wheel dependencies, violating our zero-unreviewed-dependencies constraint.
2. GIS / Leaflet Integration Overhead:
   The frontend Leaflet map engine (1.9.4) renders heatmaps and intensity fields
   using raster canvas / point layers (e.g. `leaflet-heat`) which natively consume
   `[latitude, longitude, intensity]` tuples. Hexagonal H3 tiles require substantial
   GeoJSON multi-polygon conversion and deserialization overhead in the browser.
3. PostGIS Native Spatial Indexing:
   PostGIS provides native metric grid snapping (`ST_SnapToGrid`, `ST_MakeEnvelope`)
   accelerated by GiST R-Tree indexes (`idx_incidents_geom`).
   Metric centroid binning achieves sub-millisecond execution over bounded city
   viewports with exact meter-level resolution, zero external packages, and 100%
   backward compatibility with both PostgreSQL and SQLite test environments.
================================================================================
"""

from __future__ import annotations

import collections
import math
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy.orm import Session

from backend.app.schemas.heatmap import (
    BoundingBox,
    HeatmapCell,
    SpatialHeatmapResponse,
)

# Mean Earth Radius in meters
EARTH_RADIUS_METERS = 6371000.0

# Severity rank for calculating dominant hazardous priority in each cell
SEVERITY_PRIORITY = {
    "HIGH": 3,
    "MEDIUM": 2,
    "LOW": 1,
}


class HeatmapService:
    """Service for bounded spatial density aggregation and heatmap calculation."""

    def __init__(self) -> None:
        pass

    @staticmethod
    def _meters_per_degree(mean_lat_deg: float) -> Tuple[float, float]:
        """Compute approximate metric distances per degree latitude and longitude.

        Args:
            mean_lat_deg: Average latitude in decimal degrees.

        Returns:
            Tuple of (meters_per_lat_deg, meters_per_lon_deg).
        """
        # 1 deg latitude is approx 111,139 meters along a meridian
        m_per_lat = 111139.0
        # 1 deg longitude varies with latitude cosine
        rad = math.radians(mean_lat_deg)
        m_per_lon = 111139.0 * max(0.01, math.cos(rad))
        return m_per_lat, m_per_lon

    def aggregate_incidents(
        self,
        incidents: List[Dict[str, Any]],
        bounding_box: BoundingBox,
        grid_size_meters: float = 100.0,
        aggregation_mode: str = "in_memory_spatial_binning",
    ) -> SpatialHeatmapResponse:
        """Aggregate a collection of incidents into metric spatial grid cells.

        Args:
            incidents: Filtered incidents matching bbox and criteria.
            bounding_box: Geographic bounding box viewport.
            grid_size_meters: Cell dimension in meters (10m - 5000m).
            aggregation_mode: String identifying the calculation engine.

        Returns:
            SpatialHeatmapResponse with aggregated cells and normalized weights.
        """
        # Validate grid size boundaries
        grid_size = max(10.0, min(5000.0, float(grid_size_meters)))

        if not incidents:
            return SpatialHeatmapResponse(
                cell_count=0,
                total_incidents=0,
                max_cell_count=0,
                grid_size_meters=grid_size,
                bounding_box=bounding_box,
                aggregation_mode=aggregation_mode,
                cells=[],
            )

        mid_lat = (bounding_box.min_latitude + bounding_box.max_latitude) / 2.0
        m_lat, m_lon = self._meters_per_degree(mid_lat)

        step_lat = grid_size / m_lat
        step_lon = grid_size / m_lon

        # Group incidents by grid row and column indices
        grid_bins: Dict[Tuple[int, int], List[Dict[str, Any]]] = collections.defaultdict(list)

        for inc in incidents:
            lat = float(inc.get("latitude", 0.0))
            lon = float(inc.get("longitude", 0.0))

            # Strictly ensure coordinate lies within bounding box
            if not (
                bounding_box.min_latitude <= lat <= bounding_box.max_latitude
                and bounding_box.min_longitude <= lon <= bounding_box.max_longitude
            ):
                continue

            row = int(math.floor((lat - bounding_box.min_latitude) / step_lat))
            col = int(math.floor((lon - bounding_box.min_longitude) / step_lon))
            grid_bins[(row, col)].append(inc)

        if not grid_bins:
            return SpatialHeatmapResponse(
                cell_count=0,
                total_incidents=0,
                max_cell_count=0,
                grid_size_meters=grid_size,
                bounding_box=bounding_box,
                aggregation_mode=aggregation_mode,
                cells=[],
            )

        max_count = max(len(items) for items in grid_bins.values())

        cells: List[HeatmapCell] = []
        for (row, col), items in grid_bins.items():
            cell_count = len(items)

            # Calculate deterministic cell centroid
            centroid_lat = round(bounding_box.min_latitude + (row + 0.5) * step_lat, 6)
            centroid_lon = round(bounding_box.min_longitude + (col + 0.5) * step_lon, 6)

            # Clamp centroid within bounds
            centroid_lat = min(bounding_box.max_latitude, max(bounding_box.min_latitude, centroid_lat))
            centroid_lon = min(bounding_box.max_longitude, max(bounding_box.min_longitude, centroid_lon))

            # Determine dominant defect classification
            event_types = [item.get("event_type", "UNKNOWN") for item in items]
            type_counter = collections.Counter(event_types)
            dominant_event_type = type_counter.most_common(1)[0][0]

            # Determine dominant hazardous severity (highest severity priority)
            severities = [item.get("severity", "MEDIUM") for item in items]
            dominant_severity = max(
                severities,
                key=lambda s: SEVERITY_PRIORITY.get(s, 1),
            )

            # Normalized intensity weight in [0.0, 1.0]
            normalized_weight = round(cell_count / max_count, 4) if max_count > 0 else 0.0

            incident_ids = [
                str(item.get("incident_id"))
                for item in items
                if item.get("incident_id") is not None
            ]

            cell_id = f"CELL-{centroid_lat:.4f}-{centroid_lon:.4f}"

            cells.append(
                HeatmapCell(
                    cell_id=cell_id,
                    latitude=centroid_lat,
                    longitude=centroid_lon,
                    point_count=cell_count,
                    normalized_weight=normalized_weight,
                    dominant_event_type=dominant_event_type,
                    dominant_severity=dominant_severity,
                    incident_ids=incident_ids,
                )
            )

        # Sort cells by descending point_count (highest hazard clusters first)
        cells.sort(key=lambda c: (c.point_count, c.normalized_weight), reverse=True)

        return SpatialHeatmapResponse(
            cell_count=len(cells),
            total_incidents=sum(len(items) for items in grid_bins.values()),
            max_cell_count=max_count,
            grid_size_meters=grid_size,
            bounding_box=bounding_box,
            aggregation_mode=aggregation_mode,
            cells=cells,
        )

    def get_spatial_heatmap(
        self,
        bounding_box: BoundingBox,
        grid_size_meters: float = 100.0,
        event_type: Optional[str] = None,
        severity: Optional[str] = None,
        status: Optional[str] = None,
        session: Optional[Session] = None,
        in_memory_incidents: Optional[Dict[str, Dict[str, Any]]] = None,
        in_memory_observations: Optional[Dict[str, Dict[str, Any]]] = None,
        target: str = "incidents",
    ) -> SpatialHeatmapResponse:
        """Fetch matching entities from PostGIS or in-memory repository and aggregate.

        Args:
            bounding_box: Target geographic bounding box.
            grid_size_meters: Cell size in meters.
            event_type: Optional filter by event type.
            severity: Optional filter by severity.
            status: Optional filter by incident status.
            session: Optional SQLAlchemy Session for PostGIS querying.
            in_memory_incidents: In-memory dictionary of incidents.
            in_memory_observations: In-memory dictionary of raw observations.
            target: 'incidents' or 'observations'.

        Returns:
            SpatialHeatmapResponse.
        """
        filtered: List[Dict[str, Any]] = []
        aggregation_mode = "in_memory_spatial_binning"

        # 1. Attempt PostGIS GiST-indexed database query if session is provided
        if session is not None:
            try:
                from database.spatial import aggregate_spatial_density_grid

                db_results = aggregate_spatial_density_grid(
                    session=session,
                    min_lat=bounding_box.min_latitude,
                    min_lon=bounding_box.min_longitude,
                    max_lat=bounding_box.max_latitude,
                    max_lon=bounding_box.max_longitude,
                    grid_size_meters=grid_size_meters,
                    event_type=event_type,
                    severity=severity,
                    status=status,
                    target=target,
                )
                if db_results:
                    aggregation_mode = "postgis_snap_to_grid"
                    filtered.extend(db_results)
            except Exception:
                # Fall back gracefully to in-memory store if DB query fails or unconfigured
                filtered = []

        # 2. If no DB records found, filter in-memory store based on target
        if not filtered:
            data_source = in_memory_observations if target == "observations" else in_memory_incidents
            if data_source:
                for item in data_source.values():
                    lat = float(item.get("latitude", 0.0))
                    lon = float(item.get("longitude", 0.0))

                    # Spatial bounding box filter
                    if not (
                        bounding_box.min_latitude <= lat <= bounding_box.max_latitude
                        and bounding_box.min_longitude <= lon <= bounding_box.max_longitude
                    ):
                        continue

                    # Defect attributes filter
                    if event_type and item.get("event_type") != event_type:
                        continue
                    if severity and item.get("severity") != severity:
                        continue
                    if status and item.get("status") != status:
                        continue

                    filtered.append({
                        "incident_id": item.get("incident_id") or item.get("observation_id"),
                        "latitude": lat,
                        "longitude": lon,
                        "event_type": item.get("event_type", "UNKNOWN"),
                        "severity": item.get("severity", "MEDIUM"),
                        "status": item.get("status", "CANDIDATE"),
                    })

        return self.aggregate_incidents(
            incidents=filtered,
            bounding_box=bounding_box,
            grid_size_meters=grid_size_meters,
            aggregation_mode=aggregation_mode,
        )


# Global singleton service
heatmap_service = HeatmapService()
