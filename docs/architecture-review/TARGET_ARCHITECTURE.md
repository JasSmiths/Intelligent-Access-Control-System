# Target architecture and strategy decision

Implementation decisions and progress are recorded in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md). The accepted target uses selected manual gates, explicit designated-entry admission, no ambiguous replay, 60-second recognition/scheduled catch-up, requester-bound Admin approvals, and outcome-based visitor consumption. This dated design is implemented through the existing owners; deployment remains separate.

Proposal, 12 September 2026. **All new paths, types, tables and interfaces in this document are proposed, not current repository evidence.** Current evidence and preservation constraints are in [AUDIT.md](AUDIT.md); delivery/rollback gates are in [REMEDIATION_PLAN.md](REMEDIATION_PLAN.md). Keep [the current ownership guide](../architecture.md) authoritative until each approved package lands.

## Decision

Use **B: staged replacement of specific execution/state mechanisms behind stable contracts**, inside the existing modular monolith. Combine this with deep structural refactoring where the current mechanisms are already sound. Retain FastAPI, Python, SQLAlchemy/PostgreSQL, Redis, React/TypeScript, Vite/Nginx and Docker Compose with bind mounts. Do not introduce business microservices, another worker platform, a new state manager, generic event bus, plugin framework or universal repository/DI layer.

Replace Alfred's pending-approval mechanism and automation's uncheckpointed execution mechanism. Strengthen the actuator intent/outcome/attempt protocol and historical movement operation; these can retain the existing coordinator/device/provider implementation where its semantics already fit. Replace browser report-preview calculation with the existing backend report read model. Refactor the remaining ownership/lifetime seams. This is substantial behavioral and state work, not a file-moving programme.

### Alternatives considered

| Criterion | A. Deep structural refactoring | B. Scoped replacement — recommended | C. Major/full rewrite with controlled migration |
|---|---|---|---|
| Problems resolved | Can resolve backwards imports, UI lifetimes, shared mutations and transaction placement; could also evolve journals in place | Directly resolves approval identity/claim, automation recovery, command uncertainty and historical ownership; keeps proven policy | Could redesign all state and contracts, including difficult historical entanglement |
| What remains unless addressed explicitly | Moving functions does not create durable acceptance, current-role checks or per-target uncertainty | Existing large cohesive services/ORM registry remain; adjacent interfaces must be ratcheted or coupling returns | Undocumented product rules, hardware ambiguity and migration evidence still remain; new code does not remove them |
| Retained components/knowledge | Almost all current code and fixtures | FSM/plan, ingest/ledger concepts, schedule/visitor rules, notification journal, V3 planning/catalogs, adapters, UI shell/features, schema history | Mainly contracts, product knowledge, fixtures and adapters; retaining more effectively turns C back into B |
| Prerequisites | Characterize mutation/transaction behavior and protect all callers | Same, plus durable boundary/crash matrix and explicit data compatibility | Whole behavior catalogue, external consumer inventory, legacy-data corpus, comparative inert replay and robust migration rehearsal |
| Compatibility/state | Usually fewer schema additions, but patching overloaded JSON may preserve complexity | Additive approval/action/intent records where needed; retain routes/IDs/history; adapters have removal gates | Dual schema/read paths or translating historical model; external API preservation still required |
| Deployment/rollback | Smaller releases; source/image/schema coupling still matters | One owner cutover per subsystem; historical unknown work review-only; no two live executors | Can be staged, but broader dual-operation/read complexity and harder rollback after new writes |
| Regression risk | Lower per edit; risk of missing semantic consumers across many edits | Concentrated, reviewable high-risk seams; surrounding proven code retained | Highest breadth: reconstructed direction, visitor, messaging/privacy and UI behaviors all at risk |
| Feature development | Continues, but some changes repeatedly touch old ownership | Freeze touched contract while replacing; unrelated features continue against existing owners | Long feature-parity effort; parallel implementation creates recurring port/review burden |
| Relative effort/uncertainty | Medium total if existing records fit; can become high through serial patches | Medium-high in approvals/automation/state tests; lower elsewhere; bounded unknowns | Very high and hardest to bound with current evidence; no credible calendar estimate yet |
| Deletion | Must explicitly remove former callers/policies, not keep aliases | Retire replaced JSON writer/execute loop/repair writes/browser calculators in the cutover package | Old application removed only after all feature/data/operational parity gates; greatest chance of permanent parallel architecture |
| Simpler final system? | Yes if ownership is fixed; no if merely rearranged | Yes: fewer policy/execution owners and one meaning per state boundary | Possible, but no demonstrated simplification large enough to justify replacing sound modules |

