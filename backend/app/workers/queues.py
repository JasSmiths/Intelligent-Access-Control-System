from app.core.config import settings


class QueueNames:
    """Central queue names for Redis-backed workers added in later phases."""

    LPR_READS = "iacs:lpr:reads"
    ACCESS_EVENTS = "iacs:access:events"
    NOTIFICATIONS = "iacs:notifications"


def redis_url() -> str:
    return settings.redis_url
