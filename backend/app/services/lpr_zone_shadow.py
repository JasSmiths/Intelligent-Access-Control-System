import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import LprZoneShadowObservation
from app.modules.lpr.base import PlateRead
from app.services.event_bus import event_bus


SMART_ZONE_EVIDENCE_PAYLOAD_KEY = "_iacs_smart_zone_evidence"
KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY = "_iacs_known_vehicle_plate_match"
VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY = "_iacs_visitor_pass_plate_match"
LPR_ZONE_FILTER_SUPPRESSION_REASON = "lpr_zone_filter_invalid_zone_status"
LPR_ZONE_FILTER_TARGET_ZONE_ID = "2"
LPR_ZONE_FILTER_ACCEPTED_STATUSES = frozenset({"enter", "moving"})


@dataclass(frozen=True)
class ZoneShadowDecision:
    shadow_decision: str
    shadow_reason: str
    would_suppress: bool
    should_suppress_live: bool = False
    actual_outcome: str = "shadow_zone_filter_allowed"


class LprZoneShadowService:
    async def record_decision(
        self,
        read: PlateRead,
        *,
        access_event_id: uuid.UUID | None,
        actual_decision: str | None,
        actual_direction: str | None,
        actual_outcome: str | None = None,
        person_id: uuid.UUID | None = None,
        vehicle_id: uuid.UUID | None = None,
        visitor_pass_id: uuid.UUID | None = None,
        mode: str = "shadow",
        decision_override: ZoneShadowDecision | None = None,
    ) -> list[dict[str, Any]]:
        metadata = _smart_zone_metadata(read)
        known_vehicle = bool(_known_vehicle_match(read) or vehicle_id)
        visitor = bool(_visitor_pass_match(read) or visitor_pass_id)
        zone_entries = _zone_entries(metadata)
        filter_decision = decision_override or evaluate_lpr_zone_filter_decision(
            metadata.get("zone_statuses"),
            mode=mode,
        )
        protect_event_id = _first_protect_event_id(read.raw_payload or {})
        rows: list[LprZoneShadowObservation] = []
        for zone in zone_entries:
            status = _optional_text(zone.get("status"))
            row = LprZoneShadowObservation(
                access_event_id=access_event_id,
                registration_number=read.registration_number,
                detected_registration_number=_detected_registration_number(read),
                source=read.source,
                protect_event_id=protect_event_id,
                camera_id=_metadata_text(metadata, ("smart_zone_evidence", "camera_id")),
                camera_name=_metadata_text(metadata, ("smart_zone_evidence", "camera_name")),
                camera_identifier=_metadata_text(metadata, ("smart_zone_evidence", "camera_identifier")),
                observed_at=read.captured_at,
                time_of_day=_time_of_day(metadata),
                time_of_day_source=_metadata_text(metadata, ("time_of_day_source",)) or "unknown",
                zone_id=_optional_text(zone.get("zone_id") or zone.get("zone")),
                zone_name=_optional_text(zone.get("zone_name") or zone.get("zone")),
                zone_status=status,
                zone_level=_float_or_none(zone.get("level")),
                actual_decision=actual_decision,
                actual_direction=actual_direction,
                actual_outcome=actual_outcome or filter_decision.actual_outcome,
                shadow_decision=filter_decision.shadow_decision,
                shadow_reason=filter_decision.shadow_reason,
                would_suppress=filter_decision.would_suppress,
                details={
                    "mode": _normalize_zone_filter_mode(mode),
                    "known_vehicle": known_vehicle,
                    "visitor": visitor,
                    "person_id": str(person_id) if person_id else None,
                    "vehicle_id": str(vehicle_id) if vehicle_id else None,
                    "visitor_pass_id": str(visitor_pass_id) if visitor_pass_id else None,
                    "target_zone_id": LPR_ZONE_FILTER_TARGET_ZONE_ID,
                    "accepted_statuses": sorted(LPR_ZONE_FILTER_ACCEPTED_STATUSES),
                    "should_suppress_live": filter_decision.should_suppress_live,
                    "smart_zones": metadata.get("smart_zones") if isinstance(metadata.get("smart_zones"), list) else [],
                    "raw_smart_zones": metadata.get("raw_smart_zones") if isinstance(metadata.get("raw_smart_zones"), list) else [],
                },
            )
            rows.append(row)

        async with AsyncSessionLocal() as session:
            session.add_all(rows)
            await session.commit()

        payloads = [serialize_lpr_zone_shadow_observation(row) for row in rows]
        for payload in payloads:
            await event_bus.publish("lpr_zone_shadow.observed", payload)
        return payloads

    async def recent(
        self,
        *,
        limit: int = 200,
        plate: str | None = None,
        status: str | None = None,
        decision: str | None = None,
        time_of_day: str | None = None,
    ) -> list[dict[str, Any]]:
        statement = select(LprZoneShadowObservation)
        if plate:
            statement = statement.where(LprZoneShadowObservation.registration_number.ilike(f"%{plate.strip()}%"))
        if status:
            statement = statement.where(LprZoneShadowObservation.zone_status == status.strip().lower())
        if decision:
            statement = statement.where(LprZoneShadowObservation.shadow_decision == decision.strip().lower())
        if time_of_day:
            statement = statement.where(LprZoneShadowObservation.time_of_day == time_of_day.strip().lower())
        statement = statement.order_by(LprZoneShadowObservation.observed_at.desc(), LprZoneShadowObservation.created_at.desc()).limit(limit)
        async with AsyncSessionLocal() as session:
            rows = (await session.scalars(statement)).all()
        return [serialize_lpr_zone_shadow_observation(row) for row in rows]


