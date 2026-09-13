# Recovery implementation status

Implementation authorized 2026-09-12. Deployment, production data changes, live
hardware, notifications, commits and pushes are excluded.

## Final acceptance — isolated implementation

The approved implementation is complete in the isolated working copy. The full
locked run `iacs-phase1-bhet89ul` passed every required check, including
**1318 backend unit tests, 766 PostgreSQL contracts,
50 safety diagnostics, and 209 frontend tests/build**. The 29 unit skips
are the PostgreSQL gate journal cases; all ran and passed in the persistence lane.
Source snapshot22, harness configuration19, expanded Ruff/mypy, import/writer guards,
Compose parsing, fresh/historical schema equivalence, migration checks and actual
synthetic database dump/restore passed. Each command and working directory is in
that run's `checks.json`; test results and cleanup/source-integrity receipts are
retained. The final code/test source fingerprint is
`73f0382a6ac1b3486ba8e05955f341ece2ccc85a930bfd5e2b6f62331c83f301` (the post-run edit is this status document only).

Terra implemented bounded components; Astra independently reviewed authority,
actuation, recovery, cancellation, delivery certainty and simulator restrictions.
Reported blockers were corrected and their inert/real-PostgreSQL regressions pass.
The final wording-only clarification and paired assertion are included in this
successful exact-source run. Historical failures below remain evidence, not
unresolved current failures.

### Release boundaries and remaining limitations

- Production source, its dirty status, running deployment and live data were not
  changed. No commit, push, deployment, production migration, live notification or
  supervised hardware test occurred. Remote GitHub Actions and a real browser
  smoke session were not executed; local locked CI-equivalent checks and inert
  frontend interaction tests were executed.
- Operator entry-gate designation, approved live backup/restore preparation and
  source/image/schema compatibility review remain mandatory before activation.
  The explicit designation must be an enabled commandable automatic-access target;
  no list-order default is inferred. Rollback must retain attempted/uncertain work.
- Physical evidence means verified gate opening/open state, not proof a vehicle
  crossed the boundary. HA actionable tokens confer their bound requester's
  delegated authority; HA does not independently prove the identity of the human
  holding that mobile device. Tokens are destination/configuration-bound and are
  not logged or made executable through shared conversation history.
- The static backend cycle inventory is reduced from121 edges to10, not zero.
  The remaining status-aggregation loop links `AccessDeviceService.status` with
  Home Assistant's integration facade and notification/action owners. Keep its
  compatibility fields under those named owners; any later separation must prove
  parity for door/keep-open status consumers. The ratchet permits only these10
  existing edges and one test-only simulator Presence constructor; it permits no
  growth. Frontend runtime cycle edges remain0.
- The optional expanded router lint still reports pre-existing FastAPI B008
  dependency-signature style findings. Required scoped Ruff (including actionable
  and simulator owners), mypy and whole-backend undefined-name checks all pass.

## Source and safety baseline

- Original: `/Users/jas/Documents/Intelligent Access System`, dirty `main`, commit
  `69f9d8cfc1f77417a57105223f4e0772ae62de5d`, 146 status entries.
- Working copy: `/private/tmp/iacs-recovery-7ggriel9/work`, branch
  `codex/architectural-recovery`. Includes 488 safe current files, including the
  three audit documents; deleted tracked files stay deleted.
- Evidence: `/private/tmp/iacs-recovery-7ggriel9/evidence/baseline.json`.
- Docker mount inspection confirmed original source is mounted by the running
  backend and updater. All implementation stays in the isolated working copy.
- No production environment, credentials, data, logs or dependency trees copied.

## Approved policy

1. Individual manual gate buttons target the selected gate; the global endpoint
   remains available with explicit global scope.
2. No retry/failover after possible command transmission; reconcile uncertainty.
3. Explicit designated entry gate establishes admission. No automatic selection;
   release activation requires operator configuration of an enabled commandable
   automatic-access target.
4. Reserve one-time visitors until verified entry-gate opening; definite rejection
   releases, unknown holds, late verification consumes atomically with admission.
   Authorized arrival with fresh already-open evidence can consume without a send.
5. Live recognition and unattempted scheduled hardware catch-up have a 60-second
   maximum age, with current authorization/preconditions checked before sending.
   Historical/restart backfill stays inert; attempted work is never reset pending.
6. Alfred/phrase hardware requires current active Admin and requester-bound
   confirmation. Shared history does not transfer approval authority.
7. Operational notifications, automations and incoming messages require durable
   acceptance/recovery. Enrichment/typing remain optional. Unknown sends need review.
8. Report preview uses complete backend export calculations and site timezone.
   Existing notification dispatch age remains 900 seconds.
9. Full-flow simulation is limited to isolated tests (explicit operator decision
   on 2026-09-13). The production route must reject before simulation mutations;
   no environment switch may enable it against configured operational resources.

## Ownership and gates

Lead owns all shared models, revisions, CI, shared fixtures and final lifecycle
wiring. Agents must use explicit working-copy paths and exclusive file scopes.
Full isolated harness runs are serialized. Each run gets unique evidence/resources.

