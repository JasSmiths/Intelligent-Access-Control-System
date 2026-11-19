"""Pure recurrence calculation for automation rules."""

import random
import re
from copy import copy
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from app.services.type_helpers import as_dict
from app.services.workflows.automation_definition import (
    TIME_TRIGGER_KEYS,
    ensure_aware,
    parse_datetime,
    safe_int,
)


def next_run_for_triggers(
    triggers: list[dict[str, Any]],
    *,
    now: datetime,
    last_fired_at: datetime | None = None,
) -> datetime | None:
    candidates = [
        next_run_for_trigger(trigger, now=now, last_fired_at=last_fired_at)
        for trigger in triggers
        if str(trigger.get("type") or "") in TIME_TRIGGER_KEYS
    ]
    valid = [candidate for candidate in candidates if candidate is not None]
    return min(valid) if valid else None


def due_time_trigger(
    triggers: list[dict[str, Any]],
    *,
    now: datetime,
    last_fired_at: datetime | None = None,
    scheduled_for: datetime | None = None,
) -> dict[str, Any] | None:
    scheduled_for = ensure_aware(scheduled_for) if scheduled_for else None
    due = []
    for trigger in triggers:
        if trigger["type"] not in TIME_TRIGGER_KEYS:
            continue
        if trigger["type"] == "time.every_x" and scheduled_for and scheduled_for <= now:
            due.append((scheduled_for, trigger))
            continue
        baseline = (last_fired_at or scheduled_for or now) - timedelta(seconds=1)
        next_run = next_run_for_trigger(trigger, now=baseline, last_fired_at=last_fired_at)
        if next_run and next_run <= now:
            due.append((next_run, trigger))
    due.sort(key=lambda item: item[0])
    return due[0][1] if due else None


def next_run_for_trigger(
    trigger: dict[str, Any],
    *,
    now: datetime,
    last_fired_at: datetime | None = None,
) -> datetime | None:
    trigger_type = str(trigger.get("type") or "")
    config = as_dict(trigger.get("config"))
    now = ensure_aware(now)
    end_at = parse_datetime(config.get("end_at"))
    if end_at and end_at <= now:
        return None
    if trigger_type == "time.specific_datetime":
        run_at = parse_datetime(config.get("run_at"))
        if not run_at:
            return None
        recurrence = str(config.get("recurrence") or "none")
        if recurrence == "none" or config.get("single_use", True):
            return run_at if run_at > now and not last_fired_at else None
        expression = cron_from_recurrence(run_at, recurrence)
        candidate = cron_next(expression, now, run_at.tzinfo or UTC)
    elif trigger_type == "time.every_x":
        interval = safe_int(config.get("interval"), default=1, minimum=1)
        unit = str(config.get("unit") or "minutes")
        delta = (
            timedelta(**{unit: interval})
            if unit in {"minutes", "hours", "days"}
            else timedelta(minutes=interval)
        )
        start_at = parse_datetime(config.get("start_at")) or now
        candidate = start_at if start_at > now else ((last_fired_at or now) + delta)
        while candidate <= now:
            candidate += delta
    elif trigger_type == "time.cron":
        expression = str(config.get("cron_expression") or "").strip()
        timezone = timezone_for(config.get("timezone"))
        candidate = cron_next(expression, now, timezone)
    else:
        return None
    if end_at and candidate and candidate > end_at:
        return None
    return candidate.astimezone(UTC) if candidate else None


def cron_from_recurrence(run_at: datetime, recurrence: str) -> str:
    local = ensure_aware(run_at)
    if recurrence == "daily":
        return f"{local.minute} {local.hour} * * *"
    if recurrence == "weekly":
        return f"{local.minute} {local.hour} * * {(local.weekday() + 1) % 7}"
    if recurrence == "monthly":
        return f"{local.minute} {local.hour} {local.day} * *"
    return f"{local.minute} {local.hour} * * *"


# IACS accepts stepped starts as ranges through the end of the field. A
# maximum-to-maximum range represents a full cycle, including in saved rules.
_FIELD_LIMITS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6), (0, 59), (1970, 2099))
_STEPPED_START = re.compile(r"(?:^|(?<=,))([0-9]+|[a-z]{3})/(?=[0-9]+(?:,|$))", re.IGNORECASE)
_RANDOM_STEP = re.compile(r"r(?:\(([0-9]+)-([0-9]+)\))?/([0-9]+)", re.IGNORECASE)
_ZERO_DAY = re.compile(r"(?:^|(?<=[,-]))0+(?=[,/\-]|$)")
_EXTENDED_SUNDAY = re.compile(r"(?:^|[,(l-])0*7(?=$|[),/#-])", re.IGNORECASE)


def cron_fields(expression: str) -> list[str]:
    if not expression.isascii():
        expression = "".join(str(int(char)) if char.isdecimal() else char for char in expression)
    fields = expression.split()
    if len(fields) not in (5, 6, 7):
        return fields  # croniter also accepts aliases such as @daily.
    if len(fields) > 5 and _EXTENDED_SUNDAY.search(fields[4]):
        return []  # Sunday is 0 in the seconds/year forms accepted by IACS.
    if len(fields) == 6:
        fields[2] = _ZERO_DAY.sub("1", fields[2])
    for index, field in enumerate(fields):
        if "/" not in field:
            continue
        minimum, maximum = _FIELD_LIMITS[index]
        random_step = _RANDOM_STEP.fullmatch(field)
        if random_step:
            try:
                begin = int(random_step[1] or minimum)
                end = int(random_step[2] or maximum)
                step = int(random_step[3])
            except ValueError:
                return []
            if not minimum <= begin < end <= maximum or not 0 < step <= end - begin + 1:
                return []  # An out-of-range draw cannot safely schedule an operation.
            if step == end - begin + 1:
                # Make the equal range explicit when the draw lands on its end.
                fields[index] = f"{random.randint(begin, end)}-{end}/{step}"
        else:
            fields[index] = _STEPPED_START.sub(r"\g<1>-" + str(maximum) + "/", field)
    return fields


def cron_next(expression: str, now: datetime, timezone: Any) -> datetime | None:
    fields = cron_fields(expression)
    if not fields:
        return None
    localized = ensure_aware(now).astimezone(timezone)
    try:
        schedule = croniter(" ".join(fields), localized)
        # A combined DOM/DOW schedule must have a future match on both sides.
        # Otherwise a previously dormant rule might begin operating devices.
        limited_calendar = schedule.expanded[3] != ["*"] or (
            len(schedule.expanded) == 7 and schedule.expanded[6] != ["*"]
        )
        if (
            limited_calendar
            and schedule.expanded[2][0] != "*"
            and schedule.expanded[4][0] != "*"
            and not schedule.nth_weekday_of_month
            and not schedule.nearest_weekday
        ):
            candidates: list[datetime] = []
            for day_field in (2, 4):
                # Both branches use exactly the same random-field draw.
                branch = copy(schedule)
                branch.expanded = schedule.expanded.copy()
                branch.expanded[day_field] = ["*"]
                candidates.append(branch.get_next(datetime))
            return min(candidates)
        return schedule.get_next(datetime)
    except ValueError:
        # Includes croniter parse/date errors and oversized integer conversions.
        return None


def timezone_for(value: Any) -> ZoneInfo:
    try:
        return ZoneInfo(str(value or "UTC"))
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")
