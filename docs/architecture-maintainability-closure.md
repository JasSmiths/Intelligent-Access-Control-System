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
- Approved P0/P1 backend checkpoint: `0483c6df0433b29371d5718fb2e86ac370bca993`.
  Astra Ultra approved P0 and P1 runtime packaging separately on source
  `de379a8e8be9c91b8008266204b3a37219c24c473a8f408b6831f48cbbb166c2`
  and patch `3e1cdab5cc4790a6ea5d871cff1e2e408411457ce9aef99388320f40519ce0f8`.
  All 141 full-harness checks passed; the final host-only fixture amendment
  passed 29 configuration and 22 source tests, with unchanged runtime hashes.
- Approved P1 frontend checkpoint: original `382e80de08e59241a5b25b4f63dbee583725f426`,
  integrated as `03f56bed97551fe6c97f093c789f905683c7fdc7`. Astra Ultra approved
  Node 22 build, 291 tests and 42 Chromium/WebKit cases. The integration receipt
  verifies all approved frontend blobs and all unaffected backend source hashes.
- Approved P2 checkpoint: `834daa7f2a59707dd5788de6caf4fa71aae8e4f8`,
  tree `5dfaf6c64324920f0b430185bd6b8b6e11cec19e`. Astra Ultra approved
  source `f954355add93145443c846bddf6df174e6e1b906e070ff52d34f09717492b524`
  and patch `bb43b4ce7955f1dea7d1fba0828f5e3d9a0c4a6b059feaca7f059ad0a0e044dd`.
  All 145 corrected full-harness entries passed, including 1,225 backend and 294
  frontend tests, recovery 38, E2 transaction conformance 6 and automation recovery
  30; checked/strict coverage is 60/43 with unchanged flags. Four affected browser
  cases carried forward by all 225 frontend hashes; source integrity, cleanup and
  operational snapshots are clean. Earlier failed P2 candidates are superseded.
- Approved P4 backend checkpoint: `3416d577169943e1c9a7f4db240c051e525cc7da`,
  tree `1145c5a2512e404078fa1a3d5a313f274dc26c4b`. Astra Ultra approved
  final mapped source `27f12cbdfbed61991d90b089cd2eb00e63ae0e0facd4fe6d087f112c4059da65`
  and patch `ba4e28da05c34890c4c20208994667a280ae66e69f3c1f3a89dd92c94ed0f0c0`.
  All 148 full-gate entries passed, including 1,258 backend/294 frontend tests,
  seven PostgreSQL provider extension cases and checked/strict coverage 61/44.
  A mapped closure-only sentence correction passed docs checks with all runtime,
  test, frontend, schema and harness bytes unchanged. All 727 committed source
  hashes equal the approved candidate; source, cleanup and operational state are clean.
- Approved P5 checkpoint: `208f3813b1e4c942662116d349529d5a26d40744`,
  tree `ba52d8b379f56be8551adec3906d35e84115cd0f`. Astra Ultra approved
  final mapped source `b2a13f1f3e1daeff9dec241d06f4040dbe126e2b08bff9ad6b6a1c41d310d414`
  and patch `f43b63827222051f882e5ec3d9578dcfa4336d71fba934950f211234a824067d`.
  The full gate retained 148 entries: its sole old fake missing the now-required
  commit was repaired with a correctly specced AsyncSession. All 39 complete
  affected action-confirmation/hardware HTTP tests passed; F821, host 22/29,
  docs 111 links and diff checks passed. The final two-file test/docs mapping
  preserves all runtime/schema/dependency/frontend/harness bytes from the full
  run; every other full-gate check passed. All 727 committed source hashes equal
  the approved candidate, with clean source/cleanup and unchanged operational state.
  Retention dispositions were accepted; E4 integrated conformance remains pending.
- Current checkpoint: approved P3 frontend range is being integrated sequentially
  after P5. Its 244 frontend hashes remain unchanged; backend source retains the
  approved P5 tree. P4 integration editor lifetime remains a separate frontend slice.
  The brief initial shared-checkout editing period was separated before the first
  validation checkpoint; integration remains sequential and separately reviewed.
