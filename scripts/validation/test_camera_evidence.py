"""Real PostgreSQL access re-resolution; no camera, LLM, actuator or other live I/O.

Collection first requires the existing loopback-only synthetic recovery namespace.
"""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select, text

from app.db.session import AsyncSessionLocal, engine
from app.models import AccessEvent, Person, Presence, SystemSetting, Vehicle
from app.models.enums import AccessDecision, AccessDirection, PresenceState
from app.modules.lpr.base import PlateRead
from app.services.access import execution as execution_owner
from app.services.access.evidence import AccessEvidenceResolver
from app.services.access.execution import AccessExecution
from app.services.access.reads import GATE_OBSERVATION_PAYLOAD_KEY
from app.services.lpr_ingest import LprIngestRepository
from access_window_fixture import claimed_window
from app.services.movement.sessions import MovementSessionService
from app.services.movement_ledger import get_movement_ledger_repository
from app.services.settings import get_runtime_config_for_session

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


class Trace:
    trace_id = "synthetic-camera-handoff"

    def start_span(self, *args, **kwargs):
        return SimpleNamespace(finish=lambda **kwargs: None)


@pytest.mark.parametrize("mutation", ["unchanged", "owner", "presence", "deactivated", "runtime_policy"])
async def test_access_execution_re_resolves_current_facts_after_camera_transaction_ends(monkeypatch, mutation):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE lpr_coordinator_state, lpr_ingest_events CASCADE"))
        original = Person(display_name="Synthetic Original", is_active=True)
        other = Person(display_name="Synthetic Other", is_active=True)
        session.add_all([original, other])
        await session.flush()
        vehicle = Vehicle(registration_number="SYN123", person_id=original.id, is_active=True)
        session.add(vehicle)
        for person in (original, other):
            session.add(Presence(person_id=person.id, state=PresenceState.PRESENT,
                                 last_changed_at=NOW - timedelta(minutes=1)))
        await session.commit()
        original_id, other_id, vehicle_id = original.id, other.id, vehicle.id
        runtime = await get_runtime_config_for_session(session)
    assert runtime.schedule_default_policy == "allow"
    read = PlateRead("SYN123", 0.99, "synthetic-camera-handoff", NOW, {
        "cameraId": "synthetic-camera", "deviceId": "synthetic-device",
        GATE_OBSERVATION_PAYLOAD_KEY: {"state": "closed", "observed_at": NOW.isoformat()},
    })
    entered_camera = asyncio.Event()
    release_camera = asyncio.Event()
    camera_calls = []

    async def inert_camera(self, observation, person, *, trace=None):
        # No probe/preflight transaction or checked-out connection may span this await.
        assert engine.pool.checkedout() == 0
        camera_calls.append((observation, person.id))
        entered_camera.set()
        await release_camera.wait()
        return {"direction": "exit", "confidence": 0.99, "reason": "Synthetic paused evidence"}

    monkeypatch.setattr(AccessEvidenceResolver, "_resolve_duplicate_arrival_with_camera", inert_camera)
    # Exercise the full access transaction and finalizer, with one inert actuator boundary.
    hardware = AsyncMock(return_value=SimpleNamespace(command_id=None, accepted=False))
    monkeypatch.setattr(execution_owner, "open_gate_for_access_event", hardware)
    service = AccessExecution(get_movement_ledger_repository(), MovementSessionService())
    async def execute():
        async with claimed_window(read) as (window, claim):
            return await service.execute(window, claim=claim, read=read, direction_read=read,
                runtime=runtime, trace=Trace(), finalize_started_at=NOW, webhook_trace={})
    task = asyncio.create_task(execute())
    camera_started = asyncio.create_task(entered_camera.wait())
    try:
        done, _ = await asyncio.wait({task, camera_started}, timeout=8, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task  # Preserve an early owner failure instead of masking it as a waiter timeout.
        assert camera_started in done, "Camera probe did not reach its inert boundary"
        async with AsyncSessionLocal() as session:
            if mutation == "owner":
                (await session.get(Vehicle, vehicle_id)).person_id = other_id
            elif mutation == "presence":
                (await session.get(Presence, original_id)).state = PresenceState.EXITED
            elif mutation == "deactivated":
                (await session.get(Person, original_id)).is_active = False
            elif mutation == "runtime_policy":
                session.add(SystemSetting(key="schedule_default_policy", category="access", value={"plain": "deny"}, is_secret=False))
            await session.commit()
        release_camera.set()
        result = await asyncio.wait_for(task, 8)
        assert result is not None
        assert camera_calls == [(read, original_id)]
        async with AsyncSessionLocal() as session:
            durable = await session.get(AccessEvent, result)
            presence = await session.get(Presence, durable.person_id)
            if mutation in {"deactivated", "runtime_policy"}:
                assert durable.decision == AccessDecision.DENIED and durable.direction == AccessDirection.DENIED
                hardware.assert_not_awaited()
                assert presence.last_event_id != durable.id
            elif mutation == "unchanged":
                assert durable.decision == AccessDecision.GRANTED and durable.direction == AccessDirection.EXIT
                assert presence.state == PresenceState.EXITED and presence.last_event_id == durable.id
                hardware.assert_not_awaited()
            else:
                assert durable.decision == AccessDecision.GRANTED and durable.direction == AccessDirection.ENTRY
                assert durable.person_id == (other_id if mutation == "owner" else original_id)
                assert durable.raw_payload["direction_resolution"]["source"] == "gate_state"
                hardware.assert_awaited_once()
                assert presence.last_event_id != durable.id  # Inert sink has no admission evidence.
            if mutation == "runtime_policy":
                assert durable.raw_payload["schedule"]["allowed"] is False
                assert (await get_runtime_config_for_session(session)).schedule_default_policy == "deny"
            assert len(list((await session.scalars(select(AccessEvent))).all())) == 1
    finally:
        release_camera.set()
        if not task.done():
            task.cancel()
        if not camera_started.done():
            camera_started.cancel()
        await asyncio.gather(task, camera_started, return_exceptions=True)
        # Restore the synthetic fixture prerequisite even when an assertion fails.
        async with AsyncSessionLocal() as session:
            await session.execute(delete(SystemSetting).where(SystemSetting.key == "schedule_default_policy"))
            await session.commit()
