"""Pydantic schemas for Downstream Bus Hazard Advisories.

Strictly adheres to docs/api-contract.md and the Beyonders hazard advisory specification.
Format: ADV-YYYY-XXXXXX (e.g. ADV-2026-000001).
"""

from __future__ import annotations

import datetime
import re
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class HazardAdvisory(BaseModel):
    """Downstream bus hazard advisory payload."""

    advisory_id: str = Field(
        ...,
        description="Unique advisory identifier in format ADV-YYYY-XXXXXX",
        examples=["ADV-2026-000001"],
    )
    incident_id: str = Field(
        ...,
        description="Associated incident identifier in format INC-XXXXXX",
        examples=["INC-000001"],
    )
    source_bus_id: str = Field(
        ...,
        description="Bus that initially reported or corroborated the hazard",
        examples=["BUS-001"],
    )
    route_id: str = Field(
        ...,
        description="Transit corridor / route identifier",
        examples=["ROUTE-PUNE-FC"],
    )
    latitude: float = Field(
        ...,
        ge=-90.0,
        le=90.0,
        description="WGS84 latitude of hazard",
        examples=[18.5196],
    )
    longitude: float = Field(
        ...,
        ge=-180.0,
        le=180.0,
        description="WGS84 longitude of hazard",
        examples=[73.8436],
    )
    road_aligned_latitude: Optional[float] = Field(
        None,
        ge=-90.0,
        le=90.0,
        description="Road-snapped WGS84 latitude",
        examples=[18.5196],
    )
    road_aligned_longitude: Optional[float] = Field(
        None,
        ge=-180.0,
        le=180.0,
        description="Road-snapped WGS84 longitude",
        examples=[73.8436],
    )
    severity: str = Field(
        "HIGH",
        description="Severity level (e.g. HIGH, CRITICAL)",
        examples=["HIGH"],
    )
    event_type: str = Field(
        "POTHOLE",
        description="Hazard classification type",
        examples=["POTHOLE"],
    )
    issued_at: str = Field(
        ...,
        description="ISO 8601 UTC timestamp of advisory publication",
        examples=["2026-09-22T08:35:00Z"],
    )
    expires_at: str = Field(
        ...,
        description="ISO 8601 UTC timestamp when advisory expires",
        examples=["2026-09-22T09:35:00Z"],
    )
    corridor_window_meters: float = Field(
        500.0,
        gt=0.0,
        description="Spatial corridor threshold window in meters (default 500m)",
    )
    target_buses: List[str] = Field(
        default_factory=list,
        description="Identified downstream buses within corridor window",
    )
    description: Optional[str] = Field(
        None,
        description="Human-readable hazard warning description",
    )
    confidence: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Confidence level of hazard detection",
    )

    @field_validator("advisory_id")
    @classmethod
    def validate_advisory_id(cls, v: str) -> str:
        if not re.match(r"^ADV-\d{4}-\d{6}$", v):
            raise ValueError(f"advisory_id '{v}' does not match format ADV-YYYY-XXXXXX")
        return v

    @field_validator("incident_id")
    @classmethod
    def validate_incident_id(cls, v: str) -> str:
        if not re.match(r"^INC-\d{6}$", v):
            raise ValueError(f"incident_id '{v}' does not match format INC-XXXXXX")
        return v

    def is_expired(self, current_time: Optional[datetime.datetime] = None) -> bool:
        """Check if advisory has passed its expiration time."""
        now = current_time or datetime.datetime.now(datetime.timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)

        exp_str = self.expires_at.replace("Z", "+00:00")
        try:
            exp_dt = datetime.datetime.fromisoformat(exp_str)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=datetime.timezone.utc)
            return now >= exp_dt
        except Exception:
            return False


class AdvisoryEvaluationRequest(BaseModel):
    """Request schema for evaluating and generating an advisory."""

    incident_id: str = Field(..., examples=["INC-000001"])
    route_id: Optional[str] = Field(None, examples=["ROUTE-PUNE-FC"])
    corridor_window_meters: float = Field(500.0, gt=0.0)
    ttl_seconds: int = Field(3600, gt=0)
