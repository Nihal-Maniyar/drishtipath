---
name: better-ui
description: Polishes the surfaces, icons and motion in your project with exact values for border radius, optical alignment, shadows, icon states and animation.
license: Jakub Krehel Skills (https://github.com/jakubkrehel/skills)
---

# UI Polish

This skill holds the visual polish for surfaces, icons, and motion, with exact values each detail takes. It applies once the underlying interaction is sound.

## Exact Values, Optional Polish

1. **Outer radius equals inner radius plus padding**:
   Where nested surfaces share a visible, even inset: `R_outer = R_inner + padding + border_width`.
   Past `24px` of padding, or where padding is asymmetric, treat layers as separate surfaces and keep each one's radius token.

2. **Align optically where geometry looks off**:
   Where geometric centering looks off, nudge by eye. Give a button `2px` less padding on its icon side, shift a play triangle toward its point, and fix asymmetric glyphs in SVG.

3. **Shadows for elevation, borders for structure**:
   Where a border exists only to create depth, replace it with layered transparent `box-shadow` values. Keep borders on dividers, separators, table cells, and selected states.

4. **Transitions, not keyframes, for interactive state**:
   Drive interactive state changes with CSS transitions (`150ms-200ms ease-out`). Keyframes run a fixed timeline and cannot reverse, so reserve them for sequences that run once (e.g. initial page-load entrance or toast appearance).

5. **High-frequency interactions get no animation**:
   Keystrokes, row hovers, and tab switches get instant feedback, or a transition of `150ms` or less on `opacity` or `background-color`. Reserve expressive motion for infrequent moments.

6. **Gate motion behind reduced-motion preference**:
   Wrap motion effects in `@media (prefers-reduced-motion: no-preference)`. Under reduced motion, replace with an opacity cross-fade.
