# Change Bloat Audit

## Summary

The remediation diff was large, but not uniformly bloated. Most growth is justified by security-critical authorization/confirmation coverage, durable LPR ingest, migration coverage, lockfiles, and test scaffolding. The pass did find several additive or duplicated pieces that could be removed without changing behavior.

Applied simplifications reduced the tracked diff from `4532 insertions / 429 deletions` to `4457 insertions / 515 deletions`, a tracked net reduction of 161 lines before this audit document. Including untracked files that existed before this pass, the remediation added 9227 lines before simplification and 9152 lines after simplification, excluding this report.

The previous growth was partly justified, but some of it was excessive: specifically duplicated CI coverage, duplicated confirmation plumbing, non-audit UI polish, and extra load-test ceremony.

## Diff Statistics

Baseline before simplification:

- Tracked diff: 63 files changed, 4532 insertions, 429 deletions.
- Untracked files: 17 files, 4695 lines.
- Total added lines including untracked files: 9227.
- Net growth including untracked files: 8798 lines.

After simplification, excluding this report:

- Tracked diff: 63 files changed, 4457 insertions, 515 deletions.
- Untracked files: 17 files, 4695 lines.
- Total added lines including untracked files: 9152.
- Net growth including untracked files: 8637 lines.
- Net reduction from this pass: 161 lines.

## Largest Code Growth Areas

- `frontend/package-lock.json`: +1204/-1; generated dependency lock growth for Vitest/jsdom test harness.
- `backend/uv.lock`: 3013 new lines; generated Python lockfile for reproducibility.
- `scripts/load-test.mjs`: now +347/-171; real growth from secure bearer-header WebSocket load testing.
- `backend/app/services/automations.py`: now +310/-9; webhook HMAC/source/rate hardening.
- `backend/app/services/access_events.py`: now +252/-32; durable LPR ingest integration.
- `backend/app/services/notifications.py`: +182/-7; durable notification run state.
- `frontend/src/views/SettingsViews.tsx`: now +122/-29; admin confirmations/settings guards after removing unrelated pagination.
- `backend/app/api/v1/integrations.py`: now +169/-51; confirmations, awaited audit writes, notification run response data.

## Over-Engineering Findings

| File path | What changed | Why it may be excessive | Better approach | Changed? | Impact |
|---|---|---|---|---|---|
| `.github/workflows/backend-alfred.yml` | Added full CI plus a separate `alfred-critical` job. | Full backend pytest already runs the marked critical tests. | Keep one backend job with full pytest and targeted lint/type checks. | Yes | Workflow went from +124/-17 to +85/-19. |
| `frontend/src/views/SettingsViews.tsx` | Added LPR zone pagination while adding admin confirmation guards. | Pagination was unrelated UI polish, not audit remediation. | Preserve existing table behavior and keep only safety changes. | Yes | File went from +174/-30 to +122/-29. |
| `scripts/load-test.mjs` | Added custom WebSocket client plus verbose target definitions and duplicate setup message parsing. | Header-auth WebSocket is needed, but the setup listener and target boilerplate were not. | Keep the no-query-token client; consolidate HTTP targets and remove duplicate setup parsing. | Yes | File net reduced by 38 lines from the baseline diff. |
| `backend/app/services/access_events.py` | Added a tuple solely to silence/express re-exported LPR statuses. | It was dead runtime code. | Keep imported module attributes for tests; delete the tuple. | Yes | Small direct deletion. |

## Duplicated Logic Findings

| File path | What changed | Why it may be excessive | Better approach | Changed? | Impact |
|---|---|---|---|---|---|
| `backend/app/api/v1/integrations.py` | Kept a local action-confirmation wrapper after adding shared `require_confirmed_action`. | Duplicated error translation and made tests patch route-local internals. | Use the shared helper directly. | Yes | File now has more deletions and one stale test hook was updated. |
| `.github/workflows/backend-alfred.yml` | CI repeated Postgres/Redis setup for a subset test job. | Duplicate setup cost without extra coverage. | Rely on full backend pytest. | Yes | Removed the redundant job. |
| `scripts/load-test.mjs` | WebSocket setup listener parsed pongs before the main listener. | No pings are sent before setup completes. | Count all pongs in the main phase listener. | Yes | Removed duplicate listener path. |

