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
- `realtimeEvents.ts`: compact realtime event interpretation
- `realtimeRefresh.ts`: event impact and relevant resource/route selection
- `useShellRefresh.ts`: typed shell reads and route/account request lifetime
- `refreshCoordinator.ts`: serialized batches, burst coalescing and trailing invalidations
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
- `frontend/src/api/schedules.ts`
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

Schedules:

- Direct lazy route: `features/schedules/SchedulesView.tsx`.
- Form/dependencies: `ScheduleEditor.tsx`; weekly interactions: `WeeklyScheduleGrid.tsx`.
- Interval conversion and summaries: `features/schedules/model.ts`.
- CRUD and confirmation HTTP contracts: `api/schedules.ts`.
- Default-policy persistence remains under `lib/settings.tsx`.

Workflows:

- Direct lazy entries: `features/workflows/AutomationsView.tsx` and `NotificationsView.tsx`.
- Automation node editing: `AutomationEditor.tsx`; draft/presentation helpers: `automationModel.tsx`.
- Notification editing: `NotificationEditor.tsx`, `NotificationActionCard.tsx`, `NotificationSelection.tsx`; draft/presentation helpers: `notificationModel.tsx`.
- Common lists/selection blocks: `components.tsx`; rich text: `TemplateEditor.tsx`.
- Shared model and state/read ownership: `model.ts`, `hooks.ts`.
- The aggregate WorkflowFeature and WorkflowViews paths were retired; do not recreate aliases.
- API owner: `frontend/src/api/workflows.ts`
- Backend workflow catalogs are the source of truth.
- Keep automation and notification builders sharing primitives where concepts overlap. Shared modules must not import a concrete editor.
- Use the shared request hook; do not let stale reads replace a completed mutation. Load camera choices only for an editor that can use them.
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


## Ownership and retirement checks

`frontendOwnership.test.ts` enforces direct route ownership and acyclic feature
imports. Schedule tests live in `features/schedules/`; workflow tests in
`features/workflows/`; confirmation contracts in `api/mutationContracts.test.ts`.
Refresh selection, batching and lifetime tests live beside their `app/` owners.

Add event impact entries with tests for affected and unaffected routes. Preserve
full active-route refresh on reconnect/manual refresh. Do not double-refresh routes
that already consume compact events. Check dynamic/responsive CSS consumers before
removing selectors. See [milestone 6](../validation/milestone6-frontend.md).
