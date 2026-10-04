# Documentation map

These guides describe the current source tree. They are not evidence that a
change has been deployed or passed validation. Source, contracts, migrations, and
retained results are the authority for those claims.

| Task | Start here |
| --- | --- |
| Local setup, ports, proxy, authentication | [Project README](../README.md) |
| Agent work and safety boundaries | [AGENTS.md](../AGENTS.md) |
| Service ownership, extension, retirement | [Architecture](architecture.md) |
| Backend, API, Alfred, workflows, integrations | [Backend guide](agent/backend.md) |
| React routes, typed clients, styles, realtime | [Frontend guide](agent/frontend.md) |
| LPR, gates, garages, supervised hardware tests | [Hardware safety](agent/hardware-safety.md) |
| ESPHome gate controller integration | [ESPHome](../ESPHOME.md) |
| UniFi Protect wrapper and private protocol notes | [UniFi Protect](unifi-protect-private-api.md) |
| Isolated tests and retained evidence | [Validation harness](validation/phase1.md) |
| Recovery invariants and diagnostics | [Recovery boundaries](validation/recovery-boundaries.md) |
| Schema comparison, upgrades, restore rehearsal | [Recovery schema](validation/recovery-schema.md) |
| Resident missed-exit behavior and evidence | [Missed-exit recovery](validation/missed-exit-recovery.md) |
| Frontend browser behavior checks | [GUI validation](validation/gui-completion.md) |
| Responsive layout checks | [Responsive validation](validation/frontend-responsive.md) |
| Removed runtime updater migration/rollback | [Retirement note](releases/remove-dependency-updaters.md) |

`prototypes/premium-dashboard/` is a separate fixture-driven visual prototype;
its local README and agent guide apply to that project, not the production UI.

## Keeping context useful

Keep root agent instructions short and route tasks to the relevant guide. Record
non-obvious contracts and safety boundaries beside their owning code paths.
Avoid duplicating API catalogs, line-number inventories, test counts, and release
status across documents. This follows OpenAI's
[Astra guidance for repository instructions](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra):
use contextual reading, clear execution boundaries, and completion criteria.

When behavior changes, update its guide and links in the same change. Store
run-specific outcomes in the harness's retained evidence, with source identity
and explicit limitations. Superseded phase plans, architecture checkpoints, and
milestone reports have been removed; their history remains in Git. Preserve
migration/rollback constraints and reusable validation procedures while they
still affect supported installations.