| Milestone | Status | Assigned scope / prerequisite |
|---|---|---|
| 1: P01/P10a characterization | Accepted | Corrected complete isolated run; all six hazard classes reproduced, historical schema gaps measured, exact source/locks retained. |
| 2: P02/P03/P04/P08 | Implemented; full isolated validation passed | Authority, requester-bound approvals, exact-target receipts, current dispatch authorization and paired UI pass focused gates. New recovery discovery also passes actual-auth PostgreSQL tests. |
| 3: P05/P06/P09d | Implemented; full isolated validation passed | Single admission finalizer and ordered presence/historical owners implemented. Scheduled-pass matching corrected; admission, ordering and visitor reservation PostgreSQL contracts pass. Complete access pipeline and atomic delivery integration pass the full isolated run. |
| 4: P07/P09 | Implemented; full isolated validation passed | Automation, operational handoffs, WhatsApp extraction/inbox and Discord inbox pass focused persistence gates. Actionable-output atomic recovery and single-attempt Discord transport pass independent review and combined tests. |
| 5: P10/P11 | Implemented; full isolated validation passed | Historical schema and additive revisions through0007 pass prior equivalence and actual synthetic restore tests. Release/hold contracts and architecture ratchet pass focused checks. Messaging lifecycle corrections, release/restore and the complete locked harness pass. |

## Validation record

- Initial mount inspection: passed, read-only metadata only.
- Initial source capture: passed, 488 safe files, dirty work preserved.
- Existing Python/frontend locks materialized in disposable manifest-only containers;
  current source-pinned Node image and immutable backend image recorded. No project
  dependency versions changed; dependency containers cleaned up.
- Five database-leaking unit paths explicitly isolated in three existing test files.
  Targeted network-none / database-port-1 run: **91 passed in 2.52s**.
  Evidence: `evidence/five-isolation.xml`. Original assertions preserved; no app fix.
- Original safe source retained separately under `evidence/original-source` so task
  changes can be delivered independently of pre-existing dirty work.
- Combined foundation evidence is recorded below. Final combined implementation
  validation remains pending; bounded package passes do not substitute for it.

Update this ledger at every integration gate. Do not mark a package complete on
file relocation, inert-store creation alone, or passing branch-local tests.

### Accepted foundation and first replacements

- Corrected combined baseline: `evidence/iacs-phase1-wamzjnsj`. Dedicated writable
  artifact mount, input integrity and cleanup pass; production metadata unchanged.
  Backend 905, PostgreSQL 178 and locked frontend 63 tests pass, along with build,
  lint/type/compile/Compose/migrations. Hazard diagnostics remain honestly failing
  (14 failed, 18 supporting passes); they are separate from ordinary test totals.
- Historical migration freeze: `evidence/schema-freeze-3xzlu66a`. All required
  schema gates pass. One precisely bounded `ACCEPTED_DIFFERENCE` records the old
  mutable baseline's missing `users.auth_session_version DEFAULT 0`; no other
  difference is waived. Fresh and both historical upgrade heads compare exactly.
  Revision `20260912_0002` converges existing installations without row changes.
- P02: `evidence/p02-g0pwgjve`, 96 unit/contract tests and 31 PostgreSQL real
  automation-bridge cases pass. Unknown/historical provenance denies hardware and
  preserves legitimate later notifications; one policy owner replaces bypasses.
- P08 request lifetimes: `evidence/frontend-p08a`, 75 locked frontend tests and
  build pass. P08 session socket: `evidence/frontend-p08b-85w20685`, 86 tests and
  build pass. The first socket test run failed one StrictMode harness assertion;
  the corrected test uses the installed library's strict-root option and still
  requires setup/cleanup/remount behavior. No application assertion was weakened.
- Additive Alfred approvals and device command records are now defined in lead-
  owned revisions `20260912_0003` and `20260912_0004`. These are implementation
  checkpoints, **not approved standalone releases**. Executable owner cutovers,
  populated downgrade/hold tests and combined validation are still required.

### Accepted bounded integration gates

- P03 backend: `evidence/p03-t0i1vr7z`, 152 unit/contract tests,28 PostgreSQL
  approval cases and2 independent concurrent-confirmation probes pass. Pending
  executable conversation JSON is replaced by requester/version-bound durable
  claims and retrievable results; old previews expire. Shared history remains.
- P03 frontend: `evidence/frontend-p03-zxv9juh_`,109 tests and production build
  pass. Removed ID-less confirmation reconstruction. Opaque requester-scoped
  receipts survive interrupted requests; recovery performs GET only, with no
  automatic confirmation resend. The preceding `frontend-p03-hgs1bu_y` run could
  not start tools because a disposable copy dereferenced dependency symlinks;
  preserving those symlinks fixed the runner without application edits.
- Report parity: `evidence/p08c-3fztkudq`,28 backend tests pass, including complete
  history, timezone/DST and export orchestration; frontend report fixture/context
  checks pass in the109-test run. Browser policy calculations are removed.
- P09 notification activation: `evidence/p09a-al7w7kmo` unit and PostgreSQL gates
  pass. Machine activation now uses the canonical transaction/audit participant.
  Shared pure payload normalization has no dependency on the delivery service.
