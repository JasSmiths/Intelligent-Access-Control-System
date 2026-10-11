"""One scalar snapshot for the public access resource and its movement journal.

Neither raw movement summaries nor realtime delivery are journal authority. The
bounded two-row probe exposes conflicting legacy links without selecting a winner.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any, TypedDict

from sqlalchemy import String, case, cast, func, select
from sqlalchemy.sql import Select

from app.models import AccessEvent, Anomaly, MovementSagaRecord
from app.services.snapshots import access_event_snapshot_url


class PublicAccessEvent(TypedDict):
    id: str
    resource_revision: int
    registration_number: str
    direction: str
    decision: str
    confidence: float
    source: str
    occurred_at: str | None
    timing_classification: str
    anomaly_count: int
    visitor_pass_id: str | None
    visitor_name: str | None
    visitor_pass_mode: str | None
    external_admission_mode: str | None
    external_admission_source: str | None
    snapshot_url: str | None
    snapshot_captured_at: str | None
    snapshot_bytes: int | None
    snapshot_width: int | None
    snapshot_height: int | None
    snapshot_camera: str | None
    movement_saga: dict[str, Any] | None


def _text_json(path: Any) -> Any:
    return case((func.jsonb_typeof(path) == "string", path.astext), else_=None)


def public_event_query() -> Select[Any]:
    """Select the event, bounded linked journals and count in one SQL snapshot."""
    journals = (
        select(
            MovementSagaRecord.id,
            MovementSagaRecord.state,
            MovementSagaRecord.reconciliation_required,
            MovementSagaRecord.gate_command_required,
            MovementSagaRecord.presence_committed,
            MovementSagaRecord.failure_detail,
            MovementSagaRecord.updated_at,
        )
        .where(MovementSagaRecord.access_event_id == AccessEvent.id)
        .order_by(MovementSagaRecord.id)
        .limit(2)
        .correlate(AccessEvent)
        .subquery()
    )
    summaries = (
        select(func.jsonb_agg(func.jsonb_build_object(
            "id", cast(journals.c.id, String),
            "state", func.lower(cast(journals.c.state, String)),
            "reconciliation_required", journals.c.reconciliation_required,
            "gate_command_required", journals.c.gate_command_required,
            "presence_committed", journals.c.presence_committed,
            "failure_detail", journals.c.failure_detail,
            "updated_at", journals.c.updated_at,
        )))
        .select_from(journals)
        .scalar_subquery()
    )
    raw = AccessEvent.raw_payload
    return select(
        AccessEvent.id,
        AccessEvent.resource_revision,
        AccessEvent.registration_number,
        AccessEvent.direction,
        AccessEvent.decision,
        AccessEvent.confidence,
        AccessEvent.source,
        AccessEvent.occurred_at,
        AccessEvent.timing_classification,
        AccessEvent.snapshot_path,
        AccessEvent.snapshot_bytes,
        AccessEvent.snapshot_width,
        AccessEvent.snapshot_height,
        AccessEvent.snapshot_captured_at,
        AccessEvent.snapshot_camera,
        select(func.count()).select_from(Anomaly)
        .where(Anomaly.event_id == AccessEvent.id).correlate(AccessEvent)
        .scalar_subquery().label("anomaly_count"),
        _text_json(raw["visitor_pass"]["id"]).label("visitor_pass_id"),
        _text_json(raw["visitor_pass"]["visitor_name"]).label("visitor_name"),
        _text_json(raw["visitor_pass"]["mode"]).label("visitor_pass_mode"),
        _text_json(raw["external_admission"]["mode"]).label("external_admission_mode"),
        _text_json(raw["external_admission"]["source"]).label("external_admission_source"),
        summaries.label("movement_summaries"),
    )


def _enum_text(value: object) -> str:
    return str(value.value) if isinstance(value, Enum) else str(value)


def _time_text(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return datetime.fromisoformat(value).isoformat()
    return None


def _optional_text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def public_event_payload(row: Mapping[str, Any]) -> PublicAccessEvent:
    summaries = row["movement_summaries"] or []
    movement = None
    if len(summaries) == 1:
        movement = dict(summaries[0])
        movement["updated_at"] = _time_text(movement["updated_at"])
    elif len(summaries) > 1:
        movement = {
            "id": None,
            "state": "held",
            "reconciliation_required": True,
            "gate_command_required": False,
            "presence_committed": False,
            "failure_detail": "Conflicting movement records require review.",
            "updated_at": None,
        }
    has_snapshot = bool(row["snapshot_path"])
    return {
        "id": str(row["id"]),
        "resource_revision": row["resource_revision"],
        "registration_number": row["registration_number"],
        "direction": _enum_text(row["direction"]),
        "decision": _enum_text(row["decision"]),
        "confidence": row["confidence"],
        "source": row["source"],
        "occurred_at": _time_text(row["occurred_at"]),
        "timing_classification": _enum_text(row["timing_classification"]),
        "anomaly_count": row["anomaly_count"],
        "visitor_pass_id": _optional_text(row["visitor_pass_id"]),
        "visitor_name": _optional_text(row["visitor_name"]),
        "visitor_pass_mode": _optional_text(row["visitor_pass_mode"]),
        "external_admission_mode": _optional_text(row["external_admission_mode"]),
        "external_admission_source": _optional_text(row["external_admission_source"]),
        "snapshot_url": access_event_snapshot_url(row["id"]) if has_snapshot else None,
        "snapshot_captured_at": _time_text(row["snapshot_captured_at"]) if has_snapshot else None,
        "snapshot_bytes": row["snapshot_bytes"] if has_snapshot else None,
        "snapshot_width": row["snapshot_width"] if has_snapshot else None,
        "snapshot_height": row["snapshot_height"] if has_snapshot else None,
        "snapshot_camera": row["snapshot_camera"] if has_snapshot else None,
        "movement_saga": movement,
    }
