# Milestone 6: frontend ownership and retirement

## Baseline and scope

The assessed source is milestone 5 revision
`a29569084a924554fb7767a418315981fb9e3f51`. Its 459 source hashes matched
the main checkout before work. Milestone 5 was deployed separately; its backend
image is `sha256:b33c044a4ca76f6fabc45dde08a336e39c61860ba2e1d78c58063c10ba6db09a`.

The fresh isolated baseline is
`/Users/jas/Documents/IACS Regression Baselines/iacs-phase1-ab9n43d4`.
Every gate passed: 905 backend, 178 persistence, 21 frontend and 5 harness tests,
compilation, migrations/schema comparison, Compose parsing, Ruff and mypy.
Production metadata was unchanged and cleanup reported no remaining resources.

Implementation takes place in an isolated checkout because the main project is
mounted into the live backend. Deployment remains a separate action. No schema,
backend, hardware-owner, provider, notification-delivery or stored-record changes
are part of this milestone.

## Bounded design

1. Give schedule list/policy, editor/dependencies and weekly-grid behavior their
   own feature modules. Put schedule confirmation and CRUD HTTP requests under
   one typed API owner. Keep normalization in a directly testable model.
2. Give automations and notifications separate lazy route entries and editor
   owners. Retain one implementation of their common rule list, selection modal,
   template editor and filtering/loading controls. Keep backend catalogs as the
   source of supported operations; introduce no fallback catalog.
3. Select shell resources and route refreshes by event impact. Serialize batches,
   merge bursts, retain invalidations received during a read, and cancel work at
   route/account boundaries. Load camera options only for an open editor that
   can use media. Retire aggregate facades and update current ownership guides.

## Owners and extension points

| Responsibility | Owner |
| --- | --- |
| Schedule list and default policy | `frontend/src/features/schedules/SchedulesView.tsx` |
| Schedule form and dependency read lifecycle | `features/schedules/ScheduleEditor.tsx` |
| Weekly grid interactions / interval normalization | `features/schedules/WeeklyScheduleGrid.tsx` / `model.ts` |
| Schedule CRUD, confirmation payloads, dependencies | `frontend/src/api/schedules.ts` |
| Default policy settings persistence | Existing `frontend/src/lib/settings.tsx` |
| Automation list / node editor / draft helpers | `features/workflows/AutomationsView.tsx`, `AutomationEditor.tsx`, `automationModel.tsx` |
| Notification list / rule editor / action editor / selectors | `features/workflows/NotificationsView.tsx`, `NotificationEditor.tsx`, `NotificationActionCard.tsx`, `NotificationSelection.tsx` |
| Notification draft normalization, preview and endpoint presentation | `features/workflows/notificationModel.tsx` |
| Shared list, selection and block UI | `features/workflows/components.tsx` |
| Shared template editor, state helpers and request lifecycle | `features/workflows/TemplateEditor.tsx`, `model.ts`, `hooks.ts` |
| Workflow HTTP contracts and confirmations | Existing `frontend/src/api/workflows.ts` |
| Route resource requirements / event impact | `frontend/src/app/navigation.tsx` / `realtimeRefresh.ts` |
| Typed shell reads and account/route lifetime | `frontend/src/app/useShellRefresh.ts` |
| Serialized, coalesced refresh batches | `frontend/src/app/refreshCoordinator.ts` |
| Stream transport and app composition | `frontend/src/app/App.tsx`; compact payload interpretation remains in `realtimeEvents.ts` |

To add a schedule interaction, extend its grid/model and test the resulting
intervals. To add a workflow capability, start with its backend catalog and
existing mutation owner, then extend the appropriate feature editor. Shared UI
must not import a concrete editor. Do not reintroduce an aggregate export facade.
To add an event consumer, update the event-impact map and test both affected and
unaffected routes. A route consuming a compact event directly must not also get
a redundant refresh token for it. Manual and reconnect refresh still cover the
active route completely.

## Intentional behavior changes

- Background workflow reads keep the editor mounted and retain its unsaved draft.
  Superseded reads cannot overwrite newer data or a completed mutation. A reload
  error is displayed independently of an already successful save.
- Camera lists load when a media-capable notification editor opens, rather than
  on every rule-list read. Existing camera IDs and unavailable recipients remain
  stored in the draft. The existing optional-camera failure behavior is retained.