- Schema: `evidence/schema-freeze-pqcjbq7h` passes fresh/historical/roundtrip
  comparisons through0006, including immutable observation attribution and new
  automation occurrence fields. Legacy automation rows are not made runnable.
- Lifecycle: `evidence/lifecycle-qhhtxpcl`,19 fault/readiness tests pass. Partial
  startup unwinds registered resources; one cleanup failure does not prevent the
  others. Readiness measures required process/database owners separately from
  optional vendor availability.
- Command cross-review found and is closing stale per-process configuration
  validation. The authoritative settings owner now supplies an uncached read in
  the dispatch transaction, and the same snapshot must reach provider I/O.
  Target evidence is attributed by immutable device identity and binding digest.
- CI now invokes the same locked isolated harness and architecture ratchet as
  local validation. This workflow has **not** been pushed or run on GitHub.

### Historical first foundation run (superseded by corrected run above)

Evidence: `evidence/iacs-phase1-j23gc90q` (exact manifest authoritative).

- Exact backend and frontend locks verified with zero version mismatches.
- Backend DB-free: 905 passed. Persistence: 178 passed. Targeted Ruff/mypy,
  frontend tests/build, Compose parsing, fresh migration and Alembic check passed.
- Six hazard classes: 32 cases, 14 intended invariant failures, 18 supporting
  passes; zero setup/collection errors. Reproduced multi-target truth loss, lost
  HA response failover, expired lease duplicate send, unknown automation hardware,
  duplicate V3 confirmation, and stale/ineligible presence reconstruction.
- Schema characterization: fresh/staged/roundtrip head and guarded recovery
  downgrade passed. Direct-vs-downgraded pre-recovery, future-model independence,
  historical initial/pre-recovery revision stability and initial-to-head parity
  failed. Exact diffs retained; new model integration remains gated.
- Cleanup passed; before/after production container state unchanged.
- Independent review found inherited writable `/results` alias to snapshot/deps
  via the full evidence directory. No production exposure occurred. Replace with
  dedicated writable artifacts subdirectory and verify copied input hashes before
  accepting the harness handoff. Next run validates this correction.

### Integration checkpoint: authority, command outcomes and recovery

- P04 `evidence/p04-lehxsi85`: 130 unit/contract, 20 journal persistence,
  six transmission/lease probes and 22 current-recognition-authority tests pass.
  Per-target uncertainty is retained, accepted/unknown targets are not replayed.
- Root transaction/receipt `evidence/mutations-iaqe8qrx`: 21 PostgreSQL/ASGI
  tests pass for settings/maintenance audit atomicity, target guards and receipt
  retrieval. Authentication transport was isolated in that package; actual JWT
  read-path tests were added separately below.
- P07 `evidence/p07-0m5u5a8f`: 173 unit, 27 store, 31 dispatcher, four handoff,
  three activation, 25 notification recovery and 31 unknown-recognition bridge
  tests pass. The previous run exposed expired ORM attributes after completion;
  the owner refreshes/serializes inside its transaction, and the same assertions
  pass. No diagnostic was marked passing by exclusion.
- Schema `evidence/schema-freeze-s55de7ov`: fresh installation and historical
  upgrade equivalence through additive revision0006 pass. Legacy pass rows and
  unclassified sagas are unchanged. No live migration was run.
- P07b `evidence/p07-xy7x9gui`: nine intake/actual-auth reads, prior store,
  dispatcher, handoff, activation, notification and unknown-bridge suites pass.
  One unit check failed because this temporary runner omitted the paired frontend
  fixture. The corrected exact-source run `evidence/p07-h_llhmn1` passes all179
  units, seven webhook atomicity and eight browser-loss/read-only discovery tests.
- Auth revocation cleanup moved from token lookup to the existing token-revocation
  transaction; actual bearer-token recovery GETs are tested for zero writes.
- Frontend `evidence/frontend-p03-6moq62k4`: locked tests and build pass for partial
  outcomes and automation history. Prior `frontend-p03-izqrme7h` caught a missing
  JSX closing tag; the component was fixed, with no weakened test/build checks.
- P10 `evidence/p10-release-3a5sr5ln`: 53 isolated file/process/update-unit tests
  pass, including child-process cleanup. Independent review then identified
  incomplete concurrent-editor fencing, cancellation-drain and restore-absence
  handling. Corrections and additional regression tests are in progress; this
  package is not release-accepted on the earlier pass alone.
- P05 `evidence/p07-si7gzqpj`: unit and probe06 suites pass; admission23 pass/two
  failures expose scheduled-pass selection. Journal policy tests also exposed
  stale test references to the removed maintenance lock location (three failures
  and teardown errors). The real lock and intent ordering remain required; tests
  are being moved to its actual state owner, not restored through a shim.
- Every completed resource-owning run above reports cleanup0 and unchanged
  production metadata; frontend runs additionally check source integrity.

Current source is intentionally mid-integration and is **not a deployable release**.
Full source/lock harness, combined admission/visitor/messaging recovery, current
architecture ratchet and compatible release/restore evidence remain outstanding.


### P06 integration, independent review and follow-up gates