- Retained evidence: `p0-p1-validation/iacs-validation-smbqtetg`,
  `runtime-pdf/iacs-runtime-pdf-ssq2ubtb`, `p0-p1-snapshot-to-candidate.json`,
  `p0-p1-astra-approval.json` and `p0-p1-integration.json`, relative to the
  evidence root. Runtime PDF: non-root final image, no network/overlays,
  142,385-byte PDF; source, image, hash, size and timing identities retained.
- P2 retained evidence: `p2-corrected-validation/iacs-validation-wpnrjmou`,
  `p2-candidate-corrected.json`, `p2-correction-source-mapping.json`,
  `p2-evidence-summary.json`, `p2-astra-approval.json` and
  `p2-commit-equivalence.json`, relative to the evidence root. The original audit
  remains byte-identical.
- P4 backend retained evidence: `p4-validation/iacs-validation-j64g1vik`,
  `p4-candidate-final.json`, `p4-docs-only-source-mapping.json`,
  `p4-evidence-summary.json`, `p4-astra-approval.json`,
  `p4-commit-equivalence.json` and `p4-benchmark-coordination.json`.
- P5 retained evidence: `p5-validation/iacs-validation-64wf0zqw`,
  `p5-candidate-final.json`, `p5-snapshot-to-candidate.json`,
  `p5-corrected-required-ports.log`, `p5-evidence-summary.json`,
  `p5-astra-approval.json`, `p5-commit-equivalence.json` and
  `p5-benchmark-coordination.json`. The failed original snapshot stays retained.
- Deployment, production migrations and live provider/hardware tests are excluded.

## P3 frontend evidence

The separate frontend branch was implemented from approved P2 commit
`834daa7f2a59707dd5788de6caf4fa71aae8e4f8` and rebased onto approved backend P4
commit `3416d577169943e1c9a7f4db240c051e525cc7da`. Feature-owned actual-HTTP reads replace
shell collections, seed props, numeric invalidation tokens and central feature
route/event mappings. Directory/history/report/dashboard and remaining settings,
integration, recovery, log, pass, chart and workflow readers subscribe locally.
Local save/delete/retry refreshes use the required feature owners; global manual
and connection signals remain explicit shell composition. Accepted mutations keep
their receipts when a subsequent read fails; local retry reads remain feature-owned.

The corrected frozen frontend passed the locked Node 22 build and 311 unit tests
in 54 files. Browser coverage consists of the earlier 140-case Chromium/WebKit
run, 78 passing affected cases after the receipt correction, and eight passing
new integration receipt cases after correcting their HTTP-status-prefixed error
selectors. The initial eight selector failures remain retained. Hash mappings
carry forward the 62 unchanged browser cases; the final coverage is 148 distinct
cases, not the sum of repeated runs. The Node snapshot differs from the final
frontend only in four selectors in that browser fixture.

Equivalent-output production assets, built with the same locked Node 22 image,
passed the identical inert 12/2,000-record workflow against baseline `03f56bed`
and the corrected candidate in Chromium. Initial/mutation/navigation/100-event
burst GET counts changed from 10/10/9/8 to 8/3/5/4 at both sizes. All requested
GETs completed, with no aborted, failed or pending requests. At 2,000 records,
loaded UTF-8 JSON bytes changed from 92,219/122,105/121,891/243,328 to
31,299/61,030/60,934/121,656. These synthetic response-body bytes do not establish
database work or production latency. The development server comparison remains
separate, including its initial 12-to-13 request increase under StrictMode.
Beyond-500 subject search is separate correctness evidence. Existing unpaged
group/schedule catalog contracts remain unchanged; full Dashboard presence awaits
P7.

Evidence under the retained programme root: `p3-corrected-node22/`,
`p3-corrected-browser.json`, `p3-corrected-receipt-browser.json`,
`p3-corrected-receipt-source.json`, `p3-production-comparison/` and the final
candidate/coverage mapping receipts. Astra Ultra approved the complete 100-path
P3 delta, tree `13e9fb2c51313b8b2e24567491a22e5f0b9b1093`, patch
`e9244ce6dd986e41229d9f4f2c88585e1b15736be876f9679d4dc3627ded51dc`.
Commit `aeedb724046c8d7a5b67ed0a7094eb8e21941745` preserves that exact tree.
`p3-astra-approval.json` retains the verbatim approval and immutable frontend
archive equivalence; the rebased source/docs mapping is retained separately.

