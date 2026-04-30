from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.api.v1 import events as events_api
from app.api.v1.events import (
    AlertActionRequest,
    _apply_alert_action,
    _serialize_event,
    _serialize_alert,
    _serialize_alerts,
    action_alerts,
    alert_snapshot,
    event_snapshot,
)
from app.models import AccessEvent, Anomaly, User
from app.models.enums import (
    AccessDecision,
    AccessDirection,
    AnomalySeverity,
    AnomalyType,
    TimingClassification,
)
from app.services.access_events import AccessEventService
from app.services.alert_snapshots import delete_alert_snapshot
from app.services.snapshots import access_event_snapshot_relative_path


def anomaly(registration_number: str, created_at: datetime) -> Anomaly:
    return Anomaly(
        id=uuid4(),
        anomaly_type=AnomalyType.UNAUTHORIZED_PLATE,
        severity=AnomalySeverity.WARNING,
        message="Unauthorised Plate, Access Denied",
        context={"registration_number": registration_number},
        created_at=created_at,
    )


class FakeScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def all(self) -> list[object]:
        return self.rows


class FakeAlertSession:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows
        self.committed = False
        self.flushed = False

    async def scalars(self, _query: object) -> FakeScalarResult:
        return FakeScalarResult(self.rows)

    async def get(self, _model: object, row_id: object) -> object | None:
        return next((row for row in self.rows if getattr(row, "id", None) == row_id), None)

    async def flush(self) -> None:
        self.flushed = True

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_unknown_plate_anomaly_is_warning() -> None:
    service = AccessEventService()
    event_id = uuid4()
    event = AccessEvent(
        id=event_id,
        registration_number="BK26MKF",
        direction=AccessDirection.DENIED,
        decision=AccessDecision.DENIED,
        confidence=0.91,
        source="test",
        occurred_at=datetime(2026, 4, 27, 18, 24, tzinfo=UTC),
        timing_classification=TimingClassification.UNKNOWN,
        snapshot_path=access_event_snapshot_relative_path(event_id),
        snapshot_content_type="image/jpeg",
        snapshot_bytes=4096,
        snapshot_width=320,
        snapshot_height=180,
        snapshot_captured_at=datetime(2026, 4, 27, 18, 24, 1, tzinfo=UTC),
        snapshot_camera="camera.gate",
    )

    rows = await service._build_anomalies(SimpleNamespace(), event, None, None, allowed=False)

    assert len(rows) == 1
    assert rows[0].anomaly_type == AnomalyType.UNAUTHORIZED_PLATE
    assert rows[0].severity == AnomalySeverity.WARNING
    assert rows[0].message == "Unauthorised Plate, Access Denied"
    assert rows[0].context["snapshot"]["url"] == f"/api/v1/events/{event_id}/snapshot"
    assert rows[0].context["snapshot"]["bytes"] == 4096


def test_open_unknown_plate_alerts_group_by_plate_and_local_day() -> None:
    first = datetime(2026, 4, 27, 18, 24, tzinfo=UTC)
    second = first + timedelta(hours=2)
    rows = [
        anomaly("BK26MKF", first),
        anomaly("BK26MKF", second),
        anomaly("ZZ99ZZZ", second),
    ]
    rows[1].context = {
        **rows[1].context,
        "snapshot": {
            "url": f"/api/v1/alerts/{rows[1].id}/snapshot",
            "captured_at": second.isoformat(),
            "bytes": 4096,
        },
    }

    payload = _serialize_alerts(rows, ZoneInfo("Europe/London"))

    grouped = [
        item for item in payload
        if item["grouped"] and item["registration_number"] == "BK26MKF"
    ]
    assert len(grouped) == 1
    assert grouped[0]["count"] == 2
    assert grouped[0]["severity"] == "warning"
    assert grouped[0]["message"] == "Unauthorised Plate, Access Denied"
    assert grouped[0]["local_date"] == "2026-04-27"
    assert len(grouped[0]["alert_ids"]) == 2
    assert grouped[0]["snapshot_url"] == f"/api/v1/alerts/{rows[1].id}/snapshot"
    assert grouped[0]["snapshot_bytes"] == 4096


def test_resolved_alert_serialization_retains_snapshot_metadata() -> None:
    row = anomaly("BK26MKF", datetime(2026, 4, 27, 18, 24, tzinfo=UTC))
    row.resolved_at = datetime(2026, 4, 27, 19, 0, tzinfo=UTC)
    row.context = {
        **(row.context or {}),
        "snapshot": {
            "url": f"/api/v1/alerts/{row.id}/snapshot",
            "captured_at": row.created_at.isoformat(),
            "bytes": 4096,
        },
    }

    payload = _serialize_alert(row)

    assert payload["status"] == "resolved"
    assert payload["snapshot_url"] == f"/api/v1/alerts/{row.id}/snapshot"
    assert payload["snapshot_captured_at"] == row.created_at.isoformat()
    assert payload["snapshot_bytes"] == 4096


