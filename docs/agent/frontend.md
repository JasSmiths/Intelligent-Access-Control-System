# Frontend agent guide

Use for React, typed clients, routes, styling, editors and browser validation.
Paths below are relative to `frontend/src/`. Inspect the working tree first;
current uncommitted features are part of the source under review.

## Find the owner

| Concern | Source of truth |
| --- | --- |
| Bootstrap and composition | `main.tsx` renders `app/App.tsx` and imports global styles. |
| Routes, URLs, labels, roles and required shell data | `app/navigation.tsx`; lazy view composition in `app/routes.tsx`; `api/types.ts` owns `ViewKey`. |
| Authentication and account lifetime | `app/auth.tsx`, `app/profile.ts`, `app/useShellRefresh.ts`. |
| Realtime transport and refresh | `app/useRealtimeConnection.ts`, `realtimeEvents.ts`, `realtimeRefresh.ts`, `refreshCoordinator.ts`, `useShellRefresh.ts`. |
| Search, theme and toasts | Matching modules under `app/`. |
| HTTP, errors and confirmation requests | `api/client.ts`; domain contracts under `api/`; shared response types in `api/types.ts`. |
| Common UI and editor lifecycle | `ui/primitives.tsx`, `ui/useModalFocus.ts`, `ui/useEditorDismiss.ts`, `ui/useModalClose.ts`. |
| Formatting, media, settings and floating placement | `lib/format.ts`, `lib/media.tsx`, `lib/settings.tsx`, `lib/viewportPlacement.ts`. |

Keep feature logic out of the shell. `shared.tsx`, aggregate schedule/workflow
facades and compatibility exports are retired. `frontendOwnership.test.ts`
enforces direct routes and acyclic schedule/workflow dependencies.

## Feature entrypoints

| Feature | Owner and related contracts |
| --- | --- |
| Dashboard and Access Pulse | `views/DashboardView.tsx`; `features/dashboard/AccessPulse.tsx` and its colocated CSS. |
| People, Groups and Vehicles | Direct `views/PeopleView.tsx`, `GroupsView.tsx`, `VehiclesView.tsx`; editors and shared directory helpers under `features/directory/`. |
| Schedules | `features/schedules/SchedulesView.tsx`, `ScheduleEditor.tsx`, `WeeklyScheduleGrid.tsx`, pure `model.ts`; `api/schedules.ts`. |
| Passes | `views/PassesView.tsx`; editor, details, types and lifecycle presentation under `features/passes/`. New duration passes require a number plate; legacy duration edits can retain a missing plate. |
| Events, Movements and Alerts | Matching `views/*View.tsx`, `views/useHistoryPage.ts`; `api/history.ts`. |
| Reports and Top Charts | Matching views; `api/reports.ts`. |
| Integrations and recovery panels | `views/IntegrationsView.tsx`, `features/integrations/`; `api/integrations.ts`; provider forms under `features/integrations/providers/`. Camera AI uses the existing provider settings. |
| Investigations | `/logs` retains its URL and `logs` view key; `views/LogsView.tsx` composes `features/investigations/`; `api/investigations.ts`. |
| Settings hub, device settings and Users | Direct `views/SettingsView.tsx`, `DynamicSettingsView.tsx`, `AccessDevicesSettingsView.tsx`, `ZonesSettingsView.tsx` and `UsersView.tsx`; supporting panels under `features/settings/`. |
| Command History | `views/CommandHistoryView.tsx`, shared `features/integrations/CommandReceiptHistory.tsx` and `CommandReceiptDetails.tsx`; `api/integrations.ts`. |
| Missed Exit Recovery | `features/missedExitRecovery/`; `api/missedExitRecovery.ts`; `/settings/missed-exit-recovery`. |
| Automations and Notifications | Direct routes to `features/workflows/AutomationsView.tsx` and `NotificationsView.tsx`; `api/workflows.ts`. |