**Strongest case against B:** adding approval/action/attempt state can increase schema and review complexity where a careful in-place change to current records might suffice. Current ledger and NotificationRun already solve parts of the problem; another general journal would be duplication. The first milestone therefore tests actual failure boundaries and records the minimum data needed. Prefer extending current GateCommandRecord/per-device metadata and AutomationRun before adding related tables. Use a new table only where per-target/action concurrency, immutable identity or independently claimable state requires it.

**Evidence that would change the decision:** choose more A if the current record formats express all required cases without version branching, concurrent ownership tests pass with one shared writer, and public contracts need no translation. Choose broader C only if bounded exercises show multiple foundational domains cannot express intended state without pervasive incompatible rewrites, historical migration is cheaper/safer as one controlled model replacement, and inert old/new comparison proves the existing product contract. A failed isolated test, high LOC or the 52-module syntactic SCC alone is insufficient.

## Target responsibilities

Keep one database and one application process topology. A “module boundary” means explicit policy/state ownership and permitted calls, not a new service/container. Cohesive existing files may remain at their current paths. Paths are introduced or retired only in a package that removes an identified backwards dependency, duplicate transition or hidden effect.

### Proposed backend tree

`[keep]` means an existing path stays; `[evolve]` changes responsibility in place; `[new]` is a proposed path. This is a selected responsibility tree, not a request to relocate every service or model.

```text
backend/app/
  main.py                                  [evolve: composition only, failure-safe lifetime]
  api/router.py, api/v1/*                   [keep: versioned transport/adapters]
  api/confirmations.py, dependencies.py     [keep: transport auth/error translation]
  core/config.py, crypto.py, auth_secret.py [keep: bootstrap/security primitives]
  db/session.py, base.py                    [keep: shared SQLAlchemy plumbing]
  db/bootstrap.py                           [evolve: explicit startup/schema contract]
  models/core.py, enums.py, __init__.py      [keep: schema registry, writer ownership enforced]
  services/
    access_events.py, lpr_ingest.py         [evolve: receipt/queue lifecycle only]
    access/
      reads.py, decision.py                [keep: immutable normalized data + pure policy]
      evidence.py                          [evolve: normalized evidence; no vendor I/O in DB txn]
      execution.py                         [evolve: live access application transaction]
      enrichment.py, payloads.py            [keep/evolve: optional data + typed handoffs]
      historical.py                        [new: one hardware-inert historical operation]
      hardware.py                          [keep: authorized command orchestration]
    movement_fsm.py, movement_ledger.py     [keep/evolve: decision and durable state]
    movement/presence.py, sessions.py       [evolve: sole transition eligibility/ordering]
    movement_reconciliation.py              [evolve: command-attributed recovery]
    restart_backfill.py                     [evolve: source discovery/cursor, calls historical]
    gate_commands.py                        [evolve: audited gate intent/attempt/result owner]
    access_devices.py                      [evolve: audited target device execution/config]
    gate_malfunctions.py                    [keep: bounded recovery intent producer]
    schedules.py, schedule_operations.py,
      schedule_assignments.py,
      schedule_overrides.py                 [keep: shared schedule owners]
    visitor_passes.py                       [evolve: validity/reservation/outcome transitions]
    visitor_conversations.py                [new if extraction removes channel-owned policy]
    automations.py                         [evolve: rule CRUD/selection/scheduler ingress]
    automation_policy.py                   [new: provenance/admission + cohesive pure rules]
    automation_execution.py                [new: occurrence/action journal and dispatch]
    notification_rules.py                   [evolve: all rule mutation incl machine activation]
    notification_policy.py                 [new: pure rule/plan normalization, no delivery]
    notifications.py                       [keep/evolve: rendering and provider translation]
    notification_runs.py,
      notification_dispatch.py              [keep: proven durable plan/lease/checkpoint]
    domain_events.py                        [evolve: typed durable origin handoffs where required]
    event_bus.py                            [keep: best-effort realtime fanout only]
    action_confirmations.py                 [keep: existing token confirmation API semantics]
    alfred/
      runtime.py, planner.py, executor.py,
      permissions.py, memory.py,
      feedback.py, streaming.py,
      embeddings.py, answer_contracts.py     [keep/evolve only named seams]
      approvals.py                          [new: atomic V3 approval store/state]
    chat.py                                 [evolve: conversation/turn orchestration, no approval JSON writer]
    messaging_bridge.py                     [keep: verified transport actor to V3]
    messaging/*                             [evolve: delivery, webhook and transport adapters]
    whatsapp_messaging.py                    [retire after direct caller migration; no facade backrefs]
    discord_messaging.py, icloud_calendar.py  [keep: vendor-specific orchestration to domain ops]
    settings.py, mutation_context.py, auth.py [keep/evolve: shared active actor/config invariants]
    maintenance.py                          [evolve: state + mandatory audit atomically]
    telemetry.py                            [keep: transactional audit helper + optional traces]
    reports.py, investigations/*,
      snapshots.py, snapshot_recovery.py,
      profile_photos.py, chat_attachments.py [keep: independent read/artifact owners]
    dependency_updates.py                   [evolve: complete candidate/restore artifact unit]
    ... existing enrichment/analytics owners [keep unless a named finding changes ownership]
  modules/
    lpr/*                                   [keep: vendor payload -> PlateRead]
    access_devices/*, gate/base.py           [evolve: delivery certainty and observation contracts]
    gate/access_devices.py                  [evolve: truthful aggregate; injected device owner]
    home_assistant/*, unifi_protect/*,
      notifications/*, messaging/*,
      announcements/*, icloud_calendar/*,
      dvla/*                                [keep: vendor I/O, transport errors, credentials]
    registry.py                             [keep: explicit construction; no core-policy dependency]
  ai/tools.py, tool_inputs.py, context.py    [keep: stdlib-only contracts]
  ai/providers.py, tool_groups/*             [keep: provider protocol + catalog/handlers]
  simulation/*                              [isolated-test-only full-flow suite; explicit live injection distinction]
backend/alembic/                             [evolve only approved frozen/additive migration contracts]
```