def test_access_event_serialization_retains_snapshot_metadata() -> None:
    event_id = uuid4()
    captured_at = datetime(2026, 4, 27, 18, 24, 1, tzinfo=UTC)
    event = AccessEvent(
        id=event_id,
        registration_number="BK26MKF",
        direction=AccessDirection.DENIED,
        decision=AccessDecision.DENIED,
        confidence=0.91,
        source="test",
        occurred_at=datetime(2026, 4, 27, 18, 24, tzinfo=UTC),
        timing_classification=TimingClassification.UNKNOWN,
        snapshot_path=access_event_snapshot_relative_path(event_id),
        snapshot_content_type="image/jpeg",
        snapshot_bytes=4096,
        snapshot_width=320,
        snapshot_height=180,
        snapshot_captured_at=captured_at,
        snapshot_camera="camera.gate",
    )

    payload = _serialize_event(event)

    assert payload["snapshot_url"] == f"/api/v1/events/{event_id}/snapshot"
    assert payload["snapshot_captured_at"] == captured_at.isoformat()
    assert payload["snapshot_bytes"] == 4096
    assert payload["snapshot_width"] == 320
    assert payload["snapshot_height"] == 180
    assert payload["snapshot_camera"] == "camera.gate"


def test_alert_action_resolves_and_reopens_with_note() -> None:
    row = anomaly("BK26MKF", datetime(2026, 4, 27, 18, 24, tzinfo=UTC))
    actor_id = uuid4()
    resolved_at = datetime(2026, 4, 27, 19, 0, tzinfo=UTC)

    _apply_alert_action([row], "resolve", actor_id, "Reviewed camera snapshot.", resolved_at)

    assert row.resolved_at == resolved_at
    assert row.resolved_by_user_id == actor_id
    assert row.resolution_note == "Reviewed camera snapshot."

    _apply_alert_action([row], "reopen", actor_id, None, resolved_at)

    assert row.resolved_at is None
    assert row.resolved_by_user_id is None
    assert row.resolution_note is None


@pytest.mark.asyncio
async def test_resolving_alert_retains_snapshot_for_future_purge(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.services.alert_snapshots.settings.data_dir", tmp_path)
    row = anomaly("BK26MKF", datetime(2026, 4, 27, 18, 24, tzinfo=UTC))
    snapshot_path = tmp_path / "alert-snapshots" / f"{row.id}.jpg"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_bytes(b"jpeg")
    row.context = {
        **(row.context or {}),
        "snapshot": {
            "url": f"/api/v1/alerts/{row.id}/snapshot",
            "captured_at": row.created_at.isoformat(),
            "content_type": "image/jpeg",
            "bytes": 4,
        },
    }
    actor = User(id=uuid4(), username="admin", full_name="Admin User", password_hash="hash")
    session = FakeAlertSession([row])

    async def fake_write_audit_log(*_args, **_kwargs) -> None:
        return None

    async def fake_publish(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(events_api, "write_audit_log", fake_write_audit_log)
    monkeypatch.setattr(events_api.event_bus, "publish", fake_publish)

    await action_alerts(
        AlertActionRequest(alert_ids=[row.id], action="resolve", note="Reviewed camera snapshot."),
        actor,
        session,
    )

    assert row.resolved_at is not None
    assert row.resolution_note == "Reviewed camera snapshot."
    assert snapshot_path.exists()
    assert "snapshot" in (row.context or {})
    assert session.flushed
    assert session.committed


@pytest.mark.asyncio
async def test_resolved_alert_snapshot_endpoint_serves_retained_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.services.alert_snapshots.settings.data_dir", tmp_path)
    row = anomaly("BK26MKF", datetime(2026, 4, 27, 18, 24, tzinfo=UTC))
    row.resolved_at = datetime(2026, 4, 27, 19, 0, tzinfo=UTC)
    snapshot_path = tmp_path / "alert-snapshots" / f"{row.id}.jpg"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_bytes(b"jpeg")
    row.context = {
        **(row.context or {}),
        "snapshot": {
            "url": f"/api/v1/alerts/{row.id}/snapshot",
            "captured_at": row.created_at.isoformat(),
            "content_type": "image/jpeg",
            "bytes": 4,
        },
    }

    response = await alert_snapshot(row.id, object(), FakeAlertSession([row]))

    assert str(response.path) == str(snapshot_path)
    assert response.media_type == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_event_snapshot_endpoint_serves_retained_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)
    event_id = uuid4()
    snapshot_path = tmp_path / "snapshots" / "access-events" / f"{event_id}.jpg"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_bytes(b"jpeg")
    row = AccessEvent(
        id=event_id,
        registration_number="BK26MKF",
        direction=AccessDirection.DENIED,
        decision=AccessDecision.DENIED,
        confidence=0.91,
        source="test",
        occurred_at=datetime(2026, 4, 27, 18, 24, tzinfo=UTC),
        timing_classification=TimingClassification.UNKNOWN,
        snapshot_path=access_event_snapshot_relative_path(event_id),
        snapshot_content_type="image/jpeg",
        snapshot_bytes=4,
        snapshot_width=320,
        snapshot_height=180,
        snapshot_captured_at=datetime(2026, 4, 27, 18, 24, 1, tzinfo=UTC),
        snapshot_camera="camera.gate",
    )

    response = await event_snapshot(event_id, object(), FakeAlertSession([row]))

    assert str(response.path) == str(snapshot_path)
    assert response.media_type == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store"


def test_delete_alert_snapshot_removes_file_and_context(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.services.alert_snapshots.settings.data_dir", tmp_path)
    row = anomaly("BK26MKF", datetime(2026, 4, 27, 18, 24, tzinfo=UTC))
    snapshot_path = tmp_path / "alert-snapshots" / f"{row.id}.jpg"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_bytes(b"jpeg")
    row.context = {
        **(row.context or {}),
        "snapshot": {
            "url": f"/api/v1/alerts/{row.id}/snapshot",
            "captured_at": row.created_at.isoformat(),
            "bytes": 4,
        },
    }

    delete_alert_snapshot(row)

    assert not snapshot_path.exists()
    assert "snapshot" not in row.context
