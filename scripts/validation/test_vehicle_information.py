"""Real PostgreSQL/Redis proof for optional enrichment, snapshot paging and recovery."""
import asyncio
from datetime import UTC, datetime, timedelta
import json
import os
import gc
import tracemalloc
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import delete, event as sql_event, func, insert, select, text

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models import AccessEvent, GateCommandRecord, NotificationRun, Vehicle, VehicleInformationJob, VehicleInformationSnapshot
from app.models.enums import AccessDecision, AccessDirection
from app.services import vehicle_information_jobs as jobs
from app.services.directory.reads import list_vehicles
from app.services.settings import get_runtime_config
from app.services.vehicle_information import VehicleInformationService
from app.services.vehicle_information_authorization import vehicle_information_notice_denial
from app.services.vehicle_information_contracts import ProviderResult, VehicleLookup, VehicleRecord, MotTest, resolve_information
from app.services.vehicle_information_store import apply_information, read_mot_history

pytestmark = pytest.mark.asyncio
assert os.environ.get("IACS_VALIDATION_MODE") == "persistence", "Isolated harness required"


def lookup(plate, *, tests=25):
    now = datetime.now(UTC)
    record = VehicleRecord(registration_number=plate, model="Provider Model", mot_expiry=(now-timedelta(days=1)).date(), tests=[
        MotTest(number=str(index), completed_at=now-timedelta(days=index), result="PASSED", expiry=now.date(), defects=[])
        for index in range(tests)
    ])
    results = {"dvsa": ProviderResult(status="found", record=record, checked_at=now, valid_until=now+timedelta(hours=1)), "dvla": ProviderResult(status="disabled")}
    return VehicleLookup(information=resolve_information(plate, results, now=now, timezone="Europe/London"), results=results)


@pytest_asyncio.fixture
async def records():
    plate = "SYN" + uuid.uuid4().hex[:10].upper()
    vehicle = Vehicle(id=uuid.uuid4(), registration_number=plate, make="Original", model="Manual model")
    event = AccessEvent(id=uuid.uuid4(), registration_number=plate, vehicle_id=vehicle.id, direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED, confidence=.99, source="synthetic", occurred_at=datetime.now(UTC), raw_payload={"retained":True})
    async with AsyncSessionLocal() as session:
        session.add(vehicle); await session.flush(); session.add(event); await session.commit()
    yield vehicle, event
    async with AsyncSessionLocal() as session:
        await session.execute(delete(NotificationRun).where(NotificationRun.id.in_([uuid.uuid5(event.id, name) for name in ("expired_mot_detected","expired_tax_detected")])))
        await session.execute(delete(AccessEvent).where(AccessEvent.id == event.id))
        await session.execute(delete(Vehicle).where(Vehicle.id == vehicle.id))
        await session.commit()
    await engine.dispose()


async def test_jobs_deduplicate_commit_enrichment_and_notice_atomically(records, monkeypatch):
    vehicle, event = records
    facade = SimpleNamespace(lookup=AsyncMock(return_value=lookup(vehicle.registration_number)))
    monkeypatch.setattr(jobs, "get_vehicle_information_service", lambda: facade)
    monkeypatch.setattr(jobs.event_bus, "publish", AsyncMock())
    await jobs.enqueue_arrival(event); await jobs.enqueue_arrival(event)
    worker = jobs.VehicleInformationWorker()
    claimed = await worker._claim()
    assert len(claimed) == 1
    await worker._process(*claimed[0])
    assert not await worker._claim()
    async with AsyncSessionLocal() as session:
        saved = await session.get(Vehicle, vehicle.id)
        assert saved.model == "Manual model" and saved.mot_source == "dvsa"
        result = await session.get(AccessEvent, event.id)
        assert result.raw_payload["retained"] and result.raw_payload["vehicle_information"]["model"] == "Provider Model"
        assert "tests" not in json.dumps(result.raw_payload)
        notice = await session.get(NotificationRun, uuid.uuid5(event.id,"expired_mot_detected"))
        assert notice and notice.status == "queued"
        assert await vehicle_information_notice_denial(session, notice.context, notice.id) is None
        job = await session.get(VehicleInformationJob,event.id)
        job.deadline = datetime.now(UTC)-timedelta(seconds=1); await session.flush()
        assert await vehicle_information_notice_denial(session, notice.context, notice.id) == "vehicle_information_notice_expired"
        assert await session.scalar(select(func.count()).select_from(GateCommandRecord)) == 0
        await session.rollback()


async def test_blocked_provider_releases_connections_and_registration_change_wins(records, monkeypatch):
    vehicle, event = records
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked(plate, **kwargs):
        assert engine.pool.checkedout() == 0
        entered.set(); await release.wait()
        return lookup(plate)
    monkeypatch.setattr(jobs, "get_vehicle_information_service", lambda: SimpleNamespace(lookup=blocked))
    await jobs.enqueue_arrival(event)
    worker=jobs.VehicleInformationWorker(); claimed=await worker._claim()
    task=asyncio.create_task(worker._process(*claimed[0])); await entered.wait()
    async with AsyncSessionLocal() as session:
        saved=await session.get(Vehicle,vehicle.id); saved.registration_number="CHANGED"+uuid.uuid4().hex[:8].upper(); await session.commit()
    release.set(); await task
    async with AsyncSessionLocal() as session:
        saved=await session.get(Vehicle,vehicle.id)
        assert saved.mot_source is None and saved.model == "Manual model"
        assert (await session.get(VehicleInformationJob,event.id)).outcome == "registration_changed"


