# IACS maintainability closure matrix

This ledger tracks the [approved programme](architecture-maintainability-plan.md)
against the preserved [review](architecture-maintainability-review.md). Every row
requires evidence and Astra High approval before new closure. Prior Astra Ultra
approvals below remain historical records. Planned retention is
an explicit disposition requiring approval, not a claim that code was fixed.

## Current implementation and remaining gates

The durable access coordinator, canonical revisioned event projection and bounded
completion/publication owners are integrated; RAM windows and inline optional
completion have been retired. Astra High approved the focused P6 checkpoint
`30c4938` with 263 distinct PostgreSQL cases and bounded 4/80-window allocation
and output evidence. Calendar/P7 has its approved full and browser gates.
E1's provider-setting invariant and E2/E3's real transaction/current-authority
exercises are source-approved; E2/E3 also passed 42 PostgreSQL cases. E4 now has
an existing-store mileage/reset exercise awaiting execution. P8 diagnostics and
required audit changes await composition, including shared-client shutdown and
parent-journal seams. The combined full harness, affected browser checks and
fresh complete-candidate Astra High review remain required. These focused
approvals do not close P6, P8 or P9.

## Source and implementation gates

- Reviewed baseline: original checkout `/Users/jas/Documents/Intelligent Access System`,
  branch `main`, HEAD `65e1b6d070e6327314ca9bd72c01264496b821fb`.
- Initial implementation checkout: `/Users/jas/.codex/worktrees/iacs-maintainability-programme/Intelligent Access System`,
  branch `codex/iacs-maintainability-programme`, starting at that same HEAD.
- Original untracked audit was preserved with a byte-for-byte isolated copy.
- Sol 6.1 Ultra implements; Astra High advises and reviews each new exact candidate.
- Historical evidence root: `/private/tmp/iacs-maintainability-programme`; its raw
  files were lost after restart. The checkpoint facts and prior approval identities
  below are historical records, not currently available raw evidence. Fresh
  calendar/P7 evidence uses
  `/Users/jas/.codex/worktrees/iacs-maintainability-frontend/validation-evidence`,
  with one final source/patch receipt prepared after validation.
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
- Historical approved completion-repository checkpoint: `d079bb04a12d927084acee9b2f4d013ddfe2e2f4`
  includes approved P3, P4 frontend and P5, plus the separately approved P6
  foundation (`876deec32b2da8559585c5333964af211f3d2308`) and immutable intake
  (`f9e1dba7d043bed9d378de6205a05cbb8c047856`) owner checkpoints. The integration
  preserves all 259 approved frontend files and 501 nonfrontend/nondocs intake
  sources; `p4-intake-integration-mapping.json` and its Astra approval retain the
  exact source mapping. The durable window/evidence store is separately approved
  and committed at `ef1647b23289ede8daf174e9d4e54e272dac6327`, with source
  `d143653c85797e1fb9ac5fef026a8a94ed8a3620b117b8d7fed2207e9e77a4a0` and patch
  `cbf55317abb1826f1fb63c98286553def95f154a3d1e48f276f3f2028b872c96`.
  Its receipt is `p6-window-astra-approval.json`; archive equivalence is retained in
  `p6-window-commit-equivalence.json`. The publication owner is separately
  approved and committed at `4445db74bfe1a556103647bd2bddce112b2c6646`, with source
  `6f1761640bf4c7622941033ecf48669004eade768f12af23ff2438470f333d75`
  and patch `c9d0a7c192df0651493bf210b35df97c6cb17e465d9ca7c8ea9d2a9fe67a0f07`.
  Its 18 PostgreSQL/Redis and 37 unit cases, checked/strict 73/56, host 22/29,
  migration/source/cleanup/operational evidence are retained in
  `p6-publication-candidate.json`, `p6-publication-astra-approval.json` and
  `p6-publication-commit-equivalence.json`. The origin/readiness completion
  repository is separately approved and committed at that historical
  checkpoint, with source
  `2ac90b93086842221c130a4a5ae92422b9fa2043a1128e37c75969427f4c0d1d`
  and patch `4a1cc4a4db9dcb5c73e09e62b658186b84b30621875d98af527d369351558891`.
  Its 38 PostgreSQL/91 unit cases, checked/strict 75/58, host 22/29 and clean
  migration/source/cleanup/operational gates are mapped in
  `p6-completion-candidate.json`, `p6-completion-astra-approval.json` and
  `p6-completion-commit-equivalence.json`. The approved report range
  `ef1647b23289ede8daf174e9d4e54e272dac6327..5a4ad72074ff5b50b9f4764a8ee63c268a6d575f`
  is integrated sequentially with the union of type/persistence registrations and
  separately reviewed documentation. P6 remains incomplete: the RAM/runtime
  handoff, completion applicators, HA, historical, chart, lifecycle, matching
  frontend changes and final integrated validation remain required.
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

## P8 report owner evidence (Astra approved)

The reports lane rebases its owned work onto approved P6 window checkpoint
`ef1647b23289ede8daf174e9d4e54e272dac6327`, preserving the approved frontend and
backend bytes outside the coordinated report composition. Separate compact source,
image, renderer and export owners replace the long request transaction. Preview
and export share the N + M + S 10,000 activity representation budget; vehicle
metadata has a separate 10,000 ceiling. Export admission, the whole 120-second
budget, actual HTTP disconnect/resource drain, fresh token/user authority, required
audit rollback, accepted/unknown commits and UUID artifact ownership are covered
by focused fixtures. This is report-slice evidence; calendar and AM-08 closure
remain pending.

The runtime image `6f23f9cb891d` renders the actual template as the non-root user
with networking disabled. Small smoke, external-source rejection, separate row
and image ceilings, and combined 10,000 rows/25 MiB data URLs/64 Mi-pixels are
retained in `p8-report-runtime-pdf` and `p8-report-runtime-limits`. The combined
fixture passed only in the tested 4 GiB RAM plus 4 GiB swap allowance/512 MiB
`/tmp` envelope (RAM peak 3,889,774,592; sampled swap peak 1,076,711,424 bytes).
The original 256 MiB temp failure and combined 2 GiB RAM/2 GiB swap OOM remain
explicit failures. This establishes one synthetic envelope, with ordinary app,
concurrent work and multiple workers unmeasured; it is not an RSS guarantee or
proof that 4 GiB total/no-swap suffices. No Compose/product caps were changed.

Focused PostgreSQL checks pass 17 cases, including actual blocked SQL during full
lifespan shutdown and an audit fault after both rows flush, followed by a fresh
successful retry. Unit/lifespan checks pass 92 cases; checked bodies/strict targets
and source-selection checks pass. The corrected before/final read pair preserves
exact public output at both scales and actual target plans (2,500 selected/5,000
site rows). Target full unused blob scalar bytes loaded fall from 23,285,210 to
zero; Python allocation peak falls from 44,181,925 to 15,710,081 bytes. Small query
count rises 11 → 13 and target selected SQL plan time rises 0.682 → 4.298 ms;
these are materialization results, not universal SQL/production speed claims.
The narrow download projection also proves no stored 16 MiB historical snapshot
is loaded for a PDF download, while metadata remains complete and sanitized.
The frozen full harness `iacs-validation-n606vzeg` retained 160 entries. It
failed only the inherited simulation unit and three access-pipeline assertions
that still supplied retired raw trust flags; all other groups, including report
PostgreSQL, locked Node 22 frontend build/353 tests, migrations and restore, passed.
Exact Astra-reviewed fixture corrections preserve typed gate provenance, genuine
external session/presence matching, denied forged tags, raw ingest evidence and
no-command guarantees. The complete corrected backend selection passes 1,241
tests with 29 expected database-free skips; the complete pipeline passes 31 real
PostgreSQL cases. F821 and source/harness guards pass. The final immutable mapping
carries unchanged runtime/full-run/image evidence forward; the original harness
exit remains a recorded failure, not a claimed successful rerun. Astra Ultra approved the complete report-owner source and mapped evidence.
Calendar, other P8 owners, AM-08 as a whole and P9 remain pending.
Failed progress/fake-fixture attempts, the external runner basetemp setup failure
and superseded assertion-shape run remain retained. P9 integrated programme
conformance and non-report P8 owners remain pending.

