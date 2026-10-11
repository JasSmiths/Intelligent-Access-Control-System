"""Actual media/zone participants, deadline starts and claim artifacts in PG."""
import asyncio
import io
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import event as sql_event
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from test_recovery_boundaries import isolated_resources as isolated_resources

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models import (
    AccessCompletionWork,
    AccessEvent,
    Anomaly,
    LprZoneShadowObservation,
    VehicleInformationJob,
)
from app.models.enums import AccessDecision, AccessDirection, AnomalySeverity, AnomalyType
from app.modules.unifi_protect.client import ProtectMedia
from app.services import snapshot_recovery, vehicle_information_jobs
from app.services.access import completion_media, completion_store, publication
from app.services.access.completion_contracts import ExternalCompletionOrigin
from app.services.access.completion_database import access_session
from app.services.access.completion_media import (
    AccessCompletionStages,
    PreparedAccessSnapshot,
    accept_snapshot_in_session,
    prepare_snapshot_in_session,
)
from app.services.access.completion_store import (
    AccessCompletionStore,
    reserve_external_completion_in_session,
)
from app.services.access.pipeline_contracts import PipelineLeaseLost
from app.services.access.publication import (
    AccessPublicationStore,
    PublicationMessage,
    reserve_initial_publication_in_session,
)
from app.services.access.publication_worker import AccessPublicationLane
from app.services.realtime_contracts import RealtimePublishReceipt
from app.services.snapshots import (
    SnapshotManager,
    access_event_snapshot_relative_path,
    access_event_snapshot_url,
)
from app.services.vehicle_visual_detections import VehicleVisualDetectionRecorder

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def resources(isolated_resources, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE access_events CASCADE"))
        await session.commit()


def image_bytes():
    image = Image.new("RGB", (640, 480), (20, 40, 60))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


class Provider:
    def __init__(self, *, fail_camera=False):
        self.fail_camera = fail_camera
        self.calls = []
    async def configured(self):
        return True
    async def snapshot(self, camera_id, **kwargs):
        self.calls.append(("camera", camera_id))
        if self.fail_camera:
            raise ValueError("Synthetic camera unavailable")
        return ProtectMedia(image_bytes(), "image/png")
    async def event_thumbnail(self, identity, **kwargs):
        self.calls.append(("thumbnail", identity))
        return ProtectMedia(image_bytes(), "image/png")


def stages(provider=None, *, factory=AsyncSessionLocal, publisher=None):
    return AccessCompletionStages(visual=VehicleVisualDetectionRecorder(), snapshots=SnapshotManager(),
        protect=provider or Provider(), session_factory=factory,
        publisher=publisher or SimpleNamespace(publish=AsyncMock(return_value=RealtimePublishReceipt("in_process", True))))


async def seed(stage="snapshot", *, age=0, raw=None, anomaly=False):
    async with AsyncSessionLocal() as session:
        now = await completion_store.database_now(session)
        origin = now-timedelta(seconds=age)
        payload = raw if raw is not None else {"best": {"eventId": "exact-event", "_iacs_smart_zone_evidence": {
            "zone_statuses": [{"zone_id": "2", "status": "enter"}, {"zone_id": "3", "status": "exit"}],
            "smart_zones": [2, 3], "time_of_day": "night"}}}
        event = AccessEvent(id=uuid.uuid4(), registration_number="AB12CDE", confidence=.9, source="synthetic",
            occurred_at=origin, resource_revision=1, direction=AccessDirection.ENTRY, decision=AccessDecision.DENIED, raw_payload=payload)
        session.add(event)
        await session.flush()
        if anomaly:
            session.add(Anomaly(event_id=event.id, anomaly_type=AnomalyType.UNAUTHORIZED_PLATE,
                severity=AnomalySeverity.WARNING, message="Synthetic", context={"retained": True}))
        await reserve_external_completion_in_session(session, event_id=event.id,
            origin=ExternalCompletionOrigin(uuid.uuid4(), origin))
        await session.execute(text("UPDATE access_completion_work SET state='skipped' WHERE event_id=:id AND stage!=:stage"),
                              {"id": event.id, "stage": stage})
        await session.commit()
    claim = (await AccessCompletionStore().claim(1))[0]
    assert claim.stage == stage
    return claim


async def state(claim):
    async with AsyncSessionLocal() as session:
        return await session.get(AccessEvent, claim.event_id), await session.get(AccessCompletionWork, claim.work_id)


async def artifact(claim):
    metadata = await SnapshotManager().store_image(image_bytes(),
        relative_path=access_event_snapshot_relative_path(claim.event_id, claim_token=claim.token),
        url=access_event_snapshot_url(claim.event_id), camera="synthetic")
    return PreparedAccessSnapshot(claim.event_id, claim.token, metadata, "camera_snapshot")


@pytest.mark.parametrize("commit", [False, True])
async def test_snapshot_columns_anomaly_receipt_revision_and_publication_are_atomic(commit):
    claim = await seed(anomaly=True)
    prepared = await artifact(claim)
    async with access_session(AsyncSessionLocal) as session:
        await accept_snapshot_in_session(session, claim, prepared)
        async with AsyncSessionLocal() as other:
            before = await other.get(AccessEvent, claim.event_id)
            assert before.snapshot_path is None and before.resource_revision == 1
            assert (await other.get(AccessCompletionWork, claim.work_id)).state == "leased"
        if commit:
            await session.commit()
        else:
            await session.rollback()
    event, work = await state(claim)
    async with AsyncSessionLocal() as session:
        anomaly = await session.scalar(select(Anomaly).where(Anomaly.event_id == event.id))
        publication_work = await session.scalar(select(AccessCompletionWork).where(
            AccessCompletionWork.event_id == event.id, AccessCompletionWork.stage == "publication"))
    assert (event.snapshot_path is not None) is commit
    assert event.resource_revision == (2 if commit else 1)
    assert (work.state == "succeeded") is commit
    assert ("snapshot" in anomaly.context) is commit
    assert (publication_work is not None) is commit
    assert anomaly.context["retained"] is True


async def test_final_acceptance_expiry_rolls_back_all_snapshot_effects(monkeypatch):
    claim = await seed(anomaly=True)
    prepared = await artifact(claim)
    async with AsyncSessionLocal() as session:
        await session.execute(text("UPDATE access_completion_work SET lease_until=clock_timestamp()+interval '80 milliseconds' WHERE id=:id"), {"id": claim.work_id})
        await session.commit()
    original = completion_media.mark_public_event_changed_in_session
    async def delayed(session, event_id):
        await original(session, event_id)
        await asyncio.sleep(.1)
    monkeypatch.setattr(completion_media, "mark_public_event_changed_in_session", delayed)
    with pytest.raises(PipelineLeaseLost):
        async with access_session(AsyncSessionLocal) as session:
            await accept_snapshot_in_session(session, claim, prepared)
            await session.commit()
    event, work = await state(claim)
    assert event.snapshot_path is None and event.resource_revision == 1 and work.state == "leased"


@pytest.mark.parametrize("age", [0, 6, 299])
async def test_camera_uses_original_receipt_five_seconds_then_exact_thumbnail(age):
    claim = await seed(age=age, raw={"best": {"eventId": "exact-bound", "webhook_trace": {"received_at": "2099-01-01T00:00:00Z"}}})
    provider = Provider()
    await stages(provider).snapshot(claim, asyncio.get_running_loop().time()+90)
    event, work = await state(claim)
    assert provider.calls == ([("camera", "camera.gate")] if age == 0 else [("thumbnail", "exact-bound")])
    assert work.state == "succeeded" and str(claim.token) in event.snapshot_path
    assert work.origin_at == claim.origin_at and work.deadline == claim.deadline


async def test_camera_error_uses_exact_thumbnail_without_plate_time_discovery():
    claim = await seed()
    provider = Provider(fail_camera=True)
    await stages(provider).snapshot(claim, asyncio.get_running_loop().time()+90)
    assert len(provider.calls) == 2 and provider.calls[1] == ("thumbnail", "exact-event")


async def test_no_exact_thumbnail_and_old_camera_skips_without_provider_call():
    claim = await seed(age=10, raw={"best": {}})
    provider = Provider()
    await stages(provider).snapshot(claim, asyncio.get_running_loop().time()+90)
    event, work = await state(claim)
    assert not provider.calls and work.state == "skipped" and event.snapshot_path is None


@pytest.mark.parametrize("stage", ["snapshot", "zone_shadow"])
async def test_missing_frozen_input_is_held_not_rebuilt_from_later_event_payload(stage):
    claim = await seed(stage)
    async with AsyncSessionLocal() as session:
        work = await session.get(AccessCompletionWork, claim.work_id)
        work.result = {"input": {"unexpected": "malformed"}}
        await session.commit()
    provider = Provider()
    await getattr(stages(provider), stage)(claim, asyncio.get_running_loop().time()+90)
    event, work = await state(claim)
    assert work.state == "held" and "input_unavailable" in work.outcome and not provider.calls


async def test_compact_snapshot_and_zone_input_are_immutable_and_avoid_raw_hydration():
    claim = await seed(raw={"unused": "x"*950000, "best": {"eventId": "first"}})
    statements = []
    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    sql_event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with access_session(AsyncSessionLocal) as session:
            prepared = await prepare_snapshot_in_session(session, claim)
    finally:
        sql_event.remove(engine.sync_engine, "before_cursor_execute", capture)
    assert prepared.protect_event_id == "first"
    assert all("raw_payload" not in query for query in statements)
    async with AsyncSessionLocal() as session:
        event = await session.get(AccessEvent, claim.event_id)
        event.raw_payload = {"best": {"eventId": "later"}}
        await session.commit()
    async with access_session(AsyncSessionLocal) as session:
        assert (await prepare_snapshot_in_session(session, claim)).protect_event_id == "first"


@pytest.mark.parametrize("outcome", ["before_commit", "commit_raises", "commit_applied_then_raises"])
async def test_artifact_cleanup_requires_settled_noncommit_and_retains_uncertain_outcome(monkeypatch, outcome):
    claim = await seed()
    owner = stages()
    prepared = await artifact(claim)
    monkeypatch.setattr(owner, "_capture", AsyncMock(return_value=prepared))
    if outcome == "before_commit":
        monkeypatch.setattr(completion_media, "accept_snapshot_in_session", AsyncMock(side_effect=RuntimeError("synthetic precommit")))
    else:
        actual = AsyncSession.commit
        async def commit(session):
            if outcome == "commit_applied_then_raises":
                await actual(session)
            raise RuntimeError("synthetic lost commit acknowledgement")
        monkeypatch.setattr(AsyncSession, "commit", commit)
    with pytest.raises(RuntimeError):
        await owner.snapshot(claim, asyncio.get_running_loop().time()+90)
    primary = settings.data_dir/prepared.metadata.relative_path
    archive = settings.data_dir/f"access-event-snapshot-archive/{claim.event_id}.{claim.token}.jpg"
    manifest = archive.with_suffix(".json")
    assert primary.exists() is (outcome != "before_commit")
    assert archive.exists() is (outcome != "before_commit")
    assert manifest.exists() is (outcome != "before_commit")
    event, work = await state(claim)
    assert (event.snapshot_path is not None) is (outcome == "commit_applied_then_raises")


async def test_old_claim_cleanup_cannot_delete_another_accepted_claim_or_legacy_files():
    old = await seed()
    own = await artifact(old)
    manager = SnapshotManager()
    legacy = await manager.store_image(image_bytes(), relative_path=access_event_snapshot_relative_path(old.event_id),
                                      url=access_event_snapshot_url(old.event_id), camera="synthetic")
    async with AsyncSessionLocal() as session:
        await session.execute(text("UPDATE access_completion_work SET lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"), {"id": old.work_id})
        await session.commit()
    new = (await AccessCompletionStore().claim(1))[0]
    accepted = await artifact(new)
    async with access_session(AsyncSessionLocal) as session:
        await accept_snapshot_in_session(session, new, accepted)
        await session.commit()
    with pytest.raises(PipelineLeaseLost):
        async with access_session(AsyncSessionLocal) as session:
            await accept_snapshot_in_session(session, old, own)
    await stages()._cleanup_unaccepted(old)
    assert not (settings.data_dir/own.metadata.relative_path).exists()
    assert manager.resolve_path(legacy.relative_path).is_file()
    assert manager.resolve_path(accepted.metadata.relative_path).is_file()
    assert (await state(new))[0].snapshot_path == accepted.metadata.relative_path


@pytest.mark.parametrize("delivery_fails", [False, True])
async def test_zone_rows_receipt_and_existing_feature_signal_commit_once(delivery_fails):
    claim = await seed("zone_shadow")
    calls = []
    async def publish(event_type, payload):
        async with AsyncSessionLocal() as session:
            assert (await session.get(AccessCompletionWork, claim.work_id)).state == "succeeded"
            assert await session.scalar(select(func.count()).select_from(LprZoneShadowObservation)) == 2
        calls.append((event_type, payload))
        if delivery_fails:
            raise RuntimeError("Synthetic observer unavailable")
        return RealtimePublishReceipt("in_process", True)
    owner = stages(publisher=SimpleNamespace(publish=publish))
    await owner.zone_shadow(claim, asyncio.get_running_loop().time()+90)
    assert len(calls) == 2 and all(kind == "lpr_zone_shadow.observed" for kind, _ in calls)
    assert all(payload["access_event_id"] == str(claim.event_id) for _, payload in calls)
    with pytest.raises(PipelineLeaseLost):
        await owner.zone_shadow(claim, asyncio.get_running_loop().time()+90)
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(LprZoneShadowObservation)) == 2


