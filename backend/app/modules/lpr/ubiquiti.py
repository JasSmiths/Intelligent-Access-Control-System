from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.modules.lpr.base import PlateRead, now_utc


class UbiquitiLprPayload(BaseModel):
    """Minimal Ubiquiti LPR webhook contract.

    Ubiquiti payloads can vary by firmware and webhook configuration. This model
    accepts extra fields so Phase 2 can preserve the complete payload while
    normalizing the two fields the access pipeline needs immediately.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    registration_number: str = Field(
        validation_alias=AliasChoices("registrationNumber", "registration_number", "Registration Number")
    )
    confidence: float = Field(validation_alias=AliasChoices("confidence", "Confidence"))

    @field_validator("registration_number")
    @classmethod
    def normalize_plate(cls, value: str) -> str:
        return value.strip().upper().replace(" ", "")


class UbiquitiLprAdapter:
    source_name = "ubiquiti"

    def to_plate_read(self, payload: UbiquitiLprPayload) -> PlateRead:
        raw_payload: dict[str, Any] = payload.model_dump(by_alias=True)
        return PlateRead(
            registration_number=payload.registration_number,
            confidence=payload.confidence,
            source=self.source_name,
            captured_at=now_utc(),
            raw_payload=raw_payload,
        )
