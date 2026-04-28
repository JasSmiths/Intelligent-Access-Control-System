from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.api.v1.events import _apply_alert_action, _serialize_alerts
from app.models import AccessEvent, Anomaly
from app.models.enums import (
    AccessDecision,
    AccessDirection,
    AnomalySeverity,
    AnomalyType,
    TimingClassification,
)
from app.services.access_events import AccessEventService
from app.services.alert_snapshots import delete_alert_snapshot


def anomaly(registration_number: str, created_at: datetime) -> Anomaly:
    return Anomaly(
        id=uuid4(),
        anomaly_type=AnomalyType.UNAUTHORIZED_PLATE,
        severity=AnomalySeverity.WARNING,
        message="Unauthorised Plate, Access Denied",
        context={"registration_number": registration_number},
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_unknown_plate_anomaly_is_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_capture(row: Anomaly) -> None:
        row.id = row.id or uuid4()
        row.context = {**(row.context or {}), "snapshot": {"url": f"/api/v1/alerts/{row.id}/snapshot"}}

    monkeypatch.setattr("app.services.access_events.capture_alert_snapshot", fake_capture)
    service = AccessEventService()
    event = AccessEvent(
        registration_number="BK26MKF",
        direction=AccessDirection.DENIED,
        decision=AccessDecision.DENIED,
        confidence=0.91,
        source="test",
        occurred_at=datetime(2026, 4, 27, 18, 24, tzinfo=UTC),
        timing_classification=TimingClassification.UNKNOWN,
    )

    rows = await service._build_anomalies(SimpleNamespace(), event, None, None, allowed=False)

    assert len(rows) == 1
    assert rows[0].anomaly_type == AnomalyType.UNAUTHORIZED_PLATE
    assert rows[0].severity == AnomalySeverity.WARNING
    assert rows[0].message == "Unauthorised Plate, Access Denied"
    assert rows[0].context["snapshot"]["url"].startswith("/api/v1/alerts/")


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

    grouped = [item for item in payload if item["grouped"] and item["registration_number"] == "BK26MKF"]
    assert len(grouped) == 1
    assert grouped[0]["count"] == 2
    assert grouped[0]["severity"] == "warning"
    assert grouped[0]["message"] == "Unauthorised Plate, Access Denied"
    assert grouped[0]["local_date"] == "2026-04-27"
    assert len(grouped[0]["alert_ids"]) == 2
    assert grouped[0]["snapshot_url"] == f"/api/v1/alerts/{rows[1].id}/snapshot"
    assert grouped[0]["snapshot_bytes"] == 4096


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


def test_delete_alert_snapshot_removes_file_and_context(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
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