async def test_startup_recovery_excludes_owned_events_before_whole_event_or_provider_fetch(monkeypatch):
    await seed()
    provider = Provider()
    monkeypatch.setattr(snapshot_recovery, "get_unifi_protect_service", lambda: provider)
    monkeypatch.setattr(snapshot_recovery, "get_snapshot_manager", SnapshotManager)
    result = await snapshot_recovery.recover_missing_access_event_snapshots()
    assert result.scanned == 0 and not provider.calls


@pytest.mark.parametrize("owner", ["completion", "snapshot", "publication", "information"])
async def test_db_response_delay_counts_against_original_start_deadline(monkeypatch, owner):
    claim = await seed("visual" if owner == "information" else "snapshot")
    module = {"completion": completion_store, "snapshot": completion_media,
              "publication": publication, "information": vehicle_information_jobs}[owner]
    async with AsyncSessionLocal() as session:
        now = await completion_store.database_now(session)
        if owner == "publication":
            await reserve_initial_publication_in_session(session, claim.event_id)
        if owner == "information":
            session.add(VehicleInformationJob(event_id=claim.event_id, registration_number="AB12CDE",
                created_at=now, origin_at=claim.origin_at, deadline=claim.deadline, readiness="committed_decision", status="processing",
                lease_token=claim.token, lease_until=now+timedelta(seconds=120)))
        await session.commit()
    if owner == "publication":
        claim = await AccessPublicationStore().claim()
    original = module.database_now
    sampled = []
    async def delayed(session):
        now = await original(session)
        sampled.append(asyncio.get_running_loop().time())
        await asyncio.sleep(.03)
        return now
    monkeypatch.setattr(module, "database_now", delayed)
    if owner == "snapshot":
        async with access_session(AsyncSessionLocal) as session:
            prepared = await prepare_snapshot_in_session(session, claim)
        assert prepared.sampled_monotonic <= sampled[-1]
    elif owner == "completion":
        deadline = await AccessCompletionStore().assert_owned(claim)
        assert deadline <= sampled[-1]+90
    elif owner == "publication":
        deadline = await AccessPublicationStore().assert_owned(claim)
        assert deadline <= sampled[-1]+120
    else:
        _, deadline = await vehicle_information_jobs.VehicleInformationWorker()._prepare(claim.event_id, claim.token, "AB12CDE")
        assert deadline <= sampled[-1]+90


