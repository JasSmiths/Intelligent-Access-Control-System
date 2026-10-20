"""Mandatory accumulated-history budgets; timing is evidence, never a CI gate."""
from test_recovery_boundaries import isolated_resources as isolated_resources
from test_access_window_persistence import window_resources as window_resources
from test_idle_background_work import statements, configure_inert_worker

import json
from pathlib import Path
import runpy
import time
import tracemalloc
from unittest.mock import AsyncMock

import asyncpg
import pytest
from sqlalchemy import text

from app.db.session import AsyncSessionLocal, engine
from app.services import access_events, automations
from scripts.benchmarks.evidence import write_evidence

pytestmark = pytest.mark.asyncio
OLD_DISCOVERY = """SELECT r.id FROM automation_runs r
 WHERE r.recovery_version=1 AND r.status NOT IN ('queued','processing')
 AND NOT EXISTS (SELECT a.id FROM audit_logs a WHERE a.category='automation_engine'
 AND a.action IN ('automation_rule.success','automation_rule.skipped','automation_rule.failed','automation_rule.review_required')
 AND a.metadata->>'run_id'=r.id::varchar)
 ORDER BY r.finished_at,r.id LIMIT 20"""


def nodes(plan):
    yield plan
    for child in plan.get("Plans", []):
        yield from nodes(child)


async def measured(call):
    samples, results = [], []
    tracemalloc.start()
    try:
        with statements() as queries:
            for _ in range(3):
                started = time.perf_counter()
                results.append(await call())
                samples.append((time.perf_counter()-started)*1000)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {"milliseconds":samples, "python_peak_bytes":peak, "queries":len(queries),
        "updates":sum(sql.lstrip().upper().startswith("UPDATE") for sql, _ in queries)}, results, queries


def retain(tmp_path, key, measurements):
    # This exact suite-level path is included in CI's evidence allowlist.
    path = tmp_path.parents[1] / "query-efficiency.json"
    previous = json.loads(path.read_text()).get("measurements", {}) if path.exists() else {}
    previous[key] = measurements
    write_evidence(path, {"measurements":previous, "limits":
        "Synthetic single-CPU PostgreSQL; Python allocation peaks exclude server memory. "
        "Old-query timeouts are censored observations, not measured completion times."})


async def prepared_plans(sql, parameters):
    async with engine.connect() as connection:
        raw = await connection.get_raw_connection()
        client = raw.driver_connection
        # Values are captured from the real worker statement, and restricted to
        # this fixture's known scalar arguments; never interpolate arbitrary input.
        assert all(value in ("ready", "open", .5, 1, 20) for value in parameters)
        arguments = ",".join("'" + value + "'" if isinstance(value, str) else str(value) for value in parameters)
        await client.execute("PREPARE idle_budget AS " + sql)
        plans = {}
        try:
            for mode in ("force_custom_plan", "force_generic_plan"):
                await client.execute("SET plan_cache_mode=" + mode)
                plans[mode] = (await client.fetchval(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) EXECUTE idle_budget(" + arguments + ")"))[0]
        finally:
            await client.execute("DEALLOCATE idle_budget")
            await client.execute("RESET plan_cache_mode")
        return plans


async def old_discovery_evidence(expected):
    async with engine.connect() as connection:
        raw = await connection.get_raw_connection()
        client = raw.driver_connection
        await client.execute("SET statement_timeout='750ms'")
        records = []
        try:
            tracemalloc.start()
            try:
                for _ in range(3):
                    started = time.perf_counter()
                    try:
                        rows = await client.fetch(OLD_DISCOVERY)
                        assert [row["id"] for row in rows] == expected
                        records.append({"milliseconds":(time.perf_counter()-started)*1000, "equivalent":True})
                    except asyncpg.QueryCanceledError:
                        records.append({"statement_timeout_ms":750, "timed_out":True})
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            try:
                plan = (await client.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + OLD_DISCOVERY))[0]
            except asyncpg.QueryCanceledError:
                plan = {"statement_timeout_ms":750, "timed_out":True}
        finally:
            await client.execute("RESET statement_timeout")
        return {"samples":records,"plan":plan,"sql":OLD_DISCOVERY,"queries":3,"python_peak_bytes":peak}


