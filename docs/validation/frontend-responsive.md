# Responsive frontend validation

The production suite is `frontend/e2e/responsive.spec.ts`. Run it through
`npm run test:e2e -- responsive.spec.ts` from `frontend/`, following the isolated
setup in [browser validation](gui-completion.md). Chromium and WebKit use
synthetic API reads, reject unexpected requests/mutations and close WebSockets.

## Invariants and source owners

| Area | Preserve and inspect |
| --- | --- |
| Navigation | The drawer uses the 980px boundary in `frontend/src/app/App.tsx`; compact content/editor styling uses 720px. Test 719/720/721 and 979/980/981 without reload, including desktop preference restoration and active Settings child visibility. |
| Safe areas | `frontend/index.html` enables `viewport-fit=cover`; `styles/base.css` defines independent top/right/bottom/left tokens. Test unequal and mirrored insets at shell, overlay, toast and chat boundaries. |
| Available space | No document overflow, clipped text or unreachable actions. Preserve intentional local table/calendar scrolling and short-height editor scrolling. |
| Dialogs | `ui/useModalFocus.ts` owns containment/restoration and `ui/useEditorDismiss.ts` owns dirty/pending dismissal. Preserve nested dialogs, drafts and selected records across resize. |
| Floating content | `lib/viewportPlacement.ts` handles per-edge and visual-viewport placement. Inspect report, schedule, workflow, snapshot and chat consumers when it changes. |
| State and input | Snapshot selection/keyboard use, Alfred draft/scroll-lock cleanup, named navigation controls, coarse-pointer targets, usable input text and reduced motion. |

Paths without a prefix in the table are relative to `frontend/src/`. Read the
test's viewport matrix rather than copying old browser/device version numbers.
Route/editor sweeps and enlarged directory text are covered by the companion
suites in [browser validation](gui-completion.md).

## Screenshot review

Inspect affected screens at desktop, tablet, narrow phone and short landscape
sizes, with light/dark themes as relevant. Compare equivalent data, selection,
scroll position and viewport before/after. Generated screenshots are ignored
under `frontend/test-results/playwright/`; report the fixture state and preserve
any evidence needed after subsequent runs.

## Manual device checks

Browser emulation does not reproduce physical hinges, OS keyboards, browser
toolbar transitions or every accessibility setting. On available target devices:

- Rotate and resize while editors, navigation and snapshots are open; confirm
  drafts, selections, focus and actions remain usable.
- Expand/collapse browser chrome, show/dismiss the keyboard, and reduce height
  through multitasking. Check floating menus and the last Settings item.
- Increase text size, enable reduced motion and use touch and keyboard input.
- Check local table/calendar scrolling and all outer safe-area edges. If the
  device has multiple segments or a hinge, inspect each posture and split view;
  outer safe-area insets alone do not describe an interior hinge.

Record unavailable physical checks as limits. No device-specific implementation,
deployment status, provider health or hardware success follows from a browser
layout test.
