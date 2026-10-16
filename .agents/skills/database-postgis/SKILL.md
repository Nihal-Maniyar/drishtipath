---
name: database-postgis
description: PostGIS spatial database engineering, geometry primitives (EPSG:4326), GIST spatial indexing, ST_DWithin corridor clustering, and safe Alembic migrations.
sources:
  - https://postgis.net/documentation/
  - database/README.md
  - docs/database.md
---

# PostGIS & Spatial Database Engineering

Guidelines for designing, querying, and migrating spatial models within the FleetFusion PostgreSQL/PostGIS database layer.

## 1. Spatial Columns & Coordinate Standards
- All geospatial coordinates MUST use WGS84 decimal degrees (`EPSG:4326`).
- Define spatial columns using GeoAlchemy2:
  `geometry(Point, 4326)` for vehicle locations, observations, incidents, and tickets.
  `geometry(LineString, 4326)` for transit route corridors.
- Always preserve raw satellite coordinates separately from map-matched road coordinates (`road_aligned_latitude`, `road_aligned_longitude`).

## 2. Spatial Indexing & Proximity Queries
- Maintain `GIST` indexes on all spatial geometry columns:
  `CREATE INDEX idx_incidents_location ON incidents USING GIST (location);`
- Use `ST_DWithin` (with geography casting or metric projection) for spatial proximity clustering:
  `ST_DWithin(obs1.location::geography, obs2.location::geography, 25.0)`
- Keep spatial queries efficient and bounded by incorporating temporal filters ($\le 2\text{ hours}$) alongside spatial radius checks.

## 3. Alembic Migration Hygiene
- Never modify existing migration versions once merged into `develop`.
- Always generate auto-migrations using:
  `alembic revision --autogenerate -m "descriptive_migration_name"`
- Test both `alembic upgrade head` and `alembic downgrade -1` in isolated test containers before committing.
- Ensure all enum additions and foreign key constraints are backward-compatible.
