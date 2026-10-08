"""Vendor-owned frozen connection and bounded presence command receipt."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal, Protocol

INPUT_BOOLEAN_ACTIONS = {"turn_on", "turn_off"}

HaAction = Literal["turn_on", "turn_off"]
HaDeliveryKind = Literal["not_sent", "rejected", "accepted", "unknown"]
HaEffectState = Literal["rejected", "accepted", "unknown"]
HaReason = Literal[
    "accepted", "accepted_observation_unavailable", "provider_not_sent",
    "provider_rejected", "provider_unknown", "invalid_binding",
    "client_unavailable", "operation_cancelled", "operation_expired", "worker_lost",
]


class HaConnection(Protocol):
    @property
    def home_assistant_url(self) -> str: ...

    @property
    def home_assistant_token(self) -> str: ...


@dataclass(frozen=True)
class FrozenHaConnection:
    home_assistant_url: str = field(repr=False)
    home_assistant_token: str = field(repr=False)


@dataclass(frozen=True)
class HaDelivery:
    kind: HaDeliveryKind
    reason: HaReason
    state: Literal["on", "off"] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in {
            "not_sent", "rejected", "accepted", "unknown",
        }:
            raise ValueError("Unsupported Home Assistant delivery outcome")
        if not isinstance(self.reason, str) or self.reason not in {
            "accepted", "accepted_observation_unavailable", "provider_not_sent",
            "provider_rejected", "provider_unknown", "invalid_binding",
            "client_unavailable", "operation_cancelled", "operation_expired", "worker_lost",
        }:
            raise ValueError("Home Assistant delivery requires a safe reason code")
        if self.state is not None and (
            not isinstance(self.state, str) or self.state not in {"on", "off"}
        ):
            raise ValueError("Home Assistant state projection must be on or off")

    @property
    def effect_state(self) -> HaEffectState:
        # Frozen0013 has no not_sent CHECK value; outcome retains that distinction.
        return "rejected" if self.kind == "not_sent" else self.kind


def home_assistant_connection_fingerprint(config: HaConnection) -> str:
    """Opaque provenance for the actual authenticated socket configuration."""
    material = [str(config.home_assistant_url).rstrip("/"), str(config.home_assistant_token)]
    return hashlib.sha256(json.dumps(material, separators=(",", ":")).encode()).hexdigest()