async def backfill_plan(size):
    migration = Path(__file__).resolve().parents[2] / "backend/alembic/versions/20261019_0019_idle_background_work.py"
    sql = runpy.run_path(str(migration))["BACKFILL_PENDING_AUDITS"]
    async with engine.connect() as connection:
        raw = await connection.get_raw_connection()
        client = raw.driver_connection
        transaction = client.transaction()
        await transaction.start()
        try:
            # A resource bound, not a latency target; examined work is the CI gate.
            await client.execute("SET LOCAL statement_timeout='30s'")
            plan = (await client.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql))[0]
            audits = [row for row in nodes(plan["Plan"]) if row.get("Relation Name") == "audit_logs"]
            assert len(audits) == 1
            assert audits[0]["Actual Loops"] <= 1
            assert audits[0]["Actual Rows"] <= size*4
            assert await client.fetchval("SELECT count(*) FROM automation_runs WHERE completion_audit_pending IS TRUE") == 0
            return {"sql":sql,"plan":plan,"receipt_scan_passes":audits[0]["Actual Loops"]}
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("size", [1000, 100000])
async def test_audit_discovery_cost_is_independent_of_accounted_history(size, monkeypatch, tmp_path):
    async with AsyncSessionLocal() as session:
        await session.execute(text("""INSERT INTO automation_runs
            (id,trigger_key,status,started_at,finished_at,trigger_payload,context,condition_results,
             action_results,actor,source,recovery_version,completion_audit_pending)
            SELECT md5('run-'||g)::uuid,'synthetic.idle','success',now(),
             now()+g*interval '1 millisecond','{}','{}','[]','[]','Synthetic','synthetic',1,false
            FROM generate_series(1,:size) g"""), {"size":size})
        await session.execute(text("""INSERT INTO audit_logs(id,category,action,actor,outcome,level,metadata)
            SELECT md5('audit-'||g||'-'||n)::uuid,'automation_engine',
             (ARRAY['automation_rule.success','automation_rule.skipped','automation_rule.failed','automation_rule.review_required'])[n],
             'Synthetic','success','info',jsonb_build_object('run_id',md5('run-'||g)::uuid::text)
            FROM generate_series(1,:size) g CROSS JOIN generate_series(1,4) n"""), {"size":size})
        await session.execute(text("ANALYZE automation_runs"))
        await session.execute(text("ANALYZE audit_logs"))
        await session.commit()
    migration = await backfill_plan(size)
    retain(tmp_path, f"backfill-{size}", migration)
    service, captured = automations.AutomationService(), []
    async def account(identity):
        captured.append(identity)  # Discovery cost only; real accounting tested separately.
    monkeypatch.setattr(service, "account_completed_run", account)
    for pending in (0, 25):
        async with AsyncSessionLocal() as session:
            expected = list((await session.scalars(text("""SELECT id FROM automation_runs
                ORDER BY finished_at,id LIMIT :limit"""), {"limit":min(pending,20)})).all())
            if pending:
                await session.execute(text("""UPDATE automation_runs SET completion_audit_pending=true
                    WHERE id IN (SELECT id FROM automation_runs ORDER BY finished_at,id LIMIT 25)"""))
                await session.execute(text("""DELETE FROM audit_logs WHERE metadata->>'run_id' IN
                    (SELECT id::text FROM automation_runs WHERE completion_audit_pending IS TRUE)"""))
                await session.commit()
        await service.recover_completion_audits()  # Exclude first-use ORM compilation from steady-state samples.
        captured.clear()
        measurement, _, queries = await measured(service.recover_completion_audits)
        assert captured == expected*3
        assert measurement["queries"] == 3 and measurement["updates"] == 0
        assert all("audit_logs" not in sql for sql, _ in queries)
        plans = await prepared_plans(*queries[0])
        for plan in plans.values():
            tree = list(nodes(plan["Plan"]))
            assert any(row.get("Index Name") == "ix_automation_run_pending_audit" for row in tree)
            assert all(row.get("Relation Name") != "audit_logs" for row in tree)
            assert all(row.get("Actual Rows",0) <= 20 for row in tree)
            assert sum(row.get("Rows Removed by Filter",0) for row in tree) == 0
        baseline = await old_discovery_evidence(expected)
        retain(tmp_path, f"automation-{size}-{pending}", {"runs":size,"audits_initial":size*4,
            "pending":pending,"expected_ids":[str(row) for row in expected],
            "candidate":measurement,"sql":queries[0][0],"plans":plans,"baseline":baseline})