No new `V2/` application, compatibility shim, all-purpose `shared.py`, generic repository hierarchy or new deployment boundary is proposed. Keep `GateCommandCoordinator` and `AccessDeviceService` at the audited paths required by AGENTS. Keep the central model registry initially; dividing ORM files without restricting writers would not repair IACS-04 / IACS-07.

### Ownership and dependency map

| Module | Owns | Must not own | Callers / permitted dependencies | Independent test boundary |
|---|---|---|---|---|
| Recognition ingress | Signature/source validation, normalization, durable receipt identity, debounce/suppression lifecycle | Authorization or controller retry policy | Webhook/Protect/simulator adapters → normalized read/store; no direct actuator | Malformed/duplicate/restart receipts with inert store/real isolated DB |
| Access policy/evidence | Typed identity/window/direction evidence and one decision | Vendor transport, delivery, conversation or UI policy | Live/historical application operations; pure contracts/FSM and narrow evidence adapters | Pure decision matrices and immutable evidence fixtures |
| Live access application | Short decision transaction, current permission/pass revalidation, command requirement, durable required handoff | PDF/media/LLM network work in transaction | Ingest worker → policy/visitor/movement/command owners | Real isolated DB plus inert command/enrichment sinks |
| Historical access | One event/saga/session/audit repair policy, explicit confidence/order | Any hardware or automatic notification delivery | Restart/Alfred Admin repair → normalized historical evidence and movement/visitor owners | Capability-isolation check plus no-actuation history matrices |
| Movement/presence | Eligible transition, deterministic ordering, atomic presence update and sessions | Raw GRANTED-as-passage shortcut; direct HA sync | Live/historical/reconciler → immutable committed outcome; SQLAlchemy | Concurrent fresh sessions, stale/equal-time/replay/missing-row fixtures |
| Gate/device execution | Authorized logical command, exact target set, durable attempt/receipt/observation, reconciliation | Reconstructing vehicle identity or inventing authorization from event strings | Trusted access/manual/automation/malfunction intent producers → provider protocols/ledger | Accepted/rejected/not-sent/unknown/partial/late observations with inert adapters |
| Schedules | Canonical time rules, precedence, assignment and override mutations/audit | Device calls, presentation, a duplicate visitor lifecycle | Directory/device/visitor/access adapters → typed settings/time/ORM | Time/DST/precedence plus transaction parity |
| Visitor passes | Validity at stated time, claim/reservation, consumption/outcome and history | WhatsApp payloads, delivery retries, direct controllers | API/calendar/access/conversation operation → schedules/identity/audit | Claim/cancel/lifecycle/outcome concurrency; existing consent/plate contracts |
| Visitor conversations | Channel-neutral pass-scoped consent/window/plate request decisions | Meta HTTP, arbitrary Admin tools or contact lookup outside pass | Verified visitor transport adapters → visitor operations; scoped persisted context | Same conversation on two inert channels, privacy/abuse/window matrices |
| Automation rules/policy | Rule CRUD, approved trigger scope, admission based on provenance | Retrying side effects, writing other domains' tables | API/Alfred/scheduler/webhook/origin handoff → catalogs/pure policies | Cross-trigger authority matrix including unknown denial |
| Automation execution | Durable occurrence/action progression and stable action IDs | Reimplementing schedule/pass/rule mutation, inferring success from exception absence | Admitted trigger → explicit domain operations/vendor delivery through owners | Kill/cancel at every boundary, unknown historical work review-only |
| Notifications | Rule mutation, rendered plan, recoverable delivery checkpoints | Actuation authority, automatic ambiguous resend, raw event as accepted work | Required outbox consumer/API sends → existing store/dispatcher/providers | Existing recovery suite plus origin handoff parity |
| Alfred V3 | Planning, evidence, scoped tool invocation, answers/memory; approval owner claims validated action | Becoming the owner of visitor/access/history policy; trusting tool-supplied actor | API/messaging actor → contract/catalog/permission/domain handlers | Catalogue/provider/permission fixtures and atomic approval DB tests |
| Config/auth/audit | Active actor/role/token invariants, encrypted settings, mandatory mutation audit | Vendor behavior or app-wide arbitrary policy | All operation owners → narrow primitives/ORM; no delivery graph | Role expiry/demotion, root encryption, rollback-before-commit; no credentials |
| Read models/artifacts | Server report/inspection/search interpretation, media paths and immutable export snapshots | Hardware/mutation on read or frontend alternate truth | APIs and read tools → authorized SQL queries/artifact owners | Query fixtures, redaction, preview/export parity; optional PDF visual QA |
| Composition/operations | Construct/start/stop collaborators; verified source/image/schema candidate lifecycle | Business decisions or a production-aware default test runner | main/approved operations → explicit service interfaces and existing Docker tooling | Fake lifecycle tree; offline candidate/restore; no source-bound live tests |