## P4 frontend evidence

The separate frontend lane starts from integrated approved P3/P5 checkpoint
`f9a162c0b995027b53e678abc85283c84e4b8fb2`. Canonical ordered access-device
descriptors own discovery/configuration/rendering/save metadata, and the real
owner fixture exercises an inert third descriptor through a confirmed binding
write. Complete keyed provider editors replace the central integration modal's
state/effects and own dirty/busy/read/mutation lifetimes. Managed/scalar catalog
coverage is checked; OAuth placeholders, artificial progress waits and nested
Apprise forms are retired. Accepted operations retain receipts through later read
failures, with duplicate operations, account disposal and stale reads fenced.

The corrected frozen frontend passed the locked Node 22 build and all 353 unit
tests in 57 files, with dependency locks and source bytes verified in network-free
containers (`p4-frontend-corrected-node22`). The original frozen Node run had two
receipt fixtures still expecting the retired unrelated Protect refresh; that
failed run remains retained as `p4-frontend-node22`. The corrected tests exercise
the actual settings/Apprise owner failure, assert no unrelated Protect fetch, and
preserve explicit global-refresh aggregate rejection. Runtime bytes did not change.

Chromium and WebKit passed all 64 affected provider/receipt/route cases and the
12-case dark tablet supplement, with no skipped, flaky or unexpected results.
Evidence is retained in `p4-frontend-browser` and `p4-frontend-tablet-browser`;
browser runtime was host Node 24.19.0, separate from the locked Node 22 build/unit
gate. Desktop, phone, short landscape and dark tablet screenshots preserve drafts
through actual realtime invalidation and verify bounded dialog/actions. The
supplement adds only an e2e viewport fixture; exact carry-forward hashes and full
stage identity are recorded in `p4-frontend-final-source-mapping.json` and
`p4-frontend-final-candidate.json`. Progress fixture failures and the initial
sandbox server-start failure remain retained. All fixtures are inert and the
owned loopback server has no backend proxy.

Astra Ultra approved the complete P4 frontend stage from `f9a162c0` through tree
`8812a837141dab62e88d2ceb322b8b635ff9a17e`, patch SHA-256
`35fd7946e70624b7b38dae352b0c91f20c4a0deea1aa438ac0361f6c770cdfd8`, including
all 36 changed paths and the earlier descriptor checkpoint. Verbatim approval is
retained in `p4-frontend-astra-approval.json`; the approval-wording-only ledger
amendment is mapped separately in `p4-frontend-approval-docs-mapping.json` with
runtime bytes unchanged. AM-04 is closed for this frontend stage. Backend P4
approval and later programme stages remain unchanged.

## Findings

| Finding | Owner and stages | Required closure evidence | Status / Astra approval |
| --- | --- | --- | --- |
| AM-01 workflow definitions | Workflow definitions, handlers and generated editor contracts; P2 | Exhaustive action/config/editor/handler parity, historical plan/recovery checks, E2 conformance | Closed / Astra approved P2 exact candidate |
| AM-02 feature reads and invalidation | Shell, coordinator, directory/history/report/dashboard features; P1/P3 | Composed refresh regressions; account/cancel/page/selected/draft checks; bounded subject search | Closed / Astra approved P3 exact candidate |
| AM-03 provider validation and identity | Device configuration and pure provider policies; P4 | Fingerprint parity, binding races and E1 third-provider fixture | Closed backend / Astra approved P4 exact candidate |
| AM-04 integration editor lifetime | Keyed provider editors and dialog frame; P4 | Dirty/busy dismissal, stale reads, accepted-operation receipts, browser evidence | Closed frontend / Astra approved P4 exact candidate |
| AM-05 optional access completion | Durable intake/windows and completion worker; P6 | Astra-approved schema/state design; restart/crash/deadline/late-linkage checks; E2/E4 conformance | Pending / pending |
| AM-06 access tail and chart reads | Durable intake/completion, charts and compact reads; P6/P7 | Equivalent ranking/window evidence, bounded memory, complete query plans and target-scale measurements | Pending / pending |
| AM-07 runtime resource bounds | Event bus, diagnostics, malfunction scheduler and UniFi worker; P8 | Overflow/counters/current evidence, nested publication, shutdown and 20/2,000-input probes | Pending / pending |
| AM-08 slow-operation transactions | Reports and calendar account sync; P8 | Report capacity/limits/cancellation/collision/authorization; account fencing/partial sync/late SDK checks | Pending / pending |
| AM-09 runtime PDF support | Shared backend image ancestry and final-image smoke; P1 | Runtime-user PDF from installed actual template with no network/overlays; image identity/size/build time | Closed / Astra approved P1 runtime packaging |
| AM-10 type/evidence coverage | Single type policy, harness guards and retained evidence; P0/continuous | Original coverage/flags preserved; package inheritance and negative guards; successful JSON/complete-plan retention and full harness | P0 safeguards and P2/P3/P4/P5 evidence approved; continuous P6–P9 checks pending / Astra approved P0/P2/P3/P4/P5 |
| AM-11 obsolete authority/bridges | Pure access plan, execution result and durable workflow intake; P5 | Whole-repository consumer trace; authoritative journals retained; no duplicate realtime intake | Closed P5 implementation; integrated conformance where listed remains pending / Astra approved P5 |
| AM-12 compact presence/forecast | Presence/predictor reads; P7 | Equivalent output and compact unloaded columns; bounded internal reads; 20/2,000-person density evidence; public-list exception disclosed | Pending / pending |
| AM-13 provider-information reset | Vehicle information store in-session invalidation; P5 | Plate-change aggregate, stale completion and user-field preservation; E4 conformance | Closed P5 implementation; integrated conformance where listed remains pending / Astra approved P5 |

