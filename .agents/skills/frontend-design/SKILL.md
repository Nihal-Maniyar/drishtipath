---
name: frontend-design
description: Guidance for distinctive, intentional visual design when building new UI or reshaping an existing one. Helps with aesthetic direction, typography, and making choices that don't read as templated defaults.
license: Anthropic Skills Repository (https://github.com/anthropics/skills/tree/main/skills/frontend-design)
---

# Frontend Design

Approach this as the design lead at a design studio known for giving every client a distinct visual identity that is not mistaken for anyone else's. This client has already rejected proposals that felt cliché or templated, and is paying for a distinctive point of view: make deliberate, opinionated choices about palette, typography, and layout that are specific to this brief, and take aesthetic risk if justified.

## Ground your designs in the subject matter

If the brief does not identify what the product or subject matter is, identify it yourself before designing, and confirm with the client. You can come up with one concrete subject, the design's audience, and the design's primary job, as a proposal. In FleetFusion, the subject matter is municipal transit intelligence and real-time civic sensing for Indian urban corridors (Pune, Maharashtra).

## Design principles

For web designs, the hero is the first thing viewers will see. Open with the most characteristic thing in the subject's world: live GIS transit tracking, real-time edge defect feeds, and operational road health indicators.

Typography carries the personality of the page. Use deliberate font families:
- Display: Space Grotesk / Inter Display
- Body: Inter
- Monospace: JetBrains Mono (for coordinates, bus IDs, and telemetry metrics)

Visual structure is information:
- Outlines, borders, badges, and status pills encode useful telemetry state rather than decorate.
- Use distinct, authentic color coding for severities and defect types:
  - Pothole: Crimson/Burgundy (`#B94A3B`)
  - Garbage / Solid Waste: Forest Green (`#2E7D53`)
  - Traffic Obstruction: Amber/Ochre (`#CC7A2E`)
  - Pedestrian Risk: Deep Purple (`#5B3FA8`)

## Restraint and self-critique

Spend your boldness in one place: the live interactive map and defect inspection drawer. Keep surrounding chrome quiet and disciplined. Build to a high quality floor: responsive down to tablet, visible keyboard focus, reduced motion respected, visually accessible, and harmonious color palettes across light and dark modes.