@pytest.mark.parametrize("stage", ["visual", "snapshot"])
async def test_pre_io_close_exhaustion_never_starts_optional_provider(monkeypatch, stage):
    claim = await seed(stage, age=299)
    provider = Provider()
    visual = AsyncMock()
    owner = stages(provider)
    monkeypatch.setattr(owner.visual_recorder, "recent_match", visual)
    actual = AsyncSession.close
    async def delayed(session):
        await actual(session)
        await asyncio.sleep(1.1)
    monkeypatch.setattr(AsyncSession, "close", delayed)
    with pytest.raises((PipelineLeaseLost, TimeoutError)):
        await getattr(owner, stage)(claim, asyncio.get_running_loop().time()+90)
    assert not provider.calls
    visual.assert_not_awaited()


async def test_information_real_prepare_close_consumes_original_deadline_without_lookup(monkeypatch):
    claim = await seed("visual", age=299)
    async with AsyncSessionLocal() as session:
        now = await completion_store.database_now(session)
        session.add(VehicleInformationJob(event_id=claim.event_id, registration_number="AB12CDE", created_at=now,
            origin_at=claim.origin_at, deadline=claim.deadline, readiness="committed_decision", status="processing",
            lease_token=claim.token, lease_until=now+timedelta(seconds=120)))
        await session.commit()
    actual = AsyncSession.close
    async def delayed(session):
        await actual(session)
        await asyncio.sleep(1.1)
    monkeypatch.setattr(AsyncSession, "close", delayed)
    worker = vehicle_information_jobs.VehicleInformationWorker()
    prepared, deadline = await worker._prepare(claim.event_id, claim.token, "AB12CDE")
    lookup = AsyncMock()
    monkeypatch.setattr(vehicle_information_jobs, "get_vehicle_information_service", lambda: SimpleNamespace(lookup=lookup))
    with pytest.raises(TimeoutError):
        await worker._execute(prepared, deadline, SimpleNamespace())
    lookup.assert_not_awaited()