async def test_restart_lease_and_deadline_prevent_replay(records):
    _, event=records
    await jobs.enqueue_arrival(event)
    first=await jobs.VehicleInformationWorker()._claim()
    assert len(first)==1 and not await jobs.VehicleInformationWorker()._claim()
    async with AsyncSessionLocal() as session:
        job=await session.get(VehicleInformationJob,event.id); job.lease_until=datetime.now(UTC)-timedelta(seconds=1); await session.commit()
    reclaimed=await jobs.VehicleInformationWorker()._claim()
    assert len(reclaimed)==1 and reclaimed[0][1]!=first[0][1]
    async with AsyncSessionLocal() as session:
        job=await session.get(VehicleInformationJob,event.id);job.deadline=datetime.now(UTC)-timedelta(seconds=1);await session.commit()
    assert not await jobs.VehicleInformationWorker()._claim()
    async with AsyncSessionLocal() as session:
        assert (await session.get(VehicleInformationJob,event.id)).status=="expired"


async def test_history_paging_stale_write_and_bounded_directory_reads(records):
    vehicle,_=records
    latest=lookup(vehicle.registration_number)
    async with AsyncSessionLocal() as session:
        saved=await session.get(Vehicle,vehicle.id,with_for_update=True)
        await apply_information(session,saved,latest,timezone="Europe/London");await session.commit()
        old=lookup(vehicle.registration_number);old.requested_at=latest.requested_at-timedelta(seconds=1)
        old.results["dvsa"].record.model="Old model"
        old.results["dvsa"].record.mot_expiry = datetime.now(UTC).date()+timedelta(days=30)
        retained = await apply_information(session,saved,old,timezone="Europe/London");await session.commit()
        assert retained.model == "Provider Model" and retained.mot_status == "Expired"
        page=await read_mot_history(session,vehicle.id,cursor=None,limit=10)
        second=await read_mot_history(session,vehicle.id,cursor=page.next_cursor,limit=10)
        assert page.total==25 and len(page.items)==len(second.items)==10
        assert set(test.number for test in page.items).isdisjoint(test.number for test in second.items)
        assert (await session.get(VehicleInformationSnapshot,vehicle.id)).dvsa["record"]["model"]=="Provider Model"
        captured=[]
        def collect(conn,cursor,statement,parameters,context,executemany):captured.append(statement)
        sql_event.listen(engine.sync_engine,"before_cursor_execute",collect)
        try: await list_vehicles(session,limit=50)
        finally: sql_event.remove(engine.sync_engine,"before_cursor_execute",collect)
        assert not any("vehicle_information_snapshots" in sql for sql in captured)
        with pytest.raises(ValueError):await read_mot_history(session,vehicle.id,cursor="bad",limit=10)


async def test_real_shared_redis_throttle_and_unknown_cache():
    from app.modules.dvsa.client import DvsaError
    cache=Redis.from_url(settings.redis_url,decode_responses=True)
    service=VehicleInformationService(cache)
    try:
        await cache.delete("iacs:vehicle-information:dvsa:rate", "iacs:vehicle-information:dvsa:cooldown")
        results=await asyncio.gather(*(service._dvsa_slot() for _ in range(10)),return_exceptions=True)
        assert sum(result is None for result in results)==5
        assert all(result is None or isinstance(result,DvsaError) for result in results)
    finally:await service.close()


@pytest.mark.parametrize("size", [20, 2000])
async def test_summary_cost_is_independent_of_stored_histories(records, size, tmp_path):
    vehicle, _ = records
    ids = [uuid.uuid4() for _ in range(size)]
    captured = []
    def collect(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)
    async def measure(session):
        captured.clear()
        gc.collect()
        tracemalloc.start()
        sql_event.listen(engine.sync_engine, "before_cursor_execute", collect)
        try:
            page = await list_vehicles(session, limit=50)
            payload = page.model_dump_json()
            _, peak = tracemalloc.get_traced_memory()
        finally:
            sql_event.remove(engine.sync_engine, "before_cursor_execute", collect)
            tracemalloc.stop()
        assert not any("vehicle_information_snapshots" in sql for sql in captured)
        return payload, {"queries": len(captured), "rows": len(page.items), "payload_bytes": len(payload.encode()), "peak_python_bytes": peak}
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(insert(Vehicle), [{"id": value, "registration_number": "SYN"+value.hex[:16].upper()} for value in ids])
            await list_vehicles(session, limit=50)  # Warm statement compilation for both measurements.
            before, before_metrics = await measure(session)
            sample = lookup(vehicle.registration_number).results["dvsa"].model_dump(mode="json")
            await session.execute(insert(VehicleInformationSnapshot), [{"vehicle_id": value,
                "registration_number": "SYN"+value.hex[:16].upper(), "dvsa": sample,
                "dvla": {"status": "disabled"}} for value in ids])
            after, after_metrics = await measure(session)
            assert before == after and before_metrics["queries"] == after_metrics["queries"]
            assert after_metrics["rows"] == min(size+1, 50)
            await session.execute(text("ANALYZE vehicle_information_snapshots"))
            plan = await session.execute(text("EXPLAIN (ANALYZE, FORMAT JSON) SELECT dvsa FROM vehicle_information_snapshots WHERE vehicle_id=:id"), {"id": ids[0]})
            evidence = {"vehicles": size+1, "tests_per_snapshot": 25, "before": before_metrics,
                "after": after_metrics, "snapshot_plan": plan.scalar()}
            (tmp_path / f"mot-scale-{size}.json").write_text(json.dumps(evidence))
            print("MOT_TARGET_SCALE_EVIDENCE", json.dumps(evidence))
            await session.rollback()
    finally:
        await engine.dispose()


