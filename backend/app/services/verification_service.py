"""Automated Repair Verification Engine.

Implements the closed-loop ticket verification lifecycle defined in docs/api-contract.md:
- Schedules durable verification watches for RESOLVED civic tickets.
- Validates subsequent bus observations within a 25-meter spatial threshold.
- Advances tickets to VERIFIED_RESOLVED when smooth surface passes are corroborated.
- Reopens tickets to REPORTED with FAILED_REPAIR if persistent defect signatures reoccur.
- Transitions to EXPIRED_UNVERIFIED if the 48-hour window expires without bus passes.
- Provides restart-safety via durable JSON persistence and prevents older events from overwriting newer state.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.schemas.verification import (
    VerificationState,
    VerificationWatchSchema,
)

logger = logging.getLogger("urban_intelligence.verification")

SPATIAL_THRESHOLD_METERS = 25.0
DEFAULT_WINDOW_HOURS = 48
MIN_SMOOTH_OBSERVATIONS = 2
HIGH_CONFIDENCE_INSTANT_PASS = 0.95
STORAGE_FILE = Path("runtime/verification_watches.json")


def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great circle distance between two points in meters."""
    R = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def parse_utc_dt(ts: Any) -> datetime.datetime:
    """Parse UTC ISO string or datetime to timezone-aware datetime."""
    if isinstance(ts, datetime.datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=datetime.timezone.utc)
        return ts
    if isinstance(ts, str):
        cleaned = ts.replace("Z", "+00:00")
        try:
            dt = datetime.datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt
        except Exception:
            pass
    return datetime.datetime.now(datetime.timezone.utc)


def iso_utc_str(dt: Optional[datetime.datetime] = None) -> str:
    """Format datetime as UTC ISO 8601 trailing Z string."""
    now = dt or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return now.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