## Findings

| Finding | Owner and stages | Required closure evidence | Status / Astra approval |
| --- | --- | --- | --- |
| AM-01 workflow definitions | Workflow definitions, handlers and generated editor contracts; P2 | Exhaustive action/config/editor/handler parity, historical plan/recovery checks, E2 conformance | Closed / Astra approved P2 exact candidate |
| AM-02 feature reads and invalidation | Shell, coordinator, directory/history/report/dashboard features; P1/P3 | Composed refresh regressions; account/cancel/page/selected/draft checks; bounded subject search | Closed / Astra approved P3 exact candidate |
| AM-03 provider validation and identity | Device configuration and pure provider policies; P4 | Fingerprint parity, binding races and E1 third-provider fixture | Closed backend / Astra approved P4 exact candidate |
| AM-04 integration editor lifetime | Keyed provider editors and dialog frame; P4 | Dirty/busy dismissal, stale reads, accepted-operation receipts, browser evidence | Closed frontend / Astra approved P4 exact candidate |
| AM-05 optional access completion | Durable intake/windows and completion worker; P6 | Focused 263-case PostgreSQL checkpoint and 4/80-window allocation/output evidence; full composed gate and E4 execution remain | Focused checkpoint approved / integrated full gate pending |
| AM-06 access tail and chart reads | Durable intake/completion, charts and compact reads; P6/P7 | Equivalent ranking/window evidence, bounded memory, complete query plans and target-scale measurements | P7 compact reads approved; P6 integration pending / Astra High approved P7 |
| AM-07 runtime resource bounds | Event bus, diagnostics, malfunction scheduler and UniFi worker; P8 | Overflow/counters/current evidence, nested publication, shutdown and 20/2,000-input probes | Pending / pending |
| AM-08 slow-operation transactions | Reports and calendar account sync; P8 | Report capacity/limits/cancellation/collision/authorization; account fencing/partial sync/late SDK checks | Report/calendar owners approved; P8/P9 integrated gates pending / Astra Ultra report; Astra High calendar |
| AM-09 runtime PDF support | Shared backend image ancestry and final-image smoke; P1 | Runtime-user PDF from installed actual template with no network/overlays; image identity/size/build time | Closed / Astra approved P1 runtime packaging |
| AM-10 type/evidence coverage | Single type policy, harness guards and retained evidence; P0/continuous | Original coverage/flags preserved; package inheritance and negative guards; successful JSON/complete-plan retention and full harness | P0 safeguards and P2/P3/P4/P5 evidence approved; continuous P6–P9 checks pending / Astra approved P0/P2/P3/P4/P5 |
| AM-11 obsolete authority/bridges | Pure access plan, execution result and durable workflow intake; P5 | Whole-repository consumer trace; authoritative journals retained; no duplicate realtime intake | Closed P5 implementation; integrated conformance where listed remains pending / Astra approved P5 |
| AM-12 compact presence/forecast | Presence/predictor reads; P7 | Equivalent output and compact unloaded columns; bounded internal reads; 20/2,000-person density evidence; public-list exception disclosed | Closed P7 / Astra High approved exact candidate |
| AM-13 provider-information reset | Vehicle information store in-session invalidation; P5 | Plate-change aggregate, stale completion and user-field preservation; E4 conformance | Closed P5 implementation; integrated conformance where listed remains pending / Astra approved P5 |

## Simplification candidates

| Candidate | Disposition and owner | Required evidence or retention reason | Status / Astra approval |
| --- | --- | --- | --- |
| Pure plan discarded command field | Remove; P5/AM-11 | Trace internal consumers; preserve persisted saga/receipt semantics | Closed P5 implementation; integrated conformance where listed remains pending / Astra approved P5 |
| Automation realtime bridge and notification visitor fallback | Remove; P5/AM-11 | Keep durable mapper and notification wake; negative duplicate-intake fixtures | Closed P5 implementation; integrated conformance where listed remains pending / Astra approved P5 |
| AccessExecutionResult unused window/command/skipped/outcome fields | Narrow after tracing; P5/AM-11 | Whole-repository and fixture trace; retain durable fields | Closed P5 implementation; integrated conformance where listed remains pending / Astra approved P5 |
| External-admission private method | Explicit supported admission interface; P6 | One admission implementation; preserve execution/admission ordering | Pending / pending |
| Unused shell reads and seeds | Remove with feature ownership; P3 | Actual feature read coverage and invalidation/draft lifetimes | Closed / Astra approved P3 |
| OAuth placeholder flags/button/import | Remove together; P4 | No implemented OAuth operation; provider editor checks | Closed / Astra approved P4 frontend |
| Artificial provider progress waits | Remove; P4 | Actual pending work owns feedback; no backend speedup claim | Closed / Astra approved P4 frontend |
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
| E1 third managed cover provider | P4/P9: fixture provider through discovery/configuration/persistence without central provider branches | P4 stage fixtures approved; configured-setting subset guard source-approved and 34 unit cases pass / final integrated P9 gate pending |
| E2 refresh known vehicle automation action | P2/P6/P9: definition/handler/editor/reservation without dispatcher/store branches | Concrete current-vehicle reservation/editor export/atomic audit exercise source and PostgreSQL approved / final integrated P9 gate pending |
| E3 ACCESS SCHEDULE dated exception | P2/P8/P9: access-schedule ownership, current authority and generated configuration contracts; separate from iCloud calendar sync | Test-only dated rule, precedence/DST and actual edit-before-attempt denial source and PostgreSQL approved / final integrated P9 gate pending |
| E4 last recorded mileage summary | P5/P6/P9: saved snapshot/read contract, information reset and stale-result fence | Test-only value/unit/source/date and missing/zero/reset fixture implemented / PostgreSQL and integrated review pending |
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
members in the event/saga/required-intent transaction. Astra approved the concrete
[schema/state design](architecture-maintainability-p6-design.md) at SHA-256
`035e98c75f71cdbd6a0cc2214af4af1320828c3f67657ef98806005bd78cf856`;
`p6-design-astra-approval.json` retains that exact design approval. Its introductory
pending wording is historical; implementation checkpoint approval and P6 stage
completion are separate gates.

The approved foundation and immutable-intake receipts are retained in
`p6-foundation-commit-equivalence.json` and `p6-intake-commit-equivalence.json`.
Foundation PostgreSQL checks passed 9 cases; the final corrected intake/foundation
gate passed 25 cases, types 65/48, host source/configuration 22/29 and 54 affected
units. A mislabeled overwritten foundation type log was corrected with an explicit
new exact-source run, not represented as original output; the accepted mapping is
`p6-foundation-type-evidence-correction.json`.