async def test_unknown_arrival_enriches_facts_without_directory_or_hardware(records, monkeypatch):
    _, event = records
    async with AsyncSessionLocal() as session:
        saved = await session.get(AccessEvent, event.id)
        saved.vehicle_id = None
        saved.decision = AccessDecision.DENIED
        await session.commit()
        event = saved
        before = await session.scalar(select(func.count()).select_from(Vehicle))
    facade = SimpleNamespace(lookup=AsyncMock(return_value=lookup(event.registration_number)))
    monkeypatch.setattr(jobs, "get_vehicle_information_service", lambda: facade)
    await jobs.enqueue_arrival(event)
    worker = jobs.VehicleInformationWorker()
    await worker._process(*(await worker._claim())[0])
    async with AsyncSessionLocal() as session:
        saved = await session.get(AccessEvent, event.id)
        assert saved.raw_payload["vehicle_information"]["model"] == "Provider Model"
        assert saved.decision == AccessDecision.DENIED and saved.vehicle_id is None
        assert await session.scalar(select(func.count()).select_from(Vehicle)) == before
        assert await session.scalar(select(func.count()).select_from(GateCommandRecord)) == 0


async def test_deferred_work_releases_lease_then_retries_before_deadline(records, monkeypatch):
    _, event = records
    deferred = lookup(event.registration_number)
    deferred.results["dvsa"] = ProviderResult(status="deferred", retry_at=datetime.now(UTC)+timedelta(seconds=1))
    deferred.information = resolve_information(event.registration_number, deferred.results, now=datetime.now(UTC), timezone="Europe/London")
    facade = SimpleNamespace(lookup=AsyncMock(side_effect=[deferred, lookup(event.registration_number)]))
    monkeypatch.setattr(jobs, "get_vehicle_information_service", lambda: facade)
    await jobs.enqueue_arrival(event)
    worker = jobs.VehicleInformationWorker()
    await worker._process(*(await worker._claim())[0])
    assert not await worker._claim()
    async with AsyncSessionLocal() as session:
        job = await session.get(VehicleInformationJob, event.id)
        assert job.status == "queued" and job.lease_token is None
        job.lease_until = datetime.now(UTC)-timedelta(seconds=1)
        await session.commit()
    await worker._process(*(await worker._claim())[0])
    async with AsyncSessionLocal() as session:
        assert (await session.get(VehicleInformationJob, event.id)).status == "completed"
    assert facade.lookup.await_count == 2


async def test_older_completion_uses_current_tax_outcome_for_notification(records, monkeypatch):
    vehicle, event = records
    old = lookup(event.registration_number)
    old.requested_at -= timedelta(seconds=5)
    old.results["dvla"] = ProviderResult(status="found", checked_at=datetime.now(UTC),
        valid_until=datetime.now(UTC)+timedelta(hours=1),
        record=VehicleRecord(registration_number=event.registration_number, tax_status="Untaxed"))
    old.information = resolve_information(event.registration_number, old.results, now=datetime.now(UTC), timezone="Europe/London")
    latest = lookup(event.registration_number)
    latest.results["dvla"] = old.results["dvla"].model_copy(update={"status": "not_found", "valid_until": None})
    async with AsyncSessionLocal() as session:
        saved = await session.get(Vehicle, vehicle.id, with_for_update=True)
        await apply_information(session, saved, latest, timezone="Europe/London")
        await session.commit()
    monkeypatch.setattr(jobs, "get_vehicle_information_service", lambda: SimpleNamespace(lookup=AsyncMock(return_value=old)))
    await jobs.enqueue_arrival(event)
    worker = jobs.VehicleInformationWorker()
    await worker._process(*(await worker._claim())[0])
    async with AsyncSessionLocal() as session:
        assert await session.get(NotificationRun, uuid.uuid5(event.id, "expired_tax_detected")) is None
        assert (await session.get(VehicleInformationJob, event.id)).status == "completed"