Direct cross-domain reads are permitted where they make a report/evidence query simpler, through an explicitly named read model. Cross-domain **writes** require the owner operation. A service may accept an AsyncSession as a transaction participant; it must declare whether it commits. Use immutable IDs/value records at a boundary where detached ORM instances currently permit stale overwrite; do not replace every SQLAlchemy call with a repository interface.

## Public contracts and state

The following are boundary sketches, not implementation code or final public HTTP schemas. Preserve existing `/api/v1` routes, status codes, required confirmations, field meanings, IDs, media URLs, migration history and provider protocols until an approved compatibility change says otherwise. Evolve HTTP with additive typed fields first. Existing `accepted` and `mechanically_confirmed` fields must have documented legacy projections; a richer result cannot silently redefine them as proof of passage.

| Proposed contract | Required content / invariants | Owner |
|---|---|---|
| `RecognitionObservation` | Stable receipt/source identity, captured/received times, normalized plate/confidence, typed optional direction/gate evidence, untrusted-origin marker | LPR contract/ingress |
| `AccessDecision` | Decision ID; identity resolution; allowed/denied + reason; time/config evidence; intended direction; LIVE/HISTORICAL mode; whether command is required | Access policy/application |
| `AuthorizedCommandIntent` | Stable operation ID; trusted authority variant; exact target set; desired action; reason; freshness/expiry policy; source decision/approval/rule/attempt reference | Trusted intent producer; execution owner validates shape/preconditions |
| `CommandReceipt` | Per-target `not_dispatched`, `rejected`, `accepted` or `unknown_delivery`; observed state/time/source; attempt ID; aggregate partial/unknown; reconciliation reference | Gate/device execution |
| `EligibleMovementOutcome` | Event/saga ID, committed outcome, observed time, deterministic ordering key, evidence confidence, no inference from GRANTED alone | Movement domain |
| `VisitorReservation` / outcome | Pass/version, validity basis, reservation ID, chosen consume policy and final outcome; historical evaluation cannot actuate | Visitor domain |
| `TriggerContext` | Stable origin/occurrence ID, approved actor scope, original decision/provenance, captured/accepted time, dry-run/historical flags; no caller-supplied elevation | Automation admission |
| `ActionExecution` | Run/action stable ID, immutable input/target criteria, pending/attempting/accepted/rejected/unknown/skipped, reason and lease token | Automation journal |
| `PendingApproval` | ID, real actor + conversation scope, validated action/arguments or secure references, expiry, claimed/consumed/review status, stable operation ID | Alfred approval store |
| `RequiredDeliveryIntent` | Origin + recipient selection criteria/event version + unique delivery identity, no plaintext credentials; written with origin transaction | Specific origin owner/outbox |
| `ReportPreview` | Period/options/site timezone, complete semantic report data, as-of time/completeness, same calculations as export, no artifact creation | Reports read model |
| `RealtimeEnvelope` | Versioned event name/payload identity, timestamp, correlation; declared frontend impacts/direct consumers; optional, never proof of business acceptance | Domain producer + realtime/UI fixture contract |

