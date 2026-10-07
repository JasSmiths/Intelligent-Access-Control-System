"""Provider attempt and device outcome contracts shared by command participants."""

from dataclasses import dataclass, field
from typing import Any

from app.modules.access_devices.base import AccessDeviceEntity
from app.modules.gate.base import CommandDelivery, GateState


@dataclass(frozen=True)
class AccessDeviceProviderAttempt:
    provider: str
    attempt: int = 1
    accepted: bool = False
    unavailable: bool = False
    detail: str | None = None
    state: str | None = None
    verified: bool = False
    confirmation_failed: bool = False
    delivery: CommandDelivery | None = None


@dataclass(frozen=True)
class AccessDeviceOperationResult:
    device: AccessDeviceEntity
    action: str
    accepted: bool
    state: GateState
    detail: str | None = None
    primary_provider: str | None = None
    used_provider: str | None = None
    failover_used: bool = False
    attempts: list[AccessDeviceProviderAttempt] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    delivery: CommandDelivery | None = None

    def __post_init__(self) -> None:
        if self.delivery is None:
            object.__setattr__(
                self,
                "delivery",
                CommandDelivery.ACCEPTED if self.accepted else CommandDelivery.REJECTED,
            )

    @property
    def requires_reconciliation(self) -> bool:
        if "requires_reconciliation" in self.metadata:
            return bool(self.metadata["requires_reconciliation"])
        return self.delivery == CommandDelivery.UNKNOWN or (self.accepted and not self.verified)

    @property
    def verified(self) -> bool:
        return bool(self.metadata.get("verified")) or any(
            attempt.verified for attempt in self.attempts
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "entity_id": self.device.key,
            "device_key": self.device.key,
            "name": self.device.name,
            "kind": self.device.kind,
            "action": self.action,
            "accepted": self.accepted,
            "state": self.state.value,
            "detail": self.detail,
            "primary_provider": self.primary_provider,
            "used_provider": self.used_provider,
            "failover_used": self.failover_used,
            "verified": self.verified,
            "delivery": self.delivery,
            "requires_reconciliation": self.requires_reconciliation,
            "attempts": [attempt.__dict__ for attempt in self.attempts],
            "metadata": self.metadata,
        }
