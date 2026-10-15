# IACS query efficiency and CI artifact cleanup

> Publication record: the implementation handover below was frozen before the owner authorized commit/push. See the publication follow-up at the end for the subsequent hosted result and correction.

The three focused changes are complete. The Admin user list returns the same fields and photo URLs, while ordinary lists stop loading photo bodies and both modes stop loading password hashes. Notification presence conditions return the same answers using narrow Boolean queries instead of loading every Presence object. CI now stages named reports, logs and JUnit results, leaving synthetic keys, input media, raw databases and restore dumps out of the upload.

For a synthetic 300-user list with 150 photos of 128 KiB each, median traced Python peak allocations fell from 21,512,168 to 1,597,707 bytes. Repeating nine conditions over 4,000 presence rows materializes zero ORM records instead of 36,000. Query counts remain unchanged. Small user-list timings were slightly slower, and explicit photo mode retains its expected photo cost. These are measured local synthetic results, not production speed guarantees or database wire-byte measurements.

Final full isolated validation passed: **2,830 unique pytest passes, 35 skips with exact passing persistence counterparts**, plus the host, frontend, types/lint, architecture, contracts, migrations/schema and restore gates. All 11 collector tests passed in the locked environment. One genuine independent reviewer was explicitly requested as `gpt-6-astra`/Medium. It found and then accepted correction of C's success classification; no material findings remain. Effective model/effort metadata is not exposed.

**Final estimated completion: 100%.** All changes remain local and uncommitted. No production deployment, migration, container restart, live provider/hardware call, push or remote CI run was performed. The later hosted artifact upload must still be inspected after the owner separately pushes. No browser rerun was required for these backend query and retention changes; `actionlint` was unavailable, so YAML parsing and workflow invariants were checked locally instead.

## Source identity and scope

- Checkout: `/Users/jas/Documents/Intelligent Access System`; branch `main`; starting and final HEAD `2a3a572fe10755c959feaed74790b13f65015a6f`.
- Starting safe source fingerprint: `f7d66850e18e8e05f600be877a21e2b4bcb4b6e83e3502f1daae6d0a8066ac04` (832 files).
- Final tested fingerprint: `5ccb992e0228041283a46d6aae7c77fedf5549c71be5d83e8a9d9e388ae37890` (835 files).
- Starting edits: only the earlier untracked `IACS_SECURITY_RELIABILITY_REVIEW_BUNDLE.zip`; it is preserved and excluded from this task's patch/package.
- 13 task source files: 10 modified, 3 new, 0 deleted. No generated contracts, dependencies, schema, frontend or unrelated remediation were changed.
- Handover/evidence documents are delivery files written after final code validation and intentionally excluded from source fingerprints. The ZIP is a local delivery artifact, not part of its own patch.
- Source statistics: `{"docs": {"deletions": 0, "files": 2, "insertions": 27}, "harness": {"deletions": 3, "files": 4, "insertions": 204}, "production": {"deletions": 5, "files": 2, "insertions": 30}, "tests": {"deletions": 3, "files": 4, "insertions": 474}, "workflow": {"deletions": 7, "files": 1, "insertions": 8}}`. Full file hashes and classifications are in `task-source-changes-final.json` and the evidence JSON.

## A — compact Admin user lists

Owner: `backend/app/api/v1/users.py::list_users`. Scoped `defer(..., raiseload=True)` excludes password hashes in both modes. Compact mode also defers the body and projects the established `photo_reference_expression`; explicit photo mode loads bodies in one query. `services/auth.py::serialize_user`, model-wide defaults and the explicit photo handler remain unchanged. First/last-name fallback, linked person ID, preferences, roles, timestamps, ordering and null/empty/external photo references are preserved. Existing account/realtime/V01/V02/calendar retirement code is untouched.

The real ASGI endpoint uses real PostgreSQL and actual bearer/Admin authorization. Tests compare every response field with the starting query/serializer, verify canonical photo URLs and explicit photo retrieval (including existing JPEG normalization), inspect SQL and loaded attributes, and exercise fail-fast deferred access without lazy queries. Standard/unauthenticated/inactive controls remain denied. The impossible authenticated-empty-directory edge overrides only the Admin dependency and still executes the real endpoint/storage query. Both baseline list cases fail for unnecessary loading; final cases pass.

