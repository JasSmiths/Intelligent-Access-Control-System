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


class UserRole(StrEnum):
    ADMIN = "admin"
    STANDARD = "standard"