Implementation exposed two additional intake boundaries beyond the preserved
original audit. Ready publication now uses its own short advisory transaction lock
and static PostgreSQL sequence, independent of the window coordinator's long domain
transaction. Rolled-back sequence gaps are permitted; consumption chooses the
lowest visible committed ordinal and never waits for nonexistent sequence values.
Webhook provider extras remain immutable for inspection, while operational decode
excludes the proven server-owned observation/classification/retry/ingest fields and
rebuilds them through their owners. Synthetic/internal observations require the
explicit typed `TrustedGateObservation` port. The real adapter-to-enqueue forgery,
positive server classification, concurrent publication and blocked-coordinator
fixtures are in `scripts/validation/test_access_intake_persistence.py`.

A further approved parity refinement keeps visitor-resolution markers on their
terminal window: immutable expiry and active/pruned state with indexed queries.
Only reads reaching the original visitor-suppression stage prune existing markers
across all sources, before exact-known/visitor exemptions. Pruning is irreversible;
a newly created reverse-time marker remains valid until a later applicable prune.
No global capture-time watermark is used. The source-backed reverse-time,
cross-source, endpoint, restart and exemption fixtures belong to
`scripts/validation/test_access_window_persistence.py`. Design amendment receipts
remain separate from the preserved original design/audit. Astra approved the exact
window-store checkpoint recorded above; final integrated runtime evidence remains
pending.

Window implementation also exposed an unproven 512-character token limit that
valid provider extras and OCR candidates do not satisfy. The approved amendment
retains complete TEXT tokens and uses a 32-byte SHA-256 lookup key with mandatory
exact-text comparison. The bounded ingest/kind/ordinal identity and nonunique
digest index retain distinct values even under a digest collision; ordering and
normalization remain unchanged. The strict `observation_tokens` owner has no
movement-service import, independent commit or provider I/O. Long event IDs,
path-derived Unicode IDs, OCR candidates and forced-collision PostgreSQL fixtures
exercise the actual adapter, window insertion and exact-match predicate. This is
an implementation-discovered correction, not a claim from the preserved audit.

The window-store probes compare full-payload loading with compact loading and
Python decoding on equivalent synthetic observations. They measure `tracemalloc`
allocation and load/decode time, not process RSS, database memory or end-to-end
throughput. Each webhook is 979,999 bytes; the candidate-heavy cases retain 5,001
relevant OCR candidates per observation. The retained JSON includes full query
plans and exact source, image and dependency identities. The final corrected
store run `p6-window-persistence/iacs-validation-gey1s3wr` passed all 55
window/intake/foundation cases; earlier snapshots and their setup-only failure
remain retained as superseded evidence.

| Probe | Observations | Python peak bytes, before → after | Seconds, before → after |
| --- | ---: | ---: | ---: |
| Fat irrelevant metadata, small | 4 | 7,268,172 → 6,446,074 | 0.015 → 0.032 |
| Fat irrelevant metadata, target | 60 | 62,416,947 → 6,355,838 | 0.135 → 0.114 |
| Large relevant candidate arrays, small | 4 | 13,607,191 → 11,099,401 | 0.053 → 0.099 |
| Large relevant candidate arrays, target | 12 | 33,885,568 → 14,900,856 | 0.156 → 0.199 |

Lower allocation does not imply faster small cases: three compact queries and
projection overhead make three of these four cases slower. Complete indexed
membership/token evidence and logical candidate values retain parity; the display
preview intentionally contains only the first 50 members, with at most three full
anchor payloads. Large relevant candidate arrays remain variable-sized, and their
target probe is explicitly 12 observations. These store-level results do not claim
P6 runtime throughput or bound worst-case fuzzy-window scan complexity. Final P6
burst and many-open-window runtime benchmarks remain pending.

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
persisted resident-recovery movement saga command requirement remain authoritative. Repository
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


## P6 approved publication owner

The independent committed-facts lane uses the same completion-work table, one
owned processing slot and a compact current-event projection. Event creation,
public revision increments and dirty publication reservation share the caller's
transaction. A leased publication acknowledges only its loaded revision; a newer
committed revision retains one trailing current projection. Publication recovery
has no enrichment deadline: it delivers durable committed facts without renewing
any optional or hardware authority. The old RAM access runtime is not switched by
this owner checkpoint, and full P6 integration remains pending.

The canonical frozen EventBus receipt distinguishes configured transport from
transport acceptance. A successful Redis XADD includes its stream ID; configured
Redis failure or a closed bus is non-acceptance. Explicitly disabled Redis can
accept in-process publication. This receipt does not prove a browser observed the
message or a local business handler completed. Existing observational wrappers
may deliberately ignore it; the durable lane checks it before acknowledgement.
The transport fixture permits only the disposable PostgreSQL/Redis loopback pair
inside the already validated namespace and rejects HTTP and other destinations.
The port-1 case is a guard-injected configured-transport failure, not an observed
TCP refusal. Separate real-Redis fixtures prove stream acceptance, current-state
restart recovery, and XADD rejection followed by repair without origin refresh.

Unit and PostgreSQL owner fixtures cover caller commit/rollback, current revision
coalescing, stale claim/lease fences after blocked locks, clean final flush,
metadata-only claim pages, cancellation rollback, actual send/renewal settlement
and one active send. Runtime composition, frontend update predicates/revision
handling, provider shutdown order and the complete integrated harness remain
required before P6 approval.


## P6 approved completion repository

The origin/readiness repository is a separate partial owner checkpoint. Core
transactions reserve deterministic per-event/stage work; the original receipt,
chosen member identity and deadline are immutable. An explicit typed external
observation port is available only to the code-owned no-ingest origin path;
existing ingest association prevents that fallback. Missing or malformed origin
is held rather than replaced by a recovery clock or diagnostic trace timestamp.
The work revision fences the origin contract independently of public event
revisions. Domain mutations, accepted-result records, required audit and the
completion receipt must share the caller transaction and final fresh-clock fence.

Media claims expire at the original five-minute deadline. Visitor linking has a
separate retained-result readiness contract and short database operation bound:
results accepted on time may be linked after later verified admission or restart,
without provider work or renewed authority. Current repository fixtures prove
only origin/readiness envelopes and caller transaction fences. Actual accepted
result identities, canonical consumed arrival (including USED/closed-window
cases), per-kind applied receipts, field ownership and runtime cancellation must
be proved by the next applicator checkpoint. Full P6 remains open.

The reports full run exposed inherited simulation and access-pipeline fixtures
that still supplied retired raw observation/authority flags. Their corrections
are explicitly test-only sequential handoffs, reviewed separately by Astra;
they use typed fixture-owned observation input and real session/camera matching,
retain the closed-gate domain no-command guard, and add forged raw-tag denial.
The original failures remain retained. No runtime compatibility bypass is added,
and the programme receives these exact corrected fixtures only through the
approved report integration mapping.


## P6 accepted-result/applicator candidate

The next partial owner implements compact accepted visual/information identities,
Saga-first canonical visitor hydration and explicit never-owned/owned/protected
field state. Its exact design v2 was approved by Astra at
`9a8e58bd7ebb7c84d26523c067f5e6a9181a89c8ebc058a25448aad27f718321`
on clean report/completion integration `5d735fbe558984e2cbd869e47d8e2796eecf66b6`.
The previous partial repository commit is `d079bb04a12d927084acee9b2f4d013ddfe2e2f4`;
its source and archive receipt remain retained independently. No whole P6 or final
programme approval follows from these bounded owner approvals.