## B — Boolean presence reads

Owner: `backend/app/services/notification_planning.py::NotificationPlanner._condition_matches`. `no_one_home` and `someone_home` use `EXISTS` over PRESENT state; `person_home` adds the existing person primary key. UUID parsing occurs before SQL. Exact canonical string membership is preserved: UUID objects stringify successfully, but uppercase, braces, hyphenless or whitespace forms are not newly accepted. Missing/malformed IDs and unknown modes remain false. Inactive people are not newly filtered; EXITED/UNKNOWN rows do not count as present. Sessions, condition order and fresh observations between planning calls are unchanged; no cache/index/migration was added.

Six synthetic datasets exercise all supported modes, missing/invalid/noncanonical IDs, inactive people and unknown presence. A normal inert notification plan skips, becomes pending after presence changes, then skips after another change; repeated rules retain their answers. The baseline truth answers already pass but six efficiency cases fail on ORM/scalar shape. All final tests pass. Existing primary-key plans serve selected-person checks. The broad PRESENT existence plan still inspects 4,000 rows when the only present row is last: scalar output does not imply constant-time database scanning.

## Measurements

Same fixtures, final test bytes and isolated backend/dependency images; one warm-up plus five sequential samples, fresh sessions, no providers. User workloads: 8/300 records, half with 128-KiB data URLs, both photo modes. Presence: 8/4,000 records, nine repeated conditions per sample. Full output digests/bytes, query counts, loaded fields/rows, all samples, indexes and EXPLAIN ANALYZE/BUFFERS plans are in [measurements.json](/private/tmp/iacs-query-ci-20261015/measurements.json). All four user outputs are byte-equivalent; user query counts are 1→1 and presence 9→9.

| Workload | Median traced Python peak bytes before → after | p50 ms before → after | nearest-rank p95 ms before → after |
| --- | ---: | ---: | ---: |
| users-8-photo-False | 1,204,239 → 319,289 | 1.77 → 2.33 | 1.85 → 2.39 |
| users-300-photo-False | 21,512,168 → 1,597,707 | 33.61 → 29.64 | 35.99 → 60.45 |
| users-8-photo-True | 1,882,414 → 1,882,892 | 2.32 → 2.55 | 2.64 → 2.64 |
| users-300-photo-True | 60,809,600 → 60,782,728 | 57.89 → 60.34 | 61.04 → 62.33 |
| presence-8 | 285,766 → 287,758 | 4.84 → 4.11 | 4.86 → 4.33 |
| presence-4000 | 6,840,638 → 288,004 | 1074.93 → 4.79 | 1143.52 → 4.86 |

SQL counts use SQLAlchemy before_cursor_execute (excluding driver transaction-control calls). Response bytes/digests use sorted json.dumps of returned UserResponse models, not complete HTTP wire bytes; endpoint tests separately compare decoded HTTP responses and exact media bytes. EXPLAIN uses literal equivalents of captured presence predicates. Allocation measurements use `tracemalloc` and include SQL/ORM observation overhead; user timing also includes JSON encoding. They are not total process RSS or database wire bytes. Five-sample p95 is simply the maximum; timings are noisy. Plans use freshly populated synthetic tables and do not predict production load.

## C — curated CI retention

Owners: `scripts/validation/ci_evidence.py`, `test_ci_evidence.py` and `.github/workflows/project-checks.yml`. The workflow runs this exact CLI with `if: always()`, then uploads only its new staging directory. Jobs, permissions, triggers, check gates and 14-day retention stay unchanged. Staging uses the shared lock/reserve/budget guard, checks safe relative paths, refuses symlinks and existing output, and copies only selected files.

