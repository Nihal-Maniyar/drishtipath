---
name: gis-leaflet
description: Real-time transit GIS operations, Leaflet 1.9.4 map engine integration, road corridor snapping, GeoJSON streaming, and satellite drift vector visualization.
sources:
  - https://leafletjs.com/reference.html
  - dashboard/README.md
  - docs/api-contract.md
---

# Real-Time GIS & Leaflet Engineering

Engineering guidelines for the interactive GIS dashboard, map projection, road matching, and visual telemetry rendering.

## 1. Leaflet Map Lifecycle & Theme Synchronization
- Initialize Leaflet once inside the DOM container (`#leafletMap`).
- Retain `mapCenter` and `mapZoom` across route transitions to prevent jarring viewport resets.
- Always call `leafletMap.invalidateSize()` after sidebar transitions (260ms delay) to prevent gray tile rendering bugs.
- Support dynamic dark/light mode switching by updating tile layer URLs or CSS container filters.

## 2. Road Alignment & Algorithmic Snapping
- Never allow bus markers or defect pins to float off-road. Snap raw GPS coordinates (`WGS84 / EPSG:4326`) to the nearest corridor polyline segment:
  - Pune Corridors: `ROUTE-PUNE-FC`, `ROUTE-PUNE-JM`, `ROUTE-PUNE-KARVE`, `ROUTE-PUNE-SHIVAJI`.
- If a preferred corridor is designated for a vehicle (e.g., `BUS-001` on FC Road), constrain snapping strictly to that corridor to avoid snapping across adjacent one-way parallel streets.

## 3. GNSS Satellite Drift Vectors
- When raw satellite coordinates diverge from the map-matched road position by $>5\text{m}$, draw a dashed vector polyline connecting the raw satellite fix to the road marker.
- This visually proves map matching and spatial alignment to municipal operators and evaluators.

## 4. Marker Rendering & Performance
- Maintain markers in memory dictionary indexed by ID (`busMarkers[bus_id]`, `incidentMarkers[incident_id]`).
- Mutate existing markers in-place via `.setLatLng()` and `.setIcon()` rather than destroying and recreating Leaflet DOM nodes on every WebSocket message.
- Use heading degrees (`b.heading_degrees`) to rotate the bus directional icon in real-time CSS:
  `transform: rotate(${heading}deg);`
