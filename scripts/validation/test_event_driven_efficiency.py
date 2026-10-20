"""Mandatory actual-loop idle budgets and indexed small/target readiness plans."""
from test_recovery_boundaries import isolated_resources as isolated_resources
from test_idle_background_work import statements
from test_idle_query_efficiency import measured, nodes, retain

import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sys

import pytest
from sqlalchemy import text

from app.db.session import AsyncSessionLocal, engine
from app.services.access.completion_store import AccessCompletionStore
from app.services.access.publication import AccessPublicationStore
from app.services.access.pipeline_store import AccessPipelineStore
from app.services.automation_execution import AutomationRunStore
from app.services.automations import AutomationService
from app.services.ha_presence_attempts import HaPresenceAttemptStore
from app.services.leaderboard_dirty import LeaderboardDirtyStore
from app.services.movement_work import movement_readiness
from app.services.notification_runs import NotificationRunStore
from app.services.vehicle_information_jobs import VehicleInformationWorker
from app.services.visitor_lifecycle import lifecycle_readiness

pytestmark = pytest.mark.asyncio
TABLES = ("access_events", "access_completion_work", "vehicle_information_jobs", "notification_runs", "automation_runs", "lpr_ingest_events", "visitor_passes", "automation_rules", "ha_presence_targets", "ha_presence_effects", "gate_command_records", "access_chart_dirty_state")
TASKS = {"access-completion", "access-publication", "ha-presence-worker", "leaderboard-refresh", "vehicle-information", "notification-dispatch", "automation-dispatch", "automation-scheduler", "visitor-pass-lifecycle-worker", "movement-reconciliation", "access-event-worker"}


