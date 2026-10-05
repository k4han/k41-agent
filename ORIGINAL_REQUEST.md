# Original User Request

## 2026-09-05T17:33:34Z

Optimize mobile and tablet responsiveness across the entire SolidJS Web Dashboard application, ensuring intuitive navigation, touch ergonomics, fluid layouts, and zero viewport overflow on narrow screens.

Working directory: D:\CODE_C\kaka-agent-v2\agent\delivery\http\dashboard\frontend
Integrity mode: development

## Requirements

### R1. App Shell & Navigation Ergonomics
The primary navigation, topbar header, and application shell must adapt seamlessly to mobile viewports (<= 640px) and tablets (<= 980px). The sidebar must function as a smooth off-canvas drawer with clear open/close affordances and backdrop overlay. Topbar titles, badges, and action buttons must wrap or condense cleanly without clipping or pushing elements off-screen, taking into account safe area insets on mobile devices.

### R2. Chat Experience & Composer Usability
The chat view, live message stream, code snippets, markdown elements, and tool call blocks must render cleanly on narrow screens without horizontal page overflow. The chat composer, action toolbars, and agent/model selectors must remain completely usable and accessible within thumb-reach on mobile viewports, handling virtual keyboard height and viewport resizing gracefully without layout breakage.

### R3. Dashboard Cards, Data Tables & Modals
All home dashboard panels, metrics widgets, configuration forms, settings tabs, and data tables (e.g. Tasks, Repositories, Scheduler, Agent variables) must fit within mobile screen bounds. Wide data tables must provide seamless horizontal scroll containers or responsive card views without inducing document-level horizontal scrolling. Dialogs, confirmation popups, and dropdown menus must adapt to mobile viewports (e.g. bottom-sheet style or full-width surfaces) with minimum 44px touch targets.

## Acceptance Criteria

### Build & Type Verification
- [ ] `npm run check` (TypeScript compilation) exits with code 0 and zero type errors.
- [ ] `npm run build` (Vite production build) completes successfully with zero bundling errors.

### Layout & Viewport Verification
- [ ] At mobile viewports (375px, 390px, 414px) and tablet viewport (768px), `document.documentElement.scrollWidth <= window.innerWidth` across all main routes (`/`, `/chat`, `/repositories`, `/tasks`, `/scheduler`, `/settings`).
- [ ] Mobile drawer opens, closes, and dismisses on backdrop click or navigation without layout shift or stuck scroll lock.
- [ ] Topbar action buttons and titles do not overflow or overlap on 375px width.
- [ ] Chat composer toolbar, model picker, and message send button remain visible and clickable without horizontal overflow.
- [ ] Tables in `/repositories`, `/tasks`, and `/settings` are wrapped in responsive scroll containers that prevent the outer page from overflowing horizontally.
- [ ] Interactive touch elements (drawer toggle, icon buttons, form triggers, dialog action buttons) have a minimum touch target size of 40px to 44px.


## 2026-10-04T16:48:44Z

Requested team: Full team

Refactor and comprehensively overhaul the Providers Settings experience (`/settings/providers`) across the frontend dashboard and backend API. Eliminate styling defects across light and dark themes, streamline provider setup and model selection workflows, unify user confirmation dialogs, harmonize navigation hierarchy, and implement full-stack connection verification.

Working directory: D:\CODE_C\kaka-agent-v2
Integrity mode: development

## Requirements

### R1. Design System & Theme Polish
Standardize visual appearance across both Light and Dark themes. Replace embedded inline styles in `ProvidersPage.tsx` with structured stylesheets adhering to design tokens. Ensure token color fidelity (`--primary`, `--surface`, `--border`) so accent colors, card backgrounds, and provider brand logos render legibly and harmoniously without dark-mode hardcoded artifacts.

### R2. Unified Confirmation Dialogs
Standardize all destructive and critical user actions across all provider sub-pages (Models, Search & Web, Execution Environments, Decision Models) to use the project's consistent `ConfirmDialog` component, eliminating all browser-native `window.confirm` dialogs.

### R3. Interactive Model Catalog Integration
In the provider detail view, elevate the Model Specifications & Capabilities catalog from passive information cards to actionable controls. Enable users to directly set a model as default or add it to their configured models list with a single click, eliminating required manual clipboard copy-pasting of model identifiers.

### R4. Information Architecture & Navigation Coherence
Harmonize page titles, breadcrumb labels, and tab headers across all provider sections (`Models`, `Search & Web`, `Execution Environments`, `Decision Model`). Provide clear contextual signposting distinguishing provider configuration from global decision routing (`/settings/decisions`).

### R5. Full-Stack Connection Verification & Streamlined Setup
Provide full-stack connection testing capability for configured providers, featuring backend health-check verification endpoints and frontend interactive feedback (loading, success, and error diagnosis). Reduce setup friction during new provider creation by guiding the user seamlessly from credential entry to model discovery.

## Verification Resources

- Frontend typecheck and linting: `pnpm --dir agent/delivery/http/dashboard/frontend check`
- Frontend production bundle build: `pnpm --dir agent/delivery/http/dashboard/frontend build`
- Frontend test suite: `pnpm --dir agent/delivery/http/dashboard/frontend test`
- Backend test suite: `uv run pytest tests/test_providers_module.py tests/test_dashboard_settings.py tests/test_decision_providers.py`

## Acceptance Criteria

### Theming & Visuals
- [ ] No inline `<style>` tags with hardcoded theme colors exist inside `ProvidersPage.tsx`
- [ ] In Light theme, provider cards, logo backgrounds, and status badges display high-contrast, token-compliant colors without dark-slate bleed
- [ ] Provider logos remain visible and clear in both light and dark modes

### Modal Dialogs
- [ ] Zero instances of `window.confirm` exist across `WebConnectionsPage.tsx` and `DecisionProvidersPage.tsx`
- [ ] All deletion and clear-default actions invoke `ConfirmDialog` with appropriate warning messaging and cancel/confirm semantics

### Model Selection Flow
- [ ] Model cards under Model Specifications provide interactive buttons to select as default model or add to configured list
- [ ] Clicking a model action immediately updates the corresponding form state and triggers change tracking

### Connection Verification (Full-Stack)
- [ ] Backend provides verification endpoints to validate provider API keys/connectivity
- [ ] Frontend displays a dedicated "Test Connection" trigger with loading state and clear visual feedback for success/failure
- [ ] Existing functional tests and newly added verification endpoint tests pass cleanly

### Build & Integrity
- [ ] `pnpm --dir agent/delivery/http/dashboard/frontend check` exits with code 0
- [ ] `pnpm --dir agent/delivery/http/dashboard/frontend build` exits with code 0
- [ ] `uv run pytest tests/test_providers_module.py tests/test_dashboard_settings.py tests/test_decision_providers.py` exits with code 0
