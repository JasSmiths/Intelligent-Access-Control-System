# IACS maintainability closure matrix

This ledger tracks the [approved programme](architecture-maintainability-plan.md)
against the preserved [review](architecture-maintainability-review.md). Every row
requires evidence and Astra Ultra approval before closure. Planned retention is
an explicit disposition requiring approval, not a claim that code was fixed.

## Source and implementation gates

- Reviewed baseline: original checkout `/Users/jas/Documents/Intelligent Access System`,
  branch `main`, HEAD `65e1b6d070e6327314ca9bd72c01264496b821fb`.
- Implementation checkout: `/Users/jas/.codex/worktrees/iacs-maintainability-programme/Intelligent Access System`,
  branch `codex/iacs-maintainability-programme`, starting at that same HEAD.
- Original untracked audit preserved; isolated copy is byte-for-byte identical.
- Sol Ultra implements; Astra Ultra advises and reviews each immutable candidate.
- Evidence root: `/private/tmp/iacs-maintainability-programme`; final candidate
  source/patch identities and review records are added after validation.
- Current checkpoint: P0 changes prepared; required full validation and candidate
  sign-off pending. P1 frontend work may proceed in disjoint files but does not
  alter the P0 completion gate. The brief initial shared-checkout editing period
  was separated before the first validation checkpoint: frontend work moved
  byte-for-byte to `/Users/jas/.codex/worktrees/iacs-maintainability-frontend/Intelligent Access System`
  on `codex/iacs-maintainability-frontend`; integration is sequential. No stage is complete at this checkpoint.
- Deployment, production migrations and live provider/hardware tests are excluded.

## Findings

| Finding | Owner and stages | Required closure evidence | Status / Astra approval |
| --- | --- | --- | --- |
| AM-01 workflow definitions | Workflow definitions, handlers and generated editor contracts; P2 | Exhaustive action/config/editor/handler parity, historical plan/recovery checks, E2 conformance | Pending / pending |
| AM-02 feature reads and invalidation | Shell, coordinator, directory/history/report/dashboard features; P1/P3 | Composed refresh regressions; account/cancel/page/selected/draft checks; bounded subject search | Pending / pending |
| AM-03 provider validation and identity | Device configuration and pure provider policies; P4 | Fingerprint parity, binding races and E1 third-provider fixture | Pending / pending |
| AM-04 integration editor lifetime | Keyed provider editors and dialog frame; P4 | Dirty/busy dismissal, stale reads, accepted-operation receipts, browser evidence | Pending / pending |
| AM-05 optional access completion | Durable intake/windows and completion worker; P6 | Astra-approved schema/state design; restart/crash/deadline/late-linkage checks; E2/E4 conformance | Pending / pending |
| AM-06 access tail and chart reads | Durable intake/completion, charts and compact reads; P6/P7 | Equivalent ranking/window evidence, bounded memory, complete query plans and target-scale measurements | Pending / pending |
| AM-07 runtime resource bounds | Event bus, diagnostics, malfunction scheduler and UniFi worker; P8 | Overflow/counters/current evidence, nested publication, shutdown and 20/2,000-input probes | Pending / pending |
| AM-08 slow-operation transactions | Reports and calendar account sync; P8 | Report capacity/limits/cancellation/collision/authorization; account fencing/partial sync/late SDK checks | Pending / pending |
| AM-09 runtime PDF support | Shared backend image ancestry and final-image smoke; P1 | Runtime-user PDF from installed actual template with no network/overlays; image identity/size/build time | Pending / pending |
| AM-10 type/evidence coverage | Single type policy, harness guards and retained evidence; P0/continuous | Original coverage/flags preserved; package inheritance and negative guards; successful JSON/complete-plan retention and full harness | Implemented, validation/review pending / pending |
| AM-11 obsolete authority/bridges | Pure access plan, execution result and durable workflow intake; P5 | Whole-repository consumer trace; authoritative journals retained; no duplicate realtime intake | Pending / pending |
| AM-12 compact presence/forecast | Presence/predictor reads; P7 | Equivalent output and compact unloaded columns; bounded internal reads; 20/2,000-person density evidence; public-list exception disclosed | Pending / pending |
| AM-13 provider-information reset | Vehicle information store in-session invalidation; P5 | Plate-change aggregate, stale completion and user-field preservation; E4 conformance | Pending / pending |

## Simplification candidates

