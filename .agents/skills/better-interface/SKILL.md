---
name: better-interface
description: Combines all of the `better-*` skills into a single review across accessibility, layout, writing, typography, color and UI polish.
license: Jakub Krehel Skills (https://github.com/jakubkrehel/skills)
---

# Cross-discipline Interface Review

This skill runs a cross-discipline review. It routes the interface across accessibility, layout, writing, typography, color, and UI polish, collecting evidence and consolidating one ranked verdict.

## Evidence, not taste

Press hard on the escalation triggers and leave deliberate project choices alone. A trigger is a failure whatever the style guide says. A density, radius or voice you merely disagree with is not a finding.
The bar for reporting is evidence. The bar for `Approve` is that you inspected what you claim to have inspected.

## Core review principles

1. **Resolve the scope first**: Infer the screen, flow, feature, or repository scope from the request. Cover empty, loading, error, and narrow-width states.
2. **Recon before judgment**: Identify the framework, styling system, component library, design tokens, and supported viewports. Write fixes in the project's own idiom.
3. **Domain review order**:
   1. Accessibility: Keyboard navigation, focus rings, ARIA, screen reader compatibility, and WCAG contrast.
   2. Layout: Reading order, structural grouping, adaptive breakpoints, and scroll containment.
   3. Writing: Active CTAs, clear error messages, empty-state guidance, and consistent terminology.
   4. Typography: Optical hierarchy, tabular numbers for telemetry, deliberate type scale, line lengths < 80ch.
   5. Colors: Color contrast, dark mode parity, semantic status indicators (red/amber/green/purple).
   6. UI Polish: Border radii mathematical concentricity, optical alignment, layered elevation shadows, micro-interactions.
4. **Require evidence**: Every finding must cite `path/to/file:line` with exact fix snippets.
