import logging
import sys

from pythonjsonlogger.json import JsonFormatter

from app.core.config import settings


def configure_logging() -> None:
    """Configure structured logs for containers and future live-log streaming."""

    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )

    root.handlers.clear()
    root.addHandler(handler)

    for noisy_logger in ("httpx", "httpcore"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