| Candidate | Disposition and owner | Required evidence or retention reason | Status / Astra approval |
| --- | --- | --- | --- |
| Pure plan discarded command field | Remove; P5/AM-11 | Trace internal consumers; preserve persisted saga/receipt semantics | Pending / pending |
| Automation realtime bridge and notification visitor fallback | Remove; P5/AM-11 | Keep durable mapper and notification wake; negative duplicate-intake fixtures | Pending / pending |
| AccessExecutionResult unused window/command/skipped/outcome fields | Narrow after tracing; P5/AM-11 | Whole-repository and fixture trace; retain durable fields | Pending / pending |
| External-admission private method | Explicit supported admission interface; P6 | One admission implementation; preserve execution/admission ordering | Pending / pending |
| Unused shell reads and seeds | Remove with feature ownership; P3 | Actual feature read coverage and invalidation/draft lifetimes | Pending / pending |
| OAuth placeholder flags/button/import | Remove together; P4 | No implemented OAuth operation; provider editor checks | Pending / pending |
| Artificial provider progress waits | Remove; P4 | Actual pending work owns feedback; no backend speedup claim | Pending / pending |
| Report first-500/secondary subject filtering | Replace with bounded server search and selected hydration; P3 | Subject beyond 500 and server-only searchable-field fixtures | Pending / pending |
| _commit_if_supported and guaranteed-protocol getattr wrappers | Remove after correcting fakes/ports; P5 | Concrete required ports and uncertainty-boundary regression checks | Pending / pending |
| IACS_SEED_DEMO_DATA | Retain pending external-consumer evidence; P5 | Parsed public deployment option; audit did not establish all external configuration users | Planned retention / pending |
| Calendar SDK fallback shapes | Retain unless locked-SDK/serialized-session fixtures prove obsolete; P5/P8 | Vendor/data compatibility not established by absence of one call path | Planned retention / pending |
| Public legacy cover aliases | Retain; P5 | External consumers unknown; confirmation bindings remain supported | Planned retention / pending |
| Vehicle.person_id and assignments | Retain; P5 | Derived only for exactly one assignee; attribution/authority semantics require it | Planned retention / pending |
| Camera provider interfaces and explicit unsupported local mode | Retain; P5 | Active isolation contract and deliberate no-external-analysis behavior | Planned retention / pending |
| Python Playwright and pgvector image | Retain and fix browser packaging; P1/P5 | Active PDF renderer and supported historical vector migration chain | Planned retention / pending |
| Historical retired names, upgrade snapshots and redaction | Retain; P5 | Upgrade checksums and durable audit interpretation; active retired operations stay unavailable | Planned retention / pending |
| Prototype and screenshots | Retain; P5 | Separate fixture/design evidence; no production import; no evidence-retirement request | Planned retention / pending |
| scripts/backend-pytest and scripts/load-test.mjs | Retain; P5 | External operational use unknown; neither used for this programme's isolated validation | Planned retention / pending |
| V2 privacy CI workflow | Retain; P5 | External candidate-bundle process; process retirement not established | Planned retention / pending |
| Empty retired catalog group | Remove; P5/AM-11 | Retired runtime catalog cleanup with historical upgrade fixtures retained | Pending / pending |
| Speculative runtime dependency deletion | Retain locked dependencies; P5 | No package proven unused; preserve supported migrations and relevant security fixes | Planned retention / pending |

## Extension conformance and final gate

| Exercise | Stage and acceptance | Status / Astra approval |
| --- | --- | --- |
| E1 third managed cover provider | P4/P9: fixture provider through discovery/configuration/persistence without central provider branches | Pending / pending |
| E2 refresh known vehicle automation action | P2/P6/P9: definition/handler/editor/reservation without dispatcher/store branches | Pending / pending |
| E3 calendar schedule exception | P2/P8/P9: schedule ownership, current authority and generated configuration contracts | Pending / pending |
| E4 last recorded mileage summary | P5/P6/P9: feature store owns new summary/reset and optional completion | Pending / pending |
| E5 MOT freshness in dashboard activity | P3/P7/P9: bounded summaries, typed updates and no provider I/O | Pending / pending |
| P9 final integrated candidate | Complete required release-candidate validation, exact source/patch identity, no actionable findings and fresh Astra approval | Pending / pending |

Public population-sized presence reads remain an approved compatibility exception;
internal consumers must use bounded summaries/selected reads. Forecast rows can
still grow within the unchanged history horizon. Synthetic results do not prove
production speedups or PostgreSQL memory bounds.

## P6 design prerequisites retained from advisor inspection

The initial intake payload must be immutable on duplicate/retry, including the
first gate observation and timestamps. Preserve current first-matching-window
selection, original first-list-occurrence ties, last-appended update timestamp
outside merges, and selected-then-related-pending merge order. Do not substitute
UUID sorting or maximum timestamps. Claims need fencing and unique membership.
All authority and admission consumers must use indexed/streamed full evidence;
preview limits cannot truncate OCR or movement authority. Terminalize all window
members in the event/saga/required-intent transaction. These are design inputs;
Astra must still approve P6's concrete schema/state transitions before code.

The movement reconciliation path's delayed-admission HA presence update must join
the same durable latest-state/physical-target ordering as normal enrichment. Keep
the separate hardware authority deadline at the earlier of event occurrence and
earliest linked receipt plus 60 seconds; the optional five-minute deadline cannot
extend hardware authority.
