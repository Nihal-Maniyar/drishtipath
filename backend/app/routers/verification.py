"""Router for Closed-Loop Automated Repair Verification.

Exposes endpoints for auditing verification watches, checking expiration,
and simulating smooth surface road passes.
"""

from __future__ import annotations

import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.core.security import UserAccount, require_role
from backend.app.schemas.verification import (
    SmoothObservationSimulateRequest,
    VerificationWatchSchema,
)
from backend.app.services.verification_service import verification_service

router = APIRouter(tags=["Verification"])


@router.get("/verification/watches", response_model=List[VerificationWatchSchema])
def list_verification_watches():
    """List all scheduled and historical repair verification watches."""
    return verification_service.get_all_watches()


@router.get("/tickets/{ticket_id}/verification", response_model=VerificationWatchSchema)
def get_ticket_verification(ticket_id: str):
    """Retrieve the verification watch and state for a specific ticket."""
    watch = verification_service.get_watch_for_ticket(ticket_id)
    if not watch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No verification watch scheduled for ticket {ticket_id}",
        )
    return watch


@router.post("/verification/evaluate-expired", response_model=List[VerificationWatchSchema])
def evaluate_expired_watches(
    current_user: UserAccount = Depends(require_role(["ADMIN", "DISPATCHER"])),
):
    """Evaluate and transition verification watches whose window lapsed without bus passage."""
    from backend.app.main import tickets_by_id

    expired = verification_service.evaluate_expired_watches(tickets_by_id)
    return expired


@router.post("/tickets/{ticket_id}/verify-smooth", response_model=VerificationWatchSchema)
async def simulate_smooth_verification_pass(
    ticket_id: str,
    req: SmoothObservationSimulateRequest,
    current_user: UserAccount = Depends(require_role(["ADMIN", "DISPATCHER"])),
):
    """Simulate a subsequent bus observation over a repaired pothole to test smooth road verification."""
    from backend.app.main import tickets_by_id, ws_manager

    ticket = tickets_by_id.get(ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket {ticket_id} not found",
        )

    watch = verification_service.get_watch_for_ticket(ticket_id)
    if not watch:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ticket {ticket_id} is not scheduled for verification (must be RESOLVED first)",
        )

    # Synthetic smooth surface observation at ticket coordinates
    now_obs_str = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    smooth_obs = {
        "observation_id": f"OBS-SMOOTH-{ticket_id.split('-')[-1]}-{req.bus_id}",
        "event_id": f"EVT-SMOOTH-{ticket_id.split('-')[-1]}-{req.bus_id}",
        "bus_id": req.bus_id,
        "event_type": "SMOOTH_ROAD",
        "timestamp": now_obs_str,
        "latitude": watch.latitude,
        "longitude": watch.longitude,
        "confidence": req.confidence,
        "evidence_image": req.evidence_image or "runtime/evidence/SMOOTH_SURFACE.jpg",
    }

    updated_watch, affected_ticket = verification_service.process_observation(smooth_obs, tickets_by_id)

    if affected_ticket:
        await ws_manager.broadcast({
            "type": "TICKET_STATUS_UPDATED",
            "data": affected_ticket,
        })

    return updated_watch or watch
