"""Forward retirement against populated synthetic storage; never a live account."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import uuid

import test_schema_contract as schema
from test_feature_retirement import scratch as scratch, connection

import pytest

BEFORE = "20261014_0015"
AFTER = "20261014_0016"
IDS = {name: uuid.UUID(int=index) for index, name in enumerate((
    "account", "sync", "calendar", "manual", "completed", "duration", "open_used",
    "event", "departure", "rule", "safe_rule", "queued", "claimed", "history", "orphan",
    "safe_run", "notification", "confirmation", "reservation",
), 40001)}
CALENDAR = {"id": "calendar", "type": "integration.icloud_calendar.sync", "config": {"provider": "icloud_calendar", "action": "sync_calendars"}}
HARDWARE = {"id": "gate", "type": "gate.open", "config": {}}
MANUAL = {"id": "notice", "type": "notification.enable", "config": {"notification_rule_name": "Synthetic"}}


def migrate(item, *, success=True):
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--upgrade-worker"],
        cwd=schema.ROOT / "backend", env=dict(os.environ, IACS_DATABASE_URL=schema.scratch_url(item.name)),
        capture_output=True, text=True, timeout=120)
    log = schema.safe_error(result.stdout + result.stderr)
    (item.output / ("retirement-upgrade.log" if success else "retirement-refusal.log")).write_text(log)
    assert (result.returncode == 0) is success, log
    return log


def upgrade_worker():
    from alembic import command
    from alembic.config import Config
    original = socket.socket.connect
    forbidden = []
    def guarded(sock, address):
        if not (isinstance(address, tuple) and address[:2] == ("127.0.0.1", 5432)):
            forbidden.append("provider_socket")
            raise AssertionError("Calendar retirement attempted a non-database socket")
        return original(sock, address)
    socket.socket.connect = guarded
    try:
        command.upgrade(Config(str(schema.ROOT / "backend/alembic.ini")), AFTER)
        assert not forbidden
    finally:
        socket.socket.connect = original


async def seed(item):
    assert schema.migrate(item.name, "upgrade", BEFORE, item.output, "prior-upgrade")
    db = await connection(item.name)
    try:
        await db.execute("""INSERT INTO icloud_calendar_accounts(id,apple_id,is_active,status,encrypted_session_bundle)
            VALUES($1,'synthetic@example.invalid',true,'connected','synthetic-ciphertext-only')""", IDS["account"])
        await db.execute("""INSERT INTO icloud_calendar_sync_runs(id,started_at,status,trigger_source,
            account_count,events_scanned,events_matched,passes_created,passes_updated,passes_cancelled,passes_skipped,account_results)
            VALUES($1,now(),'unknown','workflow',1,2,1,0,0,0,0,$2::jsonb)""", IDS["sync"], json.dumps([
                {"account_id":str(IDS["account"]),"apple_id":"synthetic@example.invalid","status":"unknown","passes_created":1,"error":"synthetic-provider-sensitive-error"},
                {"account_id":str(IDS["account"]),"status":"ok","passes_created":1}]))
        for key,direction in (("event","ENTRY"),("departure","EXIT")):
            await db.execute("""INSERT INTO access_events(id,registration_number,direction,decision,confidence,source,occurred_at,timing_classification)
                VALUES($1,'SYNTHETIC1',$2,'GRANTED',1,'synthetic',now(),'NORMAL')""",IDS[key],direction)
        for key,status,kind,source in (("calendar","SCHEDULED","ONE_TIME","icloud_calendar"),
            ("manual","ACTIVE","ONE_TIME","ui"),("completed","USED","ONE_TIME","icloud_calendar"),
            ("duration","ACTIVE","DURATION","icloud_calendar"),("open_used","USED","ONE_TIME","icloud_calendar")):
            ref=f"icloud:{IDS['account']}:synthetic-calendar:{key}" if source=="icloud_calendar" else None
            await db.execute("""INSERT INTO visitor_passes(id,visitor_name,pass_type,expected_time,window_minutes,status,creation_source,
                source_reference,source_metadata,number_plate,valid_from,valid_until,arrival_time,departure_time,arrival_event_id,departure_event_id)
                VALUES($1,'Synthetic visitor',$2,now(),30,$3,$4,$5,$6::jsonb,'SYNTHETIC1',now()-interval '1 hour',now()+interval '1 day',
                CASE WHEN $7 THEN now()-interval '10 minutes' END,CASE WHEN $8 THEN now() END,
                CASE WHEN $7 THEN $9::uuid END,CASE WHEN $8 THEN $10::uuid END)""",
                IDS[key],kind,status,source,ref,json.dumps({"account_id":str(IDS["account"]),"calendar_id":"synthetic-calendar","event_id":key,"apple_id":"synthetic@example.invalid"}) if ref else json.dumps({"manual":"retained"}),
                key in {"completed","duration","open_used"},key in {"completed","duration"},IDS["event"],IDS["departure"])
        for key,actions in (("rule",[HARDWARE,CALENDAR]),("safe_rule",[MANUAL])):
            await db.execute("""INSERT INTO automation_rules(id,name,is_active,triggers,trigger_keys,conditions,actions,next_run_at,run_count)
                VALUES($1,'Synthetic',true,'[{"type":"time.cron","config":{"cron_expression":"* * * * *"}}]','["time.cron"]','[]',$2::jsonb,now(),0)""",IDS[key],json.dumps(actions))
        for key,status,rule,actions in (("queued","queued","rule",[HARDWARE,CALENDAR]),("claimed","processing","rule",[HARDWARE,CALENDAR]),
            ("history","success","rule",[CALENDAR]),("orphan","queued",None,[HARDWARE,CALENDAR]),("safe_run","queued","safe_rule",[MANUAL])):
            result={"id":"calendar","type":CALENDAR["type"],"status":"unknown","accepted_any":True,"requires_review":True,
                "sync":{"status":"unknown","account_results":[{"account_id":str(IDS["account"]),"apple_id":"synthetic@example.invalid","status":"ok"}]}}
            plan=[{"action":a,"state":"pending","result":result if a==CALENDAR else None} for a in actions]
            await db.execute("""INSERT INTO automation_runs(id,rule_id,trigger_key,status,started_at,finished_at,context,trigger_payload,
                condition_results,action_results,actor,source,recovery_version,queued_at,claim_token,lease_expires_at,action_plan)
                VALUES($1,$2,'time.cron',$3,now(),CASE WHEN $4 THEN now() END,'{"version":1}','{}','[]',$5::jsonb,
                'Synthetic','synthetic',1,now(),$6,now()+interval '1 hour',$7::jsonb)""",IDS[key],IDS[rule] if rule else None,status,
                key=="history",json.dumps([result] if key=="history" else []),uuid.uuid4() if key=="claimed" else None,json.dumps(plan))
        await db.execute("""INSERT INTO notification_runs(id,trigger_event,subject,severity,status,context,queued_at,delivered_count,
            failed_count,skipped_count,failures,skipped_reasons,rules_override,delivery_plan)
            VALUES($1,'vehicle.known_plate','Synthetic','info','queued','{}',now(),0,0,0,'[]','[]',$2::jsonb,'[]')""",
            IDS["notification"],json.dumps([{"id":str(IDS["rule"]),"actions":[CALENDAR]}]))
        await db.execute("""INSERT INTO users(id,username,first_name,last_name,full_name,password_hash,role,is_active,preferences)
            VALUES($1,'synthetic-calendar-retirement','Synthetic','Admin','Synthetic Admin','synthetic-unused','ADMIN',true,'{}')""",IDS["account"])
        await db.execute("""INSERT INTO action_confirmations(id,token_hash,action,payload_hash,actor_user_id,
            target_entity,target_id,expires_at) VALUES($1,'synthetic-confirmation','icloud_calendar.sync.resume',
            'synthetic',$2,'ICloudCalendarSyncRun',$3,now()+interval '1 hour')""",IDS["confirmation"],IDS["account"],str(IDS["sync"]))
        await db.execute("INSERT INTO system_settings(key,category,value,is_secret) VALUES('icloud_calendar_token','retired','{"+'"encrypted":"synthetic"'+"}',true)")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_populated_upgrade_holds_entire_mixed_work_and_preserves_delivery_and_visits(scratch):
    await seed(scratch)
    db=await connection(scratch.name)
    try:
        before={key:dict(await db.fetchrow('SELECT * FROM visitor_passes WHERE id=$1',IDS[key])) for key in ("manual","completed","duration","open_used")}
    finally:
        await db.close()
    migrate(scratch)
    db=await connection(scratch.name)
    try:
        assert await db.fetchval("SELECT to_regclass('icloud_calendar_accounts')") is None
        assert await db.fetchval("SELECT to_regclass('icloud_calendar_sync_runs')") is None
        assert await db.fetchval("SELECT count(*) FROM system_settings WHERE key LIKE 'icloud_%'")==0
        assert await db.fetchval('SELECT status::text FROM visitor_passes WHERE id=$1',IDS["calendar"])=="CANCELLED"
        assert await db.fetchval('SELECT status::text FROM visitor_passes WHERE id=$1',IDS["duration"])=="CANCELLED"
        assert dict(await db.fetchrow('SELECT * FROM visitor_passes WHERE id=$1',IDS["manual"]))==before["manual"]
        for key in ("completed","duration","open_used"):
            after=dict(await db.fetchrow('SELECT * FROM visitor_passes WHERE id=$1',IDS[key]))
            for field in ("arrival_time","departure_time","arrival_event_id","departure_event_id","number_plate","source_reference"):
                assert after[field]==before[key][field]
        for key in ("queued","claimed","orphan"):
            run=await db.fetchrow('SELECT * FROM automation_runs WHERE id=$1',IDS[key])
            assert run["status"]=="review_required" and run["review_reason"]=="icloud_calendar_retired"
            assert run["claim_token"] is None and run["lease_expires_at"] is None
            assert len(json.loads(run["action_plan"]))==2
        rule=await db.fetchrow('SELECT * FROM automation_rules WHERE id=$1',IDS["rule"])
        assert not rule["is_active"] and rule["next_run_at"] is None and len(json.loads(rule["actions"]))==2
        assert await db.fetchval('SELECT is_active FROM automation_rules WHERE id=$1',IDS["safe_rule"])
        assert await db.fetchval('SELECT status FROM automation_runs WHERE id=$1',IDS["safe_run"])=="queued"
        history=await db.fetchrow('SELECT * FROM automation_runs WHERE id=$1',IDS["history"])
        receipt=json.loads(history["action_results"])[0]
        assert history["status"]=="success" and receipt["status"]=="unknown" and receipt["accepted_any"] and receipt["requires_review"]
        assert "synthetic@example.invalid" not in json.dumps(receipt)
        archive=json.loads(await db.fetchval("SELECT metadata::text FROM audit_logs WHERE target_entity='RetiredCalendarSyncRun'"))
        assert [x["status"] for x in archive["account_results"]]==["unknown","ok"]
        assert "synthetic-provider-sensitive-error" not in json.dumps(archive)
        assert await db.fetchval('SELECT outcome FROM action_confirmations WHERE id=$1',IDS["confirmation"])=="cancelled"
        assert await db.fetchval('SELECT status FROM notification_runs WHERE id=$1',IDS["notification"])=="review_required"
    finally:
        await db.close()
    # Historical assertions above belong to AFTER. Today's ORM must use today's
    # schema when proving that retained holds still prevent execution after upgrade.
    assert schema.migrate(scratch.name, "upgrade", "head", scratch.output, "runtime-head")
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from app.services.automation_execution import AutomationRunStore
    from app.services.visitor_passes import VisitorPassService
    engine=create_async_engine(schema.scratch_url(scratch.name))
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    try:
        store=AutomationRunStore(session_factory=sessions)
        assert await store.claim(IDS["claimed"]) is None
        assert await store.claim(IDS["orphan"]) is None
        async with sessions() as session:
            matched=await VisitorPassService().find_departure_pass(session,occurred_at=__import__("datetime").datetime.now(__import__("datetime").UTC),registration_number="SYNTHETIC1")
            assert matched.id==IDS["open_used"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('blocker',["held","reserved","ambiguous","open_visit","consumption","departure_without_arrival","reversed_visit","wrong_consumed_arrival"])
async def test_retirement_blockers_roll_back_all_feature_and_work_changes(scratch,blocker):
    await seed(scratch)
    db=await connection(scratch.name)
    try:
        if blocker in {"held","reserved","consumption"}:
            await db.execute("""INSERT INTO visitor_pass_reservations(id,visitor_pass_id,access_event_id,intent_id,pass_type,
                normalized_plate,state,dispatch_deadline) VALUES($1,$2,$3,$4,'one-time','SYNTHETIC1',$5,now())""",
                IDS["reservation"],IDS["calendar"],IDS["event"],uuid.uuid4(),"consumed" if blocker=="consumption" else blocker)
        elif blocker=="ambiguous":
            await db.execute("UPDATE visitor_passes SET creation_source='ui' WHERE id=$1",IDS["calendar"])
        elif blocker=="reversed_visit":
            await db.execute("UPDATE visitor_passes SET departure_time=arrival_time-interval '1 minute' WHERE id=$1",IDS["duration"])
        elif blocker=="wrong_consumed_arrival":
            await db.execute("""INSERT INTO visitor_pass_reservations(id,visitor_pass_id,access_event_id,intent_id,pass_type,
                normalized_plate,state,dispatch_deadline) VALUES($1,$2,$3,$4,'duration','SYNTHETIC1','consumed',now())""",
                IDS["reservation"],IDS["duration"],IDS["departure"],uuid.uuid4())
        elif blocker=="open_visit":
            await db.execute("UPDATE visitor_passes SET departure_time=NULL WHERE id=$1",IDS["duration"])
        else:
            await db.execute("UPDATE visitor_passes SET departure_time=now() WHERE id=$1",IDS["calendar"])
    finally:
        await db.close()
    assert "Calendar retirement blocked" in migrate(scratch,success=False)
    db=await connection(scratch.name)
    try:
        assert await db.fetchval('SELECT version_num FROM alembic_version')==BEFORE
        assert await db.fetchval('SELECT count(*) FROM icloud_calendar_accounts')==1
        assert await db.fetchval('SELECT status FROM automation_runs WHERE id=$1',IDS["claimed"])=="processing"
        assert await db.fetchval('SELECT is_active FROM automation_rules WHERE id=$1',IDS["rule"])
        assert await db.fetchval("SELECT count(*) FROM audit_logs WHERE metadata->>'reason'='icloud_calendar_retired'")==0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_fresh_creation_has_no_calendar_schema_and_downgrade_refuses_data_recreation(scratch):
    migrate(scratch)
    db=await connection(scratch.name)
    try:
        assert await db.fetchval("SELECT to_regclass('icloud_calendar_accounts')") is None
        assert await db.fetchval('SELECT version_num FROM alembic_version')==AFTER
    finally:
        await db.close()
    assert not schema.migrate(scratch.name,'downgrade',BEFORE,scratch.output,'refused-downgrade')


if __name__=='__main__':
    if sys.argv[1:]==['--upgrade-worker']:
        upgrade_worker()
    else:
        raise SystemExit('Only the guarded isolated worker is directly executable')


@pytest.mark.asyncio
@pytest.mark.parametrize('state',["context","generation","live_lease"])
async def test_historical_calendar_ownership_downgrade_guard_remains_intact(scratch,state):
    await seed(scratch)
    assert schema.migrate(scratch.name,'downgrade','20261008_0014',scratch.output,'historical-0014')
    db=await connection(scratch.name)
    try:
        if state=="context":
            await db.execute("UPDATE icloud_calendar_sync_runs SET run_context='{\"version\":1,\"accepted_checkpoint\":\"retained\"}'")
        elif state=="generation":
            await db.execute('UPDATE icloud_calendar_accounts SET sync_generation=1')
        else:
            await db.execute('UPDATE icloud_calendar_accounts SET sync_lease_token=$1,sync_lease_until=now()',uuid.uuid4())
    finally:
        await db.close()
    assert not schema.migrate(scratch.name,'downgrade','20261007_0013',scratch.output,'historical-refused-downgrade')
    assert 'Calendar ownership state requires' in (scratch.output/'historical-refused-downgrade.log').read_text()
    db=await connection(scratch.name)
    try:
        assert await db.fetchval('SELECT version_num FROM alembic_version')=='20261008_0014'
        assert await db.fetchval('SELECT count(*) FROM icloud_calendar_accounts')==1
        assert await db.fetchval('SELECT count(*) FROM icloud_calendar_sync_runs')==1
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('provenance',["deleted_account","truncated_reference","null_metadata"])
async def test_proven_calendar_passes_do_not_require_a_live_account_or_untruncated_event_key(scratch,provenance):
    await seed(scratch)
    db=await connection(scratch.name)
    try:
        if provenance=="deleted_account":
            await db.execute('DELETE FROM icloud_calendar_accounts')
        elif provenance=="truncated_reference":
            metadata={"account_id":str(IDS["account"]),"calendar_id":"x"*300,"event_id":"synthetic-event"}
            ref=f"icloud:{metadata['account_id']}:{metadata['calendar_id']}:{metadata['event_id']}"[:255]
            await db.execute('UPDATE visitor_passes SET source_reference=$1,source_metadata=$2::jsonb WHERE id=$3',ref,json.dumps(metadata),IDS["calendar"])
        else:
            await db.execute('UPDATE visitor_passes SET source_metadata=NULL WHERE id=$1',IDS["calendar"])
    finally:
        await db.close()
    migrate(scratch)
    db=await connection(scratch.name)
    try:
        assert await db.fetchval('SELECT status::text FROM visitor_passes WHERE id=$1',IDS["calendar"])=="CANCELLED"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_duration_multiple_consumptions_keep_older_visits_and_cancel_future_access(scratch):
    await seed(scratch)
    db=await connection(scratch.name)
    try:
        old_event=uuid.uuid4()
        await db.execute("""INSERT INTO access_events(id,registration_number,direction,decision,confidence,source,occurred_at,timing_classification)
            VALUES($1,'SYNTHETIC1','ENTRY','GRANTED',1,'synthetic',now()-interval '1 day','NORMAL')""",old_event)
        for event in (old_event,IDS["event"]):
            await db.execute("""INSERT INTO visitor_pass_reservations(id,visitor_pass_id,access_event_id,intent_id,pass_type,
                normalized_plate,state,dispatch_deadline) VALUES($1,$2,$3,$4,'duration','SYNTHETIC1','consumed',now())""",
                uuid.uuid4(),IDS["duration"],event,uuid.uuid4())
        before=await db.fetchval('SELECT count(*) FROM visitor_pass_reservations WHERE visitor_pass_id=$1',IDS["duration"])
    finally:
        await db.close()
    migrate(scratch)
    db=await connection(scratch.name)
    try:
        assert await db.fetchval('SELECT status::text FROM visitor_passes WHERE id=$1',IDS["duration"])=="CANCELLED"
        assert await db.fetchval('SELECT count(*) FROM visitor_pass_reservations WHERE visitor_pass_id=$1',IDS["duration"])==before==2
    finally:
        await db.close()