async def seed_history(size):
    """Equivalent compact terminal queues; no photos or vendor bodies."""
    sql = (
        """INSERT INTO access_events(id,registration_number,direction,decision,confidence,source,occurred_at,timing_classification)
        SELECT md5('wake-event-'||g)::uuid,'SYN'||g,'ENTRY','GRANTED',1,'synthetic',now(),'UNKNOWN' FROM generate_series(1,:size) g""",
        """INSERT INTO access_completion_work(id,event_id,stage,origin,readiness,state,due_at,attempt_count,result)
        SELECT md5('wake-completion-'||g)::uuid,md5('wake-event-'||g)::uuid,
        CASE WHEN g%2=0 THEN 'publication' ELSE 'snapshot' END,'{}','committed_decision','succeeded',now(),1,'{}' FROM generate_series(1,:size) g""",
        """INSERT INTO vehicle_information_jobs(event_id,registration_number,created_at,origin_at,deadline,status,readiness)
        SELECT md5('wake-event-'||g)::uuid,'SYN'||g,now(),now(),now()+interval '300 seconds','completed','committed_decision' FROM generate_series(1,:size) g""",
        """INSERT INTO automation_runs(id,trigger_key,status,started_at,finished_at,trigger_payload,context,condition_results,action_results,actor,source,recovery_version,completion_audit_pending,action_plan,queued_at)
        SELECT md5('wake-automation-'||g)::uuid,'synthetic.wake','success',now(),now(),'{}','{"version": 1}','[]','[]','Synthetic','synthetic',1,false,'[]',now() FROM generate_series(1,:size) g""",
        """INSERT INTO notification_runs(id,trigger_event,subject,severity,status,context,delivered_count,failed_count,skipped_count,failures,skipped_reasons,queued_at,recovery_version)
        SELECT md5('wake-notification-'||g)::uuid,'synthetic.wake','Synthetic','info','sent','{}',1,0,0,'[]','[]',now(),1 FROM generate_series(1,:size) g""",
        """INSERT INTO lpr_ingest_events(id,idempotency_key,source,registration_number,captured_at,received_at,normalized_payload,status,attempt_count,observation_state,ready_ordinal)
        SELECT md5('wake-ingest-'||g)::uuid,'synthetic-wake-'||g,'synthetic','SYN'||g,now(),now(),'{}','completed',1,'ready',g FROM generate_series(1,:size) g""",
        """INSERT INTO visitor_passes(id,visitor_name,pass_type,expected_time,window_minutes,status,creation_source)
        SELECT md5('wake-visitor-'||g)::uuid,'Synthetic '||g,'ONE_TIME',now(),30,'EXPIRED','synthetic' FROM generate_series(1,:size) g""",
        """INSERT INTO automation_rules(id,name,is_active,triggers,trigger_keys,conditions,actions,run_count)
        SELECT md5('wake-rule-'||g)::uuid,'Synthetic '||g,false,'[]','[]','[]','[]',0 FROM generate_series(1,:size) g""",
        """INSERT INTO ha_presence_targets(target_key,url,entity_id,generation,binding_fingerprint,config_fingerprint,state,barrier_version,updated_at)
        SELECT md5('wake-target-'||g),'http://synthetic.invalid','input_boolean.synthetic_'||g,1,md5('binding'),md5('config'),'settled',0,now() FROM generate_series(1,:size) g""",
        """INSERT INTO ha_presence_effects(id,target_key,generation,url,entity_id,action,binding_fingerprint,config_fingerprint,state,owner_token,owner_epoch,lease_until,attempted_at)
        SELECT md5('wake-effect-'||g)::uuid,md5('wake-target-'||g),1,'http://synthetic.invalid','input_boolean.synthetic_'||g,'turn_on',md5('binding'),md5('config'),'accepted',md5('wake-owner')::uuid,1,now(),now() FROM generate_series(1,:size) g""",
        """INSERT INTO gate_command_records(id,idempotency_key,state,action,source,gate_key,controller,reason,bypass_schedule,mechanically_confirmed,requires_reconciliation,command_metadata)
        SELECT md5('wake-command-'||g)::uuid,'synthetic-wake-'||g,'RECONCILED','open','synthetic','synthetic_gate','synthetic','Synthetic retained receipt',false,true,false,'{}' FROM generate_series(1,:size) g""",
    )
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE access_completion_work,vehicle_information_jobs,lpr_ingest_events,lpr_debounce_windows,lpr_coordinator_state,access_chart_dirty_state,ha_presence_coordinator,ha_presence_targets,ha_presence_effects,access_events,automation_runs,notification_runs,visitor_passes,automation_rules,gate_command_records CASCADE"))
        for statement in sql:
            await session.execute(text(statement), {"size":size})
        await session.execute(text("INSERT INTO lpr_coordinator_state(id,epoch,next_open_ordinal,handled_ready_ordinal) VALUES(1,0,1,:size)"), {"size":size})
        await session.execute(text("SELECT setval('lpr_ready_ordinal_seq',:size,true)"), {"size":size})
        await session.execute(text("INSERT INTO access_chart_dirty_state(id,requested_generation,acknowledged_generation) VALUES(1,:size,:size)"), {"size":size})
        for table in TABLES:
            await session.execute(text("ANALYZE " + table))
        await session.commit()


def probes():
    pipeline = AccessPipelineStore()
    return {
        "completion": AccessCompletionStore().readiness,
        "publication": AccessPublicationStore().readiness,
        "ha": HaPresenceAttemptStore().readiness,
        "chart": LeaderboardDirtyStore().readiness,
        "information": VehicleInformationWorker().readiness,
        "notification": NotificationRunStore().readiness,
        "automation": AutomationRunStore().readiness,
        "automation_clock": AutomationService().time_intake.readiness,
        "visitor": lambda: lifecycle_readiness(AsyncSessionLocal),
        "movement": lambda: movement_readiness(AsyncSessionLocal),
        "lpr": lambda: pipeline.readiness(owned=True, quiet_seconds=.5, max_seconds=1),
    }


async def plans(sql, parameters):
    async with engine.connect() as connection:
        client = (await connection.get_raw_connection()).driver_connection
        assert all(isinstance(value, (int, float)) for value in parameters)
        arguments = "(" + ",".join(str(value) for value in parameters) + ")" if parameters else ""
        await client.execute("PREPARE event_budget AS " + sql)
        result = {}
        try:
            for mode in ("force_custom_plan", "force_generic_plan"):
                await client.execute("SET plan_cache_mode=" + mode)
                value = await client.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) EXECUTE event_budget" + arguments)
                result[mode] = (json.loads(value) if isinstance(value, str) else value)[0]
        finally:
            await client.execute("DEALLOCATE event_budget")
            await client.execute("RESET plan_cache_mode")
        return result