def evaluate_zone_shadow_decision(
    *,
    zone_status: str | None,
    known_vehicle: bool = False,
    visitor: bool = False,
    zone_id: str | None = LPR_ZONE_FILTER_TARGET_ZONE_ID,
    mode: str = "shadow",
) -> ZoneShadowDecision:
    del known_vehicle, visitor
    return evaluate_lpr_zone_filter_decision(
        [{"zone_id": zone_id, "status": zone_status}] if zone_status is not None else [],
        mode=mode,
    )


def evaluate_lpr_zone_filter_for_read(read: PlateRead, *, mode: str = "shadow") -> ZoneShadowDecision:
    return evaluate_lpr_zone_filter_decision(_smart_zone_metadata(read).get("zone_statuses"), mode=mode)


def evaluate_lpr_zone_filter_decision(zone_statuses: Any, *, mode: str = "shadow") -> ZoneShadowDecision:
    normalized_mode = _normalize_zone_filter_mode(mode)
    usable_entries = _usable_zone_status_entries(zone_statuses)
    if not usable_entries:
        return ZoneShadowDecision(
            shadow_decision="skipped_missing_zone_status",
            shadow_reason=(
                "UniFi zone/status data was missing, empty, malformed, or status-less; "
                "the zone filter skipped this check and allowed the normal pipeline."
            ),
            would_suppress=False,
            actual_outcome=f"{normalized_mode}_zone_filter_skipped_missing_zone_status",
        )

    for entry in usable_entries:
        if _zone_id(entry) == LPR_ZONE_FILTER_TARGET_ZONE_ID and _zone_status(entry) in LPR_ZONE_FILTER_ACCEPTED_STATUSES:
            status = _zone_status(entry)
            return ZoneShadowDecision(
                shadow_decision="allowed",
                shadow_reason=f"UniFi reported driveway zone {LPR_ZONE_FILTER_TARGET_ZONE_ID} with status {status!r}.",
                would_suppress=False,
                actual_outcome=f"{normalized_mode}_zone_filter_allowed",
            )

    if normalized_mode == "live":
        return ZoneShadowDecision(
            shadow_decision="suppressed",
            shadow_reason=(
                f"UniFi supplied zone/status data, but no driveway zone {LPR_ZONE_FILTER_TARGET_ZONE_ID} "
                f"entry had status enter or moving."
            ),
            would_suppress=True,
            should_suppress_live=True,
            actual_outcome="live_zone_filter_suppressed",
        )

    return ZoneShadowDecision(
        shadow_decision="shadow_only",
        shadow_reason=(
            f"UniFi supplied zone/status data, but no driveway zone {LPR_ZONE_FILTER_TARGET_ZONE_ID} "
            f"entry had status enter or moving. Shadow mode recorded the would-suppress decision only."
        ),
        would_suppress=True,
        actual_outcome="shadow_zone_filter_would_suppress",
    )


