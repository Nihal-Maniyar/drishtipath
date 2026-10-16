"""Downstream Bus Hazard Advisory Service.

Handles hazard evaluation, corridor qualification, MQTT delivery, and idempotent
subscriber deduplication for the Beyonders Urban Intelligence platform.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from backend.app.schemas.advisory import HazardAdvisory

logger = logging.getLogger("urban_intelligence.advisory")


def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great circle distance between two WGS84 points in meters."""
    R = 6371000.0  # Earth radius in meters
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def compute_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate initial forward bearing from (lat1, lon1) to (lat2, lon2) in degrees [0, 360)."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)

    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
    theta = math.atan2(y, x)
    bearing = (math.degrees(theta) + 360.0) % 360.0
    return bearing


def is_bus_approaching(
    bus_lat: float,
    bus_lon: float,
    bus_heading: Optional[float],
    hazard_lat: float,
    hazard_lon: float,
    max_angle_diff: float = 90.0,
) -> bool:
    """Determine if a bus is approaching a hazard based on its heading.

    If bus heading is None or undefined, returns True if within corridor distance.
    Otherwise, computes bearing to hazard and verifies angle divergence <= max_angle_diff.
    """
    if bus_heading is None:
        return True

    bearing = compute_bearing(bus_lat, bus_lon, hazard_lat, hazard_lon)
    diff = abs(bus_heading - bearing) % 360.0
    if diff > 180.0:
        diff = 360.0 - diff

    return diff <= max_angle_diff