The allowlist keeps named root result/check/source/dependency/storage/cleanup reports and known command logs; JUnit plus query measurements directly under named suites; named schema reports/revision logs; four restore result/schema summaries; and explicitly named runtime proof/PDF. It never recursively matches scratch extensions. Raw PostgreSQL state/dumps, keys, private env/tokens, arbitrary JSON/log files, input images, caches and dependencies are excluded. The current workflow produces no browser artifacts; existing separately scoped browser evidence is unaffected. `collection.json` retains member hashes/bytes, run status, missing proof and collection errors. A claimed pass missing required proof fails collection; failed/interrupted runs retain partial reports without changing earlier validation failures into success.

Real final output comparison: **4,525 files / 45,500,167 bytes → 448 files / 17,706,042 bytes**, with zero collection errors. This includes the staging manifest; category/exclusion details and exact command are in [curation-comparison.json](/private/tmp/iacs-query-ci-20261015/curation-comparison.json). The unchanged restore rehearsal created/restored its synthetic archive and passed all equality assertions. Its useful summaries/logs remain; the raw archive is excluded. After proof/selection capture, only this task’s checksum-proved synthetic rehearsal archives were removed; prior evidence and production rollback points were preserved (synthetic-archive-cleanup.json). Retention comparison also exercises two previously completed runtime-proof trees (selection only; no new runtime smoke or image build); category-level counts separate them from this task’s current validation output. No remote artifact was uploaded or removed.

## Validation and retained failures

Bundled host Python 3.12: `/Users/jas/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3` (system Python 3.9 was not used for harness execution). Full command:

```sh
IACS_VALIDATION_BACKEND_IMAGE=iacs-backend:uiprotect-23-backfill-verified-20261013 /Users/jas/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/validation/validate.py --reuse-dependencies /private/tmp/iacs-integration-mutations-20261014/iacs-validation-focus-rstp6hsg --evidence-root /private/tmp/iacs-query-ci-20261015/verified
```

| Check | Result | Evidence relative to `/private/tmp/iacs-query-ci-20261015` |
| --- | --- | --- |
| Equivalent starting-code regressions/measurements | exit 1, 8 expected efficiency failures / 8 passes | `iacs-validation-focus-tc_y76hi`, `baseline-equivalent-console.log` |
| Focused optimized PostgreSQL suite | exit 0, 16 passes | `iacs-validation-focus-8vhnj_6r`, `focused-corrected-console.log` |
| Final full harness, including stronger final UUID cases | exit 0; all required gates pass | `verified/iacs-validation-efvudopm`, `verified-console.log` |
| Source/harness/storage/collector checks | exit 0 in full run; 22/31/17/11 tests | final `*-tests.log` |
| Real workflow CLI curation | exit 0, named proof complete | `curation-console.log`, `curated/collection.json` |
| Types/lint, architecture, generated parity, frontend build/unit, migrations/schema and database restore | all exit 0 | final `checks.json`, `results.json`, named logs and curated summaries |
| Source integrity / owned cleanup / production comparison | 0 / 0 / unchanged | final manifests and cleanup/production records |

Commands, immutable images/dependency identities and all individual exit codes are retained in final `manifest.json`, `images.json`, `dependencies.json` and `checks.json`; JUnit inventory and exact skip counterparts are in `test-inventory.json`. No useful tests were deleted. The old implementation-coupled broad-upload assertion was replaced with the curated retention contract.

Earlier setup/expectation failures remain recorded: initial presence fixture constructed RuntimeConfig incorrectly; initial photo expectation assumed PNG instead of existing JPEG normalization; one old-source focused profile accidentally attempted the new absent host gate; one host CLI test correctly hit the shared run lock; initial review-fix fixtures used a non-object check entry. A delivery-helper receipt typo also stopped the first packaging attempt after successful inert patch verification; it was corrected and packaging alone was rerun. These are not behavior reproductions or final passes. Two integrated attempts were intentionally canceled: the first for C-R1, the second to retain the runtime wrapper build log. Both drained owned work safely. The first (schema command exit 124/KeyboardInterrupt); cleanup/integrity were 0 and production unchanged. Host PyYAML absence caused one host-only skip, closed by all 11 passing collector tests in locked full validation.

## Independent review and remaining limits