@pytest.mark.parametrize("size", [100, 100000])
async def test_readiness_is_one_read_only_indexed_statement_for_empty_future_and_multibatch(size, tmp_path):
    await seed_history(size)
    evidence = {}
    for scenario in ("empty", "future", "multibatch"):
        if scenario != "empty":
            async with AsyncSessionLocal() as session:
                delay = timedelta(hours=1) if scenario == "future" else timedelta(seconds=-1)
                # Future queues reach the full target cardinality. Then a bounded
                # 45-row due prefix exceeds the serial twenty-row batch.
                await session.execute(text("UPDATE access_completion_work SET state='queued',due_at=now()+CAST(:delay AS interval) WHERE id IN (SELECT id FROM access_completion_work ORDER BY id LIMIT :active)"), {"delay":delay,"active":size if scenario == "future" else 45})
                await session.execute(text("UPDATE vehicle_information_jobs SET status='queued',lease_until=now()+CAST(:delay AS interval),deadline=origin_at+interval '300 seconds' WHERE event_id IN (SELECT event_id FROM vehicle_information_jobs ORDER BY event_id LIMIT :active)"), {"delay":timedelta(seconds=120 if scenario == "future" else -1),"active":size if scenario == "future" else 45})
                await session.execute(text("UPDATE notification_runs SET status='queued',claim_token=id,lease_expires_at=now()+CAST(:delay AS interval) WHERE id IN (SELECT id FROM notification_runs ORDER BY id LIMIT :active)"), {"delay":delay,"active":size if scenario == "future" else 45})
                await session.execute(text("UPDATE automation_runs SET status='queued',lease_expires_at=now()+CAST(:delay AS interval),finished_at=NULL WHERE id IN (SELECT id FROM automation_runs ORDER BY id LIMIT :active)"), {"delay":delay,"active":size if scenario == "future" else 45})
                await session.execute(text("UPDATE visitor_passes SET status='SCHEDULED',valid_from=now()+CAST(:delay AS interval),valid_until=now()+interval '3 hours' WHERE id IN (SELECT id FROM visitor_passes ORDER BY id LIMIT :active)"), {"delay":delay,"active":size if scenario == "future" else 45})
                await session.execute(text("UPDATE automation_rules SET is_active=true,next_run_at=now()+CAST(:delay AS interval) WHERE id IN (SELECT id FROM automation_rules ORDER BY id LIMIT :active)"), {"delay":delay,"active":size if scenario == "future" else 45})
                await session.execute(text("UPDATE ha_presence_effects SET state='attempting',lease_until=now()+CAST(:delay AS interval) WHERE id IN (SELECT id FROM ha_presence_effects ORDER BY id LIMIT :active)"), {"delay":delay,"active":size if scenario == "future" else 45})
                await session.execute(text("UPDATE gate_command_records SET state='LEASED',lease_expires_at=now()+CAST(:delay AS interval) WHERE id IN (SELECT id FROM gate_command_records ORDER BY id LIMIT :active)"), {"delay":delay,"active":size if scenario == "future" else 45})
                await session.execute(text("UPDATE access_chart_dirty_state SET requested_generation=acknowledged_generation+1,first_dirty_at=now()+CAST(:delay AS interval),last_dirty_at=now()+CAST(:delay AS interval)"), {"delay":timedelta(hours=1) if scenario == "future" else timedelta(seconds=-11)})
                if scenario == "future":
                    await session.execute(text("UPDATE lpr_ingest_events SET status='pending',observation_state='observing',ready_ordinal=NULL,capture_until=now()+interval '1 hour'"))
                else:
                    await session.execute(text("UPDATE lpr_ingest_events AS ingest SET observation_state='ready',capture_until=NULL,ready_ordinal=:size+candidate.ordinal FROM (SELECT id,row_number() OVER(ORDER BY id) AS ordinal FROM lpr_ingest_events ORDER BY id LIMIT 45) candidate WHERE ingest.id=candidate.id"), {"size":size})
                for table in TABLES:
                    await session.execute(text("ANALYZE " + table))
                await session.commit()
        for name, call in probes().items():
            await call()  # Separate first-use compilation from recorded samples.
            measurement, results, queries = await measured(call)
            assert len(queries) == 3
            sql, parameters = queries[0]
            assert "FOR UPDATE" not in sql.upper()
            assert sql.lstrip().upper().startswith(("WITH", "SELECT"))
            assert not any(token in sql for token in ("normalized_payload", "snapshot_data_url", "delivery_plan", "action_plan"))
            captured = await plans(sql, parameters)
            evidence[f"{scenario}:{name}"] = {**measurement,"sql":sql,"parameters":parameters,"plans":captured,
                "outputs":[{"ready":result.ready,"has_deadline":result.delay_seconds is not None} for result in results]}
            evidence["allocation_scope"] = "Tracemalloc includes query-capture instrumentation and retained SQL records; excludes PostgreSQL memory and process RSS."
            # Preserve the exact SQL/output/plan before a cost or equivalence gate
            # fails; a later long idle window must not hide the failing substep.
            retain(tmp_path, f"event-readiness-{size}", evidence)
            if scenario in ("empty", "future"):
                assert all(not result.ready for result in results)
            if scenario == "multibatch" and name in {"completion", "publication", "information", "notification", "automation", "visitor", "lpr", "ha", "chart", "movement", "automation_clock"}:
                assert all(result.ready for result in results)
            for plan in captured.values():
                for node in nodes(plan["Plan"]):
                    if node.get("Relation Name") in TABLES:
                        examined = (node.get("Actual Rows", 0) + node.get("Rows Removed by Filter", 0)) * node.get("Actual Loops", 1)
                        assert examined <= 128, (name, scenario, size, node)


