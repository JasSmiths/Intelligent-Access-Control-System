# Frontend Agent Notes

Use this when touching React, TypeScript, CSS, frontend tests, routing, app shell,
API clients, integrations UI, workflow UI, or visual behavior.

## Current V2 Structure

- Bootstrap: `frontend/src/main.tsx`
- App shell: `frontend/src/app/*`
- Typed API modules: `frontend/src/api/*`
- Domain-neutral primitives: `frontend/src/ui/*`
- Reused helpers: `frontend/src/lib/*`
- Feature owners: `frontend/src/features/*`
- Route views: `frontend/src/views/*`
- Styles: `frontend/src/styles.css` imports `frontend/src/styles/*`

`frontend/src/shared.tsx` was removed. Do not recreate a shared compatibility
shim or route imports through one.

## App Shell Ownership

`frontend/src/main.tsx` should only render `App` and import global styles.

`frontend/src/app/*` owns:

- `App.tsx`: app composition
- `auth.tsx`: login/setup/session behavior
- `routes.tsx`: route registry/composition
- `navigation.tsx`: sidebar/nav metadata
- `realtimeEvents.ts`: realtime event interpretation/refresh wiring
- `searchPalette.tsx`: global search UI/state
- `theme.tsx`: light/dark/system theme behavior
- `toasts.tsx`: toast state/UI
- `chatLauncher.tsx`: Alfred launcher wiring
- `alerts.tsx`: app-level alert/status helpers
- `profile.ts`: profile helpers

Do not move feature-specific logic into `app`.

## API Ownership

Low-level fetch belongs in `frontend/src/api/client.ts`.

Typed resource owners include:

- `frontend/src/api/integrations.ts`
- `frontend/src/api/workflows.ts`
- `frontend/src/api/search.ts`
- `frontend/src/api/chat.ts`
- `frontend/src/api/types.ts`

Rules:

- Use relative API URLs for LAN/Nginx compatibility.
- Feature views should use typed API owners instead of direct `fetch`.
- Direct `fetch(` outside `frontend/src/api/*` needs explicit justification.
- Do not introduce a second API client pattern.

## Feature Ownership

Integrations:

- Route view: `frontend/src/views/IntegrationsView.tsx`
- Feature modules: `frontend/src/features/integrations/*`
- Shared provider primitives live in the feature folder unless truly domain-neutral.
- Backend metadata/config/status is the source of truth; do not recreate frontend fallback catalogs.

Workflows:

- Route shell: `frontend/src/views/WorkflowViews.tsx`
- Feature owner: `frontend/src/features/workflows/WorkflowFeature.tsx`
- API owner: `frontend/src/api/workflows.ts`
- Backend workflow catalogs are the source of truth.
- Keep automation and notification builders sharing primitives where concepts overlap.
- Do not reintroduce frontend fallback workflow/notification catalogs.

## UI And Helpers

- Domain-neutral UI primitives live in `frontend/src/ui/primitives.tsx`.
- Formatting/date/value helpers live in `frontend/src/lib/format.ts`.
- Media helpers live in `frontend/src/lib/media.tsx`.
- Notification metadata helpers live in `frontend/src/lib/notifications.tsx`.
- Settings form helpers live in `frontend/src/lib/settings.tsx`.
- Keep domain-specific helpers near the feature or route that owns them.

## Styling Rules

- Operational console, not a marketing/landing page.
- Fixed desktop sidebar, dense readable cards/tables, status badges, light/dark/system theme.
- Use lucide icons for tool/action buttons where available.
- Card radius should stay restrained, usually `8px`.
- No nested cards.
- Text must not overflow or overlap on mobile or desktop.
- Do not broad-style badge spans. Keep `.badge` inline-flex.
- Scope integration header span styles to title selectors.
- Delete CSS only after checking selector usage by search/build context.

## Route Notes

Current routes include:

- Dashboard
- People
- Groups
- Schedules
- Passes
- Vehicles
- Movements
- Top Charts
- Events
- Alerts
- Reports
- API & Integrations
- Logs/Telemetry/Audit
- Settings
- Alfred Training

Non-shell routes are lazy chunks. Do not move route bodies back into `main.tsx`
or raise Vite chunk limits just to hide growth.

## Frontend Validation

```bash
cd frontend && npm run build
cd frontend && npm test
git diff --check
```

Search checks:

```bash
rg "from ['\"].*/shared|shared.tsx|frontend/src/shared" frontend/src
rg "fetch\\(" frontend/src
```

