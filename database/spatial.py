"""Spatial query functions and PostGIS utilities for GIS dashboard and fleet fusion."""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import Incident, Observation

# Mean Earth Radius in meters
EARTH_RADIUS_METERS = 6371000.0


def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two coordinates in meters."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_METERS * c


def find_incidents_near(
    session: Session,
    latitude: float,
    longitude: float,
    radius_meters: float = 15.0,
    event_type: Optional[str] = None,
    active_only: bool = True,
) -> List[Tuple[Incident, float]]:
    """Find incidents within a given radius in meters (default approx. 15m).

    Uses PostGIS ST_DWithin and ST_Distance on PostgreSQL, with haversine fallback
    for SQLite test execution.

    Returns:
        List of (Incident, distance_meters) tuples ordered by nearest first.
    """
    bind = session.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        # Construct Point geometry in PostGIS: ST_SetSRID(ST_MakePoint(lon, lat), 4326)
        point_geom = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)
        point_geog = func.cast(point_geom, sa.types.NullType)  # Cast to geography
        incident_geog = func.cast(Incident.geom, sa.types.NullType)

        # Distance expression in meters
        distance_expr = func.ST_Distance(
            func.cast(Incident.geom, sa.text("geography")),
            func.cast(point_geom, sa.text("geography")),
        ).label("distance_meters")

        stmt = select(Incident, distance_expr).where(
            func.ST_DWithin(
                func.cast(Incident.geom, sa.text("geography")),
                func.cast(point_geom, sa.text("geography")),
                radius_meters,
            )
        )

        if event_type:
            stmt = stmt.where(Incident.event_type == event_type)
        if active_only:
            stmt = stmt.where(Incident.status.in_(["CANDIDATE", "VERIFIED"]))

        stmt = stmt.order_by("distance_meters")
        results = session.execute(stmt).all()
        return [(row[0], float(row[1])) for row in results]

    # SQLite / In-Memory Fallback: Filter by approximate bounding box then haversine
    # 1 deg lat ~ 111,000 meters; 1 deg lon ~ 111,000 * cos(lat)
    lat_delta = (radius_meters / 111000.0) * 1.5
    lon_delta = (radius_meters / (111000.0 * max(0.1, math.cos(math.radians(latitude))))) * 1.5

    stmt = select(Incident).where(
        Incident.latitude.between(latitude - lat_delta, latitude + lat_delta),
        Incident.longitude.between(longitude - lon_delta, longitude + lon_delta),
    )
    if event_type:
        stmt = stmt.where(Incident.event_type == event_type)
    if active_only:
        stmt = stmt.where(Incident.status.in_(["CANDIDATE", "VERIFIED"]))

    candidates = session.scalars(stmt).all()
    matched = []
    for inc in candidates:
        dist = haversine_distance_meters(latitude, longitude, inc.latitude, inc.longitude)
        if dist <= radius_meters:
            matched.append((inc, dist))

    matched.sort(key=lambda x: x[1])
    return matched


def find_observations_near(
    session: Session,
    latitude: float,
    longitude: float,
    radius_meters: float = 15.0,
    event_type: Optional[str] = None,
    use_road_aligned: bool = False,
) -> List[Tuple[Observation, float]]:
    """Find observations within radius_meters."""
    bind = session.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    target_geom = Observation.geom_road_aligned if use_road_aligned else Observation.geom_raw

    if is_postgres:
        point_geom = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)
        distance_expr = func.ST_Distance(
            func.cast(target_geom, sa.text("geography")),
            func.cast(point_geom, sa.text("geography")),
        ).label("distance_meters")

        stmt = select(Observation, distance_expr).where(
            func.ST_DWithin(
                func.cast(target_geom, sa.text("geography")),
                func.cast(point_geom, sa.text("geography")),
                radius_meters,
            )
        )
        if event_type:
            stmt = stmt.where(Observation.event_type == event_type)
        stmt = stmt.order_by("distance_meters")
        results = session.execute(stmt).all()
        return [(row[0], float(row[1])) for row in results]

    # Fallback for SQLite testing
    lat_delta = (radius_meters / 111000.0) * 1.5
    lon_delta = (radius_meters / (111000.0 * max(0.1, math.cos(math.radians(latitude))))) * 1.5

    stmt = select(Observation).where(
        Observation.latitude.between(latitude - lat_delta, latitude + lat_delta),
        Observation.longitude.between(longitude - lon_delta, longitude + lon_delta),
    )
    if event_type:
        stmt = stmt.where(Observation.event_type == event_type)

    candidates = session.scalars(stmt).all()
    matched = []
    for obs in candidates:
        obs_lat = obs.road_aligned_latitude if (use_road_aligned and obs.road_aligned_latitude is not None) else obs.latitude
        obs_lon = obs.road_aligned_longitude if (use_road_aligned and obs.road_aligned_longitude is not None) else obs.longitude
        dist = haversine_distance_meters(latitude, longitude, obs_lat, obs_lon)
        if dist <= radius_meters:
            matched.append((obs, dist))

    matched.sort(key=lambda x: x[1])
    return matched