def serialize_lpr_zone_shadow_observation(row: LprZoneShadowObservation) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "access_event_id": str(row.access_event_id) if row.access_event_id else None,
        "registration_number": row.registration_number,
        "detected_registration_number": row.detected_registration_number,
        "source": row.source,
        "protect_event_id": row.protect_event_id,
        "camera_id": row.camera_id,
        "camera_name": row.camera_name,
        "camera_identifier": row.camera_identifier,
        "observed_at": row.observed_at.isoformat(),
        "time_of_day": row.time_of_day,
        "time_of_day_source": row.time_of_day_source,
        "zone_id": row.zone_id,
        "zone_name": row.zone_name,
        "zone_status": row.zone_status,
        "zone_level": row.zone_level,
        "actual_decision": row.actual_decision,
        "actual_direction": row.actual_direction,
        "actual_outcome": row.actual_outcome,
        "shadow_decision": row.shadow_decision,
        "shadow_reason": row.shadow_reason,
        "would_suppress": row.would_suppress,
        "details": row.details or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _smart_zone_metadata(read: PlateRead) -> dict[str, Any]:
    value = (read.raw_payload or {}).get(SMART_ZONE_EVIDENCE_PAYLOAD_KEY)
    return dict(value) if isinstance(value, dict) else {}


def _zone_entries(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    statuses = metadata.get("zone_statuses")
    if isinstance(statuses, list) and statuses:
        entries = [dict(item) for item in statuses if isinstance(item, dict)]
        return entries or [{}]
    smart_zones = metadata.get("smart_zones")
    if isinstance(smart_zones, list) and smart_zones:
        return [{"zone": str(smart_zones[0]), "zone_id": str(smart_zones[0]), "zone_name": str(smart_zones[0])}]
    return [{}]


def _usable_zone_status_entries(zone_statuses: Any) -> list[dict[str, Any]]:
    if not isinstance(zone_statuses, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in zone_statuses:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        if _zone_status(entry):
            entries.append(entry)
    return entries


def _zone_id(entry: dict[str, Any]) -> str | None:
    return _optional_text(entry.get("zone_id") or entry.get("zone"))


def _zone_status(entry: dict[str, Any]) -> str | None:
    status = _optional_text(entry.get("status"))
    return status.lower() if status else None


def _normalize_zone_filter_mode(mode: str) -> str:
    return "live" if str(mode or "").strip().lower() == "live" else "shadow"


def _time_of_day(metadata: dict[str, Any]) -> str:
    value = str(metadata.get("time_of_day") or "").strip().lower()
    return value if value in {"day", "night", "unknown"} else "unknown"


def _known_vehicle_match(read: PlateRead) -> dict[str, Any] | None:
    value = (read.raw_payload or {}).get(KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY)
    return dict(value) if isinstance(value, dict) else None


def _visitor_pass_match(read: PlateRead) -> dict[str, Any] | None:
    value = (read.raw_payload or {}).get(VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY)
    return dict(value) if isinstance(value, dict) else None


def _detected_registration_number(read: PlateRead) -> str | None:
    match = _known_vehicle_match(read)
    if match:
        detected = _optional_text(match.get("detected_registration_number"))
        if detected:
            return detected
    payload = read.raw_payload or {}
    for key in ("registrationNumber", "registration_number", "plate", "value"):
        detected = _optional_text(payload.get(key))
        if detected:
            return re.sub(r"[^A-Za-z0-9]", "", detected).upper()
    return read.registration_number


def _first_protect_event_id(payload: dict[str, Any]) -> str | None:
    for value in _payload_values(payload, ("eventId", "event_id")):
        text = _optional_text(value)
        if text:
            return text
    for value in _payload_values(payload, ("eventPath", "eventLocalLink", "event_local_link")):
        text = _optional_text(value)
        if not text:
            continue
        match = re.search(r"/event/([^/?#]+)", text)
        if match:
            return match.group(1)
    return None


def _payload_values(value: Any, keys: tuple[str, ...]) -> list[Any]:
    found: list[Any] = []
    wanted = {_payload_key(key) for key in keys}
    if isinstance(value, dict):
        for key, item in value.items():
            if _payload_key(str(key)) in wanted:
                found.append(item)
            found.extend(_payload_values(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.extend(_payload_values(item, keys))
    return found


def _payload_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _metadata_text(metadata: dict[str, Any], path: tuple[str, ...]) -> str | None:
    value: Any = metadata
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return _optional_text(value)


def _optional_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    return text or None


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class _LprZoneShadowServiceSingleton:
    instance: LprZoneShadowService | None = None


_singleton = _LprZoneShadowServiceSingleton()


def get_lpr_zone_shadow_service() -> LprZoneShadowService:
    if _singleton.instance is None:
        _singleton.instance = LprZoneShadowService()
    return _singleton.instance
