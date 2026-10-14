import pytest
import json

from app.services.access.evidence import AccessEvidenceResolver
from app.models.enums import AccessDirection
from app.services.movement_fsm import MovementDirectionFSM, RecoveryDirectionIntent


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "NaN", "Infinity", 1e100, True, 10**400, -(10**400)])
def test_malformed_camera_confidence_cannot_authorize_a_tiebreaker(value):
    assert not AccessEvidenceResolver()._camera_tiebreaker_is_clear({"confidence": value})


@pytest.mark.parametrize("value", [.9, "0.9", 90, "90"])
def test_supported_fraction_and_percentage_confidence(value):
    assert AccessEvidenceResolver()._camera_tiebreaker_is_clear({"confidence": value})


@pytest.mark.parametrize("value", [10**400, -(10**400)])
def test_extreme_json_confidence_is_unclear_through_parsing_and_recovery_policy(value):
    resolver = AccessEvidenceResolver()
    direction, confidence, _ = resolver._parse_camera_direction_analysis(json.dumps({"direction": "entry", "confidence": value}))
    assert direction == "entry" and confidence is None
    decision = MovementDirectionFSM.resolve_recovery(RecoveryDirectionIntent(allowed=True, exact_plate=True,
        explicit_direction=None, phone_ready=False, phone_reason="phone_journey_missing", safety_block=None,
        camera_direction=AccessDirection.ENTRY, camera_clear=resolver._camera_tiebreaker_is_clear({"confidence": value})))
    assert decision.allowed is False and decision.method == "none"
