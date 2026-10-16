"""Pydantic schemas for Closed-Loop Automated Repair Verification.

Aligned with docs/api-contract.md and the Beyonders verification lifecycle policy.
Format: VW-YYYY-XXXXXX (e.g. VW-2026-000001).
"""

from __future__ import annotations

import datetime
import re
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class VerificationState(str, Enum):
    """Lifecycle states for a repair verification watch."""

    WATCHING = "WATCHING"
    VERIFIED_RESOLVED = "VERIFIED_RESOLVED"
    FAILED_REPAIR = "FAILED_REPAIR"
    EXPIRED_UNVERIFIED = "EXPIRED_UNVERIFIED"


class VerificationWatchSchema(BaseModel):
    """Schema for a scheduled repair verification watch."""

    watch_id: str = Field(
        ...,
        description="Unique watch identifier in format VW-YYYY-XXXXXX",
        examples=["VW-2026-000001"],
    )
    ticket_id: str = Field(
        ...,
        description="Target ticket identifier in format POT-YYYY-XXXXXX",
        examples=["POT-2026-000001"],
    )
    scheduled_at: str = Field(
        ...,
        description="ISO 8601 UTC timestamp when verification watch was scheduled",
        examples=["2026-09-22T08:35:00Z"],
    )
    expires_at: str = Field(
        ...,
        description="ISO 8601 UTC timestamp when verification window lapses",
        examples=["2026-09-24T08:35:00Z"],
    )
    state: VerificationState = Field(
        default=VerificationState.WATCHING,
        description="Current verification lifecycle state",
    )
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    required_smooth_observations: int = Field(default=2, ge=1)
    smooth_observations_count: int = Field(default=0, ge=0)
    defect_observations_count: int = Field(default=0, ge=0)
    smooth_buses: List[str] = Field(default_factory=list)
    defect_buses: List[str] = Field(default_factory=list)
    last_checked_at: Optional[str] = None
    completed_at: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("watch_id")
    @classmethod
    def validate_watch_id(cls, v: str) -> str:
        if not re.match(r"^VW-\d{4}-\d{6}$", v):
            raise ValueError(f"watch_id '{v}' does not match format VW-YYYY-XXXXXX")
        return v

    def is_expired(self, current_time: Optional[datetime.datetime] = None) -> bool:
        """Check if verification window has lapsed."""
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


class SmoothObservationSimulateRequest(BaseModel):
    """Request payload to simulate a smooth bus pass observation over a repaired defect."""

    bus_id: str = Field(..., examples=["BUS-002"])
    confidence: float = Field(default=0.92, ge=0.0, le=1.0)
    evidence_image: Optional[str] = Field(None, examples=["runtime/evidence/SMOOTH_001.jpg"])