@pytest.mark.parametrize("size", [1000, 100000])
async def test_empty_lpr_worker_cost_is_independent_of_handled_history(size, monkeypatch, tmp_path):
    configure_inert_worker(monkeypatch)
    service = access_events.AccessEventService()
    coordinator = await service._pipeline.acquire_coordinator()
    async with AsyncSessionLocal() as session:
        await session.execute(text("""INSERT INTO lpr_ingest_events
            (id,idempotency_key,source,registration_number,captured_at,received_at,observation_state,
             ready_ordinal,normalized_payload,status,attempt_count)
            SELECT md5('ingest-'||g)::uuid,'synthetic-'||g,'synthetic','AB12CDE',now(),now(),'ready',g,
             jsonb_build_object('unused',repeat('x',4096)),'succeeded',1 FROM generate_series(1,:size) g"""), {"size":size})
        await session.execute(text("UPDATE lpr_coordinator_state SET handled_ready_ordinal=:size"), {"size":size})
        await session.execute(text("""INSERT INTO lpr_debounce_windows
            (id,open_ordinal,source,state,first_seen,updated_at,min_received_at,member_count,ranking_context)
            SELECT md5('window-'||g)::uuid,g,'synthetic','completed',now(),now(),now(),0,'{}'
            FROM generate_series(1,:size) g"""), {"size":size})
        await session.execute(text("ANALYZE lpr_ingest_events"))
        await session.execute(text("ANALYZE lpr_debounce_windows"))
        await session.commit()
    async def finish_empty_probe(generation, pending, *, fallback_seconds):
        assert not pending.ready
        service._stop_event.set()
    monkeypatch.setattr(service._wake, "wait", finish_empty_probe)
    async def boundary():
        service._stop_event.clear()
        service._last_probe_generation = service._wake.generation
        await service._wait_until_ready(owned=True)
        assert service._stop_event.is_set()
        return False
    assert await boundary() is False
    candidate, results, queries = await measured(boundary)
    assert results == [False]*3 and candidate["queries"] == 3 and candidate["updates"] == 0
    assert all("FOR UPDATE" not in sql and "normalized_payload" not in sql for sql, _ in queries)
    plans = await prepared_plans(*queries[0])
    retain(tmp_path, f"lpr-{size}", {"handled_ingests":size,"completed_windows":size,
        "candidate":candidate,"plans":plans,"equivalent_results":results})
    for plan in plans.values():
        tree = list(nodes(plan["Plan"]))
        ingests = [row for row in tree if row.get("Relation Name") == "lpr_ingest_events"]
        assert len(ingests) == 2  # Ready receipt seek and abandoned-capture deadline seek.
        assert all(row["Node Type"] in {"Index Scan", "Index Only Scan"} for row in ingests)
        assert any("ready_ordinal >" in row.get("Index Cond", "") for row in ingests)
        assert any(row.get("Index Name") == "ix_lpr_window_quiet_due" for row in tree)
        assert any(row.get("Index Name") == "ix_lpr_window_max_due" for row in tree)
        assert all(row.get("Actual Rows",0) <= 1 for row in tree if row.get("Relation Name"))
        assert all(row.get("Actual Rows",0) <= 5 for row in tree if row["Node Type"] == "Values Scan")
        assert sum(row.get("Rows Removed by Filter",0) for row in tree) <= 1
    # Compare the current fenced processing path with its readiness gate bypassed.
    # The separate actual-8259 benchmark owns historical before/after attribution.
    with monkeypatch.context() as patch:
        patch.setattr(service._pipeline, "has_pending_work", AsyncMock(return_value=True))
        service._stop_event.clear()
        baseline, before, _ = await measured(lambda: service._process_boundary(coordinator))
    assert before == results and baseline["updates"] >= 6
    retain(tmp_path, f"lpr-{size}", {"handled_ingests":size,"completed_windows":size,"candidate":candidate,
        "baseline":baseline,"sql":queries[0][0],"plans":plans,"equivalent_results":results})