The initial exact candidate passed 94 PostgreSQL cases (35 result/link, 38 completion,
nine foundation and 12 whole-information), checked/strict types 82/65, 46 affected
units, host 22/29 and clean migration/source/cleanup/operational checks. JUnit is the
count authority; earlier receipt prose incorrectly split result/information as 36/11
while retaining the correct total 94. The predecessor passed 77 cases before compact
preparers and additional contention fixtures. The initial unit setup command named a
nonexistent file; no tests ran, and the corrected actual selection passed 46. Those
results and exact snapshots remain retained.

Final review found malformed retained verification-state/visitor UUID/readiness
cases that could raise instead of holding. Narrow guards and ten negative fixtures
supersede the 94-case source. The complete corrected result suite passed 45 cases at
`p6-result-guard-persistence/iacs-validation-ym6tkxca`, with migration parity,
F821, source integrity, cleanup and operational equality clean. Exact corrected
checked/strict types 82/65 and host 22/29 also pass. Unchanged schema, completion,
foundation and information owner bytes map the other 59 PostgreSQL cases from the
94-case run; this is carried evidence, not a new combined 104-case invocation.
The final closure paragraph is a separately mapped documentation-only follow-up.
Evidence remains under `/private/tmp/iacs-maintainability-programme/p6-result-*`;
Astra approved this bounded owner on source `e491c609…` / patch `7d531a02…`;
commit `710655362174334ba76468e4aeedfe198b9f6606` archives all 792 selected hashes
unchanged. The approval and archive equivalence are retained in
`p6-result-v2-astra-approval.json` and `p6-result-v2-commit-equivalence.json`.
Worker/core/full-P6 obligations below remain open.

The information result path now retires Job-to-Event locking and inline visitor
mutation together. The current postcommit enqueue bridge derives original receipt
from persisted linked ingests; missing/inconsistent provenance holds and replay does
not refresh authority. This transition gap must disappear when core reservation is
wired atomically. Fresh database-time claim/failure fences, remaining-lease budget,
actual slot/renewal/cancellation ownership and shutdown are addressed by the separate
worker/shared-client candidate below, whose approval is independent. Old RAM optional enrichment also
remains pending, along with HA/historical/chart owners, frontend revisions, complete
runtime performance/lifecycle evidence and the full integrated harness.


### P6 HA schema compatibility and migration-parent checkpoint

The four nonindexed HA target/effect URL and entity columns now use TEXT. Existing
settings/Person JSONB accepted values are not newly restricted during atomic
reservation. Bounded 64-character target keys/fingerprints, exact full identities,
RESTRICT retention and UNKNOWN barriers remain intact. The inert schema fixture
uses controlled identity hashes; it does not claim the future HA runtime owner has
been implemented. Long URL/entity same-prefix distinctions and accepted-information
loss-only downgrade refusal are added to the foundation suite. The complete 15-case
foundation PostgreSQL suite passes at
`p6-ha-schema-persistence/iacs-validation-oihsdgqb`, with zero migration drift,
clean source/cleanup/operational checks, exact checked/strict types 82/65, host 22/29,
Ruff/F821 and documentation/diff checks. The first run `iacs-validation-cbx98asc`
retains 14 passes and one new JSONB fixture-binding failure, corrected without a
runtime/schema change before rerunning all 15. Astra approved exact source
`7083184a…` / patch `01cff788…`; commit
`a09eea32b31e0d6a8b999765f89190f074578401` preserves all 792 selected hashes.
The approval/archive receipts are `p6-ha-schema-astra-approval.json` and
`p6-ha-schema-commit-equivalence.json`. Migration `20261007_0013` is now the frozen
parent handed to calendar `0014` in `p6-calendar-0013-parent-handoff.json`;
later changes require a new migration or an explicitly reviewed parent rebase.


### P6 information-worker/shared-client candidate

The bounded worker checkpoint implements the approved v3 design
`cff937dc8de2f1e68485b2310a1a6dd2e1e6c4c4853daf234cc681aee26d0025`
on `a09eea32`. Two actual worker slots include provider/result cleanup, the scalar
32-candidate scan leases only free capacity, and fresh database clocks fence claim,
renewal and failure after locks and writes. Shared lookup/connection-test children
have a single cancel-and-drain boundary. Per-call receipts and one whole-service
cleanup task keep clients open through late admitted notification/request work;
main closes this resource after those consumers and before database disposal.
Normal parallel provider results and accepted-result receipts retain their semantics.
Migration `0013` and schema/model bytes are unchanged.

The resumed daemon no longer contained the historical validation image. Its exit125
setup failure is retained; no tests ran under that invocation. Explicitly selected
immutable backend image `sha256:40d52f1d4d6ca22933eb8a73a5b699dc03963a420b5e38f5fa3a5ff150198ea1`
received fresh exact-lock preparation through the unchanged harness at
`p6-resumed-dependencies/iacs-validation-d25e173x`. Offline lock/tree verification,
version identities, cleanup/source integrity and unchanged operational state passed.
Prior dependency receipts were not rewritten or reused across differing image IDs.

The first candidate passed 59 focused units, 1,268 backend units/29 expected skips,
types 82/65 and a corrected 89-case PostgreSQL matrix. Its immutable candidate
`c2f405d1…` and receipts remain retained, but Astra found a real session-close
ownership gap during final review: SQLAlchemy's normal context exit could leave
its shielded close child running after cancellation. That source is superseded.
The correction preserves the settings getter's cache/conversions, explicitly drains
close at that owner and all worker transaction boundaries, and owns whole lookup/job
children so repeated cancellation cannot abandon active SQL cleanup. No generic
session framework or schema change was introduced.

The corrected runtime passes 67 focused lifetime units and all 1,276 backend units,
with 29 expected skips, types 82/65, Ruff/F821 and host 22/29. Genuine PostgreSQL
run `iacs-validation-qch_j0jg` passes all 23 worker, 45 result/link and 15 foundation
cases. Its whole 95 selection has one inherited small read-cost fixture failure:
public output is equal, but a settings-cache expiry adds a query to only one
measurement (5 versus 4). The measurement-only repair supplies the same real fixture
runtime outside both reads and retains exact payload/query-count/no-snapshot-SQL
assertions plus measured SQL. The whole 12-case information suite then passes at
`iacs-validation-0r565j41`, with migration parity, isolation, cleanup, source integrity
and operational equality clean. The other 83 cases carry by exact unchanged hashes
from `qch_j0jg`; this is not a new combined 95-pass invocation. The repaired tested
source maps to the final candidate through three documentation files only.

Earlier setup/failed evidence remains retained: unavailable old image exit125,
nonexistent unit selection with no tests, missing fixture test registration before
its cancellation barrier, and `iacs-validation-5no4w3a3` 88/89 with a cached
PostgreSQL activity-observer snapshot. The activity poll repair and original corrected
89 run `iacs-validation-0hg5ac9x` remain part of that superseded candidate history.
Astra approved the exact corrected worker source
`830146f3e67824cf6ef0a41fe27b29d8224ca552899d70e94ddcd79e63de7a12`
and patch `4738d696ace3e83570cee4c8f9ab5e459aee1de56a919427824e0036c8578435`.
Commit `893614f909a995170c685a0b87af1bf40378ebd8` preserves all 794 selected hashes;
its archive has only the two established policy exclusions. Verbatim approval and
archive equivalence remain in `p6-information-worker-v2-astra-approval.json` and
`p6-information-worker-v2-commit-equivalence.json`. Those immutable receipts are
not rewritten by the later pre-I/O deadline correction below.