- `evidence/p07-rrrvwkts`: 29 journal,25 admission,25 current-authority,5 paused-camera,
  2 reconciliation fairness and36 recognition/presence PostgreSQL cases pass.
  The frozen visitor suite exposed11 implicit ORM refresh failures in22 cases;
  the244-case unit selection had one obsolete fake-session failure. These were
  not excluded: the visitor owner now explicitly refreshes its database-generated
  timestamp before preparing transactional payloads, and the pure unit fixture
  isolates its audit participant. `evidence/p07-ld17x3y_` reruns all22 visitor
  persistence cases successfully, plus visitor/camera/WhatsApp/notification units.
- The camera experiment releases the database connection before optional vendor
  evidence, then re-resolves identity, activity, presence and runtime policy in
  the final transaction. All five paused-vendor mutation cases pass.
- The first concrete messaging transport/configuration extraction passes its
  frozen unit and contract selection in `p07-ld17x3y_`. Concrete conversation
  ownership, incoming recovery and facade retirement are still unfinished.
- Independent integration review found recognition automation could race core
  admission, and optional visitor enrichment could conceal a stale returned
  transition snapshot. Root refreshes the visitor result in the final transaction;
  the automation owner is implementing a durable unattempted admission dependency.
  These corrections require their new interleaving/pipeline tests before acceptance.
- The original dependency ratchet passes at233 Python/81 frontend modules after
  removing a type-only runtime import from access payloads. No baseline expansion.
- `evidence/p10-release-n2xjhlm0`:77 release/process tests pass, covering source
  promotion fences, retained foreign edits, rollback preimages and cancellation.
  Cross-review then identified missing durable executor generation ownership and
  asymmetric restore receipt checkpointing. Those fixes and PostgreSQL tests are
  in progress; release/restore is not yet accepted.
- Every resource-owning run above reports cleanup0 and production_unchanged=true.
  No production deployment, data restore or hardware test was performed.

### Admission ordering, delivery and restore checkpoint

- `evidence/p07-bewq9r76`: admission25/reservation22/approval29/actual-auth8
  PostgreSQL tests passed. Pipeline failures exposed an unflushed anomaly UUID and
  detached vehicle reads; these were corrected in the actual transaction/audit
  owners. Old hardware unit fakes were updated to assert the canonical durable
  delivery handoff, without restoring duplicate audit or notification owners.
- `evidence/p07-yzndjwvn`: admission30/fairness2/automation-ordering26 and real
  unknown-recognition bridge31 passed. Recognition automation waits durably for
  the original admission owner; the original freshness deadline is retained.
  Its unit command named a nonexistent test file (not attempted), and pipeline
  setup used an obsolete notification getter (errors, subsequently corrected).
- `evidence/p07-gtywscsl`:213 unit tests,22 incoming-store PostgreSQL cases,
  24 automatic-garage output recovery cases and12 real compressed archive/source
  restoration cases passed. Access pipeline31/32 passed. The remaining simulation
  case is deliberately failing: the existing process-global simulation path does
  not provide durable admission evidence. A production restriction requires the
  pending explicit user decision; the route has not been changed or excluded.
- Automatic garage commands retain immutable origin identity; their initial audit,
  required notice and output checkpoint commit together. The existing reconciler
  recovers those outputs without provider I/O. The incoming store is validated but
  remains an inert checkpoint until the concrete messaging caller cutover.
- `evidence/p07-l39p6vis`:86 release/process/unit and16 release-job PostgreSQL
  cases passed. The dedicated database executor lock fences promotion and restore;
  complete candidate/preimage evidence is retained before publication. Actual
  archive restoration above is distinct from application deployment or DB restore.
- `evidence/p07-9stqptm1`: fresh/historical/roundtrip schema and Alembic checks
  through0007,17 populated/legacy/constraint compatibility cases, and actual
  pinned-client PostgreSQL dump/restore passed. Two disposable databases contained
  linked synthetic release records, uncertain commands and uncertain incoming
  replies. All row fingerprints, sequences and revisions matched; the source DB
  remained unchanged. Raw schema differences are retained: PostgreSQL reparses
  array casts into an equivalent representation. CHECK/predicate expressions are
  normalized by PostgreSQL temporary views; indexes are compared by structure,
  keys, operator classes, collation and predicate, not by a blanket text waiver.
  Earlier restore attempts `p07-wndr0j4k` (incomplete synthetic seed) and
  `p07-_fbw9yn_` (raw expression representation mismatch) remain recorded failures.
- Every completed resource-owning run in this checkpoint reports cleanup0 and
  production_unchanged=true. No live source modification, deployment, live data
  restore, notification or hardware operation was performed.

Still in progress: concrete visitor-conversation/messaging recovery cutover and
facade deletion; feedback worker and orphan-reservation integration; actual
recovery-hold routes/worker isolation; full combined locked validation and final
boundary/deletion review. Recovery hold is being implemented as an explicit
same-application deployment posture, not as permission to restore production.

### Recovery hold, feedback and visitor checkpoint

