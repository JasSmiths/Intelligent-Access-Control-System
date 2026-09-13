"""A deployment hold preserves runnable records until operator reconciliation."""
from app.core.config import settings


def is_recovery_hold() -> bool:
    return settings.recovery_hold


class RecoveryHoldError(RuntimeError):
    pass


def require_effects_enabled() -> None:
    if is_recovery_hold():
        raise RecoveryHoldError("Recovery hold is active; no new external action is permitted.")