“Accepted” means provider accepted a request, not physical passage. “Observed open/opening” is actuator evidence, not identity or permission. A provider timeout after possible transmission yields unknown delivery, not failure eligible for blind fallback. No command retry policy can be inferred solely from HTTP status or an exception class.

### Authority directions

- Manual operator intent: current active Admin and valid payload/target-bound confirmation. Changing target set invalidates confirmation.
- Recognition intent: allowed typed decision from the access owner, with known/visitor authorization and agreed current-time revalidation. Unknown denied observation never becomes an automated command through a later event bridge.
- Scheduled/signed-webhook automation: active rule with explicit approved scope/provenance, independently governed by automation admission. Not every scheduled action requires a fictitious vehicle decision.
- Malfunction recovery: explicit bounded recovery authority, current maintenance/keep-open/device preconditions and stable attempt identity; retain existing supervised configuration behavior.
- Phrase trigger: permitted user scope is a recorded product decision, not silently inherited from the label “Automation Engine.”

Internal typed authority is not a magic security token: only trusted owner functions construct it, transport inputs cannot supply it, and execution validates current revocation/preconditions where relevant. Use normal direct calls; no cryptographic internal bus or policy framework is needed.

### Persistence ownership and migration requirements

| Record group | Target writer / persistence rule |
|---|---|
| People/vehicles/groups/assignments | Directory owner plus explicit assignment participant; enrichment updates only owned vehicle fields |
| Schedule/override | Existing schedule owners; audit in same transaction; callers cannot mutate schedule rows to bypass validation |
| VisitorPass | Visitor operation; channel metadata scoped/merged atomically; consumption tied to approved outcome policy |
| LprIngestEvent | Ingest owner; durable terminal suppression/recovery policy and stable identity |
| AccessEvent/saga/session/presence | Access application transaction uses movement/visitor participants; historical owner uses same eligible transition; no direct Alfred/restart writes |
| GateCommandRecord/observations/malfunction | Existing coordinator/ledger/device/malfunction owners; extend current records for exact targets/delivery certainty; child target-attempt rows only if isolated concurrency experiment requires them |
| NotificationRule/Run/action context/outbox | Rule owner for state; store/dispatcher for delivery; specific origin writes unique outbox intent in its transaction |
| AutomationRule/Run | Rule/policy owner; run/action journal. Prefer extend current run; per-action child records if needed for fenced independent checkpoints |
| Alfred approval | Proposed dedicated approval record (separate from conversation JSON), keyed by current confirmation ID; action/actor/expiry/operation/attempt status durable. Generic ActionConfirmation retains its token API. Share current actor/schema invariants, not an all-purpose workflow engine |
| ChatSession/messages/learning | Conversation/memory owner; approvals no longer overwritten by context save. Scoped private/shared access is explicit |
| Settings/auth/maintenance/audit | Existing respective owners; mandatory audit same transaction; optional traces may be best effort |
| Reports/media/analytics/updates | Their current owner, with read/projection vs mutation distinction. Update candidate stores full manifest/lock/image/schema compatibility metadata |