- `evidence/p07-fxv33h54`: actual recovery-hold routes/auth/schema isolation7,
  feedback19, orphan-reservation30 and channel-neutral visitor-conversation23
  PostgreSQL cases passed. Unit selection253/257 passed; four older Alfred
  hardware tests still invoked obsolete handler seams. Their replacements now
  exercise current Admin/requester approval and exact target bindings, but have
  not yet been executed. Access pipeline remains31/32; the known full-flow
  simulation defect is unchanged and has not been excluded.
- Resource cleanup succeeded and production metadata remained unchanged.
- The concrete WhatsApp composition, webhook-before-ack intake, fixed recipients,
  and common prepared notification delivery cutover are now being integrated.
  These edits are **not validated by the preceding frozen run**. Independent
  review identified destination/configuration drift and resumed-announcement
  maintenance checks; their fixes and new PostgreSQL tests await a coherent
  source capture. No completion claim or production release is implied.

### Confirmed notifications and concrete messaging integration

- Frozen source `notifications-whatsapp-frozen-_5i2tv8h` contains637 captured files,
  fingerprint `b4e0bc182192bc695a4fe204b57569c318ba112c94ed45cb123bd0568cac7257`.
  API announcements/mobile/rule tests and Alfred workflow tests now share prepared
  NotificationRun claims. Confirmation consumption, required audit and delivery
  intent commit together; current actor, target and configuration are checked at
  dispatch. Unsaved Alfred previews use stable content identity, while execution
  retains the original approval operation identity. Visitor custom/outreach sends
  use that owner and exact-pass output/history participants.
- `notification-wa-5bj9_jxi` could not run application tests: the previous backend
  test image was no longer present locally. Cleanup succeeded, production was
  unchanged and the captured source remained unchanged. This is blocked execution,
  not an application test result. The repository-pinned Python base was restored;
  `notification-wa-fw8f0nd3` verifies its Python3.12.14 version string and complete
  dependency package inventory match the retained exact-lock environment. Its
  distinct image identity and reason are retained in runtime-transition.json.
- `notification-wa-fw8f0nd3`: confirmed notifications40, incoming store22 and
  actual recovery hold7 PostgreSQL cases passed. Unit selection305/323, concrete
  WhatsApp inbox1/9 and neutral visitor conversation34/35 passed. Failures remain
  recorded: obsolete unit seams, inbox provider-ID test collisions, one implicit
  timestamp refresh in a test, and one outdated lifecycle cleanup expectation.
  These test corrections do not weaken intended outcomes and are undergoing the
  complete selected rerun in `notification-wa-wchwp8i6`.
- The WhatsApp facade is deleted after all current consumers were moved to their
  concrete owners. Incoming webhook acceptance is durable before acknowledgement;
  attempted replies remain reviewable without resending. The new administrative
  history projection reads canonical notification outcomes after interrupted
  metadata publication. The frontend incoming recovery panel is implemented,
  but its exact-lock tests/build remain pending.
- A server-created messaging authority binding now prevents queued Discord work
  inheriting a newly linked or changed actor. Its PostgreSQL tests are pending.
  Discord runtime cutover, actionable-result delivery consolidation, required
  local/CI suite parity and the final combined release gate remain unfinished.
- Every completed resource-owning run above reports cleanup0 and unchanged
  production metadata/source snapshot. No application deployment or live effect
  was performed. Original-source preservation must be rechecked at final handoff.

### Explicit user pause

Implementation is paused at the user's request. See [PAUSE_CHECKPOINT.md](PAUSE_CHECKPOINT.md)
for exact completed120 PostgreSQL cases, the corrected-but-unrerun unit fixture,
incomplete Discord/notification changes, new exact-lock dependency receipt and
resumption sequence. All agents and task-owned runtime resources have stopped.
Original488 captured files, branch, commit and full dirty status are unchanged.

### Resumed with Terra/Luna implementation and Astra review

The user authorized resumption using Terra Max for bounded domain implementation,
Luna Max for bounded tests, and Astra for integration and independent review.
`evidence/resume-source-verification.json` confirms the original488 source hashes,
original branch/commit/full dirty status and all646 paused-copy hashes still match.
The preceding explicit pause remains a historical checkpoint.

- Terra owns notification delivery certainty/current-rule validation and Discord
  cutover in separate exclusive files. Luna added four NR-02 PostgreSQL tests;
  they are statically parsed but not yet executed. Shared models, migrations,
  lifecycle registration and final validation remain lead-owned.
- `frontend-resume-fxqryzmb` and `frontend-resume-usrr0wxu`:208/209 frontend tests
  passed and the locked build passed. The remaining failure is a recovery-panel
  test query matching the processing status and timestamp label; the first
  correction selected the badge wrapper rather than its label and also failed.
  Both failures remain retained. The assertion now selects the existing badge
  label, without changing application behavior or weakening the expected status.
- Lead inspection found actionable gate follow-ups could offer Force Open after
  an uncertain response, label accepted-unverified commands as physically opened,
  and race one-use token consumption. Certainty projections and atomic token
  consumption are being corrected. Required result/audit/delivery consolidation
  is still unfinished; these edits are not a completed recovery boundary.
- All completed frontend runs used network-disabled disposable containers,
  retained exact source/lock/image identity, reported cleanup0 and unchanged
  production metadata. No live integration or hardware operation was performed.