class VerificationService:
    """Closed-loop repair verification service."""

    def __init__(self, storage_path: Path = STORAGE_FILE) -> None:
        self.storage_path = storage_path
        self._watches: Dict[str, VerificationWatchSchema] = {}
        self._ticket_to_watch: Dict[str, str] = {}
        self._seen_observations: Set[str] = set()
        self._counter: int = 0
        self.load_from_disk()

    def schedule_watch(
        self,
        ticket: Dict[str, Any],
        window_hours: int = DEFAULT_WINDOW_HOURS,
    ) -> VerificationWatchSchema:
        """Schedule a verification watch for a RESOLVED ticket."""
        ticket_id = ticket["ticket_id"]

        # If a watch already exists and is active, return existing
        existing_watch_id = self._ticket_to_watch.get(ticket_id)
        if existing_watch_id and existing_watch_id in self._watches:
            existing = self._watches[existing_watch_id]
            if existing.state == VerificationState.WATCHING:
                return existing

        now = datetime.datetime.now(datetime.timezone.utc)
        expires = now + datetime.timedelta(hours=window_hours)
        year = now.strftime("%Y")

        self._counter += 1
        watch_id = f"VW-{year}-{self._counter:06d}"

        lat = float(ticket.get("road_aligned_latitude") or ticket["latitude"])
        lon = float(ticket.get("road_aligned_longitude") or ticket["longitude"])

        watch = VerificationWatchSchema(
            watch_id=watch_id,
            ticket_id=ticket_id,
            scheduled_at=iso_utc_str(now),
            expires_at=iso_utc_str(expires),
            state=VerificationState.WATCHING,
            latitude=lat,
            longitude=lon,
            required_smooth_observations=MIN_SMOOTH_OBSERVATIONS,
            smooth_observations_count=0,
            defect_observations_count=0,
            smooth_buses=[],
            defect_buses=[],
            notes=f"Scheduled {window_hours}h closed-loop verification watch.",
        )

        self._watches[watch_id] = watch
        self._ticket_to_watch[ticket_id] = watch_id

        ticket["verification_status"] = VerificationState.WATCHING.value
        ticket["verification_watch_id"] = watch_id

        self.save_to_disk()
        logger.info("Scheduled verification watch %s for ticket %s", watch_id, ticket_id)
        return watch

    def process_observation(
        self,
        observation: Dict[str, Any],
        tickets: Dict[str, Dict[str, Any]],
    ) -> Tuple[Optional[VerificationWatchSchema], Optional[Dict[str, Any]]]:
        """Evaluate an incoming observation against active verification watches.

        Returns:
            Tuple of (updated_watch, affected_ticket) if an active watch was affected, else (None, None).
        """
        obs_id = observation.get("observation_id", observation.get("event_id"))
        if obs_id and obs_id in self._seen_observations:
            return None, None

        obs_lat = float(observation.get("road_aligned_latitude") or observation["latitude"])
        obs_lon = float(observation.get("road_aligned_longitude") or observation["longitude"])
        obs_time = parse_utc_dt(observation.get("timestamp"))
        bus_id = observation.get("bus_id", "BUS-UNKNOWN")
        event_type = observation.get("event_type", "POTHOLE")
        confidence = float(observation.get("confidence", 0.90))

        now_iso = iso_utc_str()

        for watch in list(self._watches.values()):
            if watch.state != VerificationState.WATCHING:
                continue

            # Check if watch is expired
            if watch.is_expired():
                watch.state = VerificationState.EXPIRED_UNVERIFIED
                watch.completed_at = now_iso
                watch.notes = "Verification window lapsed without sufficient bus passage."
                t = tickets.get(watch.ticket_id)
                if t:
                    t["verification_status"] = VerificationState.EXPIRED_UNVERIFIED.value
                self.save_to_disk()
                continue

            # Spatial distance check (< 25m)
            dist = haversine_distance_meters(obs_lat, obs_lon, watch.latitude, watch.longitude)
            if dist > SPATIAL_THRESHOLD_METERS:
                continue

            # Temporal safety check: older events cannot overwrite newer ticket resolution state!
            ticket = tickets.get(watch.ticket_id)
            if not ticket:
                continue

            ticket_scheduled_dt = parse_utc_dt(watch.scheduled_at)
            if obs_time < ticket_scheduled_dt:
                logger.info(
                    "Ignored older event %s (ts=%s) against newer ticket resolution (ts=%s)",
                    obs_id,
                    observation.get("timestamp"),
                    watch.scheduled_at,
                )
                continue

            if obs_id:
                self._seen_observations.add(obs_id)

            watch.last_checked_at = now_iso

            # Check if this observation indicates a persistent defect or a smooth surface
            is_defect = (
                event_type == "POTHOLE"
                and confidence >= 0.80
            )

            if is_defect:
                # Defect detected! Reopen ticket -> FAILED_REPAIR
                watch.defect_observations_count += 1
                if bus_id not in watch.defect_buses:
                    watch.defect_buses.append(bus_id)

                watch.state = VerificationState.FAILED_REPAIR
                watch.completed_at = now_iso
                watch.notes = f"FAILED_REPAIR: Persistent defect detected by {bus_id} with confidence {confidence:.2f}."

                # Reopen ticket to REPORTED
                ticket["status"] = "REPORTED"
                ticket["verification_status"] = VerificationState.FAILED_REPAIR.value
                ticket["updated_at"] = now_iso

                # Update timeline
                timeline = ticket.get("timeline", [])
                if isinstance(timeline, list):
                    for step in timeline:
                        if step.get("label") == "Resolved":
                            step["done"] = False
                            step["time"] = "Reopened (Failed Repair)"
                    timeline.append({
                        "label": "Failed Repair Reopened",
                        "done": True,
                        "time": now_iso,
                    })

                self.save_to_disk()
                logger.warning(
                    "Ticket %s failed repair verification! Reopened to REPORTED by %s",
                    watch.ticket_id,
                    bus_id,
                )
                return watch, ticket

            else:
                # Smooth road passage observed!
                if bus_id not in watch.smooth_buses:
                    watch.smooth_buses.append(bus_id)
                watch.smooth_observations_count += 1

                # Check if threshold reached
                qualified = (
                    watch.smooth_observations_count >= watch.required_smooth_observations
                    or confidence >= HIGH_CONFIDENCE_INSTANT_PASS
                )

                if qualified:
                    watch.state = VerificationState.VERIFIED_RESOLVED
                    watch.completed_at = now_iso
                    watch.notes = (
                        f"VERIFIED_RESOLVED: Smooth road verified across {len(watch.smooth_buses)} "
                        f"independent fleet pass(es)."
                    )

                    ticket["status"] = "RESOLVED"
                    ticket["verification_status"] = VerificationState.VERIFIED_RESOLVED.value
                    ticket["updated_at"] = now_iso

                    # Update timeline
                    timeline = ticket.get("timeline", [])
                    if isinstance(timeline, list):
                        for step in timeline:
                            if step.get("label") == "Resolved":
                                step["done"] = True
                                step["time"] = now_iso
                        timeline.append({
                            "label": "Verified Resolved",
                            "done": True,
                            "time": now_iso,
                        })

                    self.save_to_disk()
                    logger.info("Ticket %s successfully advanced to VERIFIED_RESOLVED!", watch.ticket_id)
                    return watch, ticket

                self.save_to_disk()
                return watch, ticket

        return None, None

    def evaluate_expired_watches(
        self,
        tickets: Dict[str, Dict[str, Any]],
        current_time: Optional[datetime.datetime] = None,
    ) -> List[VerificationWatchSchema]:
        """Check for active watches whose window has lapsed without sufficient bus passage."""
        expired_list: List[VerificationWatchSchema] = []
        now = current_time or datetime.datetime.now(datetime.timezone.utc)
        now_iso = iso_utc_str(now)

        for watch in list(self._watches.values()):
            if watch.state == VerificationState.WATCHING and watch.is_expired(now):
                watch.state = VerificationState.EXPIRED_UNVERIFIED
                watch.completed_at = now_iso
                watch.notes = "Verification window lapsed with 0 qualifying bus passages."

                ticket = tickets.get(watch.ticket_id)
                if ticket:
                    ticket["verification_status"] = VerificationState.EXPIRED_UNVERIFIED.value
                    ticket["updated_at"] = now_iso

                expired_list.append(watch)

        if expired_list:
            self.save_to_disk()
            logger.info("Marked %d verification watch(es) as EXPIRED_UNVERIFIED", len(expired_list))

        return expired_list

    def get_watch_for_ticket(self, ticket_id: str) -> Optional[VerificationWatchSchema]:
        """Retrieve the verification watch for a ticket."""
        watch_id = self._ticket_to_watch.get(ticket_id)
        if watch_id:
            return self._watches.get(watch_id)
        return None

    def get_all_watches(self) -> List[VerificationWatchSchema]:
        """Retrieve all verification watches sorted by scheduled date descending."""
        return sorted(list(self._watches.values()), key=lambda w: w.scheduled_at, reverse=True)

    def save_to_disk(self) -> None:
        """Persist watches state to disk for restart-safety."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "counter": self._counter,
                "seen_observations": list(self._seen_observations),
                "watches": {wid: w.model_dump() for wid, w in self._watches.items()},
                "ticket_to_watch": self._ticket_to_watch,
            }
            self.storage_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug("Failed saving verification watches to disk: %s", e)

    def load_from_disk(self) -> None:
        """Restore watches state from disk if present."""
        if not self.storage_path.is_file():
            return
        try:
            content = self.storage_path.read_text(encoding="utf-8")
            data = json.loads(content)
            self._counter = data.get("counter", 0)
            self._seen_observations = set(data.get("seen_observations", []))
            self._ticket_to_watch = data.get("ticket_to_watch", {})
            self._watches = {
                wid: VerificationWatchSchema.model_validate(wdata)
                for wid, wdata in data.get("watches", {}).items()
            }
            logger.info("Restored %d verification watches from %s", len(self._watches), self.storage_path)
        except Exception as e:
            logger.warning("Error reading verification watches file %s: %s", self.storage_path, e)

    def reset_for_tests(self) -> None:
        """Reset in-memory state and remove disk file for isolated testing."""
        self._watches.clear()
        self._ticket_to_watch.clear()
        self._seen_observations.clear()
        self._counter = 0
        if self.storage_path.is_file():
            try:
                self.storage_path.unlink()
            except Exception:
                pass


# Global singleton instance
verification_service = VerificationService()