New approval/action/outbox fields/tables are **additive proposals requiring separate implementation and release approval**. No data rewrite is part of this audit. Historical unfinished runs are not eligible for automatic execution just because a new worker understands them. Preserve migration identifiers and stored history; freeze schema baseline semantics only through an approved, tested history-preserving correction. Do not delete the narrowly retained legacy column or downgrade notification recovery evidence.

## Representative critical workflow

1. A verified recognition adapter emits `RecognitionObservation`; ingress commits its unique receipt before acknowledging durable acceptance.
2. The worker gets optional camera/vision evidence through a provider adapter outside a DB transaction. Evidence has timestamp/source/freshness. Failure follows the existing explicitly characterized direction rule, not a new guessed safety default.
3. A short transaction reloads mutable identity/permission/pass state and locks the relevant claim. Pure policy yields one decision. It records event/saga/reservation and any required delivery intent. It emits a command requirement with stable ID, not a provider call. No pass/presence outcome claims physical success at this point.
4. The existing coordinator commits exact-target attempt intent, then invokes one configured provider per target outside transaction. A definitely-not-dispatched failure may use approved fallback; unknown delivery remains unknown. Per-target outcomes remain visible independent of ordering.
5. A short outcome transaction persists the receipt. Movement owner applies presence/session only if eligible under the agreed outcome/evidence policy. Visitor owner consumes/releases/marks review using its approved rule. Unknown physical outcome is reconcilable; no automatic duplicate request.
6. Reconciliation uses target/time/attempt evidence and the same movement owner. Historical discovery calls the inert historical operation, with no actuator capability in its dependencies.
7. Required origin outbox work becomes uniquely identified NotificationRun/AutomationRun through explicit owner calls. Existing notification dispatcher checkpoints its provider attempts. Realtime updates UI best effort; browser displays command ID, acceptance, physical observation freshness and review state separately.

This adds no second live decision path. Comparisons during migration use captured **synthetic or separately approved redacted** inputs with inert command/message sinks; outputs are compared, never actuated twice.

## Proposed frontend tree and boundaries

```text
frontend/src/
  main.tsx                                [keep: bootstrap only]
  app/
    App.tsx, auth.tsx, routes.tsx,
      navigation.tsx, theme.tsx, ...        [keep: composition/session shell]
    useRealtimeConnection.ts               [new: authenticated-session transport lifetime]
    realtimeEvents.ts, realtimeRefresh.ts   [evolve: typed impacts/current consumers]
    useShellRefresh.ts, refreshCoordinator.ts [keep: resource lifetime/batching]
  api/
    client.ts, types.ts, schedules.ts,
      workflows.ts, integrations.ts,
      chat.ts, investigations.ts, search.ts [keep/evolve: typed resource transport]
    accessCommands.ts                      [new: confirmed exact scope + CommandReceipt]
    reports.ts                             [new: one preview/export read contract]
  features/
    schedules/*, workflows/*,
      investigations/*, integrations/*     [keep; named lifetime repairs only]
    reports/                               [new only with server-preview ownership cutover]
      ReportsView.tsx, reportModel.ts        [presentation/options only, no duration policy]
    accessCommands/                         [new if needed for receipt/status UI reuse]
      CommandResult.tsx                      [display receipt vs observed state]
  views/
    DashboardView.tsx, DirectoryViews.tsx,
      PassesView.tsx, ChatWidgetView.tsx,
      SettingsViews.tsx, ...                [keep; relocate only with a real owner change]
  ui/primitives.tsx                         [keep: domain-neutral UI]
  lib/format.ts, media.tsx, ...              [evolve: pure formatting separate from settings reads]
  styles.css, styles/*                      [keep: no visual/CSS rewrite justified]
```

App owns account/session and transport; route features own drafts/selection/read lifetime; API resource modules own request/response/confirmation formats. Form validation remains useful client-side feedback, while authorization, schedule validity, visitor consumption and report calculations remain server authority. Feature modules may use API/UI/pure helpers; neutral helpers cannot import feature editors or implicitly fetch settings. Features do not import one another's mutable state hooks. Read lifetimes use existing AbortSignal/generation/coordinator patterns, with a small shared primitive only where semantics actually match.