- `frontend-resume-9tbkyrtn`: all209 frontend tests and the locked TypeScript/Vite
  build passed. Frozen source was unchanged, cleanup0, production unchanged.
  Host-only source-snapshot22 and harness-configuration19 checks also passed after
  resumption. No backend runtime result is implied by these frontend/host checks.

- `backend-focused-efjht2jc`: existing actionable notification11 unit cases passed
  after initial certainty/one-use edits (later partial/title refinements await
  rerun). `backend-focused-az6_3a2c`:96/97 architecture/access/Alfred guards passed;
  the ratchet exposed five new runtime cycle edges. Exact edges and inventory are
  retained in `architecture-inventory-2uf_2jps`; the baseline has not been raised.
- Lead removed inappropriate visitor caller dependencies: welcome reservation now
  belongs to concrete WhatsApp outbound delivery, not the LLM visitor adapter;
  Alfred no longer imports NotificationService merely to wake its poller; HA
  timeframe decisions call the neutral visitor owner directly. The WhatsApp
  adapter's obsolete welcome method is deleted. Pending validation will establish
  whether those three cycle edges are eliminated without behavior regressions.
  Discord incoming/outgoing composition is being separated to remove the other
  two edges, with app lifecycle wiring owned by the lead.
- Independent Astra review confirmed the actionable cutover must revoke durable
  dispatch authority before classifying an interrupted operation, and must never
  take context -> parent/child locks against the dispatch owner's opposite order.
  An already-committed attempting receipt remains uncertain even after revocation.
  The full output cutover remains pending; this review is static evidence only.

- `backend-focused-1bh4d9fm`:82 WhatsApp/actionable/architecture unit cases passed,
  including the dependency ratchet after caller/Discord boundary changes. This
  does not validate Discord runtime composition, which is still being finalized.
- `persistence-focused-0kx_p4at`: actionable8 and visitor-conversation35 PostgreSQL
  cases passed, including a real blocked-lock expiry check and moved welcome
  reservation behavior. `persistence-focused-riq5juu4`: confirmed notifications44
  and new dispatch-truth9 cases passed; existing notification recovery24/25 passed.
  Its failing crash-injection fake rejected the new `prepare_output` keyword before
  reaching the intended commit failure; the fake signature is corrected and awaits
  rerun. The assertion still requires one send and no retry after commit failure.
- Astra's frozen notification review identified missing cases despite those passes:
  filtered/truncated receipt labels could erase uncertainty; ordinary HA sends
  dropped the final config snapshot; confirmed failure/partial audit labels and
  aggregate failure reasons could misrepresent outcomes. Corrections and added
  tests are assigned back to Terra. Passing tests are not used to waive these
  review findings.
- Every completed run above cleaned up its owned resources and reported unchanged
  production metadata and frozen source. No production/live effect was performed.

- `backend-focused-jiwy6vea`:110 lifecycle, recovery-hold, messaging-bridge and
  notification workflow unit cases passed. Main now explicitly composes the
  Discord gateway with the neutral messaging bridge; reverse cleanup stops the
  bot producer before its incoming worker and before notification/database sinks.
  Concrete Discord callback draining still has separate implementation/tests in
  progress; these injected lifecycle tests do not establish that runtime result.

### Continued Terra/Luna implementation with independent Astra review

- `iacs-phase1-le79nzoq`: exact locks/identity/isolation, architecture guards,
  compilation, mypy, frontend209/build and Compose passed. Backend1149 passed,
  8 failed/135 setup errors/29 skipped; Ruff33 findings. Most setup errors came
  from missing nested pytest evidence parents. Harness now creates parents before
  execution and its19 configuration tests pass. Retained failures are not waived.
- `persistence-focused-p3nqzypq`: Discord inbox, real messaging confirmation4 and
  notification recovery25 passed; migration-up and cleanup passed. Astra found
  an untested real membership-refresh missing import; corrected with a concrete
  method regression. `backend-focused-kjivms5c`:46 Discord/lifecycle tests passed.
- `persistence-focused-30tk23r0`: notification dispatch truth14 and confirmed44
  passed, including the four prior independent-review blockers. Ownership passed
  to lead; Terra begins the separate actionable finalization/recovery cutover.
- `lint-fix-l3vraaoq`: pinned offline Ruff import/unused-import fixes applied only
  to13 root-owned files after checking their frozen input hashes. Its source
  changes are intentional, unlike read-only test snapshots; production unchanged.
- `backend-focused-k8hid14j`: lifecycle/hold passed after explicit producer,
  approval-drain and resource cleanup groups. `backend-focused-5ructz1t`:58 passed,
  one stale movement projection assertion failed (new admission fields omitted);
  assertion corrected without removing legacy fields. New paused-claim/shielded
  approval shutdown test passed in this run.
- Astra identified SDK-internal Discord webhook/channel retries after possible
  transmission, timed-button registration requirements, partial-batch truth and
  additional cancellation/late-claim hazards. These are active corrections, not
  completed validation. The transport is being replaced by a bounded single-send
  vendor adapter; no dependency changes. Gateway cancellation now drains owned
  checkpoint children, with isolated PG regression running separately.