This is not P6 completion. The temporary postcommit information enqueue bridge must
still move into the core decision transaction. Old RAM runtime replacement, media,
HA effects/resolution, historical candidates, charts, frontend revisions and the
full integrated runtime/lifecycle/performance harness remain outstanding.


### P6 media/link/zone owner checkpoint

This checkpoint was built on approved base `893614f909a995170c685a0b87af1bf40378ebd8`.
It implements the separate approved media design
`d9791fc9f2834cee57ac0df5aade8c55dc5557cb0c0e3765a5e91decb2180175`.
It defines two actual optional-work slots and an independent publication lifetime,
explicit closed stage ports, owned session/provider/thread cleanup, immutable
snapshot claim keys and compact frozen stage input. It preserves late provider-free
visitor linkage and the existing postcommit zone feature signal. Nothing starts
these new owners in production yet: the atomic core and matching frontend-consumer
cutover remain mandatory. Existing RAM/optional paths are retired at that cutover.

Review found a relative-budget boundary in the preceding information checkpoint:
slow session close or scheduling could extend a provider start past its original
deadline. The follow-up uses a conservative loop-time anchor before the DB-time
query, passes an absolute minimum through preparation/close/actual invocation, and
keeps one original parent operation timeout. Fresh DB fences remain authoritative;
cleanup can outlive the deadline without granting fresh work. Publication uses
its current lease, independently of optional enrichment's five-minute deadline.
The whole backend gate then exposed a competing nested timeout cancelling already
active lease-loss cleanup. That 1,313-pass/one-failure snapshot is retained; the
corrected single-parent timer and shielded actual visual child pass all 1,315
backend units with 29 expected skips in that retained run. Camera metadata records the actual local
provider receipt time before its later DB proof, rather than file-storage time.

The first media PG run `iacs-validation-lv1i2053` retains 20/24: three new fixtures
omitted the explicit committed resource revision and one omitted required job
creation time. After correcting those fixtures, `iacs-validation-gjxxmj0m` passes
all 180 cases: media29, completion38, publication18, worker23, information12,
result/link45 and foundation15. The first final timing run `iacs-validation-0fmuzlzw`
passes 65 but is superseded by the demonstrated competing-timeout source correction.
`iacs-validation-fqej9r5d` passes the complete media30, worker23 and information12
suites. Review of the first frozen media candidate then reproduced an analogous
publication failure: renewal loss cancelled transport cleanup, and its inline
send timer cancelled it a second time. The failing reproducer/source and original
candidate `3b90eb149ca438e5b41ff273bb4d3c92a48bb8281fe0abe55f0f5dc7c4a00ffe`
remain immutable. A shielded actual transport child now exits its timer before
single cancellation/drain, checks the same original deadline at invocation, and
retains its sole lane until cleanup settles. It cannot ACK/retry on renewal loss
or claim a second identifier while the first child remains occupied.

Final `iacs-validation-cp4j2oh2` passes the whole18 publication PG/Redis suite.
The unchanged65 media/worker/information cases carry from `fqej9r5d`, and unchanged98
completion/result/foundation cases carry from `gjxxmj0m` by exact source/dependency
mapping. Publication18 is no longer claimed as carried, and there is no claimed
combined181-pass invocation. All three namespaces have clean source/cleanup,
migration parity and identical operational snapshots. Final whole backend passes
1,317 units with29 expected skips; types87/70, host22/29 and Ruff/F821 pass.

Filesystem tests cover legacy and claim-specific primary/archive/manifest restore,
all three actual thread stages, exact unlinked cleanup and retention after commit
uncertainty. PG tests prove event/anomaly/result/revision atomicity and expiry rollback,
immutable compact inputs, current plate guards, no late provider start after DB/close
delays, exact thumbnail fallback and postcommit zone signals without reinsertion on
delivery failure. Large relevant zone arrays remain complete and can themselves be
large; unused vendor blobs are excluded. This is correctness/allocation-boundary
evidence, not a new end-to-end performance or production-throughput claim.

Astra approved this bounded owner and the subsequent EOF-only mapping. Commit
`702fb9cf53223f9ca022b5c77869acf9a4f29409` preserves final source
`fb1194e31ac27361f43993a9e909b64a79a16e807bf27678861222f62a59de73`
and full patch `76f3a25c62676f03daf993b78a92ab4a61ceea75cc14ecb28a3a27b6dacaade5`.
Its archive SHA `c40815ecd0489b42cad362a92a83e71b5fb55a9ae9c9278cb735ad4a4422b68b`
matches all 801 selected files; only the two established templates are excluded.
The staged precommit check found one extra terminal newline in a new source file.
Its removal preserved the AST and all other 800 hashes; the failed preflight and
exact approval/mapping remain in `p6-media-final-astra-approval.json` and
`p6-media-final-commit-equivalence.json`. No runtime gate was repeated for whitespace.
P6 still needs the atomic decision/reservation/window terminalization and RAM switch,
HA desired effects/settlement, historical candidates, charts, frontend revision
consumers, real production lifecycle composition and the complete integrated harness
with equivalent-output burst/many-window performance evidence. Crash/unknown-outcome
claim files may be retained on disk; this owner does not invent a sweep.


### Approved confirmation rejection audit prerequisite

This bounded early P8 audit slice supports the pending P6 HA acknowledgement owner.
The approved design is `59c93424ed944d24cfc40adc394d683052cb409408e8dd12e6afe25dd5e26d6e`.
The confirmation participant now returns frozen scalar rejection evidence through
its typed error. With `commit=False` it neither mutates a rejected confirmation nor
commits, rolls back, writes an audit or starts another task/session. Notification
send and grouped-alert owners finish rollback first, then await the confirmation
owner's fresh required audit transaction before returning the original rejection.
That releases the preheld Admin row before the audit actor foreign-key check.
Missing-token 428 remains unaudited. Standalone `commit=True` remains a commit-owning
API: rejection audit and the existing rejected/consumed disposition share its session.
Required audit failure is visible; there is no detached fallback. Active audit SQL
remains cancellable and actual session close is drained before the owner returns.

Final `iacs-validation-f7cdgx74` passes all 132 real PostgreSQL cases: 19 new rejection
cases plus 113 complete existing notification, authority, feature and schedule cases.
The initial 130-pass progress run is retained separately. The final matrix covers
preheld actor locks for missing/consumed/invalid tokens, caller mutation/token rollback,
reuse of an otherwise eligible invalid token after rollback, both actual outer adapters,
original scalar correlation, and standalone/participant/outer required-audit failures.
It retains the original missing-token behavior and forbids notification dispatch.
Migration parity, isolation, unchanged source, empty cleanup and equal operational
snapshots pass. Full backend passes 1,318 with 29 expected skips; 63 affected units
include an actual session-close barrier with repeated cancellation. Types88/71,
host22/29 and Ruff/F821 pass on the corrected source. An initial Ruff import-order
failure was repaired; the later no-escalation Docker socket setup failure ran no
checks and is retained separately from the successful final tooling invocation.

The separate calendar branch is adding account removal/resume confirmation
participants. They must adopt the same rollback-before-audit contract with real
actor-lock and audit-failure tests before integration; this checkpoint covers the
existing programme consumers and does not claim that unintegrated caller migrated.

