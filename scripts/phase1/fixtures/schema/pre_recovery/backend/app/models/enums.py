from enum import StrEnum


class GroupCategory(StrEnum):
    FAMILY = "family"
    FRIENDS = "friends"
    VISITORS = "visitors"
    CONTRACTORS = "contractors"


class AccessDecision(StrEnum):
    GRANTED = "granted"
    DENIED = "denied"


class AccessDirection(StrEnum):
    ENTRY = "entry"
    EXIT = "exit"
    DENIED = "denied"


class PresenceState(StrEnum):
    UNKNOWN = "unknown"
    PRESENT = "present"
    EXITED = "exited"


class TimingClassification(StrEnum):
    UNKNOWN = "unknown"
    EARLIER_THAN_USUAL = "earlier_than_usual"
    NORMAL = "normal"
    LATER_THAN_USUAL = "later_than_usual"


class VisitorPassStatus(StrEnum):
    ACTIVE = "active"
    SCHEDULED = "scheduled"
    USED = "used"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class VisitorPassType(StrEnum):
    ONE_TIME = "one-time"
    DURATION = "duration"


class AnomalyType(StrEnum):
    UNAUTHORIZED_PLATE = "unauthorized_plate"
    DUPLICATE_ENTRY = "duplicate_entry"
    DUPLICATE_EXIT = "duplicate_exit"
    OUTSIDE_SCHEDULE = "outside_schedule"


class AnomalySeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class GateMalfunctionStatus(StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    FUBAR = "fubar"


class MovementSagaState(StrEnum):
    OBSERVED = "observed"
    DIRECTION_RESOLVED = "direction_resolved"
    PHYSICAL_COMMAND_PENDING = "physical_command_pending"
    PHYSICAL_COMMAND_ACCEPTED = "physical_command_accepted"
    PRESENCE_COMMITTED = "presence_committed"
    COMPLETED = "completed"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class GateCommandState(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    RECONCILED = "reconciled"


class UserRole(StrEnum):
    ADMIN = "admin"
    STANDARD = "standard"
