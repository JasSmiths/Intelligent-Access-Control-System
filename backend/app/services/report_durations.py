"""Pure movement-report duration policy, shared by snapshots and benchmarks.

The report reader supplies period observations plus strictly earlier predecessor
state. Callers supply the site timezone; this owner has no configuration, storage
or rendering effects.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.models.enums import AccessDecision, AccessDirection


class MovementRecord(Protocol):
    @property
    def id(self) -> uuid.UUID: ...

    @property
    def registration_number(self) -> str: ...

    @property
    def direction(self) -> AccessDirection: ...

    @property
    def decision(self) -> AccessDecision: ...

    @property
    def occurred_at(self) -> datetime: ...


@dataclass(frozen=True)
class MovementPoint:
    """Projected report state: history and timelines do not need payload/media."""

    id: uuid.UUID
    registration_number: str
    direction: AccessDirection
    decision: AccessDecision
    occurred_at: datetime


def build_duration_lookup(
    report_events: Sequence[MovementRecord],
    selected_history: Sequence[MovementRecord],
    *,
    timezone: ZoneInfo,
) -> dict[str, dict[str, Any]]:
    """Calculate durations in one pass, excluding every equal-time observation."""
    durations: dict[str, dict[str, Any]] = {}
    history = sorted((event for event in selected_history if is_movement_event(event)),
                     key=lambda item: item.occurred_at)
    global_state: dict[AccessDirection, MovementRecord] = {}
    plate_state: dict[str, dict[AccessDirection, MovementRecord]] = {}
    position = 0
    for event in sorted(report_events, key=lambda item: item.occurred_at):
        # Strict-before is essential: neither another plate nor a same-time
        # arrival/departure can establish duration for this timestamp.
        while position < len(history) and history[position].occurred_at < event.occurred_at:
            prior = history[position]
            global_state[prior.direction] = prior
            plate_state.setdefault(normalize_plate(prior.registration_number), {})[prior.direction] = prior
            position += 1
        event_id = str(event.id)
        if not is_movement_event(event):
            durations[event_id] = {"label": "N/A", "tone": "muted"}
            continue
        state = plate_state.get(normalize_plate(event.registration_number), {}) if event.direction == AccessDirection.ENTRY else global_state
        arrival = state.get(AccessDirection.ENTRY)
        departure = state.get(AccessDirection.EXIT)
        if event.direction == AccessDirection.ENTRY:
            if arrival is None:
                durations[event_id] = {"label": "New Arrival", "tone": "new"}
            elif departure is not None and departure.occurred_at > arrival.occurred_at:
                durations[event_id] = format_duration_info(
                    departure.occurred_at, event.occurred_at,
                    "Time since this vehicle was last on site", timezone=timezone,
                )
            else:
                durations[event_id] = {"label": "No prior departure", "tone": "muted"}
        elif arrival is None:
            durations[event_id] = {"label": "No arrival found", "tone": "muted"}
        elif departure is not None and departure.occurred_at > arrival.occurred_at:
            durations[event_id] = {"label": "No active visit", "tone": "muted"}
        else:
            durations[event_id] = format_duration_info(
                arrival.occurred_at, event.occurred_at,
                "Time on site since last arrival", timezone=timezone,
            )
    return durations


def format_duration_info(
    start: datetime,
    end: datetime,
    detail: str,
    *,
    timezone: ZoneInfo,
) -> dict[str, Any]:
    total_minutes = max(0, int((end - start).total_seconds() // 60))
    total_hours = total_minutes // 60
    total_days = total_hours // 24
    minutes = total_minutes % 60
    hours = total_hours % 24
    if total_hours < 24:
        return {"label": f"{total_hours}hr{'s' if total_hours != 1 else ''} {minutes}m" if total_hours else f"{minutes}m"}
    if total_days < 14:
        return {"label": f"{_plural(total_days, 'Day')}, {hours}hr{'s' if hours != 1 else ''} {minutes}m"}
    return {
        "label": _format_reference_date(start, timezone),
        "tooltip": _verbose_duration(total_minutes),
        "tooltipDetail": detail,
    }


def is_movement_event(event: MovementRecord) -> bool:
    return event.decision == AccessDecision.GRANTED and event.direction in {
        AccessDirection.ENTRY,
        AccessDirection.EXIT,
    }


def normalize_plate(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", value).upper()


def _format_reference_date(value: datetime, timezone: ZoneInfo) -> str:
    local = value.astimezone(timezone)
    return local.strftime("%d/%m/%Y - %H:%M")


def _verbose_duration(total_minutes: int) -> str:
    total_days = max(0, total_minutes // (24 * 60))
    years = total_days // 365
    months = (total_days % 365) // 30
    days = (total_days % 365) % 30
    parts = [
        _plural(years, "Year") if years else None,
        _plural(months, "Month") if months else None,
        _plural(days, "Day") if days else None,
    ]
    return ", ".join(part for part in parts if part) or "Less than 1 Day"


def _plural(value: int, singular: str) -> str:
    return f"{value} {singular}{'' if value == 1 else 's'}"

