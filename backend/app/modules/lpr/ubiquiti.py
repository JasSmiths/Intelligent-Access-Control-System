import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.lpr.base import PlateRead, now_utc


PLATE_KEYS = {
    "registrationnumber",
    "registration_number",
    "registration number",
    "licenseplate",
    "license_plate",
    "license plate",
    "platenumber",
    "plate_number",
    "plate number",
    "plate",
}
CONFIDENCE_KEYS = {
    "confidence",
    "score",
    "plateconfidence",
    "plate_confidence",
    "plate confidence",
}
SMART_ZONE_KEYS = {
    "zone",
    "zone_name",
    "zonename",
    "smartdetectzone",
    "smart_detect_zone",
    "smart detect zone",
    "smartdetectzonename",
    "smart_detect_zone_name",
    "smart detect zone name",
    "smartdetectzoneid",
    "smart_detect_zone_id",
    "smart detect zone id",
}
SMART_ZONE_CONTAINER_KEYS = {
    "zones",
    "smartdetectzones",
    "smart_detect_zones",
    "smart detect zones",
}
SMART_ZONE_TRIGGER_MARKERS = ("zone", "smart detect zone", "smartdetectzone")


@dataclass(frozen=True)
class PlateSmartZoneEvidence:
    smart_zones: list[str]
    present: bool
    explicit_empty: bool
    source: str
    camera_identifier: str | None = None