class AdvisoryService:
    """Manages creation, qualification, MQTT publishing, and retrieval of hazard advisories."""

    GLOBAL_TOPIC = "beyonders/advisories/v1"
    DEFAULT_CORRIDOR_METERS = 500.0

    def __init__(self, mqtt_client: Optional[Any] = None) -> None:
        self.mqtt_client = mqtt_client
        self._advisories: Dict[str, HazardAdvisory] = {}
        self._advisory_counter: int = 0
        self._published_history: List[Dict[str, Any]] = []

    def set_mqtt_client(self, client: Any) -> None:
        """Update or inject MQTT client instance."""
        self.mqtt_client = client

    def qualify_downstream_buses(
        self,
        hazard_lat: float,
        hazard_lon: float,
        route_id: str,
        fleet: Dict[str, Dict[str, Any]],
        source_bus_id: Optional[str] = None,
        corridor_window_meters: float = DEFAULT_CORRIDOR_METERS,
    ) -> List[str]:
        """Find active buses on the same route approaching the hazard within corridor_window_meters."""
        qualified_buses: List[str] = []

        for bus_id, bus_info in fleet.items():
            # Skip the reporting bus
            if source_bus_id and bus_id == source_bus_id:
                continue

            # Must be active/online
            if bus_info.get("status") == "OFFLINE":
                continue

            # Route relevance check
            bus_route = bus_info.get("route_id")
            if not bus_route or bus_route != route_id:
                continue

            bus_lat = bus_info.get("latitude")
            bus_lon = bus_info.get("longitude")
            if bus_lat is None or bus_lon is None:
                continue

            dist = haversine_distance_meters(bus_lat, bus_lon, hazard_lat, hazard_lon)
            if dist > corridor_window_meters:
                continue

            # Heading direction check
            bus_heading = bus_info.get("heading_degrees")
            if is_bus_approaching(bus_lat, bus_lon, bus_heading, hazard_lat, hazard_lon):
                qualified_buses.append(bus_id)

        return sorted(qualified_buses)

    def create_advisory(
        self,
        incident_id: str,
        source_bus_id: str,
        route_id: str,
        latitude: float,
        longitude: float,
        road_aligned_latitude: Optional[float] = None,
        road_aligned_longitude: Optional[float] = None,
        severity: str = "HIGH",
        event_type: str = "POTHOLE",
        corridor_window_meters: float = DEFAULT_CORRIDOR_METERS,
        target_buses: Optional[List[str]] = None,
        description: Optional[str] = None,
        confidence: Optional[float] = None,
        ttl_seconds: int = 3600,
    ) -> HazardAdvisory:
        """Construct and register a stable hazard advisory."""
        now = datetime.datetime.now(datetime.timezone.utc)
        expires = now + datetime.timedelta(seconds=ttl_seconds)

        now_str = now.isoformat().replace("+00:00", "Z")
        exp_str = expires.isoformat().replace("+00:00", "Z")

        year = now.strftime("%Y")
        self._advisory_counter += 1
        advisory_id = f"ADV-{year}-{self._advisory_counter:06d}"

        advisory = HazardAdvisory(
            advisory_id=advisory_id,
            incident_id=incident_id,
            source_bus_id=source_bus_id,
            route_id=route_id,
            latitude=round(latitude, 6),
            longitude=round(longitude, 6),
            road_aligned_latitude=round(road_aligned_latitude, 6) if road_aligned_latitude is not None else None,
            road_aligned_longitude=round(road_aligned_longitude, 6) if road_aligned_longitude is not None else None,
            severity=severity,
            event_type=event_type,
            issued_at=now_str,
            expires_at=exp_str,
            corridor_window_meters=corridor_window_meters,
            target_buses=target_buses or [],
            description=description or f"Hazard Advisory: Severe {event_type.lower()} on corridor {route_id}.",
            confidence=confidence,
        )

        self._advisories[advisory.advisory_id] = advisory
        self._published_history.append(advisory.model_dump())
        logger.info(
            "Created advisory %s for incident %s targeting %d bus(es)",
            advisory.advisory_id,
            incident_id,
            len(advisory.target_buses),
        )
        return advisory

    def publish_advisory(self, advisory: HazardAdvisory) -> bool:
        """Publish advisory to MQTT topics (global and route-specific) with QoS 1."""
        payload_str = json.dumps(advisory.model_dump())
        route_topic = f"{self.GLOBAL_TOPIC}/{advisory.route_id}"

        published = False
        if self.mqtt_client:
            try:
                # Publish to global advisory topic
                self.mqtt_client.publish(self.GLOBAL_TOPIC, payload_str, qos=1)
                # Publish to route-specific advisory topic
                self.mqtt_client.publish(route_topic, payload_str, qos=1)
                published = True
                logger.info("Published advisory %s to %s and %s", advisory.advisory_id, self.GLOBAL_TOPIC, route_topic)
            except Exception as e:
                logger.warning("Failed to publish advisory %s over MQTT: %s", advisory.advisory_id, e)

        return published

    def evaluate_and_publish(
        self,
        incident: Dict[str, Any],
        fleet: Dict[str, Dict[str, Any]],
        source_bus_id: Optional[str] = None,
        route_id: Optional[str] = None,
        corridor_window_meters: float = DEFAULT_CORRIDOR_METERS,
        ttl_seconds: int = 3600,
    ) -> Optional[HazardAdvisory]:
        """Evaluate an incident and publish an advisory if severity qualifies."""
        severity = incident.get("severity", "MEDIUM")
        event_type = incident.get("event_type", "POTHOLE")
        status = incident.get("status", "CANDIDATE")

        # Qualify if severe defect or verified high confidence
        is_severe_pothole = (
            event_type == "POTHOLE"
            and (severity in {"HIGH", "CRITICAL"} or status == "VERIFIED" or float(incident.get("confidence", 0)) >= 0.90)
        )

        if not is_severe_pothole:
            return None

        lat = float(incident.get("road_aligned_latitude") or incident["latitude"])
        lon = float(incident.get("road_aligned_longitude") or incident["longitude"])

        chosen_route = route_id or incident.get("route_id") or "ROUTE-PUNE-FC"
        bus_id = source_bus_id or (incident.get("buses") or ["BUS-UNKNOWN"])[0]

        target_buses = self.qualify_downstream_buses(
            hazard_lat=lat,
            hazard_lon=lon,
            route_id=chosen_route,
            fleet=fleet,
            source_bus_id=bus_id,
            corridor_window_meters=corridor_window_meters,
        )

        advisory = self.create_advisory(
            incident_id=incident.get("incident_id", "INC-000001"),
            source_bus_id=bus_id,
            route_id=chosen_route,
            latitude=lat,
            longitude=lon,
            road_aligned_latitude=incident.get("road_aligned_latitude"),
            road_aligned_longitude=incident.get("road_aligned_longitude"),
            severity=severity,
            event_type=event_type,
            corridor_window_meters=corridor_window_meters,
            target_buses=target_buses,
            description=incident.get("description"),
            confidence=incident.get("confidence"),
            ttl_seconds=ttl_seconds,
        )

        self.publish_advisory(advisory)
        return advisory

    def get_advisories(self, active_only: bool = True) -> List[HazardAdvisory]:
        """Return all registered advisories, optionally filtering out expired ones."""
        now = datetime.datetime.now(datetime.timezone.utc)
        res = []
        for adv in self._advisories.values():
            if active_only and adv.is_expired(now):
                continue
            res.append(adv)
        return sorted(res, key=lambda a: a.issued_at, reverse=True)

    def get_advisory_by_id(self, advisory_id: str) -> Optional[HazardAdvisory]:
        """Get single advisory by ID."""
        return self._advisories.get(advisory_id)

    def get_advisories_for_bus(self, bus_id: str, fleet: Dict[str, Dict[str, Any]]) -> List[HazardAdvisory]:
        """Get currently active advisories applicable to a specific bus."""
        bus = fleet.get(bus_id)
        if not bus:
            return []

        route_id = bus.get("route_id")
        bus_lat = bus.get("latitude")
        bus_lon = bus.get("longitude")
        bus_heading = bus.get("heading_degrees")

        matching: List[HazardAdvisory] = []
        now = datetime.datetime.now(datetime.timezone.utc)

        for adv in self.get_advisories(active_only=True):
            if adv.route_id != route_id:
                continue

            if bus_lat is not None and bus_lon is not None:
                dist = haversine_distance_meters(bus_lat, bus_lon, adv.latitude, adv.longitude)
                if dist <= adv.corridor_window_meters:
                    if is_bus_approaching(bus_lat, bus_lon, bus_heading, adv.latitude, adv.longitude):
                        matching.append(adv)
            elif bus_id in adv.target_buses:
                matching.append(adv)

        return matching