Requested implementer: GPT-6.1 Sol / Extra High; effective session metadata unavailable. Explicit genuine delegation: `gpt-6-astra`, effort `medium`, reviewer `/root/query_ci_review`, one reviewer at a time, read-only. Effective model/effort metadata for implementer/reviewer is unavailable. Initial snapshot `88a9807f…9753` review found P2 C-R1: successful validation's Boolean preservation field bypassed completeness checking, and provisional smoke success could override final runtime failure. Correction follows each producer's final contract, adds realistic result/failure tests and requires attempted-command logs. Targeted follow-up reviewed `5ccb992e0228041283a46d6aae7c77fedf5549c71be5d83e8a9d9e388ae37890`; source and final evidence closure are recorded in `review-final.json`. No material findings remain; Sol's self-review is not substituted for that review.

No application-wide certification is claimed. Focused review covered the 13 changed source files and relevant serialization/auth, presence/model, harness/storage/schema/restore and workflow callers. Unrelated providers, hardware, browser flows and the retired calendar feature were not reopened. No actionlint binary or hosted upload was available/executed; later actual upload contents remain an owner verification step. Token usage is not exposed. Disk guard records and cleanup receipts show bounded reuse and owned database cleanup; no broad pruning or repeated dependency installation occurred.

Portable package mapping: current reports are under `evidence/curated/validation/iacs-validation-efvudopm/`, console logs under `evidence/console/`, prior attempt metadata under `evidence/prior/`, and the task patch/manifests/review/measurements under `evidence/`. The earlier absolute/local paths refer to this host; these relative package paths remain usable elsewhere.

## Next independent verification

1. Inspect `task.patch`, `task-source-changes-final.json` and baseline/final hashes; apply the included patch to the inert `baseline/` context. Package verification already reproduced all changed final bytes without touching Git.
2. Review `users.py::list_users` with `auth.serialize_user`, compact photo references and the real endpoint/media/loaded-column tests.
3. Review planner presence predicates and the truth matrix, repeated planning calls, actual indexes and EXPLAIN plans.
4. Run `test_ci_evidence.py`; inspect real `curated/collection.json` and missing-proof/symlink/failed-runtime fixtures. After a separately authorized push, inspect hosted artifact contents and job status, not just the YAML path.
5. Confirm the source fingerprint, all 35 exact passing skip counterparts, schema/restore summaries and cleanup/production records. Production remains unchanged; deployment/migration is a separate task.

## Authorized publication follow-up — 15 October 2026

The owner subsequently authorized commit, push and monitoring. Commit `726fa44318deae19cdacaace93eb6c385017372b` was pushed to `main`. [Hosted run 38744800237](https://github.com/JasSmiths/Intelligent-Access-Control-System/actions/runs/38744800237) passed the complete isolated harness (2,830 unique pytest passes and 35 skips with exact passing counterparts), runtime PDF smoke, image builds and dependency audits. Its final evidence collection failed because fresh dependency preparation produced five attempted-command logs omitted from the finite allowlist. The failure artifact retained 432 verified report hashes and an actionable missing-file list; tests themselves did not fail.

The correction adds only the exact top-level `python-dependencies`, `frontend-dependencies`, `pull-node`, `pull-pgvector-pgvector` and `pull-redis` logs. New fresh/reused preparation regressions reproduce the prior failure and prove unrelated/nested log names remain excluded. Before: one expected failure; after: 12 passing collector tests plus one host PyYAML skip. Source selection (22), harness configuration (31) and storage (17) tests pass. An additional locked local check was blocked before container creation because its recorded image is no longer cached; no download or duplicate environment was created. A targeted follow-up with the same requested Astra/Medium reviewer found no material issue; effective metadata remains unavailable.

Corrected source fingerprint: `b9ba04c581fd4d1eb9b998a12b9891f5287956478d2543505116cdfaef27e025`. The hosted rerun is pending at this correction commit; it will also exercise the PyYAML case in its locked environment. Publication monitoring and actual archive verification are recorded separately in `/private/tmp/iacs-query-ci-20261015/publication/publication.json` and `artifact-verification.json` (the latter is created only after successful verification). The portable bundle receives these publication receipts after completion. Original evidence above remains attributed to its original fingerprint rather than relabelled as a test of new bytes. Production was not deployed, migrated or restarted.