async def test_publication_real_owned_lease_close_delay_prevents_transport_send(monkeypatch):
    claim = await seed()
    async with AsyncSessionLocal() as session:
        await reserve_initial_publication_in_session(session, claim.event_id)
        await session.commit()
    store = AccessPublicationStore()
    picked = await store.claim()
    async with AsyncSessionLocal() as session:
        await session.execute(text("UPDATE access_completion_work SET lease_until=clock_timestamp()+interval '150 milliseconds' WHERE id=:id"), {"id": picked.work_id})
        await session.commit()
    monkeypatch.setattr(store, "load_message", AsyncMock(return_value=PublicationMessage(1, "access_event.finalized", {})))
    actual = AsyncSession.close
    async def delayed(session):
        await actual(session)
        await asyncio.sleep(.2)
    monkeypatch.setattr(AsyncSession, "close", delayed)
    publish = AsyncMock()
    with pytest.raises(PipelineLeaseLost):
        await AccessPublicationLane(store, SimpleNamespace(publish=publish))._send(picked)
    publish.assert_not_awaited()


@pytest.mark.parametrize("stage", ["visual", "snapshot"])
async def test_fresh_stage_input_cannot_renew_original_outer_operation_budget(monkeypatch, stage):
    claim = await seed(stage)
    provider = Provider()
    owner = stages(provider)
    visual = AsyncMock()
    monkeypatch.setattr(owner.visual_recorder, "recent_match", visual)
    actual = AsyncSession.close
    async def delayed(session):
        await actual(session)
        await asyncio.sleep(.1)
    monkeypatch.setattr(AsyncSession, "close", delayed)
    with pytest.raises(TimeoutError):
        await getattr(owner, stage)(claim, asyncio.get_running_loop().time()+.05)
    assert not provider.calls
    visual.assert_not_awaited()