Critical frontend contract fixtures cover command target/receipt, valid V3 confirmation ID and realtime event impact. The ID-less tool-name confirmation fallback is removed after response characterization. A new tool should require catalog/handler/tests, not branches in ChatWidget. Browser report preview calls server-owned semantics; it cannot infer completeness from a recent events feed.

## Current-to-target mapping

| Current major component | Target action / concrete ownership removed |
|---|---|
| `AccessEventService` + new stages | Keep stage split; receipt lifecycle no longer determines side-effect authority through generic event dictionaries |
| `GateCommandCoordinator`/device/provider stack | Keep audited entry paths; replace bool/exception ambiguity and first-target truth with exact-target stable outcome contract |
| `restart_backfill` + Alfred repair handler | Discovery/presentation stay; duplicated event/presence/pass/saga construction removed into historical domain operation |
| Presence helper | One eligible atomic transition; remove latest-GRANTED and direct stale writes |
| VisitorPassService | Keep canonical CRUD; explicit validity/reservation/outcome semantics replace overloaded status transition timing |
| Notification store/dispatcher | Keep; required origin acceptance gains transactional handoff; no second delivery loop |
| AutomationService | Keep rule catalogue/selection; replace claimed-list handoff and final-only action record with specific journal; no direct notification rule writes |
| Chat + V3 modules | Keep planner/evidence/answers; approval JSON read-clear-execute replaced by durable approval claim; current actor policy enforced centrally at execution |
| WhatsApp facade/mixins | Transport remains; channel-neutral visitor policy extracted; facade backreferences/wildcard ownership retired |
| Schedule/feature operation owners | Keep as internal pattern; close only named audit/machine activation seams |
| Reports frontend/backend | Keep backend export/read model; remove browser duplicate attribution/duration/history calculation |
| App socket / route fetch code | Keep batching/impact owners; new session transport independent of route; remove invalid started flags/stale reads |
| Shared ORM model registry | Keep initially; enforce writer allowlist and transaction participants rather than cosmetic model-file split |
| Alembic/bootstrap | Preserve history/data; freeze baseline meaning and verify old/fresh upgrade equivalence |
| Dependency updater/Compose | Preserve feature/topology; complete candidate/backup contract includes lock/source/image/schema; eliminate misleading compile-as-health proof |
| CI/docs/test wrapper | Same tools with safely isolated default, locked environments, persistence gates and ratchet |

## Durable rules and success measures

Enforce with the existing Python AST tests, pytest/PostgreSQL scripts, Vitest/TypeScript and GitHub Actions. No new architecture framework is required.

| Measure | Current baseline | Acceptance / ratchet |
|---|---|---|
| Denied unknown reaching automation hardware | Reachable static bridge path; live rule exposure unknown | Zero inert commands for every denied-unknown trigger/action combination; explicit durable reason |
| Atomic V3 approval execution owner | None; JSON read-clear-execute | One owner; simultaneous confirmations produce one operation ID/at most one dispatch; current actor checked |
| Gate aggregate independent of list order | Inert probe differs for reversed two targets | Identical aggregate truth; per-target unknown/partial always retained |
| Presence eligibility owners | Live helper, restart latest-grant selector, direct Alfred writer | One atomic eligibility/ordering owner used by all three paths; no outside writes |
| Required origin-to-delivery gap | Access/visitor/calendar/message producers commit before delivery acceptance | Named required producers commit unique intent with origin; zero lost/replayed inert actions at boundary probes |
| Notification activation owners | Canonical CRUD + automation direct write | One operation for both actor types; actual source audit preserved |
| Runtime WhatsApp SCC | Five modules, verified runtime backrefs | Zero facade backreferences; no provider dependency in visitor policy |
| Syntactic Python SCC | One 52-module component including deferred/type-only edges | No new cycles/edges in baseline; reduce only through real owner changes, not moved imports |
| Frontend import SCC | Zero in conservative scan | Keep zero; expand checked graph beyond schedule/workflow |
| Report semantic computation owners | Backend export and browser capped preview | One backend read policy; preview/export fixture parity |
| Frontend event/command contracts | Handwritten casts and selected request tests | Required envelope changes fail paired Python/TS fixtures; event consumers/impact explicitly tested |
| Safely isolated backend suite | 900 pass / five DB-dependent failures in DB-free run | Explicit unit/persistence partition or isolated DB fixture; no unexpected network/credentials |
| Locked frontend validation | Cached run passes; 15 direct/dev mismatches | Complete pass against manifest+lock fingerprint before release |
| Broad lint | 639 diagnostics in 102 app files | Freeze machine-readable baseline; no additions in changed owners; delete baseline entries as fixed, no blanket suppression |
| Migration/restore equivalence | Not executed this audit; dynamic old baseline/update lock gap proven statically | Empty + historic checkpoints → equivalent schema; backup/restore manifest+lock+source/image/schema identity |