async def measure_process(tmp_path, phase, backend):
    output, log = tmp_path / (phase + ".json"), tmp_path / (phase + ".log")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(backend) + os.pathsep + str(Path(__file__).parent)
    helper = Path(__file__).with_name("event_driven_measurement.py")
    with log.open("wb") as stream:
        process = await asyncio.create_subprocess_exec(sys.executable, str(helper), "--phase",phase,"--seconds","125","--output",str(output), env=environment,stdout=stream,stderr=stream)
        try:
            async with asyncio.timeout(170):
                code = await process.wait()
        except BaseException:
            process.kill()
            await process.wait()
            raise
    assert code == 0, log.read_text()[-8000:]
    return json.loads(output.read_text())


async def test_actual_125_second_idle_workers_budget_and_optional_frozen_baseline(tmp_path):
    await seed_history(100000)
    baseline_path = os.environ.get("IACS_EVENT_DRIVEN_BASELINE")
    baseline = await measure_process(tmp_path, "baseline", Path(baseline_path)) if baseline_path else None
    candidate = await measure_process(tmp_path, "candidate", Path(__file__).resolve().parents[2] / "backend")
    if baseline is not None:
        assert baseline["durable_outputs"] == candidate["durable_outputs"]
        assert len(candidate["queries"]) < len(baseline["queries"])
    by_task = candidate["counts_by_task"]
    # Initial probe plus repairs at sixty and 120 seconds; leadership renewal is
    # a separate necessary write stream, never hidden inside the discovery gate.
    for task in TASKS:
        assert 1 <= by_task.get(task, 0) <= 3, (task, by_task)
    renewal = [row for row in candidate["queries"] if row["task"] == "lpr-coordinator-renewal"]
    discovery = [row for row in candidate["queries"] if row["task"] != "lpr-coordinator-renewal"]
    assert renewal and len(renewal) <= 100
    assert all("FOR UPDATE" not in row["sql"].upper() for row in discovery)
    assert all(row["sql"].lstrip().upper().startswith(("WITH", "SELECT")) for row in discovery)
    assert not candidate["errors"]
    retain(tmp_path, "event-actual-idle", {"candidate":candidate,"baseline":baseline,
        "historical_comparison":"actual frozen source worker paths" if baseline else "not attempted: IACS_EVENT_DRIVEN_BASELINE absent",
        "baseline_expected_head":"8259a7af778526c57459c44c8537cf26ca3e541c",
        "separate_lpr_renewal_queries":len(renewal),"discovery_queries":len(discovery)})