Astra approved exact source
`df7ecbe13d27b2d8b44581cdb0a409d9c3938123374e8ce6896881c2b24ee6f3`
and full patch `cd27694b4464d292c801ba36cff651f04258dc6c91663527149fa13e816c471e`.
Commit `77e1384a684e9deaa201ca9bae896f79bcf1283d`, tree
`f364a8a24b3a2a6e22ce6fe63be5754639f53e73`, matches all 802 selected source
hashes. Archive SHA `d1c889df5247f5ffafa58fc767c1a121cfb42cb29c2e14bbce83fcde8cb83473`
has 804 files with only the two established policy exclusions. Verbatim approval and
mapping are retained in `p6-confirmation-astra-approval.json` and
`p6-confirmation-commit-equivalence.json`. This is no HA send or settlement
implementation, no production activation and no P6/P8 completion.
Atomic core reservations, RAM retirement, HA/historical/chart owners, frontend
consumer revisions, full lifecycle composition and integrated performance/full
harness gates remain pending.


### Dormant HA reservation/attempt store progress candidate

Design v3 SHA `01fd70e37dcfd68940112cc96f270969ce84620bb1c930da2975f1db8479e397`
is approved for implementation. The current partial source has typed desired/attempt
ports, pure binding policy, one canonical vendor connection fingerprint, locked scalar
Person authority, auth-root/setting fencing, original absolute budgets, immutable
certainty and UNKNOWN barrier handling. Global capacity remains owned through terminal
receipt and actual cleanup, with capacity-only renewal and explicit exact-token release.
The exact approved inert auth-root dependency is copied; the coherent running writer
boundary is still pending integration. Frozen 0013 and model bytes are unchanged.

The original source-only snapshots and the stopped-daemon limitation are retained.
After root verified the already-running runtime, focused run `iacs-validation-dkfy1aiw`
passed 100 real PostgreSQL cases: foundation15, resident26, store40 and settlement19.
Migration parity, isolation, selected-SQL/actual-close cases, unchanged source, empty
cleanup and equal operational snapshots pass. The settlement suite retains 13
SQLAlchemy transaction-reset warnings following the explicit original connection
rollback and ORM reset. The immutable source-only store snapshot remains retained
separately. Subsequent
dormant worker/vendor and preview/acknowledgement source now exists with explicit
post-commit/pre-close ownership notification, actual receipt/client cleanup, safe
frozen transport certainty and exact immutable resolution retries. A renewal-loss
receipt cancellation escape found in source review is corrected and its dedicated
inert fixtures now pass. The exact approved auth.py dependency is mapped
in `p6-ha-auth-prerequisite-copy.json`, without claiming its coherent writer boundary
is integrated. The new settlement service requires an explicit local-capacity read
and is not registered with API/startup. Both old and new sender contracts remain
transitional source until their atomic cutover; the old sender is the sole active
authority. A subsequent acknowledgement review found cleanup could hide frozen
rejection evidence or a known receipt. The corrected owner retains one whole
rollback/actual-close/audit child, proves rollback of the observed original live
transaction, and never treats local invalidation as that proof. Unresolved cleanup
carries the frozen outcome; confirmed resolution survives a late close fault with
bounded owned fallback cleanup and safe error reporting.

Supplemental socket-denied macOS checks use copied exact manifests, pinned uv0.12.13
and Python3.12.14. The first snapshot passed53 focused units; its literal-typing and
import-order failures are retained. The corrected snapshots passed 57 and then 215
focused units, 100 checked/83 strict targets and Ruff/F821. These supplemental checks
remain separate from the genuine Linux evidence. The corrected immutable Linux run
passes 1,375 backend tests with 29 expected skips, 100 checked/83 strict targets,
preflight and Ruff/F821. Its earlier attempt retains the wrong synthetic database-name
prefix and missing results-parent setup failures, plus the stale auth FakeSession.get
keyword signature failure. Only that fixture signature changed after the 100-case
PostgreSQL run; runtime, schema and dependency hashes are unchanged, and the complete
Linux rerun passes. The full integrated harness and exact-source Astra approval
remain pending. Host source/harness checks pass
22/29; AST and frozen0013/model hash checks pass. The first AST attempt used unsupported
host Python3.9 and is retained as a tooling setup failure; bundled Python parsed the
unchanged PEP695 source. No additional programme completion is claimed.

The dormant HA candidate `bef76a28e73b39a6f4a2aa493e2f281836ec69c130972742ffc262292bdbd64d`
received bounded exact-source approval, with activation and full P6 still pending.
Before commit, a cross-owner source check found that its setting fence used plain
keys while `settings.update_settings` uses `iacs:setting:{key}`. The original source,
approval and execution evidence are preserved. The narrow correction aligns those
keys and adds a missing-row contention fixture through the actual settings writer,
including whole-caller rollback and original-budget retry. Corrected run
`iacs-validation-ogybyege` passes the whole affected 41-case store group, migration
parity, isolation, unchanged source, empty cleanup and operational comparison.
The corrected Linux checks pass 215 affected units, types100/83 and Ruff/F821.
Host22/29, links and full staged/unstaged diff checks pass. The unchanged 60
PostgreSQL cases and original 1,375 backend tests remain mapped evidence, not a
newly executed combined run. Renewed exact-source approval remains pending. No
schema, auth-root writer, active sender or production startup change is part of
this correction.


## Continuation and evidence availability

The continuation checkpoint is clean branch `codex/iacs-maintainability-programme`,
commit `a87115e1184dce902060a3a224676bb16e329a3c`, tree
`a4583f9930a8d372e1165dd0f69555b5c732bddf`, in
`/Users/jas/.codex/worktrees/iacs-maintainability-programme/Intelligent Access System`.
The operational checkout remains separate. Sol6.1 Ultra continues implementation;
new advisory/review/final-signoff work uses Astra High by the user's revised setting.
Historical Ultra approvals above remain historical approvals.

The former `/private/tmp/iacs-maintainability-programme` evidence and canonical
dependency preparation directories disappeared after the session/reboot. Git source
and these historical summaries survive; absent raw logs, manifests, artifacts and
reusable dependencies are not currently inspectable and must not be represented as
available evidence. Fresh validation will retain its own exact source/dependency
identities, commands and log paths in the final candidate receipt. This loss and the
new design preparation do not increase the reported programme completion.


## P8 calendar owner (approved; integrated gates remain open)

Calendar design v3 `36ba560bc422195813456a4e92fce6db3858a9b29dcfeb17d79a831f264431b7`
and bounded SDK/auth-root/schema, current-authority and real workflow-engine
checkpoints have Astra Ultra approval. The current-authority receipt is
`90d7c571bf5dd8f6bc0255f94ee7782c2d39efe572bea97bf4db0c689961e3e5`
(61 real PG cases plus affected auth/workflow units). The engine receipt is
`f91eae4a50d6a9ae0c4e073966b976cc8f11dc3383e31f54584ee9fff5beee69`
(17 real PG cases and final 140 affected workflow cases). The nonblocking root
handoff is separately approved at `cf60ea85e0ad411aa47667e898d9e79b6d2c3acd1f55b275c9275c99836cb642`.
These approvals bind their copied source only.

