"""Small natural-language helpers for Alfred guided flows.

Alfred's live routing is owned by the v3 LLM planner.  This module only keeps
the lightweight parsers still used by guided visitor-pass/schedule flows and
diagnostic answer repair.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.chat_contracts import DEFAULT_CHAT_TIMEZONE, SCHEDULE_DAY_ALIASES, SCHEDULE_DAY_PATTERN


class ChatRoutingMixin:
    def _looks_like_visitor_pass_request(self, lower: str) -> bool:
        return (
            "visitor pass" in lower
            or "guest pass" in lower
            or bool(re.search(r"\b(?:create|make|add|book|set\s*up|setup)\s+(?:a\s+)?pass\b", lower))
            or bool(re.search(r"\bpass\s+(?:for|to)\b", lower))
            or bool(re.search(r"\bpass\b.*\b(?:coming|arriving|visiting|expected|tomorrow|today|tonight)\b", lower))
            or "visitor coming" in lower
            or "guest coming" in lower
            or "visitor arriving" in lower
            or "guest arriving" in lower
            or "expected visitor" in lower
            or "expected guest" in lower
            or bool(
                re.search(
                    r"\b(visitor|guest)\b.*\b(pass|coming|arriving|expect|expected|cancel|delete|remove|revoke|visit)\b",
                    lower,
                )
            )
        )

    def _looks_like_calendar_integration_request(self, lower: str) -> bool:
        calendar_reference = "icloud" in lower or "calendar" in lower or "calendars" in lower
        sync_reference = any(word in lower for word in ["sync", "check", "scan", "refresh", "update", "pull"])
        gate_reference = any(phrase in lower for phrase in ["open gate", "gate pass", "visitor pass", "passes"])
        return calendar_reference and (sync_reference or gate_reference)

    def _looks_like_visitor_pass_create_request(self, lower: str) -> bool:
        if self._looks_like_visitor_pass_cancel_request(lower):
            return False
        if self._looks_like_visitor_pass_query_request(lower):
            return False
        return self._looks_like_visitor_pass_request(lower) and any(
            phrase in lower
            for phrase in [
                "coming",
                "arriving",
                "expecting",
                "expect ",
                "create",
                "new",
                "add",
                "book",
                "set up",
                "setup",
                "make",
                "coming over",
            ]
        )

    def _looks_like_visitor_pass_cancel_request(self, lower: str) -> bool:
        return self._looks_like_visitor_pass_request(lower) and bool(re.search(r"\b(cancel|delete|remove|revoke)\b", lower))

    def _looks_like_visitor_pass_query_request(self, lower: str) -> bool:
        if not self._looks_like_visitor_pass_request(lower):
            return False
        if re.search(r"\b(?:are there|any|do we have|have we got|show|list|check|find|lookup|what|which)\b", lower):
            return True
        status_query = re.search(
            r"\b(?:visitor passes|guest passes|passes)\b.*\b(?:today|tomorrow|tonight|active|scheduled|setup|set up)\b",
            lower,
        )
        create_marker = re.search(r"\b(?:create|make|add|book|set\s*up|setup|expect|expecting)\b", lower)
        return bool(status_query and not create_marker)

    def _is_pending_visitor_pass_create_cancel_message(self, lower: str) -> bool:
        return bool(re.fullmatch(r"(?:no|cancel|cancel that|cancel this|stop|never mind|nevermind|forget it|abort)", lower))

    def _should_abandon_pending_visitor_pass_create(self, lower: str) -> bool:
        if self._looks_like_visitor_pass_query_request(lower):
            return True
        if self._looks_like_device_action_request(lower) or self._looks_like_device_state_request(lower):
            return True
        if self._looks_like_schedule_create_request(lower) or self._looks_like_calendar_integration_request(lower):
            return True
        return bool(
            re.search(r"\b(?:status|help|show|list|check|what|why|when|where|open|close|shut)\b", lower)
            and not self._looks_like_visitor_pass_create_request(lower)
        )

    def _visitor_name_from_message(self, message: str) -> str | None:
        patterns = [
            r"(?:called|named)\s+([A-Za-z][A-Za-z' -]{1,48})",
            r"(?:visitor|guest)\s+(?:called|named)\s+([A-Za-z][A-Za-z' -]{1,48})",
            r"(?:expecting|expect|having|have)\s+(?:a\s+)?(?:visitor|guest)?\s*(?:called|named)?\s+([A-Za-z][A-Za-z' -]{1,48})",
            r"\b([A-Za-z][A-Za-z' -]{1,48})\s+(?:is\s+|'s\s+)?(?:coming|arriving|visiting)\b",
            r"(?:visitor|guest)\s+([A-Za-z][A-Za-z' -]{1,48})(?:\s+(?:at|on|tomorrow|today|tonight|coming|arriving)\b|$)",
            r"\bfor\s+([A-Za-z][A-Za-z' -]{1,48})(?:\s+(?:at|on|tomorrow|today|tonight)\b|$)",
            r"^([A-Za-z][A-Za-z' -]{1,48})(?:\s+(?:at|on|tomorrow|today|tonight|in)\b|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if not match:
                continue
            cleaned = self._clean_visitor_name_candidate(match.group(1))
            if cleaned:
                return cleaned
        return None

    def _clean_visitor_name_candidate(self, value: str) -> str | None:
        name = re.split(
            r"\b(?:at|around|about|on|today|tomorrow|tonight|this|next|in|from|for|with|coming|arriving|visiting)\b",
            value,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        name = re.sub(r"\b(?:a|an|the|visitor|guest|is|will|be|called|named)\b", " ", name, flags=re.IGNORECASE)
        name = " ".join(name.strip(" .,!?'\"").split())
        if not name or name.lower() in {
            "coming",
            "arriving",
            "visiting",
            "today",
            "tomorrow",
            "tonight",
            "pass",
            "passes",
            "pass setup",
            "passes setup",
            "setup",
            "set up",
        }:
            return None
        return name[:80]

    def _visitor_expected_time_from_message(
        self,
        message: str,
        timezone_name: str = DEFAULT_CHAT_TIMEZONE,
    ) -> str | None:
        text = message.strip()
        iso_match = re.search(r"\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?", text)
        if iso_match:
            raw = iso_match.group(0).replace(" ", "T")
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                parsed = None
            if parsed:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
                return parsed.astimezone(ZoneInfo(timezone_name)).isoformat()

        timezone = ZoneInfo(timezone_name)
        now = datetime.now(tz=timezone)
        lower = text.lower()
        relative = re.search(r"\bin\s+(\d{1,3})\s*(minute|minutes|mins|min|hour|hours|hrs|hr)\b", lower)
        if relative:
            amount = int(relative.group(1))
            unit = relative.group(2)
            delta = timedelta(hours=amount) if unit.startswith(("hour", "hr")) else timedelta(minutes=amount)
            return (now + delta).isoformat()

        time_match = re.search(
            r"\b(?:at|around|about|by)?\s*(?:approx(?:imately)?|roughly|circa|about|around)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
            lower,
        )
        if not time_match:
            time_match = re.search(
                r"\b(?:at|around|about|by)\s*(?:approx(?:imately)?|roughly|circa|about|around)?\s*(\d{1,2}):(\d{2})\b",
                lower,
            )
        if not time_match:
            if re.search(r"\b(noon|midday)\b", lower):
                hour, minute = 12, 0
            elif "midnight" in lower:
                hour, minute = 0, 0
            else:
                return None
        else:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            meridiem = time_match.group(3)
            if meridiem == "pm" and hour != 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
            if hour > 23 or minute > 59:
                return None

        days_offset = 1 if "tomorrow" in lower else 0
        expected = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days_offset)
        if days_offset == 0 and "today" not in lower and expected <= now:
            expected = expected + timedelta(days=1)
        return expected.isoformat()

    def _visitor_window_from_message(self, lower: str) -> int | None:
        match = re.search(r"(?:\+/-|plus/minus|window|within|for)\s*(\d{1,3})\s*(?:minutes|minute|mins|min|m)?", lower)
        if not match:
            return None
        minutes = int(match.group(1))
        if minutes in {30, 60, 90, 120, 180}:
            return minutes
        return max(1, min(minutes, 180))

    def _looks_like_schedule_create_request(self, lower: str) -> bool:
        return "schedule" in lower and any(word in lower for word in ["create", "new", "add", "make"])

    def _is_confirmation_message(self, lower: str) -> bool:
        return bool(re.search(r"\b(yes|confirm|confirmed|update|replace|proceed|go ahead|do it|approved|approve)\b", lower))

    def _is_rejection_message(self, lower: str) -> bool:
        return bool(re.search(r"\b(no|cancel|stop|leave|unchanged|do not|don't)\b", lower))

    def _refers_to_previous_timeframe(self, lower: str) -> bool:
        return any(phrase in lower for phrase in ["already told", "told you", "as i said", "same as before", "previous"])

    def _clean_schedule_name(self, message: str) -> str:
        name = re.sub(r"\s+", " ", message.strip(" .?\"'"))
        return name[:120].strip()

    def _parse_schedule_time_blocks(self, message: str) -> dict[str, list[dict[str, str]]] | None:
        lower = message.lower()
        if any(token in lower for token in ["24/7", "24-7", "24 hours", "all day every day"]):
            return {str(day): [{"start": "00:00", "end": "24:00"}] for day in range(7)}

        days = self._schedule_days_from_message(lower)
        time_range = self._schedule_time_range_from_message(lower)
        if not days or not time_range:
            return None

        start, end = time_range
        blocks: dict[str, list[dict[str, str]]] = {str(day): [] for day in range(7)}
        for day in days:
            blocks[str(day)].append({"start": start, "end": end})
        return blocks

    def _schedule_days_from_message(self, lower: str) -> list[int]:
        if any(phrase in lower for phrase in ["weekday", "week day", "workday", "work day"]):
            return list(range(5))
        if any(phrase in lower for phrase in ["weekend", "saturday and sunday", "sat and sun"]):
            return [5, 6]
        if any(phrase in lower for phrase in ["every day", "daily", "all week", "each day", "mon-sun", "monday to sunday"]):
            return list(range(7))

        range_match = re.search(
            rf"\b({SCHEDULE_DAY_PATTERN})\b"
            r"\s*(?:-|to|through|until|thru)\s*"
            rf"\b({SCHEDULE_DAY_PATTERN})\b",
            lower,
        )
        if range_match:
            start = self._schedule_day_index(range_match.group(1))
            end = self._schedule_day_index(range_match.group(2))
            if start is not None and end is not None:
                if start <= end:
                    return list(range(start, end + 1))
                return list(range(start, 7)) + list(range(0, end + 1))

        days: list[int] = []
        for token in re.findall(rf"\b({SCHEDULE_DAY_PATTERN})\b", lower):
            day = self._schedule_day_index(token)
            if day is not None and day not in days:
                days.append(day)
        return days

    def _schedule_day_index(self, value: str) -> int | None:
        normalized = value.lower()[:3]
        return SCHEDULE_DAY_ALIASES.get(value.lower(), SCHEDULE_DAY_ALIASES.get(normalized))

    def _schedule_time_range_from_message(self, lower: str) -> tuple[str, str] | None:
        match = re.search(
            r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|to|until|through|thru)\s*"
            r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
            lower,
        )
        if not match:
            return None

        start = self._schedule_minute_from_parts(match.group(1), match.group(2), match.group(3))
        end = self._schedule_minute_from_parts(match.group(4), match.group(5), match.group(6))
        if start is None or end is None:
            return None
        if end <= start and not match.group(3) and not match.group(6) and int(match.group(4)) <= 12:
            end += 12 * 60
        if start < 0 or end > 24 * 60 or end <= start:
            return None
        if start % 30 or end % 30:
            return None
        return self._format_schedule_minute(start), self._format_schedule_minute(end)

    def _schedule_minute_from_parts(self, hour_text: str, minute_text: str | None, meridiem: str | None) -> int | None:
        hour = int(hour_text)
        minute = int(minute_text or "0")
        if minute not in {0, 30}:
            return None
        if meridiem:
            if hour < 1 or hour > 12:
                return None
            if meridiem == "am":
                hour = 0 if hour == 12 else hour
            else:
                hour = 12 if hour == 12 else hour + 12
        if hour < 0 or hour > 24:
            return None
        return hour * 60 + minute

    def _format_schedule_minute(self, minute: int) -> str:
        if minute == 24 * 60:
            return "24:00"
        return f"{minute // 60:02d}:{minute % 60:02d}"

    def _chat_schedule_summary(self, time_blocks: dict[str, list[dict[str, str]]]) -> str:
        selected_slots = 0
        active_days = 0
        for intervals in time_blocks.values():
            day_slots = 0
            for interval in intervals:
                start = self._parse_schedule_summary_minute(str(interval["start"]))
                end = self._parse_schedule_summary_minute(str(interval["end"]))
                day_slots += max(0, (end - start) // 30)
            if day_slots:
                active_days += 1
                selected_slots += day_slots
        if not selected_slots:
            return "no allowed time"
        if selected_slots == 48 * 7:
            return "24/7"
        hours = selected_slots / 2
        display_hours = int(hours) if hours.is_integer() else round(hours, 1)
        return f"{display_hours}h across {active_days} day{'s' if active_days != 1 else ''}"

    def _parse_schedule_summary_minute(self, value: str) -> int:
        if value in {"24:00", "23:59"}:
            return 24 * 60
        hour, minute = value.split(":")
        return int(hour) * 60 + int(minute)

    def _looks_like_device_state_request(self, lower: str) -> bool:
        device_words = ["gate", "door", "garage", "cover"]
        if not any(word in lower for word in device_words):
            return False
        return bool(
            re.search(r"\b(?:is|are|was|were)\b[^?]*\b(?:open|closed|opening|closing|locked|unlocked)\b", lower)
            or re.search(r"\b(?:state|status)\b", lower)
            or re.search(r"\b(?:what(?:'s| is)|check)\b", lower)
        )

    def _looks_like_device_action_request(self, lower: str) -> bool:
        return self._looks_like_device_open_request(lower) or self._looks_like_device_close_request(lower)

    def _looks_like_device_open_request(self, lower: str) -> bool:
        if not any(word in lower for word in ["gate", "garage", "door", "cover"]):
            return False
        if re.search(r"\b(?:is|are|was|were)\b[^?]*\bopen\b", lower):
            return False
        return bool(
            re.search(r"^\s*(?:please\s+)?(?:confirm(?:ed)?\s+)?open\s+(?:the\s+|my\s+)?", lower)
            or re.search(r"\b(?:can|could|would)\s+you\s+open\s+(?:the\s+|my\s+)?", lower)
        )

    def _looks_like_device_close_request(self, lower: str) -> bool:
        if not any(word in lower for word in ["garage", "door", "cover"]):
            return False
        if re.search(r"\b(?:is|are|was|were)\b[^?]*\bclosed?\b", lower):
            return False
        return bool(
            re.search(r"^\s*(?:please\s+)?(?:confirm(?:ed)?\s+)?(?:close|shut)\s+(?:the\s+|my\s+)?", lower)
            or re.search(r"\b(?:can|could|would)\s+you\s+(?:close|shut)\s+(?:the\s+|my\s+)?", lower)
        )

    def _looks_like_access_diagnostic_request(self, lower: str) -> bool:
        diagnostic_terms = [
            "why",
            "why didn't",
            "why didnt",
            "did not",
            "didn't",
            "didnt",
            "slow",
            "slower",
            "longer",
            "latency",
            "timing",
            "took",
            "recognise",
            "recognize",
            "debug",
            "diagnose",
            "diagnostic",
            "notification",
            "notify",
            "notified",
            "alert",
            "failed",
            "failure",
            "problem",
            "issue",
            "reason",
            "cause",
            "explain",
        ]
        access_terms = [
            "lpr",
            "number plate",
            "numberplate",
            "plate",
            "scan",
            "read",
            "recognition",
            "process",
            "processing",
            "detection",
            "detected",
            "arrival",
            "entry",
            "event",
            "gate open",
            "gate",
            "vehicle",
            "car",
            "unknown",
            "stranger",
            "visitor",
            "access event",
            "access log",
        ]
        return any(term in lower for term in diagnostic_terms) and any(term in lower for term in access_terms)

    def _looks_like_lpr_timing_request(self, lower: str) -> bool:
        return any(
            term in lower
            for term in [
                "lpr",
                "plate",
                "number plate",
                "scan",
                "recognise",
                "recognize",
                "recognition",
                "process",
                "processing",
                "slow",
                "slower",
                "longer",
                "latency",
                "timing",
                "took",
                "ms",
                "millisecond",
                "milliseconds",
            ]
        )
