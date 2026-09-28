# Responsive frontend regression record

The Playwright suite lives in `frontend/e2e/responsive.spec.ts` and runs with
Chromium and WebKit using the exact Playwright version pinned in
`frontend/package.json`. It mocks the authenticated session and every `/api/v1`
request. Only known read endpoints receive fixture data; unknown routes and
mutating requests are blocked and fail the test. The realtime WebSocket is
closed locally. No hardware command or real backend is reachable from the suite.

The browser checks cover the approved viewport samples and 719/720/721 pixel
transitions without page reloads, document overflow, mobile navigation access,
dashboard snapshot keyboard use and state through resize, a resized group-form
draft and modal focus, native dialog keyboard containment, JavaScript date
popover placement, chat draft and scroll-lock cleanup, asymmetric and mirrored
safe-area bounds, and coarse-pointer target/input sizing. Representative
short-wide and small-phone screenshots are saved in Playwright's ignored
`frontend/test-results/playwright` output directory for review or failure
evidence. Vitest remains the unit/component runner.

## Audit and changes

| Area | Finding and implemented correction |
| --- | --- |
| Viewport and safe areas | Added `viewport-fit=cover` with independent `--safe-top/right/bottom/left` and spacing-aware edge tokens. Shell, authentication, overlays, toasts and chat apply insets at their boundaries. |
| Navigation | Removed orientation-dependent header rules, aligned compact navigation at the existing 720px content threshold, bounded the intermediate rail, and retained accessible names when labels are visually hidden. Removed main-content resize animation that caused transient overflow. |
| Available space | Removed the document minimum width; dashboard, integration and schedule grids use local/intrinsic sizing. Deliberate table/calendar scrolling remains. Presence previews are bounded by their card's content area. |
| Dialogs | Bounded widths/heights, preserved short-height scrolling, and added shared focus isolation/restoration, nested-dialog handling and visual-viewport keyboard bounds without remounting editors. |
| Floating content | Added measured placement with per-edge bounds and visual-viewport/container updates for report, schedule, workflow, snapshot and chat menus. No device or posture identification. |
| State and touch | Dashboard snapshots remain selected across resizing with click/keyboard access; chat updates its scroll lock across the compact threshold without losing drafts. Coarse-pointer targets and form text remain usable. |

No backend contracts, command confirmation semantics, PWA installation features,
native Apple APIs, or experimental fold APIs were added or changed. Existing
working-tree changes were retained.

Design references: [Apple HIG](https://developer.apple.com/design/human-interface-guidelines/designing-for-iphone-duo),
[Apple preparation talk](https://developer.apple.com/videos/play/tech-talks/111461/),
[WebKit safe areas](https://webkit.org/blog/7929/designing-websites-for-iphone-x/).
Current MDN compatibility data lists no Safari implementation of
[Viewport Segments](https://github.com/mdn/browser-compat-data/blob/main/api/Viewport.json)
or [Device Posture](https://github.com/mdn/browser-compat-data/blob/main/api/DevicePosture.json).

## Browser audit evidence

| Stage | Chromium | WebKit | Evidence |
| --- | --- | --- | --- |
| Before responsive changes | Not captured | Not captured | Responsive edits were already present in the uncommitted worktree when this browser suite was assigned. |
| After responsive changes | 9/9 passed | 9/9 passed | Playwright 1.62.1; Chromium 151.0.7922.34 and WebKit 26.5. All `/api/v1` requests were fulfilled or blocked locally. |

Validation used bundled Node.js 24.19.0 and the pre-existing installed dependency
tree, which differs from the manifest: React 19.2.6, TypeScript 6.0.3, Vite 8.1.0,
Vitest 4.1.7, jsdom 29.1.1, Testing Library React 16.3.2, and Vite React plugin
6.0.1. The manifest's corresponding versions are 19.3.0, 7.0.2, 8.3.0, 5.0.0,
30.0.1, 16.3.3, and 6.1.1. These unrelated upgrades were not overwritten.
TypeScript checking, the production build, 224 unit tests and `git diff --check`
passed with the installed tree. There is no configured lint script.

Playwright is pinned separately at 1.62.1. Standard reproduction commands after
installing the manifest dependencies:

```sh
cd frontend
npx playwright install chromium webkit
npm run test:e2e
npm test
npm run build
```

Here, npm was unavailable on PATH; validation invoked the same local CLI scripts
with the bundled Node executable. Browser binaries were installed under
`/tmp/iacs-playwright-browsers` and selected with `PLAYWRIGHT_BROWSERS_PATH`.
The checked-in test configuration has no dependency on those local paths.

## Manual iPhone Duo checklist

Browser-engine tests do not emulate the Duo hinge, software keyboard, posture,
or toolbar changes. Xcode/`simctl` was unavailable here. Verify in Xcode 27.1's
Duo simulator and on a physical Duo where available, in both orientations and
on both displays:

- Open and close the device while the console is loaded; rotate while loaded.
- Check book and tabletop folds, including the app spanning the hinge.
- Check the app in both left and right Split View positions.
- Repeat with Safari chrome expanded/collapsed, show and dismiss the software
  keyboard, and reduce available height through Picture-in-Picture/multitasking.
- Scroll the dashboard, directories, schedules, movements, reports, workflows,
  integrations, settings, logs, and Alfred screens; check dense tables and the
  weekly schedule's intentional local scrolling.
- Open navigation, alerts, search, dialogs, and floating menus near each edge;
  confirm controls remain reachable, Escape works, and focus returns after a
  dialog closes.
- Enter and edit forms, resize or rotate while drafts and selections are active,
  and verify the values remain. Expand and collapse a dashboard snapshot, then
  change posture, rotate, and resize.
- Check reduced-height layouts and long labels with enlarged text.
- If standalone launch is available on the device, repeat the edge and toolbar
  checks there.

Safe-area insets protect the outside edges. They do not identify or describe the
interior hinge, so hinge avoidance requires this physical-device review.
