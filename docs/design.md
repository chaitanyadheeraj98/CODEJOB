# CODEJOB Design Documentation

## 1) UI/UX structure

The frontend is a single-page operations dashboard with a two-panel shell:

- **Left rail (navigation + brand + quick actions)**
- **Main pane (header + context page content)**

The workflow is task-oriented: configure → run → review failures/queue → approve/reject → monitor trends.

## 2) Layout patterns

- **Grid shell**: `grid-template-columns: 326px minmax(0, 1fr)`
- **Sticky sidebar** and **sticky top header** for persistent controls
- **Card-based sections** for grouped configuration and operational data
- **State views by page mode** (`run_queue`, `needs_review`, `failed_mapping`, `recent_runs`, `sent_items`)

## 3) Color usage

Color system is tokenized with CSS custom properties in `App.css`:

- Primary: `--primary` / `--primary-strong` (blue action accents)
- Surface/background: `--bg`, `--surface`, `--surface-soft`
- Borders: `--line`, `--line-strong`
- Semantic: `--danger`, `--danger-soft`, `--ok`

Visual semantics:
- Blue = primary actions and active nav
- Red/pink = error/blocked/failure states
- Green-ish = safe routing or positive trend states
- Dark gradient card = live monitor emphasis

## 4) Typography

- Global font imported from Google Fonts: **Geist** (fallback: Segoe UI, sans-serif).
- Heavy emphasis on large numeric/stat typography for operations visibility.
- Hierarchy via large H1 title, card H2 headers, and compact metadata labels.

## 5) Component design

Key reusable component patterns:

- **Sidebar navigation component** (`components/Sidebar.tsx`) with active state and counters.
- **Card components** (`.card`, `.statCard`, `.emailItem`) for consistent grouping.
- **Toggle switch pattern** (`.toggleSwitch` + `.toggleTrack`) used across settings.
- **Routing evidence panel** with safe/blocked variants.
- **Draft compose pattern**: editable textarea + live HTML preview.

## 6) Navigation flow

- Sidebar drives primary section changes.
- Each section maps to a focused workflow:
  - `run_queue`: controls + live analytics
  - `needs_review`: decision and send workflow
  - `failed_mapping`: routing correction workflow
  - `recent_runs`: operational auditing
  - `sent_items`: output verification history

## 7) Visual hierarchy

- Primary CTAs (**Sync + Queue**, **Connect Gmail**, **Approve & Send**) are high contrast.
- Metrics and counts are prominently surfaced in stat cards and live monitor ticker.
- Decision-critical warnings (routing not safe, errors) are highlighted with strong color + weight.
- Contextual metadata appears in subtle text to reduce visual noise.

## 8) Responsive behavior

Implemented breakpoints:

- **≤1200px**:
  - Shell collapses to single column
  - Sidebar becomes non-sticky
  - Config grid moves to 2 columns
- **≤900px**:
  - Header wraps
  - Typography scales down
  - Stats/config/action layout becomes single-column
  - Monitor header stacks vertically

## 9) Reusable design patterns

- Tokenized theming with CSS variables
- Unified form control styling (`input`, `textarea`, `select`)
- Reusable row/button/toggle/chip patterns
- Consistent border radius + border style language
- Status chips/tags for connection and model states

## 10) UX strengths and caveats

### Strengths
- Strong operations-first information density
- Clear queue state segmentation
- Manual safety gates before outbound communication
- Inline correction and retry flow for routing failures

### Caveats
- Single-page file (`App.tsx`) is large; future UX iteration would benefit from component decomposition.
- Some UI controls (e.g., placeholder actions) are present without full backend workflow coupling.
