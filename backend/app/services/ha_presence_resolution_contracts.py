"""Sanitized no-send settlement contracts, without bearer/provider payload output."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import TypedDict

HA_ACKNOWLEDGEMENT_ACTION = 'home_assistant.presence_effect.acknowledge'


class HaResolutionError(ValueError):
    def __init__(self, detail: str, *, status_code: int = 409) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class HaAdminRequest:
    actor_user_id: uuid.UUID
    token: str = field(repr=False)


@dataclass(frozen=True)
class HaAcknowledgement:
    attempt_id: uuid.UUID
    expected_preview_fingerprint: str
    previous_request_settled: bool
    reason: str
    confirmation_id: uuid.UUID
    confirmation_token: str | None = field(repr=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.expected_preview_fingerprint, str)
            or re.fullmatch('[a-f0-9]{64}', self.expected_preview_fingerprint) is None):
            raise HaResolutionError('Create a fresh exact-attempt preview.')
        if self.previous_request_settled is not True:
            raise HaResolutionError('Explicitly confirm that the former provider request has settled.')
        if not isinstance(self.reason, str) or not self.reason.strip() or len(self.reason) > 500:
            raise HaResolutionError('A settlement reason of 1 to 500 characters is required.', status_code=422)


class HaResolutionReceipt(TypedDict):
    attempt_id: str
    confirmation_id: str
    target_fingerprint: str
    target_generation: int
    barrier_version: int
    actor_id: str
    reason: str
    resolved_at: str


class HaAttemptPreview(TypedDict):
    attempt_id: str
    attempt_generation: int
    attempt_state: str
    action: str
    entity_label: str
    attempted_at: str
    completed_at: str | None
    physical_target_fingerprint: str
    attempt_binding_fingerprint: str
    attempt_config_fingerprint: str
    target_generation: int
    target_state: str
    barrier_version: int
    barrier_attempt_id: str | None
    current_person_id: str | None
    current_person_exists: bool
    current_person_active: bool | None
    current_binding_fingerprint: str | None
    current_config_fingerprint: str | None
    current_config_available: bool
    live_owner: bool
    resolution: HaResolutionReceipt | None
    preview_fingerprint: str


def acknowledgement_payload(request: HaAcknowledgement) -> dict[str, object]:
    """Generic confirmation binds the preview; its new ID/token is not self-input."""
    return {
        'attempt_id': str(request.attempt_id),
        'preview_fingerprint': request.expected_preview_fingerprint,
        'previous_request_settled': True,
        'reason': request.reason.strip(),
    }
