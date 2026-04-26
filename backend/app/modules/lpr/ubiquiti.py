import re
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
        plate = re.sub(r"[^A-Za-z0-9]", "", value).upper()
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


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_")


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


def _looks_like_lpr_trigger(value: str) -> bool:
    normalized = value.lower()
    return any(token in normalized for token in ("lpr", "license", "licence", "plate", "registration"))


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