- Every completed run listed above removed owned resources and retained unchanged
  production metadata. No deployment, migration against production, notification,
  hardware operation, commit or push was performed. Full combined release evidence
  remains outstanding; these passes do not establish release readiness.

- `persistence-focused-hbs5un2a`: Discord inbox15 passed with fresh sender denial
  and repeated-cancellation checkpoint coverage. `persistence-focused-swl3q2e_`:
  expanded Discord16 and WhatsApp9 passed, including blocked preclaim intake.
  A subsequent review strengthened the slow-checkpoint test to assert shutdown
  remains blocked before releasing it; that final assertion awaits rerun.
- `backend-focused-6gb68n4w`: focused lifecycle, movement contract and backend
  architecture guards passed. New ownership boundaries did not require raising
  the dependency-cycle/writer baseline.
- `continued-source-preservation-2zgdov56.json`: all488 original safe-source hashes,
  branch, commit and complete dirty working-tree status remain unchanged.

- UniFi follow-up: independent review found that late approval/query callers
  could reopen a stopped client and callbacks could escape the stop snapshot.
  The owner now fences lazy reopen/restart after stop, closes callback admission
  before task drain, and closes a client whose bootstrap fails or overlaps stop.
  `backend-focused-1md8x_xz` and `backend-focused-8g1j49si` retained four setup errors
  from an incorrectly named new test fixture constructor (31 existing tests passed
  each); corrected fixture uses the actual `UnifiProtectIntegrationService`.
  `backend-focused-jokt42w0`:35 UniFi/lifecycle tests passed. No live provider calls.

- `undefined-names-ebargyi0`: pinned Ruff F821 passed across all backend application
  source. This now runs in the repository harness/CI in addition to scoped checks;
  configuration19 tests passed after adding the gate.
- `backend-focused-35yg68bf`: initial Discord transport30 passed/3 failed, exposing
  the dropped final embed batch. Terra fixed the missing flush and retained full
  ordered-payload assertions; the adapter also preserves the SDK's documented bot
  User-Agent. `backend-focused-6vdzolfq`:33 transport/service cases passed. Independent
  review verified pinned aiohttp POST has no internal connection retry and timed
  view registration follows the installed SDK's actual connection-state path.
- `persistence-focused-8qb0058q`: Discord17 passed, including closed-intake handling
  of already claimed/prehandler work. `backend-focused-45msdqgb`:39 tests passed,
  one architecture guard failed on the then-active actionable direct access-device
  import. It was removed by routing manual previews through the existing gate
  coordinator/adapter boundary. `backend-focused-986qzgvy`:15 gate-preview,
  strengthened UniFi cleanup and architecture guard tests passed. The cycle
  baseline remains unchanged.

- `backend-focused-mxr7yq8s`: direct actionable-output composition in the existing
  movement reconciler passes the dependency guard and movement contract tests.
  Recovery uses its own bounded cursor; no generic callback registry was added.
  Full actionable persistence acceptance is pending the owner's current fixes.
- Independent actionable review found missing dispatch-lease eligibility,
  mobile configuration fencing, verified/not-sent already-open projection and a
  recipient-rebinding race during output claims. These are returned to Terra with
  concrete tests; the frozen implementation is not accepted yet.
- The Home Assistant caller logged full actionable bearer tokens on successful
  handling and exceptions. It now logs only an enumerated action kind and exception
  class. `backend-focused-1u5ibo_b`: notification workflow/lifecycle tests pass,
  including non-consuming wrong-action and pre-consumption exception log checks.
- Final Discord handoff: `discord-transport-handoff-4cy56h2v.json` records exact
  baseline/source/lock identities, changed files, contracts, deletions, tests and
  rollback restrictions. Independent Astra review accepted the final correction.

- Operator resolved the full-flow simulation decision: isolated tests only.
  Terra owns the bounded route/direct-call restriction and inert seven-scenario
  persistence repair. Existing failed simulation evidence remains recorded until
  the strengthened isolated suite passes. No production behavior was changed.
- Resumed host checks: source snapshot22 and harness configuration19 passed.
  Actionable review additionally found implicit audit-FK lock inversion and
  initial-button binding to a newer config than its actual delivery snapshot.
  Both are assigned to the actionable owner before acceptance.

- `iacs-phase1-ds5xnsbi`: all1315 backend tests passed (29 existing skips),
  frontend209/build, Ruff, undefined names, architecture, source/lock checks and
  Compose parsing passed. Three mypy errors exposed conditional callback names
  with differing closure signatures. The dispatcher now selects uniquely named,
  typed callbacks; `dispatcher-typecheck-ufwompu3` passed the corrected owner.
- `persistence-focused-c8sri5gw`: movement reconciliation fairness and notification
  dispatch truth passed, including a new actionable-scan failure case that still
  reconciles verified gate evidence. Scan failures retain their cursor and log
  only an error class; they do not block the independent gate recovery owner.
- Astra accepted the bounded UniFi/Discord cancellation lifecycle delta after
  reviewing held-close and durable-checkpoint tests. This is independent static
  review in addition to the previously recorded isolated runtime evidence.
- Full combined frozen validation started at `iacs-phase1-m_1a445_`; it predates
  the final simulator/actionable corrections and cannot establish their acceptance.

