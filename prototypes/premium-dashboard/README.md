# Premium dashboard preview

Standalone React/Vite prototype for the selected Prismatic Glass dashboard design.

When started with the command below, the preview is available locally at **http://127.0.0.1:4173/** and on the host's LAN address. The recorded LAN address is `10.0.0.50`; it is not a guarantee that a server is running. All state is in memory; there are no production API, hardware or realtime connections. Portraits, snapshots, people and vehicle details are samples.

## Interactions

- Choose 6h or 12h, hover/focus intervals, or select an event marker. Arrow keys, Home and End move timeline focus; Enter/Space selects. Phone Previous/Next controls also select intervals.
- Click event rows to switch or collapse their evidence. Older sample events demonstrate unavailable snapshots.
- Inside, Expected and Exited open their respective sample rosters.
- Search filters recent events by person, plate or event type.
- **Preview controls → Simulate arrival** adds Jamie once, changes presence from 3/3 to 4/2, and preserves the selected event. **Reset preview** restores the initial fixture and Steph snapshot; the animation preference remains selected.
- **Preview controls → Animations** compares motion with an instant update. System reduced motion always takes precedence.
- The sidebar, alerts and account controls show local preview content. Access-point statuses are display-only.

## Development and checks

```sh
npm ci
npm run dev -- --host 0.0.0.0 --port 4173 --strictPort
npm run build
npm test
```

The initial time is frozen at 2 October 2026, 22:15 Europe/London. Simulated arrival advances it by one minute. `src/model.js` owns the sample fixtures and timeline calculations.

`src/App.jsx`, `src/Pulse.jsx` and `src/Presence.jsx` own the preview interactions. `npm test` runs fixture/model and Sites worker tests. `npm run build` emits both the Vite app and the Sites handoff artifacts described in [AGENTS.md](AGENTS.md); it does not publish them.

See [design-qa.md](design-qa.md) for retained reference captures and a repeatable review checklist. The production frontend remains a separate application.
