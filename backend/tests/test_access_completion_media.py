"""Snapshot thread/filesystem and compact-read parity without provider access."""

import asyncio
import io
import json
import threading
import uuid
from datetime import UTC, datetime

import pytest
from PIL import Image

from app.core.config import settings
from app.modules.lpr.base import PlateRead
from app.services.access.completion_media import AccessCompletionStages, SnapshotInput
from app.services.access.snapshot_identity import protect_event_id_from_payload
from app.services.lpr_zone_shadow import (
    build_zone_shadow_rows,
    compact_zone_shadow_read,
    serialize_lpr_zone_shadow_observation,
    zone_shadow_read_from_payload,
)
from app.services.snapshots import (
    SnapshotManager,
    access_event_snapshot_relative_path,
    access_event_snapshot_url,
)
from app.services.vehicle_visual_detections import VehicleVisualDetectionRecorder


def image_bytes():
    image = Image.new("RGB", (640, 480), (50, 100, 150))
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


@pytest.mark.asyncio
async def test_claim_artifact_primary_archive_manifest_restore_and_legacy_coexist(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    manager = SnapshotManager()
    event, first, second = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    stored = []
    for token in (None, first, second):
        stored.append(await manager.store_image(image_bytes(),
            relative_path=access_event_snapshot_relative_path(event, claim_token=token),
            url=access_event_snapshot_url(event), camera="synthetic"))
    for metadata in stored:
        primary = manager.resolve_path(metadata.relative_path)
        original = primary.read_bytes()
        primary.unlink()
        assert manager.resolve_path(metadata.relative_path).read_bytes() == original
        assert metadata.url == access_event_snapshot_url(event)
    await manager.delete_access_snapshot_claim(event, first)
    assert not (tmp_path / stored[1].relative_path).exists()
    assert not (tmp_path / f"access-event-snapshot-archive/{event}.{first}.jpg").exists()
    assert not (tmp_path / f"access-event-snapshot-archive/{event}.{first}.json").exists()
    assert manager.resolve_path(stored[0].relative_path).is_file()
    assert manager.resolve_path(stored[2].relative_path).is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["compact", "primary", "archive"])
async def test_actual_snapshot_thread_settles_before_cancellation_and_exact_cleanup(tmp_path, monkeypatch, stage):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    manager = SnapshotManager()
    event, token = uuid.uuid4(), uuid.uuid4()
    relative = access_event_snapshot_relative_path(event, claim_token=token)
    started, release = threading.Event(), threading.Event()
    if stage == "compact":
        original = manager._compact_jpeg
        def blocked(*args):
            started.set(); release.wait(); return original(*args)
        monkeypatch.setattr(manager, "_compact_jpeg", blocked)
    elif stage == "primary":
        original = manager._write_snapshot
        def blocked(*args):
            if str(args[0]).endswith(relative):
                started.set(); release.wait()
            return original(*args)
        monkeypatch.setattr(manager, "_write_snapshot", blocked)
    else:
        original = manager._write_access_event_archive
        def blocked(*args):
            started.set(); release.wait(); return original(*args)
        monkeypatch.setattr(manager, "_write_access_event_archive", blocked)
    task = asyncio.create_task(manager.store_image(image_bytes(), relative_path=relative,
        url=access_event_snapshot_url(event), camera="synthetic"))
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel(); await asyncio.sleep(0); task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await manager.delete_access_snapshot_claim(event, token)
    assert not (tmp_path / relative).exists()
    assert not (tmp_path / f"access-event-snapshot-archive/{event}.{token}.jpg").exists()
    assert not (tmp_path / f"access-event-snapshot-archive/{event}.{token}.json").exists()
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()
                and task.get_name() == "access-snapshot-thread"]


@pytest.mark.asyncio
async def test_camera_timeout_keeps_actual_provider_cleanup_owned_after_repeated_cancel():
    stages = AccessCompletionStages(visual=VehicleVisualDetectionRecorder(), snapshots=SnapshotManager(), protect=None)
    cleaning, release = asyncio.Event(), asyncio.Event()
    cancellations = 0
    async def provider():
        nonlocal cancellations
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations += 1
            cleaning.set()
            await release.wait()
            raise
    task = asyncio.create_task(stages._provider_media(provider, deadline=asyncio.get_running_loop().time()+0.01))
    await cleaning.wait()
    task.cancel(); await asyncio.sleep(0); task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done() and cancellations == 1
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancellations == 1


@pytest.mark.asyncio
async def test_snapshot_budget_counts_actual_session_close_elapsed_time():
    now = datetime.now(UTC)
    prepared = SnapshotInput(uuid.uuid4(), "AB12CDE", now, "exact", 2,
                             asyncio.get_running_loop().time() - 3, asyncio.get_running_loop().time()+120)
    assert prepared.remaining_camera_budget() == 0


