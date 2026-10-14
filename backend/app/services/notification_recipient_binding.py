"""Catalog-origin binding for positional notification recipients.

An ordered-list fingerprint deliberately holds selections after ANY list edit or
auth-root rotation. Neither save nor dispatch invents a new binding for old IDs.
Explicit all-recipient delivery has no positional dependency unless template
visibility refers to a particular Apprise occurrence.
"""
import hmac
from typing import Any

from app.modules.notifications.apprise_urls import split_apprise_urls
from app.services.integration_settings import apprise_revision


def referenced_recipients(action: dict[str, Any]) -> list[str]:
    values = list(action.get("target_ids") or []) if action.get("target_mode", "all") != "all" else []
    restrictions = action.get("variable_recipients")
    if isinstance(restrictions, dict):
        for entries in restrictions.values():
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and isinstance(entry.get("target_ids"), list):
                        values.extend(entry["target_ids"])
    return [value for value in values if isinstance(value, str)]


def recipient_binding_error(action: dict[str, Any], configured: str) -> str | None:
    kind = action.get("type")
    if not isinstance(kind, str) or kind not in {"mobile", "voice"} or action.get("delivery_mode") == "literal":
        return None
    mode = action.get("target_mode", "all")
    targets = action.get("target_ids", [])
    if not isinstance(mode, str) or mode not in {"all", "many", "selected"} or not isinstance(targets, list):
        return "recipient_selection_invalid"
    if mode != "all":
        prefixes = ("apprise:", "home_assistant_mobile:") if kind == "mobile" else ("home_assistant_tts:",)
        if not targets or any(not isinstance(value, str) or not value.startswith(prefixes)
                              or value.endswith(":*") for value in targets):
            return "recipient_selection_invalid"
    count = len(split_apprise_urls(configured))
    positions = [value for value in referenced_recipients(action) if value.startswith("apprise:")]
    for value in positions:
        suffix = value.removeprefix("apprise:")
        if not suffix.isascii() or not suffix.isdecimal() or len(suffix) > len(str(count)) or str(int(suffix)) != suffix or int(suffix) >= count:
            return "recipient_selection_invalid"
    frozen = action.get("frozen_apprise_indexes", [])
    if not isinstance(frozen, list) or any(type(index) is not int or not 0 <= index < count for index in frozen):
        return "recipient_selection_invalid"
    if positions:
        supplied = action.get("apprise_revision")
        if not isinstance(supplied, str) or len(supplied) != 64 or not supplied.isascii():
            return "recipient_binding_missing"
        if not hmac.compare_digest(supplied, apprise_revision(configured)):
            return "recipient_binding_stale"
    return None