async def test_current_registration_change_cannot_begin_snapshot_or_visual_io(monkeypatch):
    claim = await seed()
    async with AsyncSessionLocal() as session:
        await session.execute(text("UPDATE access_events SET registration_number='CHANGED' WHERE id=:id"), {"id": claim.event_id})
        await session.commit()
    provider = Provider()
    with pytest.raises(PipelineLeaseLost):
        await stages(provider).snapshot(claim, asyncio.get_running_loop().time()+90)
    assert not provider.calls and (await state(claim))[0].snapshot_path is None


async def test_camera_metadata_keeps_provider_receipt_time_through_delayed_database_proof(monkeypatch):
    from datetime import UTC, datetime
    claim = await seed()
    provider = Provider()
    owner = stages(provider)
    proof_after = []
    original = owner._camera_returned_in_time
    async def delayed(claim):
        await asyncio.sleep(.1)
        proof_after.append(datetime.now(UTC))
        return await original(claim)
    monkeypatch.setattr(owner, "_camera_returned_in_time", delayed)
    await owner.snapshot(claim, asyncio.get_running_loop().time()+90)
    event, work = await state(claim)
    assert event.snapshot_captured_at < proof_after[0]
    assert provider.calls == [("camera", "camera.gate")] and work.state == "succeeded"