Workflow editors own their specific draft/presentation models. Reuse
`features/workflows/components.tsx`, `model.ts`, `hooks.ts` and `TemplateEditor.tsx`
without importing a concrete editor into those shared modules.
`VariableRichTextEditor.tsx` and `lib/templateRecipients.ts` handle template UI.
Backend integration/workflow catalogs remain authoritative; do not invent
fallback catalogs in React. Automations use explicit date/cron schedules.
Notifications retain mobile, in-app and voice delivery. Investigations uses
structured filters and retained activity evidence; it has no question interpreter.

## Contracts to preserve

- Use relative `/api/v1` URLs and existing typed API modules. Low-level `fetch`
  belongs in `api/client.ts`; direct feature fetches need explicit justification.
- Preserve Admin gates and server confirmation contracts. Read the
  [hardware guide](hardware-safety.md) before changing command or live-test UI.
  Configuration, provider connectivity, command acceptance and verified outcome
  are distinct states.
- Keep initial critical-read failure distinct from empty data; retain usable
  data with an explicit stale state after a later refresh failure.
- Preserve dirty drafts during refresh/resize, prevent dismissal and duplicate
  submission while saving, and restore focus after close. Use shared lifecycle
  helpers rather than independent Escape/backdrop handlers.
- Centered dialogs share motion in `styles/motion.css`. Wrap accepted close/save
  callbacks with `useModalClose`; run dirty/pending guards before that callback
  and complete mutations before animating a successful close. Keep focus ownership
  in `useModalFocus` (or native `<dialog>`), including throughout the exit.
- Preserve request/account lifetime checks so stale reads cannot overwrite a
  completed mutation. Load camera choices only for editors that use them.
- Extend realtime impact tests for affected and unaffected routes. Reconnect and
  manual refresh still refresh the active route; avoid a second refresh for
  views already consuming compact events.
- Alert detail links use a member UUID from `alert_ids` for grouped alerts.
  The synthetic group `id` identifies the row and confirmation target; it is
  not valid for `/api/v1/alerts/{alert_id}` detail reads.
- Keep durable cursor history, direct-record links and expired-history refresh
  behavior separate from recent realtime feeds.

## Layout and styles

`styles.css` imports global/shell styles. Some features load CSS with their lazy
entrypoints, including integrations, workflows, investigations and
Access Pulse. Inspect imports and selector consumers before removing styles.

Shared shell and editor responsiveness lives in `styles/responsive.css`; feature styles remain with their owners.

The navigation drawer boundary is **980px** in `app/App.tsx` and responsive CSS;
the compact content/editor boundary remains **720px**. Keep them distinct and
test both through resize. Use per-edge safe-area variables from `styles/base.css`
and `lib/viewportPlacement.ts` for floating controls.

Keep the console dense and readable with restrained radii, status badges and
light/dark/system themes. Stream health appears as a dot on the topbar refresh
control, with its title and detail available on hover, keyboard focus and to
screen readers. Keep the sidebar footer for the account menu. Use existing lucide action icons. Avoid nested cards,
text clipping and document overflow; allow deliberate table/calendar scrolling.
Keep `.badge` inline-flex and scope title styles instead of broad span rules.
The sample design under `prototypes/premium-dashboard/` is a separate app,
not the source of production routes or contracts.

Alert filters share 40px control heights and align at their bottom edges. Keep
dropdown appearance consistent across browsers and date fields contained when
the filters wrap; the panel and active status tab use a flat visual treatment.

## Validation

From the repository root:

```sh
cd frontend
npm run build
npm test
```

Run `git diff --check` from the root. For layout, interaction, route or role-gate
changes also follow [browser validation](../validation/gui-completion.md) and
[responsive validation](../validation/frontend-responsive.md). Relevant unit
tests live beside owners; `api/mutationContracts.test.ts` verifies confirmations,
and `frontendGuardrails.test.ts`/`frontendOwnership.test.ts` enforce boundaries.
For backend-contract changes use the [Phase 1 validator](../validation/phase1.md).
Report the actual dependency environment and result; old pass counts are not
evidence for the current tree.