@pytest.mark.parametrize("mode", ["shadow", "live", "unexpected"])
@pytest.mark.parametrize("by_path", [False, True])
def test_compact_zone_input_preserves_exact_mapper_output_and_recursive_id_order(mode, by_path):
    raw = {
        "unused_vendor_blob": "x" * 900_000,
        "nested": {"eventPath": "/event/path-first"} if by_path else {"event_id": "nested-first"},
        "alarm": {"eventId": "alarm-second"} if not by_path else {},
        "registrationNumber": "ab 12 cde",
        "_iacs_known_vehicle_plate_match": {"detected_registration_number": "AB12CDF"},
        "_iacs_smart_zone_evidence": {
            "zone_statuses": [{"zone_id": "2", "status": "exit", "level": 0.5},
                              {"zone_id": "3", "status": "enter"}],
            "smart_zones": [2, 3], "raw_smart_zones": ["driveway", "other"],
            "time_of_day": "night", "time_of_day_source": "fixture",
            "unused_metadata": "y" * 100_000,
            "smart_zone_evidence": {"camera_id": "one", "camera_name": "Gate",
                                    "camera_identifier": "camera.gate", "unused": "z" * 100_000},
        },
    }
    read = PlateRead("AB12CDE", 0.9, "synthetic", datetime.now(UTC), raw)
    compact = compact_zone_shadow_read(read)
    decoded = zone_shadow_read_from_payload(compact)
    args = {"access_event_id": uuid.uuid4(), "actual_decision": "denied", "actual_direction": "entry",
            "vehicle_id": uuid.uuid4(), "mode": mode}
    original = [serialize_lpr_zone_shadow_observation(row) for row in build_zone_shadow_rows(read, **args)]
    projected = [serialize_lpr_zone_shadow_observation(row) for row in build_zone_shadow_rows(decoded, **args)]
    assert original == projected
    assert len(json.dumps(compact)) < 2_000 and len(json.dumps(raw)) > 1_000_000
    assert "unused" not in json.dumps(compact)


@pytest.mark.parametrize("payload", [{}, {"best": {"eventId": "root", "alarm": {"event_id": "alarm", "triggers": [{"eventId": "trigger"}]}}},
    {"vehicle_session": {"protect_event_ids": ["session"]}}, {"protect_evidence": {"event_id": "evidence"}, "best": {"eventId": "best"}}])
def test_pure_snapshot_decoder_preserves_established_exact_order(payload):
    expected = ("trigger" if payload.get("best", {}).get("alarm") else
                "best" if payload.get("best") else "session" if payload.get("vehicle_session") else None)
    assert protect_event_id_from_payload(payload) == expected


@pytest.mark.parametrize("value", [None, [], {}, {"source": "synthetic"}])
def test_malformed_compact_zone_input_returns_no_read(value):
    assert zone_shadow_read_from_payload(value) is None


@pytest.mark.asyncio
async def test_delayed_actual_camera_dispatch_cannot_start_after_absolute_deadline(monkeypatch):
    import time
    stages = AccessCompletionStages(visual=VehicleVisualDetectionRecorder(), snapshots=SnapshotManager(), protect=None)
    original = asyncio.create_task
    invoked = []
    def delayed(coroutine, **kwargs):
        task = original(coroutine, **kwargs)
        # Child is ready before timeout_at installs its already-due callback.
        time.sleep(.02)
        return task
    async def forbidden():
        invoked.append(True)
        raise AssertionError("Expired camera start")
    monkeypatch.setattr(asyncio, "create_task", delayed)
    with pytest.raises(TimeoutError):
        await stages._provider_media(forbidden, deadline=asyncio.get_running_loop().time()+.01)
    assert not invoked


@pytest.mark.asyncio
async def test_hydrated_alert_uses_exact_claim_path_and_legacy_deletion_cannot_remove_it(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.services.snapshots import alert_snapshot_metadata, alert_snapshot_path
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    event_id, claim = uuid.uuid4(), uuid.uuid4()
    manager = SnapshotManager()
    metadata = await manager.store_image(image_bytes(),
        relative_path=access_event_snapshot_relative_path(event_id, claim_token=claim),
        url=access_event_snapshot_url(event_id), camera="synthetic")
    event = SimpleNamespace(id=event_id, snapshot_path=metadata.relative_path,
        snapshot_content_type=metadata.content_type, snapshot_bytes=metadata.bytes,
        snapshot_width=metadata.width, snapshot_height=metadata.height,
        snapshot_camera=metadata.camera, snapshot_captured_at=metadata.captured_at)
    alert = SimpleNamespace(id=uuid.uuid4(), event=event,
        context={"snapshot":{"url":metadata.url},"keep":"retained"})
    assert alert_snapshot_metadata(alert)["url"] == metadata.url
    legacy = alert_snapshot_path(alert.id)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(image_bytes())
    manager.delete_alert_snapshot(alert)
    assert not legacy.exists() and (tmp_path/metadata.relative_path).exists()
    assert alert.context == {"keep":"retained"}


def test_relevant_zone_arrays_remain_complete_and_are_not_claimed_to_have_tiny_fixed_size():
    values = [{"zone_id":str(index),"status":"enter","level":.5} for index in range(5000)]
    read = PlateRead("AB12CDE", .9, "synthetic", datetime.now(UTC),
        {"_iacs_smart_zone_evidence":{"zone_statuses":values}})
    compact = compact_zone_shadow_read(read)
    decoded = zone_shadow_read_from_payload(compact)
    assert len(compact["raw_payload"]["_iacs_smart_zone_evidence"]["zone_statuses"]) == 5000
    assert 100000 < len(json.dumps(compact)) < 1000000
    assert len(build_zone_shadow_rows(decoded, access_event_id=uuid.uuid4(),
        actual_decision="denied", actual_direction="entry", mode="shadow")) == 5000


@pytest.mark.asyncio
async def test_visual_actual_child_cleanup_survives_deadline_and_repeated_cancellation(monkeypatch):
    recorder = VehicleVisualDetectionRecorder()
    owner = AccessCompletionStages(visual=recorder, snapshots=SnapshotManager(), protect=None)
    cleaning, release = asyncio.Event(), asyncio.Event()
    cancellations = 0
    async def match(*args, **kwargs):
        nonlocal cancellations
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations += 1
            cleaning.set()
            await release.wait()
            raise
    monkeypatch.setattr(recorder, "recent_match", match)
    task = asyncio.create_task(owner._visual_match("AB12CDE", datetime.now(UTC),
                                                 asyncio.get_running_loop().time()+.01))
    await cleaning.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(.01)
    assert not task.done() and cancellations == 1
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancellations == 1