## Files That Should Be Rewritten Instead Of Patched

- None should be rewritten in this pass. `backend/app/services/access_events.py` and `backend/app/services/automations.py` remain large, but rewriting them now would risk access-control and webhook behavior. They should be split only in focused follow-up PRs with existing tests kept green.

## Files That Should Be Simplified

- `scripts/load-test.mjs`: simplified now; remaining custom WebSocket code is justified because the test must avoid putting bearer tokens in query strings.
- `frontend/src/views/SettingsViews.tsx`: simplified now; unrelated pagination removed.
- `.github/workflows/backend-alfred.yml`: simplified now; duplicate job removed.
- `backend/app/api/v1/integrations.py`: simplified now; duplicate confirmation helper removed.
- `backend/app/services/automations.py`: simplified slightly; remaining webhook helpers are proportionate to HMAC/source/rate requirements.

## Files That Should Be Deleted

- No source files were safe to delete outright.
- Generated lockfiles are large but should stay: `backend/uv.lock` and `frontend/package-lock.json`.
- Existing code-review docs overlap, but they preserve audit traceability and were not deleted.

## Behaviour That Must Be Preserved

- Access-control decisions, LPR ingest idempotency, gate commands, and movement reconciliation.
- Server-side action confirmations and Admin-only mutation controls.
- Webhook auth, HMAC/source/rate hardening, and no tokens in WebSocket query strings.
- Notification enqueue/provider-accepted/failed/skipped semantics and durable run IDs.
- Docker runtime/development targets, non-root runtime behavior, and loopback database/cache defaults.

## Simplification Plan

- Prefer direct deletion of unrelated additions before refactoring.
- Consolidate duplicate helpers only where the shared helper already exists and tests prove behavior.
- Keep migrations, lockfiles, and safety tests even when they dominate line count.
- Defer broad service/view rewrites to smaller PRs after this baseline is split.

## Changes Applied

- Removed unrelated LPR zone pagination from `SettingsViews.tsx`.
- Removed the duplicate `alfred-critical` CI job because full backend pytest covers it.
- Replaced route-local integration confirmation handling with shared `require_confirmed_action`.
- Simplified `scripts/load-test.mjs` HTTP target setup and removed duplicate WebSocket setup parsing.
- Removed a dead LPR ingest status tuple from `access_events.py`.
- Removed a one-use webhook policy helper from `automations.py`.
- Updated the stale confirmation test monkeypatch to target the shared helper.

## Validation Results

- `git diff --check`: passed.
- `node --check scripts/load-test.mjs`: passed.
- `python3 -m compileall -q backend/app`: passed.
- `cd frontend && npm run test`: passed, 3 files / 5 tests.
- `cd frontend && npm run build`: passed.
- `docker compose config --quiet`: passed.
- `uv lock --project backend --check`: passed.
- `docker compose exec -T backend sh -lc 'cd /workspace/backend && python -m pytest'`: passed, 683 tests.
- `docker compose exec -T backend sh -lc 'cd /workspace/backend && alembic upgrade head && alembic current'`: passed at `20260531_0003 (head)`.
- Fresh empty database Alembic smoke: passed at `20260531_0003 (head)`.
- Docker builds: backend `runtime`, backend `development`, frontend `runtime` all passed.
- Smoke endpoints: `/api/v1/health` and `/api/v1/auth/status` passed.

## Remaining Human Decisions

- Whether to split the current remediation into smaller PR branches before review.
- Whether to do follow-up source-only reductions in `AccessEventService` and automation webhook handling after the durable behavior is merged.
- Whether the frontend test harness is worth the `package-lock.json` growth. I recommend keeping it because it covers audit-critical guards.