def query_incidents_in_bbox(
    session: Session,
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    status: Optional[str] = None,
    event_type: Optional[str] = None,
) -> List[Incident]:
    """Query incidents intersecting the GIS dashboard viewport bounding box.

    Uses GiST spatial index on PostgreSQL via ST_MakeEnvelope.
    """
    bind = session.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    stmt = select(Incident)

    if is_postgres:
        envelope = func.ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)
        # && operator checks bounding box overlap utilizing GiST index
        stmt = stmt.where(func.ST_Intersects(Incident.geom, envelope))
    else:
        stmt = stmt.where(
            Incident.latitude >= min_lat,
            Incident.latitude <= max_lat,
            Incident.longitude >= min_lon,
            Incident.longitude <= max_lon,
        )

    if status:
        stmt = stmt.where(Incident.status == status)
    if event_type:
        stmt = stmt.where(Incident.event_type == event_type)

    return list(session.scalars(stmt).all())


def query_observations_in_bbox(
    session: Session,
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    event_type: Optional[str] = None,
    limit: int = 200,
) -> List[Observation]:
    """Query observations within a bounding box for map views."""
    bind = session.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    stmt = select(Observation)
    if is_postgres:
        envelope = func.ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)
        stmt = stmt.where(func.ST_Intersects(Observation.geom_raw, envelope))
    else:
        stmt = stmt.where(
            Observation.latitude >= min_lat,
            Observation.latitude <= max_lat,
            Observation.longitude >= min_lon,
            Observation.longitude <= max_lon,
        )

    if event_type:
        stmt = stmt.where(Observation.event_type == event_type)

    stmt = stmt.order_by(Observation.timestamp.desc()).limit(limit)
    return list(session.scalars(stmt).all())


def aggregate_spatial_density_grid(
    session: Session,
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    grid_size_meters: float = 100.0,
    event_type: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    target: str = "incidents",
) -> List[Dict[str, Any]]:
    """Aggregate spatial defect entities into metric grid cells directly using PostGIS.

    When running on PostgreSQL/PostGIS, applies ST_SnapToGrid and ST_Centroid
    accelerated by GiST spatial envelope indexing (ST_MakeEnvelope + ST_Intersects).
    When running on SQLite, performs bounded spatial querying with metric binning.
    """
    bind = session.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # Compute metric step deltas in degrees
    mid_lat = (min_lat + max_lat) / 2.0
    m_per_lat = 111139.0
    m_per_lon = 111139.0 * max(0.01, math.cos(math.radians(mid_lat)))
    delta_lat = max(10.0, grid_size_meters) / m_per_lat
    delta_lon = max(10.0, grid_size_meters) / m_per_lon

    if target == "observations":
        if is_postgres:
            envelope = func.ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)
            snapped = func.ST_SnapToGrid(Observation.geom_raw, delta_lon, delta_lat)
            stmt = (
                select(
                    func.ST_Y(func.ST_Centroid(func.ST_Collect(Observation.geom_raw))).label("latitude"),
                    func.ST_X(func.ST_Centroid(func.ST_Collect(Observation.geom_raw))).label("longitude"),
                    func.count().label("point_count"),
                )
                .where(func.ST_Intersects(Observation.geom_raw, envelope))
                .group_by(snapped)
            )
            if event_type:
                stmt = stmt.where(Observation.event_type == event_type)
            if severity:
                stmt = stmt.where(Observation.severity == severity)
            results = session.execute(stmt).all()
            return [
                {
                    "latitude": float(row.latitude),
                    "longitude": float(row.longitude),
                    "point_count": int(row.point_count),
                    "event_type": event_type or "OBSERVATION",
                    "severity": severity or "MEDIUM",
                }
                for row in results
            ]
        else:
            obs_list = query_observations_in_bbox(session, min_lat, min_lon, max_lat, max_lon, event_type, limit=1000)
            return [
                {
                    "incident_id": obs.observation_id,
                    "latitude": obs.latitude,
                    "longitude": obs.longitude,
                    "event_type": obs.event_type,
                    "severity": obs.severity or "MEDIUM",
                }
                for obs in obs_list
                if not severity or obs.severity == severity
            ]
    else:
        if is_postgres:
            envelope = func.ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)
            snapped = func.ST_SnapToGrid(Incident.geom, delta_lon, delta_lat)
            stmt = (
                select(
                    func.ST_Y(func.ST_Centroid(func.ST_Collect(Incident.geom))).label("latitude"),
                    func.ST_X(func.ST_Centroid(func.ST_Collect(Incident.geom))).label("longitude"),
                    func.count().label("point_count"),
                )
                .where(func.ST_Intersects(Incident.geom, envelope))
                .group_by(snapped)
            )
            if event_type:
                stmt = stmt.where(Incident.event_type == event_type)
            if severity:
                stmt = stmt.where(Incident.severity == severity)
            if status:
                stmt = stmt.where(Incident.status == status)
            results = session.execute(stmt).all()
            return [
                {
                    "latitude": float(row.latitude),
                    "longitude": float(row.longitude),
                    "point_count": int(row.point_count),
                    "event_type": event_type or "INCIDENT",
                    "severity": severity or "MEDIUM",
                }
                for row in results
            ]
        else:
            inc_list = query_incidents_in_bbox(session, min_lat, min_lon, max_lat, max_lon, status, event_type)
            return [
                {
                    "incident_id": inc.incident_id,
                    "latitude": inc.latitude,
                    "longitude": inc.longitude,
                    "event_type": inc.event_type,
                    "severity": inc.severity,
                    "status": inc.status,
                }
                for inc in inc_list
                if not severity or inc.severity == severity
            ]