- Known realtime events refresh their relevant resources. Configuration changes
  refresh affected catalogs and schedule policy; access-device configuration and
  schedule-override events now have explicit consumers. Passes and charts retain
  their direct event handling without a second token-driven read.
- Stream refreshes are coalesced at five-second intervals, with a trailing batch
  for events received during a read. Explicit refresh can advance a pending batch.
  Lifecycle refresh uses the same queue; the former separate one-second lifecycle
  timer is retired. Requests from different routes/accounts cannot update each
  other's state, including StrictMode cleanup/remount paths.

All form layouts, CSS, rule payload formats, permissions, confirmation metadata,
provider choices, dry-run/test routing and schedule time conventions are retained.
No live notification or hardware command is used for these tests.

## Request budgets and build evidence

These are deterministic source/test request counts, not live latency measurements.
They exclude the event transport and separately owned route consumers.

| Trigger and current route | Before | After |
| --- | --- | --- |
| `alerts.updated`, dashboard | 8 shell GETs | 1 alerts GET |
| Schedule CRUD audit, schedules | 3 shell GETs | 1 schedules GET |
| Notification rule audit, notifications with editor closed | 4 shell + 3 workflow GETs | 2 workflow GETs |
| Notification list read | catalog + rules + cameras | catalog + rules |

Catalog reads remain fresh when their route is invalidated; there is no persistent
catalog cache with implicit invalidation rules. People/vehicle shell reads retain
`include_media=false`; workflow user reads explicitly retain `include_photo=false`
(the backend already defaulted to false, so this is not a new bandwidth saving).

The former combined workflow chunk was 83.09 kB uncompressed. Separate workflow
entries now share common code and load their own editor code. Total application
size is not claimed to have shrunk: the goal is local ownership and avoiding
loading both editors when only one is visited. The final build log is authoritative.

## Retirement evidence

Removed `views/SchedulesView.tsx`, `views/WorkflowViews.tsx` and
`features/workflows/WorkflowFeature.tsx`; all routes and tests now import the
actual owner. Removed `useRefreshableWorkflowLoad`, the broad
`shouldRefreshDataForRealtimeEvent` predicate and its event/prefix tables, and
App's overlapping promise/timer/ref coordination. Their replacements have explicit
inputs and regression tests. No compatibility re-export was left behind.

The extracted declarations expose only names consumed by another owner or test.
Unused imports were removed. Strict TypeScript unused-local/parameter checks pass.
No rarely used feature, provider, history or audit data was inferred to be dead.

All ten imported stylesheets have retained consumers. CSS is unchanged; class
name search alone cannot prove that a responsive or conditional selector is dead.

| Stylesheet | Retained consumers |
| --- | --- |
| `base.css` | App shell and domain-neutral primitives |
| `dashboard.css` | Dashboard and common view layout |
| `data-views.css` | Event, movement, report, directory and chart views |
| `search-palette.css` | Global search palette |
| `integrations.css` | Integration route and provider panels |
| `passes-schedules.css` | Passes route and the new schedule feature owners |
| `workflows.css` | Both workflow editors and their shared components |
| `investigations.css` | Investigation feature and Logs route |
| `auth-directory-modals.css` | Auth, directory and settings forms |
| `chat-responsive.css` | Alfred chat and responsive shell/feature layouts |

## Acceptance and release boundary

Focused tests cover weekly interval round trips, form retry, confirmation rejection,
recipient editing, paused duplication, route save flows, stale reads, cancellation,
StrictMode, request budgets, batch failure/trailing work and import boundaries.
The full isolated harness is required before handoff, along with TypeScript unused
checks, source comparison, production metadata comparison and cleanup evidence.
The retained handoff records exact revision, final results, source hashes, patch,
reverse patch and test paths; this document does not authorize a live release.

Rollback is limited to the milestone 6 patch or frontend image. Check the reverse
patch against the deployed source before applying it; stop on conflicts and preserve
unrelated changes. No database restore or migration belongs in this rollback.

This is the last milestone in the six-stage recovery roadmap. After its separately
authorized release and read-only smoke checks, review the programme against its
original values before starting another bounded task. Durable automation dispatch,
previously assessed separately in milestone 4, remains a follow-up candidate; do
not infer permission to retry hardware actions from notification recovery behavior.
