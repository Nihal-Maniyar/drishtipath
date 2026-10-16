---
name: frontend-design
description: Guidance for distinctive, intentional visual design when building new UI or reshaping an existing one. Helps with aesthetic direction, typography, and making choices that don't read as templated defaults.
license: Anthropic Skills Repository (https://github.com/anthropics/skills/tree/main/skills/frontend-design)
---

# Frontend Design

Approach this as the design lead at a design studio known for giving every client a distinct visual identity that is not mistaken for anyone else's. This client has already rejected proposals that felt cliché or templated, and is paying for a distinctive point of view: make deliberate, opinionated choices about palette, typography, and layout that are specific to this brief, and take aesthetic risk if justified.

## Ground your designs in the subject matter

If the brief does not identify what the product or subject matter is, identify it yourself before designing, and confirm with the client. You can come up with one concrete subject, the design's audience, and the design's primary job, as a proposal. In FleetFusion, the subject matter is municipal transit intelligence and real-time civic sensing for Indian urban corridors (Pune, Maharashtra). Build with the brief's real content and subject matter throughout.

## Design principles

For web designs, the hero is the first thing viewers will see. Open with the most characteristic thing in the subject's world, in the form that is most appropriate: live GIS transit tracking, real-time edge defect feeds, and operational road health indicators. Be deliberate with your choice: a big number with a small label, supporting stats, and a gradient accent is the default treatment, so only use it if that's truly the best option.

Typography carries the personality of the page. You don't need a different typeface for display or headline text and body content: use one family or two, and if two, make them clearly distinct:
- Display: Space Grotesk / Inter Display
- Body: Inter
- Monospace: JetBrains Mono (for coordinates, bus IDs, and telemetry metrics)

Choose your typefaces deliberately, not the default families you would reach for on any other project, and set a clear type scale following the default guidance of The Elements of Typographic Style with intentional weights, widths, and spacing. When type is used as a headline or visual element, use the type treatment itself as an active part of the design, not a neutral delivery vehicle for the content.

Default to line lengths of less than 80 characters. Serif typefaces can have slightly longer line lengths; give serif body text slightly more line-height than a sans-serif.

Avoid these default typographic treatments; they are the commonest tells of a generated page:
- Accenting just a single word or phrase in a headline, like putting one word in italic/bold or a different color.
- Using all caps for labels.
- Adding unnecessary typographic labels above content.

Visual structure is information. Structural devices like outlines, borders, numbering, eyebrows, dividers, labels, etc., encode useful information about the content rather than decorate it:
- Outlines, borders, badges, and status pills encode useful telemetry state rather than decorate.
- Use distinct, authentic color coding for severities and defect types:
  - Pothole: Crimson/Burgundy (`#B94A3B`)
  - Garbage / Solid Waste: Forest Green (`#2E7D53`)
  - Traffic Obstruction: Amber/Ochre (`#CC7A2E`)
  - Pedestrian Risk: Deep Purple (`#5B3FA8`)

Use non-user-triggered motion sparingly and deliberately, only to draw attention. A single orchestrated moment — one page-load sequence or one reveal — lands better than scattered effects; fade-and-slide-up entrances on each section and hover transitions on every card are the generic default and read as AI-generated. Motion that answers a person's action (opening, expanding, confirming) is welcome when it shows what changed.

## Process: plan, review against the brief, build, critique

Work in two passes. First, brainstorm a short design plan based on the client's design brief: create a compact token system with color, type, layout, and principles.
- Color: describe the core base palette as 4–6 named hex values.
- Type: the typefaces and their roles.
- Layout: a layout concept, using one-sentence prose descriptions and ASCII wireframes to ideate and compare.
- Principles: the high-level guidance for what makes this page unique.

Then review that plan against the brief before building. Only after you've confirmed the relative uniqueness of your design plan should you start to write the code.

## Restraint and self-critique

Spend your boldness in one place: the live interactive map and defect inspection drawer. Keep surrounding chrome quiet and disciplined. Cut any decoration that does not serve the brief. Build to a high quality floor: responsive down to tablet, visible keyboard focus, reduced motion respected, visually accessible, and harmonious color palettes across light and dark modes.

## Writing in design

Words appear in a design for one reason: to make it easier to understand and use. They are design content, not decoration:
- Write from the end user's perspective. Name things by what users will understand in simple language, not by how the system is built.
- Use active voice as default. A CTA says exactly what happens when it is used: "Save changes," not "Submit."
- Treat failure and emptiness as moments for direction, not mood. Explain what went wrong and how to fix it, in the interface's voice rather than a person's.
- Keep the tone conversational: plain verbs, sentence case, no filler.
