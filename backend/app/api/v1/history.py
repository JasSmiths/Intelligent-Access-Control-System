"""Keyset history contracts shared by the durable activity routes."""

import base64
import hashlib
import json
import uuid
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, or_


class HistoryPage[T](BaseModel):
    items: list[T]
    next_cursor: str | None
    as_of: datetime


def site_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Europe/London")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def range_boundary(raw: str | None, timezone: ZoneInfo) -> datetime | None:
    if not raw:
        return None
    try:
        if len(raw) == 10:
            local = datetime.combine(date.fromisoformat(raw), time.min, tzinfo=timezone)
        else:
            local = datetime.fromisoformat(raw)
            if local.tzinfo is None:
                local = local.replace(tzinfo=timezone)
        return local.astimezone(UTC)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid history date boundary.") from exc


def filter_digest(filters: dict[str, Any]) -> str:
    body = json.dumps(filters, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode()).hexdigest()[:24]


def read_cursor(
    raw: str | None, filters: dict[str, Any]
) -> tuple[datetime, datetime | None, uuid.UUID | None]:
    if not raw:
        return datetime.now(UTC), None, None
    try:
        parsed = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if parsed["v"] != 1 or parsed["f"] != filter_digest(filters):
            raise ValueError("mismatch")
        cutoff = datetime.fromisoformat(parsed["a"])
        stamp = datetime.fromisoformat(parsed["t"])
        row_id = uuid.UUID(parsed["i"])
        if cutoff.tzinfo is None or stamp.tzinfo is None or len(raw) > 1024:
            raise ValueError("invalid")
        return cutoff.astimezone(UTC), stamp.astimezone(UTC), row_id
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid or stale history cursor.") from exc


def write_cursor(
    cutoff: datetime, stamp: datetime, row_id: uuid.UUID, filters: dict[str, Any]
) -> str:
    body = {
        "v": 1,
        "f": filter_digest(filters),
        "a": cutoff.isoformat(),
        "t": stamp.isoformat(),
        "i": str(row_id),
    }
    return (
        base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def older_than(stamp_column: Any, id_column: Any, stamp: datetime, row_id: uuid.UUID) -> Any:
    return or_(stamp_column < stamp, and_(stamp_column == stamp, id_column < row_id))
