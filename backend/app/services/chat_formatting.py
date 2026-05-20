"""Direct Alfred response formatting helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.chat_contracts import DEFAULT_CHAT_TIMEZONE
from app.services.type_helpers import as_dict


class ChatResultFormattingMixin:
    def _device_open_direct_text(self, output: dict[str, Any]) -> str:
        device = as_dict(output.get("device"))
        name = str(device.get("name") or output.get("target") or "that device").strip()
        action = str(output.get("action") or "open")
        if output.get("requires_details"):
            return str(output.get("detail") or f"Which gate or garage door should I {action}?")
        if output.get("requires_confirmation"):
            return f"Please confirm before I {action} {name}. I'll keep the cape off this one until you press the button."
        success = bool(output.get("opened") if action == "open" else output.get("closed"))
        if success:
            return f"{'Opened' if action == 'open' else 'Closed'} {name}. Logged, tidy, and pleasingly uneventful."
        return str(output.get("detail") or output.get("error") or f"I could not {action} {name}.")

    def _schedule_delete_direct_text(self, output: dict[str, Any]) -> str:
        schedule = as_dict(output.get("schedule"))
        name = str(schedule.get("name") or output.get("schedule_name") or "that schedule").strip()
        if output.get("requires_confirmation"):
            return str(output.get("detail") or f"Delete the {name} schedule? Use the confirmation button to continue.")
        if output.get("deleted"):
            return f"Deleted {name}."
        if output.get("dependencies"):
            return f"I cannot delete {name} because it is still assigned. Remove its assignments first, then try again."
        return str(output.get("detail") or output.get("error") or f"I could not delete {name}.")

    def _camera_snapshot_direct_text(self, output: dict[str, Any]) -> str:
        if output.get("fetched"):
            return "Here's the latest snapshot."
        camera = output.get("camera") or "that camera"
        detail = str(output.get("error") or "I could not fetch the snapshot.")
        return f"I couldn't fetch {camera}: {detail}"

    def _chat_time_from_iso(self, value: str) -> str | None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(ZoneInfo(DEFAULT_CHAT_TIMEZONE)).strftime("%H:%M")