Later account-auth/removal, actual API/disconnect/rejection settlement, exact-run
editor recovery, selected-pass metadata and process lifetime composition remain
unapproved WIP. A supplemental matched-lock host Node24.19 run passed tsc and 32
selected tests at `p8-calendar-frontend-host-progress/run-1791451362289659000`.
API progress attempt
`u3oczads` stopped before tests during dependency-copy disk exhaustion. Scoped
completed-run dependency copies were retired with manifest bodies/reference
identities retained; `p8-calendar-duplicate-dependencies-retired.json` records all
38 exact paths and the direct-replay limitation. Canonical `uu6ccsow` preparation,
source snapshots, logs/results and immutable approved evidence remain retained.
After the runtime became available again, frozen `7ebc7cf` passed 95 selected
Linux calendar/auth/lifecycle units and empty migration down/re-up/parity checks.
The first actual API run retained 12 passes and 13 fixture failures: its inert
telemetry port lacked `start_span`. The attributable test-only correction keeps
the actual middleware and passes all 25 API cases at
`p8-calendar-api-fixture-corrected-progress/iacs-validation-mk4k826g`. Astra accepted
that exact fixture/evidence mapping, not full calendar implementation. The cases
cover checked-out database isolation during SDK work, actual HTTP disconnect,
same-run recovery, removal/resume rejection audit settlement and owned postaccept
cleanup. Source/cleanup/operational comparisons pass.

The separate calendar correction commit `e31214d` preserves the frozen candidate
and adds a calendar-owned scrolling body through the shared dialog's ordinary
class-name port. The original phone Resume click obstruction remains failed
browser evidence. Combined corrected runtime `44b06ad` passes locked Node22 build
and all 371 tests in 60 files. Its broad browser run passes 178 of 180 cases; the
two failures are the same stale pass-detail fixture in Chromium and WebKit.
The corrected fixture retains the accepted pass DTO and checks the actual selected
detail response; its whole-file browser rerun was pending at that checkpoint.
The resumed current candidate and fresh browser gate are recorded below.

Commit `bf1f2d5` composes the canonical approved `77e1384` confirmation prerequisite,
including both existing outer callers, fixtures, rejection PG suite and checked
registrations. Its source mapping is accepted; actual combined consumer/full-harness
validation remains required. The approved inherited history-fixture retirement is
separately attributable at `933cda5`. Full calendar/AM-08/P9 remain pending; the E3
access-schedule exercise is a separate final conformance obligation.


### P7 compact reads — approved

The separate compact-read branch starts at the frozen, unapproved calendar WIP
`7ebc7cf`. Astra approved only the concrete P7 design v2 (`7bc9a84a…ef3be5`).
The source now shares typed scalar chart reads, ranks unknown plates before latest
snapshot hydration, projects public presence without entities/photo bodies, and
provides one-statement global counts plus six ordered roster rows. Forecast reads
preserve recorded identity and distinct single-current-person fallback with only
required scalar columns. Dashboard uses bounded presence/expected-name summaries
and explicit hidden counts; Exited Today retains recent-event semantics.

The first source-only P7 freeze (`16c6d81`) is superseded by a narrow public DTO
correction: unknown chart rows retain `latest_snapshot`, as consumed by Top Charts.
The correction adds public-service and real chart-consumer image regressions,
updates prepared PG assertions, and records complete public response keys in the
before/after probe. Neither source freeze implies implementation approval.

Host Node 24 matched-lock TypeScript and 40 affected frontend tests provide
supplemental progress only. A separate exact-calendar-lock macOS Python 3.12
environment, with socket connections denied before imports, passes 12 existing
chart/predictor units and strict checks for the three new/factored read owners.
Original stale-fake/import-order and trailing-blank-line failures remain retained.
The corrected `963a4ce` source passes all 11 compact-read PG cases and 14 affected
Linux units at `p7-compact-persistence-progress/iacs-validation-ach_ipoa`, with
migration, isolation, source/cleanup and operational comparisons clean. Public
chart DTO/image tests exercise `get_leaderboard` and the unchanged Top Charts
consumer, rather than asserting only the internal projection. The combined Node22
and browser evidence above covers the current compact frontend runtime; whole
relevant fixture correction, equivalent-output plans/allocation/output probes,
the complete combined harness and exact Astra implementation approval remain
pending. AM-06/AM-12, calendar/AM-08, P9
and the full programme remain open. Full-history chart aggregation, population-sized
public presence/forecast and density-dependent forecast memory remain limitations;
no cache, persisted rank/count projection or schema change is introduced by P7.


### Calendar/P7 validation resumed on 2026-10-11

The prior `/private/tmp/iacs-maintainability-programme` evidence and dependency
directories were lost after restart. Earlier entries above are historical recorded
results; their absent raw logs are not presented as fresh evidence. The original
P7 `o6ph7jvq` probe failed during raw SQL seeding before measurement because required
Python-only model defaults were omitted. The corrected fixture supplies those
values explicitly. No benchmark result is attributed to that failed attempt.

Fresh evidence is retained outside source and operational mounts in the persistent
`iacs-maintainability-frontend/validation-evidence` sibling. Exact-lock preparation
`dependency-preparation/iacs-validation-9hyc842c` verifies Python 3.12.14, the locked
Python environment, pinned Linux Node 22.23.2, npm lock versions and dependency tree.
The existing immutable backend image is reused without starting its application.
No tooling image rebuild or operational restart is needed.

`p7-compact-comparison/comparison.json` records all 22 successful phases across the
same 11 before/after workloads, three samples each, with identical full public
JSON/SHA outputs, actual driver statements/parameters, complete EXPLAIN ANALYZE
plans and root row counts. Baseline is `7ebc7cf`; candidate read owners are unchanged
from `52a187f`. Both run the identical corrected fixture at SHA-256
`23838b41f1c6e502bcac1443be0af121a4e3203eee9261fdb9146fe32706fd47`
and share one verified read-only dependency environment. Backend limits are 3 GiB
RAM and 4 GiB total RAM/swap, with a separate 1 GiB PostgreSQL cap and one CPU each.
No phase is OOM-killed, owned cleanup is empty and operational state is unchanged.

At 2,000 people, public presence retains its identical 301,334-byte population
response while median Python allocation falls 10,769,289 to 1,753,281 bytes and
queries fall five to one. Low-density forecast retains 400,236 output bytes, with
144,939,124 to 36,972,977 allocation bytes and 15 to one queries. High-density
forecast seeds 480,000 movements, returns 478,000 after the fixed current-time
cutoff, and retains the same 400,236-byte response; allocation is 1,373,421,700 to
308,529,720 bytes and queries 15 to one. Its median measured operation is 40.201 to
21.935 seconds. These are Python allocations, not process RSS or PostgreSQL memory.

The one-million-event chart retains equal 29,293-byte output and two queries.
Its median time increases 0.785 to 0.881 seconds despite allocation decreasing
530,513 to 448,175 bytes. The separate small fat-column chart shows 15,959,471 to
446,253 allocation bytes. Unknown-chart lookup plans still perform 25 selected
plate bitmap lookups, each combining roughly 58,823 snapshot-index entries. This
is selected result hydration, not a bounded-scanned-work or optimal-index claim;
full-history aggregation, public population reads and forecast density growth
remain explicit costs. No speculative index, cache or storage projection is added.

