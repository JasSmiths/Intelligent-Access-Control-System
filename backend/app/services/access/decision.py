"""Pure access policy. Evidence and execution own I/O; the FSM owns direction."""

from dataclasses import dataclass

from app.models.enums import AccessDecision, AccessDirection


@dataclass(frozen=True)
class AccessPlan:
    allowed: bool
    direction: AccessDirection
    decision: AccessDecision


def access_is_allowed(
    *,
    schedule_allowed: bool,
    identity_active: bool,
    visitor_pass_matched: bool,
    external_admission_matched: bool,
) -> bool:
    return schedule_allowed and (
        identity_active or visitor_pass_matched or external_admission_matched
    )


def plan_access(
    *,
    allowed: bool,
    direction: AccessDirection,
) -> AccessPlan:
    return AccessPlan(
        allowed=allowed,
        direction=direction,
        decision=AccessDecision.GRANTED if allowed else AccessDecision.DENIED,
    )
