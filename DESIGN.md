# SPVH Options Desk: design system

Register: product (a trading cockpit used during market hours). The design serves the task: read state at a glance, act with confidence, never mistake paper for live.

## Visual language
- White canvas with faint ambient washes (SPVH blue, teal) so frosted panels have depth. Liquid glass is a web approximation: `backdrop-filter` + inset highlight + hairline ring. Falls back to solid white under `prefers-reduced-transparency`.
- SPVH brand blue is the single accent: primary actions, the selected nav item, selected segments, and the LIVE state, always with white text. It is never used for decoration.
- SPVH logo in brand blue. Strategy identity: ES = muted violet `rgb(106 79 176)`, OB = teal `rgb(26 122 105)` (kept apart from the blue accent).
- Semantic colours: up `rgb(11 125 69)`, down `rgb(196 43 35)`, warn `rgb(154 91 0)`.

## Tokens (`frontend/src/index.css`, `frontend/tailwind.config.js`)
| Token | Use | Contrast on white |
|---|---|---|
| `ink` | primary text | 17.8:1 |
| `ink2` | secondary text | 10.4:1 |
| `muted` | labels, captions | 6.1:1 |
| `faint` | disabled only | 3.4:1 |
| `accent` (spvh-600) | blue text and borders | 7.0:1 |
| `btn-primary` | blue fill with white text | 5.4:1+ |

## Type
Calibri (Carlito fallback) for everything; tabular figures on by default. The scale is nudged up ~1px because Calibri sets small: xs 13, sm 14.5, base 16, lg 18, xl 21, 2xl 26.

## Shape
Panels `rounded-panel` (18px), controls `rounded-control` (10px), pills and segmented controls fully round.

## Components
- `Segmented` (`components/ui/Segmented.tsx`): iOS segmented control; selected segment is SPVH blue.
- `.pressable`: 0.97 scale on press, 140ms ease-out.
- LIVE badge: blue pill with a pulsing red dot. PAPER badge: neutral inset pill.
- Icons: Phosphor (`@phosphor-icons/react`) only, regular weight, fill weight for the active state.

## Live trading safety in the UI
- Go-Live is per strategy (header switches). Going live requires a loaded readiness check, no open trades for that strategy, and typing `GO LIVE ES` / `GO LIVE OB`.
- Every open position shows its own LIVE or PAPER tag; exiting a position asks for confirmation.