Fresh host source-selection checks pass 22 and 29 tests. The first full resumed
run, `calendar-p7-full/iacs-validation-agwct_s_`, records inherited calendar import
ordering, cleanup-warning and explicit-None-return source-check failures. Narrow
repairs preserve known acceptance and add no payload or exception disclosure.
Its store cancellation fixture also expected definite cancellation after native
asyncpg cancellation invalidated a previously used connection. Astra High approved
a narrow assertion correction: only that case expects `CalendarOutcomeUnknown`
with `CancelledError` as its cause; the ordinary receipt-close case still expects
`CancelledError`. Repeated cancellation, actual close, zero checked-out connections
and durable UUID-state assertions remain. Runtime settlement proof is unchanged.
Two settings-concurrency fixture cases also observed their second writer only
after actor loading, which now follows the real auth-root fence. Astra High
approved moving only the observation barrier before that real fence. PostgreSQL
blocking-graph proof, real authority/lock owners, held audit, final row and both
old/new audit checks remain. This proves actual writer serialization, not
independent per-setting-lock coverage.
The failed run remains retained. The corrected full run
`calendar-p7-full/iacs-validation-y7dhiral` passes all 205 result entries, including
1,303 backend tests (29 expected database-free skips), all 371 frontend tests in
60 files and the pinned Node 22 production build, 109 checked-body/92 strict type
targets, 55 PostgreSQL suites with 960 cases, migration parity and database restore.
Source integrity and owned cleanup are clean; operational state is unchanged.
This includes the 11 compact-read PostgreSQL cases and actual calendar SDK cleanup,
late acceptance, original-run recovery and authority contention fixtures.
Astra High exact-candidate approval is recorded below.
Browser dependency preflight separately
refuses an installed source-map-js 1.2.1 against lock 1.2.2. Its corrected isolated
copy uses only the trusted canonical 1.2.2 package, with complete lock verification;
operational dependencies remain unchanged.


`calendar-p7-browser-corrected` passes all 180 affected cases in Chromium and
WebKit, with one worker and no retries. Its frontend input hashes match the
corrected full snapshot and remain unchanged after the run. The calendar recovery
checks cover 390×844, 844×390, 1440×900 and dark 900×1000 layouts, exact original-run
recovery, lost responses, retained dirty authentication and keyboard/footer access.
The prior selected-pass fixture failure is superseded by the fresh complete
affected-file pass. Saved viewport images were inspected. These are inert browser
fixtures, not physical-device or live-provider tests. Browser execution uses host
Node 24.19.0; the pinned Linux Node 22 build and unit run remains authoritative.
The complete copied dependency lock passes before and after execution, the
original host dependency bytes remain unchanged, and the owned server is closed.
Only this closure documentation differs from the successful full-run source.
The final calendar/P7 source and patch identity is recorded in one
`calendar-p7-final-receipt.json`. Astra High approved the exact candidate with no
source findings: reviewed receipt SHA-256
`cb8a2e3a46c42ba8648a307831dc00a41adb6bcb19aae744b1d1042657eb4303`,
patch `da61d5657aa7a027442a395dc0ed40ed7576262d9fe4bcd44d72910283120288`
and source fingerprint
`84d19a053c5d323204e1740309cea1ccdaf705100388049943d498918766c09e`.
The approved commit mapping adds only this status prose; runtime, fixtures, schema,
dependencies and harness bytes match the successful evidence. Remaining P6/P8/P9
owners, final merged validation and integrated conformance are separate gates.


### Resumed chart/historical checkpoint

The chart/history slice uses the existing 0013 singleton and candidate tables.
One evaluator commits claimed leader progress and deterministic original-deadline
intent while retaining trailing work; directory and historical changes mark silently.
Historical discovery reserves canonical Protect identity with immutable first facts,
prepares token-owned media outside the transaction, and atomically commits repair,
audit, publication, chart dirty work and final HA invalidation. Uncertain preparation
and five failed candidate attempts remain explicit holds. No schema, extra scheduler,
compatibility sender or general cleanup framework was added.

Fresh selected evidence is retained under
`/Users/jas/.codex/worktrees/iacs-maintainability-frontend/validation-evidence/chart-history`.
Initial `focused-kfc8m59x` passed all 13 chart cases and 11 historical cases; its
historical blocker-connection observation and obsolete shared sender mock failures
remain retained. Corrected `corrected-21yowc32` passed all 12 historical cases and
the existing historical pipeline case, including actual API/publication consumers:
26 distinct PostgreSQL cases pass across the unchanged chart evidence and corrected
historical evidence. Both runs prove cleanup, source/dependency integrity and
operational equality. Architecture, the 22 source/29 harness checks, current
29 affected unit cases and all 117 strict targets pass. The checked-body set has
one pending old enrichment-caller error, resolved by core's coordinated deletion.
Initial type inventory and read-only lint-cache invocation failures remain retained;
the corrected lint passes. Astra High approved the exact owner integration checkpoint with no scoped source
findings: receipt `d4d0fea8f8d6a0b7b0eb2f0a3e44ad21d91d78576cc544df8356c9fcc85e51a6`,
patch `027a68f82a5671b026e90de8019e4cfea3293d2677c9d308da5c4bb2539f06f0`.
The commit mapping preserves those runtime/test bytes; only this approval prose
differs from the frozen candidate. Core owns retirement of the old enrichment caller and lifecycle composition;
this slice removes its legacy chart evaluator/tests. The integrated P6 full harness,
burst performance, P8/P9 and final programme gates remain open. Earlier missing raw
logs remain historical records as disclosed above.


### Composed P6 focused checkpoint (2026-10-11)

Core source through `22ca2d4` retires RAM queue/windows, inline enrichment and the
old HA sender. Durable intake/window terminalization, event/Saga, required intents,
optional reservations, initial publication and chart work join the owning decision
transaction. All Presence and HA mutation callers use the last in-session HA
participant; person/configuration edits invalidate prior generations, including
A→B→A changes. The existing publication lane retains named linked visitor signals
with at-least-once acknowledgment semantics. No schema, generic framework or
Vehicle deletion protocol was added. Source-only Astra approvals do not close P6.

Evidence lives under
`/Users/jas/.codex/worktrees/iacs-maintainability-programme/validation-evidence`.
`p6-focused-4dj2f1bz` retains the original activation failures and green intake16,
media30, accepted-results45 and information12 cases. `p6-focused-17a6i7z8` supplies
corrected camera5, resident9 and publication28 cases; its pipeline and two HA fixture
failures remain retained. `p6-focused-pu857lc6` supplies HA49 and window39 cases,
including the total retry/restart, owned close, complete evidence, backlog and
4/80-window measurements; its remaining synthetic cleanup failure is retained.
`p6-focused-ib9399ld` supplies corrected pipeline30, including all seven inert
simulation scenarios with scoped cleanup and unrelated sentinel preservation.
Together these mapped ten suites cover 263 distinct PostgreSQL cases. Each run's
manifest binds exact frozen source, HEAD/tree, patch and dependencies; cleanup,
source/dependency integrity, production and operational equality pass. The
141-case composed inert unit selection also passes. This is focused feedback,
not a full integrated harness result.

The distinct-window output preserves matching window, chosen rank and member count.
At 4 windows the Python allocation peak was 1,285,330 before / 326,374 after; at
80 it was 11,615,283 / 437,021. Vendor-body retention changed from 4/80 inputs to
one current input; ordered summaries use one/two 50-row pages, with no vendor
payload column in matching SQL. Target retained vendor JSON was 10,508,000 bytes.
Plans and raw measurements are in the window suite artifacts. These compare local
load/append allocation only, not RSS or throughput; after timings were not faster.
Optional-provider-A blocking while core-B commits is proven by the actual composed
pipeline fixtures, separate from this memory comparison. Full P6/P8 composition,
required harness/browser checks and exact final-candidate review remain open.


Astra High approved the focused P6 checkpoint `30c4938` after independently
checking the 263-case mapping, retained run/log hashes, source/cleanup proofs and
bounded allocation evidence. The ordinary resident-count correction changes only
documentation. Full composed P6/P8/P9 validation and fresh final review remain open.
