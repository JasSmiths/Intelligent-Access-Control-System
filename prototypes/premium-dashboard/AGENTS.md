# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before substantial visual changes, read the current Product Design skill from the available skill catalog and resolve the intended visual reference. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.

## Accepted design and scope

- Option 3, Prismatic Glass, is the approved visual reference. Prefer dark slate, selective glass, restrained color, fine timeline marks and dense, readable operational content. Avoid saturated blue glows, oversized icon tiles and generic card grids.
- Keep the horizontal presence summary, timeline and expandable event evidence. Default to Steph at 19:23 and the 19:15–19:45 interval.
- This is a standalone sample preview. Do not connect production APIs, realtime streams, access devices or hardware. Production integration is a separate task.
- Initial sample time is 2 October 2026, 22:15 Europe/London. Generated portraits and snapshots are fictional sample assets.
- Use 220 ms movement/expansion and 180 ms fades, honor system reduced motion, and retain the manual comparison toggle. Keep images at their natural aspect ratio.
- Desktop retains the full sidebar; tablet starts with an expandable icon rail; phone uses a focus-managed drawer and explicit 44 px timeline controls.
- When a preview is requested, bind to `0.0.0.0` on port 4173 so LAN and loopback links work. The recorded host address is `10.0.0.50`; verify the current address and server before claiming that a preview is running.
