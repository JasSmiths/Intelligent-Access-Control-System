# Premium dashboard design review

The approved direction is option 3, Prismatic Glass; durable design constraints
live in [AGENTS.md](AGENTS.md). This is a sample-data prototype, separate from
production IACS. Its screenshots do not establish live-system behavior.

## Retained reference captures

These captures were reviewed on 2 October 2026. They document that design state,
not a current build/test result. Use the initial fixture for comparison: 22:15
Europe/London, Steph’s 19:23 snapshot expanded, and 19:15–19:45 selected.

- [Reference and implementation, 1440 × 1024](qa/reference-comparison.jpg)
- [Desktop, 1440 × 1024](qa/desktop-1440.jpg)
- [Tablet, 1024 × 768](qa/tablet-1024.jpg)
- [Phone, 390 × 844](qa/mobile-390.jpg)
- [Phone with Sylvia’s evidence expanded](qa/mobile-evidence-390.jpg)

The reference was normalized from 1487 × 1058 for the paired comparison.
Intentional adaptations include fictional sample imagery, labelled preview
controls, proportional presence bars, a tablet rail and stacked phone sections.

## Repeat after changes

1. Run the build and tests in [README.md](README.md). Record actual results and
   source context; prototype tests do not validate the production frontend.
2. Compare matching viewport, fixture, selection and scroll state. Check slate
   colors, restrained glass, density, text fit and natural image proportions.
3. Exercise 6h/12h ranges, hover and keyboard interval selection, linked event
   highlights, one expanded evidence row and unavailable snapshots.
4. Open all rosters, filter and clear search, simulate Jamie’s arrival once, and
   reset. Check counts, selected evidence and the retained animation preference.
5. Check sidebar/drawer focus, Escape and focus return; named controls; short
   screens; 44px phone timeline controls; resize without losing selection.
6. Compare animation on/off and the OS reduced-motion preference. Inspect
   missing images, console errors and overflow at all three reference sizes.

Use actual devices for touch and OS/browser behavior when available. Browser
clicks at a phone viewport do not establish physical touch or keyboard behavior.
No production APIs, realtime streams, provider tests or hardware commands belong
in this review.