- Full inventory review found29 PostgreSQL-only gate journal tests were skipped
  in the DB-free suite but not selected by default persistence checks. They are
  now mandatory in the shared local/CI inventory, with a host assertion protecting
  that selection. Inventory checks continue to reject missing classified files.
- Combined run exposed three old coordinator fakes missing required command_context
  and one old assertion naming the superseded automation-only origin validator.
  Fixtures now implement the current boundary and assert its stable identity;
  the denial assertion uses the canonical notification owner's reason. All replay,
  single-call, failure and durable-row assertions remain intact. Rerun pending.
- `continued-source-preservation-10vasocl.json`: all488 original hashes, branch,
  commit and complete dirty status still match the preserved source baseline.

- `iacs-phase1-m_1a445_` completed with source integrity, cleanup and production
  preservation intact. Backend1315, frontend209, lint/type checks, schema/restore
  and deployment parsing passed. Persistence717/727 passed;10 failures belong to
  the known simulator, four stale fixtures/expectations, and five actionable tests
  captured during active implementation. Diagnostic24/50 passed;24 failures expose
  an outdated WhatsApp fake lacking the new preparation/authorization boundary,
  while two terminal-partial reconciliation expectations await independent review.
  This combined run is failed and does not establish release readiness.
- `gate-journal-persistence-isgn4wz_`: all29 isolated PostgreSQL gate journal cases
  passed, including concurrent claims, lost responses, lock ordering, maintenance,
  authorization refresh, exact-target receipt truth and recovery fairness.

- `persistence-focused-ftmef8hm`: corrected automation dispatch31 passed and
  diagnostic48/50 passed; the remaining two diagnostic expectations were reviewed
  independently against the canonical `partial_entry_verified_other_rejected`
  contract. Terminal verified+rejected targets now expect partial/no ongoing
  reconciliation, with added exact child receipts and forbidden-provider replay
  assertions. Accepted-but-unverified cases still require reconciliation.
- `persistence-focused-h2iacoaj`: access pipeline32, persistence12 and all50
  diagnostics passed. All seven isolated full-flow scenarios now include durable
  synthetic gate evidence and cleanup assertions; no real hardware calls occur.
  `backend-focused-vstf4bga`:12 simulator/guard tests passed, including HTTP410 and
  direct-call isolation proof failures before mutations.
- `backend-focused-dc3td1w9`: actionable/workflow/guard73 passed;
  `persistence-focused-mb2ahigg`: actionable25 and reconciliation fairness3 passed.
  Independent review nevertheless identified missing mixed already-open+accepted
  wording, a new-recipient duplicate during a held output lock, and stale DB time
  after a blocking recipient lock. These final corrections and tests are active;
  the previous pass is not final acceptance of that owner.
- `final-owner-lint-fwq28mwh`: expanded optional owner lint found21 issues across
  actionable/simulation (including pre-existing styles). Scoped import/unused and
  exception-boundary corrections are assigned without blanket suppression or
  unsafe autofix. Existing mandatory harness lint had passed its current scope.

- Simulator final cleanup delta: `persistence-focused-ski51ut4`32 access pipeline
  tests and `backend-focused-rq9_v6j3`12 simulator/guard tests passed. Astra accepted
  the run-scoped movement/session cleanup with same-source unrelated sentinels.
  The full-flow endpoint is HTTP410; the direct runner requires and revalidates
  the isolated test capability. Existing arrival/misread routes are unchanged.
- `architecture-inventory-321ykg9q`: current Python cyclic import edges10 versus
  captured121; frontend0 versus0. The remaining loop includes access-device
  status merging with Home Assistant's integration facade; it is retained, not
  claimed eliminated. The guard baseline is tightened to the measured10 edges
  and one isolated-simulator Presence constructor, so removed violations cannot
  return. No permitted edge/writer count was increased.
- `backend-focused-jmbk3d0s`: final actionable unit/workflow/guard73 passed.
  `persistence-focused-xb3xxe8p`:28 passed/2 new deadline-test setup failures.
  Independent review found the test mutated stored expiry after capturing its
  immutable bound context, so the callback correctly rejected it before the
  intended Person lock. The owner is correcting the fixture and adding the
  remaining canonical terminal-partial/not-sent wording case. No failed case is
  excluded or marked expected failure.

- Astra accepted the final actionable corrections in the `iacs-phase1-bym0sk5n`
  freeze: deadline tests reload the committed bound context; all canonical terminal
  partial combinations preserve delivery/physical truth and forbid force children.
  Its31 PostgreSQL actionable tests pass in the combined run. A final wording-only
  follow-up replaces “did not reach” with “were not confirmed in” for unknown
  physical states; its paired assertion changes with it. This follow-up will be
  included in the final exact-source validation before packaging.
- Mandatory Ruff coverage now also includes actionable notification ownership and
  the isolated simulator runner. Existing FastAPI route signature B008 findings
  from the optional broader router lint are retained as pre-existing diagnostics;
  the full required lint/type/import gates pass without blanket suppressions.

- Final accepted full run: `iacs-phase1-bhet89ul`; all required checks passed, owned resources cleaned, source integrity preserved and production metadata unchanged.