class AdvisorySubscriber:
    """Subscriber-side handler ensuring idempotent processing of QoS 1 MQTT advisories."""

    def __init__(self, bus_id: Optional[str] = None, route_id: Optional[str] = None) -> None:
        self.bus_id = bus_id
        self.route_id = route_id
        self.seen_advisories: Set[str] = set()
        self.received_advisories: List[HazardAdvisory] = []

    def process_message_payload(self, raw_payload: str | bytes | dict) -> Optional[HazardAdvisory]:
        """Process an incoming MQTT message payload with deduplication and expiration validation.

        Returns:
            HazardAdvisory if newly received and valid; None if duplicate, expired, or invalid.
        """
        try:
            if isinstance(raw_payload, (str, bytes)):
                data = json.loads(raw_payload)
            elif isinstance(raw_payload, dict):
                data = raw_payload
            else:
                return None

            advisory = HazardAdvisory.model_validate(data)

            # 1. Deduplication check (idempotency for QoS 1 delivery)
            if advisory.advisory_id in self.seen_advisories:
                logger.debug("Dropped duplicate advisory %s", advisory.advisory_id)
                return None

            # 2. Expiration check
            if advisory.is_expired():
                logger.info("Dropped expired advisory %s", advisory.advisory_id)
                return None

            # 3. Route check (if subscriber is filtered)
            if self.route_id and advisory.route_id != self.route_id:
                logger.debug("Advisory %s is for route %s, ignoring", advisory.advisory_id, advisory.route_id)
                return None

            # 4. Target bus check (if bus_id is set and target_buses is specified)
            if self.bus_id and advisory.target_buses and self.bus_id not in advisory.target_buses:
                logger.debug("Bus %s not in target_buses for %s", self.bus_id, advisory.advisory_id)
                # Still relevant if on same route, but note target list
                pass

            self.seen_advisories.add(advisory.advisory_id)
            self.received_advisories.append(advisory)
            return advisory

        except Exception as e:
            logger.error("Failed to parse incoming advisory payload: %s", e)
            return None


# Global singleton instance for backend
advisory_service = AdvisoryService()