Track common change impact after each package; target file counts below are **estimates**, not gates:

| Future change | Current required inspection/change surface | Target estimated surface and why |
|---|---|---|
| Add a recognition source | Adapter/registry/webhook, read identity metadata, access worker normalization, evidence and historical source handling | Adapter + ingress registration + normalized observation/contract fixtures (roughly 2–4 app files); no policy/actuator rewrite if existing evidence contract fits |
| Change visitor validity/consumption rule | Visitor service, evidence claim, live execution, restart and Alfred repair, messaging conversation/lifecycle and frontend presentation | Visitor owner + shared policy fixtures + presentation if new state is visible (roughly 1–3 owners); callers use one result contract |
| Add notification destination | Provider/service branch/catalog plus workflow UI channel types/presentation, direct messaging automation paths | Adapter + explicit destination registration/presentation and paired contract tests (roughly 2–4 owners); same journal/producer path |
| Add Alfred mutating tool | Catalog/handler/domain owner; check confirmation enforcement manually and old ChatWidget branches | Catalog/handler/domain owner/tests only; approval/executor enforces common claim/current actor rules |
| Change presence eligibility | Live execution, movement helper, reconciliation, restart latest-grant and Alfred direct writes | One movement owner + matrix fixtures; consumers render/query committed outcome |

Update concise current guidance only when a package lands: one ownership table, allowed imports/writers, safe validation entrypoint, and retirement rule. Record an architecture decision when a change alters authority, transaction/delivery guarantees, persisted state meaning, public contract, or deployment/rollback boundary—not for ordinary file organization. The plan's acceptance gates, not documentation alone, keep this structure durable.

## Frozen implementation boundary requirements (2026-09-12)

These requirements refine the conceptual contracts above without adding a second
framework or identity scheme. Exact DTO fixtures must land with each owner.

- **Target identity:** existing access devices use stable string `key` values,
  not a new UUID namespace. The additive gate-open request field is
  `target_device_key`; omitted retains the global automatic-access set. The
  designated entry is a validated existing gate key. A confirmation binds the
  resolved keys, designation and relevant binding/configuration fingerprint.
- **Operation identity:** reuse `GateCommandIntent.intent_id` and
  `idempotency_key`. Alfred approval and automation occurrence/action owners
  supply stable values. Reusing an identity with different action/targets is an
  error. Wire callers cannot manufacture trusted actor/provenance authority.
- **Receipt:** retain legacy fields as projections from the owner, add an exact
  per-target receipt and `admission_verified`. Provider delivery certainty and
  physical observation are separate fields. Mixed states do not use the first
  target. A selected manual non-entry gate has no admission/presence effect.
- **Evidence:** fresh target-specific OPEN/OPENING evidence establishes entry
  availability; it is not proof of passage. Link evidence to the relevant command
  observation window or explicit already-open authorization decision. A later
  unrelated gate state must not retroactively verify an earlier command.
- **Concurrency:** persist each target's prepared/attempting state before I/O;
  a fencing token guards outcome writes. Expired attempting work becomes unknown,
  never an invitation to resend. Independent manual/global intents addressing
  the same physical target must share serialization.
- **Admission:** pass reservation, safe movement/session transition and presence
  commit together when entry evidence qualifies. Existing historical callers
  remain inert and retain their current pass-claim semantics.
- **Recovery:** persist origin facts and stable identity; revalidate current rule
  activation, mutable visitor validity and hardware conditions immediately before
  every new send. Attempted/unknown actions never become pending on restart.
- **Reports:** one read-only snapshot builder serves preview/export. Preview does
  not create Report rows, snapshots or PDF files. Complete selected history and
  site timezone are identical across both routes.
