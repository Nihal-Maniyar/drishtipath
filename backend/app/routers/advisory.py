"""Router for Downstream Bus Hazard Advisories.

Exposes REST endpoints for querying active advisories, inspecting corridor-specific
warnings, and manually/automatically triggering hazard advisories.
"""

from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.app.core.security import UserAccount, require_role
from backend.app.schemas.advisory import AdvisoryEvaluationRequest, HazardAdvisory
from backend.app.services.advisory_service import advisory_service

router = APIRouter(prefix="/advisories", tags=["Advisories"])


@router.get("", response_model=List[HazardAdvisory])
def list_advisories(active_only: bool = Query(True, description="Filter out expired advisories")):
    """List all registered downstream bus hazard advisories."""
    return advisory_service.get_advisories(active_only=active_only)


@router.get("/{advisory_id}", response_model=HazardAdvisory)
def get_advisory(advisory_id: str):
    """Retrieve an advisory by ID."""
    adv = advisory_service.get_advisory_by_id(advisory_id)
    if not adv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Advisory {advisory_id} not found",
        )
    return adv


@router.get("/bus/{bus_id}", response_model=List[HazardAdvisory])
def get_advisories_for_bus(bus_id: str):
    """Retrieve active hazard advisories applicable to a specific bus based on route and position."""
    from backend.app.main import buses_by_id

    advs = advisory_service.get_advisories_for_bus(bus_id, buses_by_id)
    return advs


@router.post("/evaluate", response_model=HazardAdvisory, status_code=status.HTTP_201_CREATED)
def evaluate_hazard_advisory(
    req: AdvisoryEvaluationRequest,
    current_user: UserAccount = Depends(require_role(["ADMIN", "DISPATCHER"])),
):
    """Evaluate an existing incident and issue a downstream hazard advisory if qualified."""
    from backend.app.main import buses_by_id, incidents_by_id

    incident = incidents_by_id.get(req.incident_id)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident {req.incident_id} not found",
        )

    advisory = advisory_service.evaluate_and_publish(
        incident=incident,
        fleet=buses_by_id,
        route_id=req.route_id,
        corridor_window_meters=req.corridor_window_meters,
        ttl_seconds=req.ttl_seconds,
    )

    if not advisory:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incident does not qualify for a downstream hazard advisory (must be high-severity pothole).",
        )

    return advisory