## Simplification candidates

| Candidate | Disposition and owner | Required evidence or retention reason | Status / Astra approval |
| --- | --- | --- | --- |
| Pure plan discarded command field | Remove; P5/AM-11 | Trace internal consumers; preserve persisted saga/receipt semantics | Closed P5 implementation; integrated conformance where listed remains pending / Astra approved P5 |
| Automation realtime bridge and notification visitor fallback | Remove; P5/AM-11 | Keep durable mapper and notification wake; negative duplicate-intake fixtures | Closed P5 implementation; integrated conformance where listed remains pending / Astra approved P5 |
| AccessExecutionResult unused window/command/skipped/outcome fields | Narrow after tracing; P5/AM-11 | Whole-repository and fixture trace; retain durable fields | Closed P5 implementation; integrated conformance where listed remains pending / Astra approved P5 |
| External-admission private method | Explicit supported admission interface; P6 | One admission implementation; preserve execution/admission ordering | Pending / pending |
| Unused shell reads and seeds | Remove with feature ownership; P3 | Actual feature read coverage and invalidation/draft lifetimes | Closed / Astra approved P3 |
| OAuth placeholder flags/button/import | Remove together; P4 | No implemented OAuth operation; provider editor checks | Pending / pending |
| Artificial provider progress waits | Remove; P4 | Actual pending work owns feedback; no backend speedup claim | Pending / pending |
| Report first-500/secondary subject filtering | Replace with bounded server search and selected hydration; P3 | Subject beyond 500 and server-only searchable-field fixtures | Closed / Astra approved P3 |
| _commit_if_supported and guaranteed-protocol getattr wrappers | Remove after correcting fakes/ports; P5 | Concrete required ports and uncertainty-boundary regression checks | Closed P5 implementation; integrated conformance where listed remains pending / Astra approved P5 |
| IACS_SEED_DEMO_DATA | Retain pending external-consumer evidence; P5 | Parsed public deployment option; audit did not establish all external configuration users | Retained / Astra approved P5 |
| Calendar SDK fallback shapes | Retain unless locked-SDK/serialized-session fixtures prove obsolete; P5/P8 | Vendor/data compatibility not established by absence of one call path | Retained / Astra approved P5 |
| Public legacy cover aliases | Retain; P5 | External consumers unknown; confirmation bindings remain supported | Retained / Astra approved P5 |
| Vehicle.person_id and assignments | Retain; P5 | Derived only for exactly one assignee; attribution/authority semantics require it | Retained / Astra approved P5 |
| Camera provider interfaces and explicit unsupported local mode | Retain; P5 | Active isolation contract and deliberate no-external-analysis behavior | Retained / Astra approved P5 |
| Python Playwright and pgvector image | Retain and fix browser packaging; P1/P5 | Active PDF renderer and supported historical vector migration chain | Retained / Astra approved P5 |
| Historical retired names, upgrade snapshots and redaction | Retain; P5 | Upgrade checksums and durable audit interpretation; active retired operations stay unavailable | Retained / Astra approved P5 |
| Prototype and screenshots | Retain; P5 | Separate fixture/design evidence; no production import; no evidence-retirement request | Retained / Astra approved P5 |
| scripts/backend-pytest and scripts/load-test.mjs | Retain; P5 | External operational use unknown; neither used for this programme's isolated validation | Retained / Astra approved P5 |
| V2 privacy CI workflow | Retain; P5 | External candidate-bundle process; process retirement not established | Retained / Astra approved P5 |
| Empty retired catalog group | Removed early by definition-derived catalog; P2/AM-11 | Catalog omits empty groups; historical names and upgrade fixtures retained | Closed / Astra approved P2 |
| Speculative runtime dependency deletion | Retain locked dependencies; P5 | No package proven unused; preserve supported migrations and relevant security fixes | Retained / Astra approved P5 |

