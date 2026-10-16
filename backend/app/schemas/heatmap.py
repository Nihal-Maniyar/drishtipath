"""Pydantic schemas for Spatial Density Heatmaps and PostGIS Aggregations.

Strictly aligned with docs/api-contract.md, docs/database.md, and the GIS Leaflet contract.
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


class BoundingBox(BaseModel):
    """Geographic bounding box for spatial density aggregation."""

    model_config = ConfigDict(extra="forbid")

    min_latitude: float = Field(
        ...,
        ge=-90.0,
        le=90.0,
        description="Southern boundary latitude in WGS84 decimal degrees",
        examples=[18.4500],
    )
    max_latitude: float = Field(
        ...,
        ge=-90.0,
        le=90.0,
        description="Northern boundary latitude in WGS84 decimal degrees",
        examples=[18.6000],
    )
    min_longitude: float = Field(
        ...,
        ge=-180.0,
        le=180.0,
        description="Western boundary longitude in WGS84 decimal degrees",
        examples=[73.7500],
    )
    max_longitude: float = Field(
        ...,
        ge=-180.0,
        le=180.0,
        description="Eastern boundary longitude in WGS84 decimal degrees",
        examples=[73.9500],
    )

    @model_validator(mode="after")
    def validate_bounds(self) -> BoundingBox:
        """Validate bounding box coordinate consistency and span constraints."""
        if self.min_latitude > self.max_latitude:
            raise ValueError(
                f"min_latitude ({self.min_latitude}) cannot be greater than max_latitude ({self.max_latitude})"
            )
        if self.min_longitude > self.max_longitude:
            raise ValueError(
                f"min_longitude ({self.min_longitude}) cannot be greater than max_longitude ({self.max_longitude})"
            )
        # Prevent runaway table scans across continents (max 5.0 degree span ~ 550 km)
        lat_span = self.max_latitude - self.min_latitude
        lon_span = self.max_longitude - self.min_longitude
        if lat_span > 5.0 or lon_span > 5.0:
            raise ValueError(
                f"Bounding box span (lat={lat_span:.2f}°, lon={lon_span:.2f}°) exceeds maximum 5.0° limit"
            )
        return self


class HeatmapCell(BaseModel):
    """Aggregated spatial cluster cell for GIS heatmaps and corridor visualization."""

    model_config = ConfigDict(extra="forbid")

    cell_id: str = Field(
        ...,
        description="Deterministic cell grid identifier",
        examples=["CELL-18.5196-73.8436"],
    )
    latitude: float = Field(
        ...,
        ge=-90.0,
        le=90.0,
        description="Centroid latitude of the aggregated spatial cluster",
        examples=[18.5196],
    )
    longitude: float = Field(
        ...,
        ge=-180.0,
        le=180.0,
        description="Centroid longitude of the aggregated spatial cluster",
        examples=[73.8436],
    )
    point_count: int = Field(
        ...,
        ge=1,
        description="Number of incidents within this cell",
        examples=[3],
    )
    normalized_weight: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Normalized density intensity between 0.0 and 1.0 for Leaflet heatmap rendering",
        examples=[0.85],
    )
    dominant_event_type: str = Field(
        ...,
        description="Primary defect classification (e.g. POTHOLE, GARBAGE)",
        examples=["POTHOLE"],
    )
    dominant_severity: str = Field(
        ...,
        description="Highest severity observed within this cell (HIGH, MEDIUM, LOW)",
        examples=["HIGH"],
    )
    incident_ids: List[str] = Field(
        default_factory=list,
        description="List of incident identifiers aggregated inside this cell",
        examples=[["INC-000001", "INC-000002"]],
    )


class SpatialHeatmapResponse(BaseModel):
    """Response payload for spatial aggregation API."""

    model_config = ConfigDict(extra="forbid")

    cell_count: int = Field(
        ...,
        description="Total number of aggregated cells returned",
        examples=[12],
    )
    total_incidents: int = Field(
        ...,
        description="Total number of matching incidents in the query viewport",
        examples=[45],
    )
    max_cell_count: int = Field(
        ...,
        description="Highest point count in any single cell (used for relative weight scaling)",
        examples=[8],
    )
    grid_size_meters: float = Field(
        ...,
        description="Configured metric cell dimension in meters",
        examples=[100.0],
    )
    bounding_box: BoundingBox
    aggregation_mode: str = Field(
        default="grid_binning",
        description="Spatial aggregation engine used: 'postgis_snap_to_grid' or 'in_memory_spatial_binning'",
        examples=["in_memory_spatial_binning"],
    )
    cells: List[HeatmapCell] = Field(
        default_factory=list,
        description="List of aggregated spatial cells",
    )
