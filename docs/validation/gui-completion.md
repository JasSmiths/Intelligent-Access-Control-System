# Frontend browser validation

Use this for route, editor, role, navigation and layout changes. Ownership is in
the [frontend guide](../agent/frontend.md); responsive invariants and manual
device checks are in [frontend-responsive.md](frontend-responsive.md).

## Run the isolated browser suite

From the repository root, with the frontend lockfile dependencies installed:

```sh
cd frontend
npx playwright install chromium webkit
npm run test:e2e
```

`frontend/playwright.config.ts` runs Chromium and WebKit against loopback port
5174 with `frontend/vite.e2e.config.ts`, which has no backend proxy. Tests install
synthetic `/api/v1` fixtures and close WebSockets. Unrecognized API requests are
blocked and recorded; selected GUI cases override specific routes with simulated
mutations to exercise pending saves, auth changes and upload failures. They do
not contact the backend or command hardware.

The config may reuse an existing server outside CI. Confirm port 5174 belongs to
this isolated preview before running; do not reuse a production/dev proxy there.
Browser installation may need network access. The pinned version is in
`frontend/package.json`/`package-lock.json`; keep it aligned with installed browser
binaries rather than copying old local runtime paths.

## Current coverage owners

| Suite | What it checks |
| --- | --- |
| `frontend/e2e/gui-completion.spec.ts` | Fixture-backed route sweep, Settings navigation/search/theme, Admin and standard-user access, landscape drawer, long directory labels, short-screen editors, dirty/pending saves, settings category isolation, read-failure retry, account reset and Movement detail scrolling. |
| `frontend/e2e/responsive.spec.ts` | Resize without reload, 720px/980px boundaries, sidebar preferences, snapshots, profile menus, modal focus/drafts, date popovers, safe areas, coarse pointers and reduced motion. |
| `frontend/e2e/directory-layout.spec.ts` | Long labels, enlarged text, overlap/overflow and keyboard row activation in People, Groups and Vehicles. |

The route sweep uses a fixed list in the test: when adding a route, compare it
with `frontend/src/app/navigation.tsx` and extend fixtures/coverage explicitly.
Do not assume a passing sweep automatically covers a new destination.

For a focused rerun, use the existing Playwright CLI, for example:

```sh
npm run test:e2e -- gui-completion.spec.ts --project=webkit
```

## Record evidence and limits

- Run the required frontend build/unit checks in the agent guide as well.
- Review generated screenshots for the affected state, theme and viewport.
  The ignored `frontend/test-results/playwright/` directory contains screenshots
  and retained failure traces; preserve needed evidence before a subsequent run.
- Record source revision/working-tree context, commands, engine, viewport,
  fixture/live status, actual results and unresolved failures in the task report.
  Distinguish a full clean run from focused repairs; do not reuse historical
  counts or machine-specific temporary paths as current evidence.
- Fixtures validate browser behavior and layout. They do not prove provider
  health, physical outcomes, production migrations, live notification delivery,
  backend cursor consistency or live hardware recovery.
  Backend invariants use [Phase 1](phase1.md). Live deployment and operational
  checks need separate authorization under the
  [hardware safety guide](../agent/hardware-safety.md).