## Extension conformance and final gate

| Exercise | Stage and acceptance | Status / Astra approval |
| --- | --- | --- |
| E1 third managed cover provider | P4/P9: fixture provider through discovery/configuration/persistence without central provider branches | Backend fixture approved; frontend/P9 integration pending / Astra approved P4 backend |
| E2 refresh known vehicle automation action | P2/P6/P9: definition/handler/editor/reservation without dispatcher/store branches | P2 conformance approved; P6/P9 integration pending / Astra approved P2 |
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

## P2 implementation candidate

The pure action and channel definitions own configuration, defaults and metadata.
One action-type binding map composes handlers; runtime integration enabled checks
remain outside definitions. Transactional actions use their caller's session and
owned pending operation identity. That identity is not proof of an external
attempt; durable effects still use the existing attempted checkpoint and domain
authorization.

New/edited writes reject unsupported types and malformed config. Retained unknown
records stay readable, and malformed unfinished work is held for review before
attempt. Recovery never re-normalizes attempted config, ordered operation IDs or
frozen targets. Rendered notification fields and observed literal producer shapes
have explicit adapters; stores remain separate.

The generated TypeScript contract freshness check is part of the harness/CI.
Negative schema, binding, historical and editor fixtures, plus a test-only
transactional action through real intake/reservation/completion/audit, passed the
corrected P2 gate and received Astra approval on the exact checkpoint recorded
above. No new production action is shipped.

## P5 implementation and retained dispositions

The pure access plan no longer carries the discarded command field or its unused
hardware inputs. The execution result exposes only consumed fields, with named
construction; execution locals, persisted saga/journal/outcome payloads and the
separate resident-recovery command requirement remain authoritative. Repository
consumer traces found no supported readers of the removed result fields.

The automation no-op realtime subscription and notification visitor fallback are
removed. Durable producers still use their pure mappers, and
`notification.trigger` still wakes the notification dispatcher. The two manual
hardware HTTP paths now require AsyncSession.commit before a negative receipt;
the concrete HA client close port is called directly. Test fakes implement those
required ports. Optional ESPHome verification and provider close/subscription
capabilities, heterogeneous provider errors and locked-vendor Calendar fallback
shapes remain: those are distinct optional/vendor contracts, not guaranteed ports.

Vehicle-information invalidation now lives beside snapshot application in its
store. It deletes the snapshot and clears the one owned summary-field inventory
in the caller's transaction. Directory still owns authorization, row lock, cached
replacement, required audit and commit. Unit and PostgreSQL fixtures cover no
independent commit, rollback, late old-plate completion and preserved user fields.
The E4 last-mileage extension remains an inert final integrated exercise pending
P6/P9; no new production summary field is introduced here.

All planned-retention rows above keep their stated disposition: public deployment
options/scripts and cover aliases have unproven external consumers; assignment
attribution and camera interfaces remain active contracts; Python Playwright,
pgvector and exact locked dependencies support active rendering/migration paths;
historical names/checksums/redaction preserve audit interpretation; the prototype,
screenshots and V2 privacy workflow are separate retained evidence/processes.
Astra accepted these explicit retention decisions in the exact P5 review; they
are not claims of deletion or completed final integrated conformance.
