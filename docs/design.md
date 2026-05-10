# CODEJOB Design Documentation

## 1. UI/UX Structure

The frontend is a single-page operations dashboard built in React (`dashboard/src/App.tsx`) with a two-region layout:

- **Left rail (navigation):** brand, quick status, section navigation, footer actions.
- **Main pane (workspace):** sticky top header + page content area with cards, grids, and workflow lists.

## 2. Layout Patterns

- **Primary shell:** CSS grid (`.gmailShell`) with desktop split view and responsive collapse.
- **Sticky regions:**
  - Sidebar remains fixed on desktop (`.leftRail`).
  - Top header remains sticky while content scrolls (`.topHeader`).
- **Card-driven content:** settings and queue content grouped into bordered cards (`.card`, `.emailItem`, `.statCard`).
- **Form-heavy configuration layout:** `configGrid` (3-column desktop, fewer columns on smaller viewports).

## 3. Color Usage

Color tokens are centrally defined in `App.css` under `:root`:

- **Primary actions:** `--primary` / `--primary-strong` (blue)
- **Background/surfaces:** `--bg`, `--surface`, `--surface-soft`
- **Text:** `--ink`, `--muted`, `--muted-soft`
- **Borders:** `--line`, `--line-strong`
- **Status/error:** `--danger`, `--danger-soft`, `--ok`

Patterns:
- Primary buttons and active nav states use blue.
- Error/failure states use red variants.
- Routing safety uses green-ish `safe` panel styling and red `blocked` panel styling.

## 4. Typography

- Global font stack in `index.css`: **Geist**, fallback to Segoe UI/sans-serif.
- Large, bold display text for branding and page title.
- Compact utility text (`.subtle`, labels, counters) for metadata.

## 5. Component Design

### Key UI elements
- **Sidebar component (`Sidebar.tsx`)**
  - Navigation items with active highlighting and counters.
  - Queue status label that changes based on `running` state.
- **Status cards**
  - Gmail/AI/automation summary at top-level for operational health.
- **Routing panel**
  - Displays confidence, status, evidence items, and safety warning.
- **Draft editor + preview pair**
  - Editable textarea and rendered HTML preview (`draftToPreviewHtml`).

## 6. Navigation Flow

Navigation is state-driven (no router). `activePage` switches visible sections:

- `run_queue`
- `needs_review`
- `failed_mapping`
- `recent_runs`
- `sent_items`

Each section conditionally renders with focused controls for that workflow.

## 7. Visual Hierarchy

- **Top-level hierarchy:** page title + stat cards + primary sync actions.
- **Second-level hierarchy:** configuration cards grouped by concern.
- **Operational hierarchy:** per-candidate cards with details, warnings, and action buttons.
- **Critical indicators:**
  - error text (`.errorMessage`)
  - blocked routing warning (`.routingWarning`)
  - disabled buttons for invalid action states.

## 8. Responsive Behavior

Defined media queries in `App.css`:

- `@media (max-width: 1200px)`
  - Shell collapses to single-column.
  - Sidebar becomes non-sticky.
  - Config grid reduces columns.
- `@media (max-width: 900px)`
  - Header wraps.
  - Search input shrinks.
  - Stats and config become single-column.
  - Action bar stacks vertically.

## 9. Reusable Design Patterns

- **Token-based theme variables** for consistent color and spacing decisions.
- **Shared form control styling** (`input`, `textarea`, `select`, buttons) for consistency.
- **Toggle switch pattern** reused for feature enable/disable flags.
- **Chip pattern** for must-have skills with add/remove behavior.
- **Tag and subtle text pattern** for low-emphasis metadata.