class UbiquitiLprPayload(BaseModel):
    """Minimal Ubiquiti LPR webhook contract.

    Ubiquiti payloads can vary by firmware and webhook configuration. This model
    accepts extra fields so the complete payload is preserved while normalizing
    the fields the access pipeline needs.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    registration_number: str = Field(
        validation_alias=AliasChoices("registrationNumber", "registration_number", "Registration Number")
    )
    confidence: float = Field(default=1.0, validation_alias=AliasChoices("confidence", "Confidence"))
    captured_at: datetime | None = Field(default=None, validation_alias=AliasChoices("capturedAt", "captured_at"))

    @model_validator(mode="before")
    @classmethod
    def normalize_alarm_manager_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        normalized = dict(value)
        if not _first_present(normalized, PLATE_KEYS):
            extracted_plate = _extract_plate(normalized)
            if extracted_plate:
                normalized["registrationNumber"] = extracted_plate

        if not _first_present(normalized, CONFIDENCE_KEYS):
            extracted_confidence = _find_nested_value(normalized, CONFIDENCE_KEYS)
            if extracted_confidence is not None:
                normalized["confidence"] = extracted_confidence

        if "capturedAt" not in normalized and "captured_at" not in normalized:
            captured_at = _extract_timestamp(normalized)
            if captured_at:
                normalized["capturedAt"] = captured_at
        return normalized

    @field_validator("registration_number")
    @classmethod
    def normalize_plate(cls, value: str) -> str:
        plate = _normalize_plate(value)
        if not plate:
            raise ValueError("Ubiquiti LPR webhook did not include a registration number.")
        return plate

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: Any) -> float:
        if value is None or value == "":
            return 1.0
        if isinstance(value, str):
            value = value.strip().removesuffix("%")
        confidence = float(value)
        if confidence > 1:
            confidence = confidence / 100
        return max(0.0, min(confidence, 1.0))


class UbiquitiLprAdapter:
    source_name = "ubiquiti"

    def to_plate_read(self, payload: UbiquitiLprPayload) -> PlateRead:
        raw_payload: dict[str, Any] = payload.model_dump(by_alias=True, mode="json")
        return PlateRead(
            registration_number=payload.registration_number,
            confidence=payload.confidence,
            source=self.source_name,
            captured_at=payload.captured_at or now_utc(),
            raw_payload=raw_payload,
        )


def extract_smart_zone_names(payload: Any) -> list[str]:
    """Return UniFi smart-zone names/IDs carried by an LPR webhook payload."""

    zones: list[str] = []
    _collect_smart_zone_names(payload, zones)
    return _dedupe_preserving_order(zones)


def extract_plate_smart_zone_evidence(payload: Any, registration_number: str) -> PlateSmartZoneEvidence:
    """Return smart-zone evidence scoped to the plate that produced this read."""

    target = _normalize_plate(registration_number)
    if not target or not isinstance(payload, dict):
        return _empty_plate_zone_evidence()

    trigger = _matching_lpr_trigger(payload, target)
    if trigger is not None:
        return _plate_zone_evidence_from_mapping(
            trigger,
            source="alarm.trigger",
            camera_identifier=_camera_identifier_from_trigger(trigger) or _camera_identifier_from_payload(payload),
        )

    direct_plate = _normalize_plate(_first_present(payload, PLATE_KEYS) or "")
    if direct_plate == target:
        return _plate_zone_evidence_from_mapping(
            payload,
            source="payload",
            camera_identifier=_camera_identifier_from_payload(payload),
        )

    nested = _find_plate_mapping(payload, target)
    if nested is not None:
        return _plate_zone_evidence_from_mapping(
            nested,
            source="payload.nested",
            camera_identifier=_camera_identifier_from_payload(nested) or _camera_identifier_from_payload(payload),
        )

    return _empty_plate_zone_evidence()


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_")


def _normalize_plate(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def _first_present(payload: dict[str, Any], keys: set[str]) -> Any | None:
    for key, value in payload.items():
        if _normalize_key(key) in keys and value is not None and value != "":
            return value
    return None


def _extract_plate(payload: dict[str, Any]) -> str | None:
    direct = _first_present(payload, PLATE_KEYS)
    if direct:
        return str(direct)

    alarm = payload.get("alarm")
    if isinstance(alarm, dict):
        triggers = alarm.get("triggers")
        if isinstance(triggers, list):
            for trigger in triggers:
                if not isinstance(trigger, dict):
                    continue
                trigger_text = " ".join(str(trigger.get(key, "")) for key in ("key", "type", "source", "name"))
                if _looks_like_lpr_trigger(trigger_text):
                    value = trigger.get("value") or trigger.get("plate") or trigger.get("registrationNumber")
                    if value:
                        return str(value)

    nested = _find_nested_value(payload, PLATE_KEYS)
    return str(nested) if nested else None


def _collect_smart_zone_names(value: Any, zones: list[str]) -> None:
    if isinstance(value, dict):
        _collect_alarm_trigger_zones(value, zones)
        for key, item in value.items():
            normalized = _normalize_key(str(key))
            if normalized in SMART_ZONE_KEYS:
                _collect_zone_value(item, zones)
                continue
            if normalized in SMART_ZONE_CONTAINER_KEYS:
                _collect_zone_value(item, zones)
                continue
            _collect_smart_zone_names(item, zones)
    elif isinstance(value, list):
        for item in value:
            _collect_smart_zone_names(item, zones)


def _collect_alarm_trigger_zones(payload: dict[str, Any], zones: list[str]) -> None:
    triggers = payload.get("triggers")
    if not isinstance(triggers, list):
        alarm = payload.get("alarm")
        triggers = alarm.get("triggers") if isinstance(alarm, dict) else None
    if not isinstance(triggers, list):
        return
    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue
        descriptor = " ".join(
            str(trigger.get(key, ""))
            for key in ("key", "type", "source", "name", "label")
        ).lower()
        if not any(marker in descriptor for marker in SMART_ZONE_TRIGGER_MARKERS):
            continue
        _collect_zone_value(trigger.get("value") or trigger.get("zone") or trigger.get("zoneName"), zones)


def _collect_zone_value(value: Any, zones: list[str]) -> None:
    if value is None or value == "":
        return
    if isinstance(value, str):
        zones.append(value)
        return
    if isinstance(value, int | float):
        zones.append(str(value))
        return
    if isinstance(value, dict):
        normalized_items = {_normalize_key(str(key)): item for key, item in value.items()}
        if "zone" in normalized_items:
            _collect_zone_value(normalized_items["zone"], zones)
            return
        for key in ("name", "displayName", "display_name", "label", "id"):
            if key in value and value[key] not in {None, ""}:
                zones.append(str(value[key]))
        return
    if isinstance(value, list):
        for item in value:
            _collect_zone_value(item, zones)


def _normalize_zone_name(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold().replace("_", " ").replace("-", " "))


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = _normalize_zone_name(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(str(value).strip())
    return deduped


def _looks_like_lpr_trigger(value: str) -> bool:
    normalized = value.lower()
    return any(token in normalized for token in ("lpr", "license", "licence", "plate", "registration"))


def _empty_plate_zone_evidence() -> PlateSmartZoneEvidence:
    return PlateSmartZoneEvidence(smart_zones=[], present=False, explicit_empty=False, source="missing")


def _alarm_triggers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    triggers = payload.get("triggers")
    if not isinstance(triggers, list):
        alarm = payload.get("alarm")
        triggers = alarm.get("triggers") if isinstance(alarm, dict) else None
    if not isinstance(triggers, list):
        return []
    return [trigger for trigger in triggers if isinstance(trigger, dict)]


def _matching_lpr_trigger(payload: dict[str, Any], registration_number: str) -> dict[str, Any] | None:
    for trigger in _alarm_triggers(payload):
        if _trigger_registration_number(trigger) == registration_number:
            return trigger
    return None


def _trigger_registration_number(trigger: dict[str, Any]) -> str:
    descriptor = " ".join(str(trigger.get(key, "")) for key in ("key", "type", "source", "name", "label"))
    if not _looks_like_lpr_trigger(descriptor):
        return ""
    group = trigger.get("group")
    candidates = [
        trigger.get("value"),
        trigger.get("plate"),
        trigger.get("registrationNumber"),
        trigger.get("registration_number"),
    ]
    if isinstance(group, dict):
        candidates.extend([group.get("name"), group.get("matchedName"), group.get("matched_name")])
    for candidate in candidates:
        plate = _normalize_plate(candidate)
        if plate:
            return plate
    return ""


def _plate_zone_evidence_from_mapping(
    mapping: dict[str, Any],
    *,
    source: str,
    camera_identifier: str | None,
) -> PlateSmartZoneEvidence:
    smart_zones, present, explicit_empty = _smart_zone_values_from_mapping(mapping)
    return PlateSmartZoneEvidence(
        smart_zones=_dedupe_preserving_order(smart_zones),
        present=present,
        explicit_empty=explicit_empty,
        source=source,
        camera_identifier=camera_identifier,
    )


def _smart_zone_values_from_mapping(mapping: dict[str, Any]) -> tuple[list[str], bool, bool]:
    values: list[str] = []
    present = False
    explicit_empty = False
    for key, item in mapping.items():
        normalized = _normalize_key(str(key))
        if normalized == "zones":
            present = True
            found, empty = _zone_values_from_container(item)
            values.extend(found)
            explicit_empty = explicit_empty or empty
        elif normalized in SMART_ZONE_KEYS or normalized in SMART_ZONE_CONTAINER_KEYS:
            present = True
            found, empty = _zone_values_from_value(item)
            values.extend(found)
            explicit_empty = explicit_empty or empty
    return values, present, explicit_empty


def _zone_values_from_container(value: Any) -> tuple[list[str], bool]:
    if isinstance(value, dict):
        normalized_items = {_normalize_key(str(key)): item for key, item in value.items()}
        if "zone" in normalized_items:
            return _zone_values_from_value(normalized_items["zone"])
    return _zone_values_from_value(value)


def _zone_values_from_value(value: Any) -> tuple[list[str], bool]:
    if value is None or value == "":
        return [], True
    if isinstance(value, str):
        return [value], False
    if isinstance(value, int | float):
        return [str(value)], False
    if isinstance(value, list):
        if not value:
            return [], True
        values: list[str] = []
        empty = False
        for item in value:
            found, item_empty = _zone_values_from_value(item)
            values.extend(found)
            empty = empty or item_empty
        return values, empty and not values
    if isinstance(value, dict):
        values: list[str] = []
        for key in ("name", "displayName", "display_name", "label", "id"):
            if key in value and value[key] not in {None, ""}:
                values.append(str(value[key]))
        if values:
            return values, False
        normalized_items = {_normalize_key(str(key)): item for key, item in value.items()}
        if "zone" in normalized_items:
            return _zone_values_from_value(normalized_items["zone"])
        return [], not bool(value)
    return [], False


def _find_plate_mapping(value: Any, registration_number: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        direct = _normalize_plate(_first_present(value, PLATE_KEYS) or "")
        if direct == registration_number:
            return value
        for nested in value.values():
            found = _find_plate_mapping(nested, registration_number)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_plate_mapping(item, registration_number)
            if found is not None:
                return found
    return None


def _camera_identifier_from_trigger(trigger: dict[str, Any]) -> str | None:
    return _text_or_none(trigger.get("device") or trigger.get("deviceId") or trigger.get("device_id") or trigger.get("cameraId") or trigger.get("camera_id"))


def _camera_identifier_from_payload(payload: dict[str, Any]) -> str | None:
    direct = _text_or_none(
        payload.get("device")
        or payload.get("deviceId")
        or payload.get("device_id")
        or payload.get("cameraId")
        or payload.get("camera_id")
    )
    if direct:
        return direct
    alarm = payload.get("alarm")
    if isinstance(alarm, dict):
        sources = alarm.get("sources")
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, dict):
                    identifier = _text_or_none(source.get("device") or source.get("deviceId") or source.get("device_id"))
                    if identifier:
                        return identifier
    return None


def _text_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value).strip()


def _find_nested_value(value: Any, keys: set[str]) -> Any | None:
    if isinstance(value, dict):
        direct = _first_present(value, keys)
        if direct is not None:
            return direct
        for nested in value.values():
            found = _find_nested_value(nested, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_nested_value(item, keys)
            if found is not None:
                return found
    return None


def _extract_timestamp(payload: dict[str, Any]) -> datetime | None:
    value = payload.get("timestamp") or payload.get("time")
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int | float):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None
