"""Camera authority expires independently of owned cancellation cleanup."""

import asyncio
import threading
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.lpr.base import PlateRead
from app.services.access import evidence as owner


class EmptySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def close(self):
        return None

    async def execute(self, statement):
        return SimpleNamespace(first=lambda: SimpleNamespace(id=uuid.UUID(int=1),
            missed_exit_recovery_enabled=True, state=None))


def recovery_probe(monkeypatch):
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Synthetic recovery")
    read = PlateRead("SYN123", 0.99, "synthetic-camera", datetime.now(UTC), {})
    resolver = owner.AccessEvidenceResolver(SimpleNamespace(site_timezone="UTC", missed_exit_recovery_enabled=True))

    async def resolve(*args, **kwargs):
        raise owner.CameraEvidenceRequired(person, recovery=True)

    monkeypatch.setattr(owner, "AsyncSessionLocal", EmptySession)
    monkeypatch.setattr(resolver, "resolve", resolve)
    return resolver, read


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_parent", [False, True])
async def test_recovery_camera_drains_single_cancel_and_discards_late_success(monkeypatch, cancel_parent):
    resolver, read = recovery_probe(monkeypatch)
    entered, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancellations = 0

    async def camera(*args, **kwargs):
        nonlocal cancellations
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations += 1
            cancelling.set()
            await release.wait()
            return {"direction": "exit", "confidence": 1.0, "reason": "late success"}

    monkeypatch.setattr(resolver, "_resolve_duplicate_arrival_with_camera", camera)
    task = asyncio.create_task(resolver.prepare_camera_evidence(read, read, None, None))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if cancel_parent:
            task.cancel()
        await asyncio.wait_for(cancelling.wait(), 6)
        assert not task.done()
        if cancel_parent:
            task.cancel()  # Repeated shutdown cannot detach or recancel the child.
            await asyncio.sleep(0)
        assert cancellations == 1
        assert not task.done()
        release.set()
        if cancel_parent:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await task
            assert result.decision["direction"] == "unknown"
            assert result.decision["confidence"] == 0.0
            assert result.decision["reason"] == "camera_timeout"
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_camera_cancellation_waits_for_actual_artifact_thread(monkeypatch):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def write():
        entered.set()
        release.wait(10)
        finished.set()
        return {"path": "synthetic-artifact"}

    async def store(*args, **kwargs):
        return await asyncio.to_thread(write)

    runtime = SimpleNamespace(openai_model="synthetic", unifi_protect_snapshot_width=1,
                              unifi_protect_snapshot_height=1, site_timezone="UTC")
    resolver = owner.AccessEvidenceResolver(runtime)
    read = PlateRead("SYN123", 0.99, "synthetic-camera", datetime.now(UTC), {})
    trace = SimpleNamespace(trace_id="synthetic", start_span=lambda *a, **kw:
                            SimpleNamespace(span_id="synthetic", finish=lambda **kw: None))
    provider = AsyncMock(return_value=SimpleNamespace(content=b"synthetic", content_type="image/jpeg"))
    analysis = AsyncMock()
    monkeypatch.setattr(owner, "get_unifi_protect_service", lambda: SimpleNamespace(snapshot=provider))
    monkeypatch.setattr(owner.telemetry, "store_artifact", store)
    monkeypatch.setattr(owner, "analyze_image_with_provider", analysis)
    task = asyncio.create_task(resolver._resolve_duplicate_arrival_with_camera(
        read, SimpleNamespace(id=uuid.uuid4(), display_name="Synthetic"), trace=trace))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        analysis.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_recovery_camera_discards_done_result_when_timeout_resume_is_late(monkeypatch):
    resolver, read = recovery_probe(monkeypatch)
    clock = [10.0]
    monkeypatch.setattr(owner, 'time', SimpleNamespace(monotonic=lambda: clock[0]))

    async def camera(*args, **kwargs):
        return {'direction': 'exit', 'confidence': 1.0, 'reason': 'completed late'}

    async def delayed_wait(tasks, *, timeout):
        assert timeout == 5
        await asyncio.gather(*tasks)
        # The loop can resume the waiter after the camera task has completed,
        # even though the authority deadline expired before result selection.
        clock[0] = 15.001
        return tasks, set()

    monkeypatch.setattr(resolver, '_resolve_duplicate_arrival_with_camera', camera)
    monkeypatch.setattr(owner.asyncio, 'wait', delayed_wait)
    result = await resolver.prepare_camera_evidence(read, read, None, None)
    assert result.decision['reason'] == 'camera_timeout'
    assert result.decision['direction'] == 'unknown' and result.decision['confidence'] == 0
