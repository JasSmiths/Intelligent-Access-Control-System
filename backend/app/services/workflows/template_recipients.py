"""Recipient visibility for individual @Variable occurrences in notification copy."""

from __future__ import annotations

import re
from typing import Any

from app.services.workflows.context import (
    AT_TOKEN_PATTERN,
    canonical_key,
    normalize_template_token_spacing,
)


class TemplateRecipientError(ValueError):
    pass


FIELDS = ("title_template", "message_template")
PREFIXES = {
    "mobile": ("home_assistant_mobile:", "apprise:"),
    "voice": ("home_assistant_tts:",),
}


def normalize_variable_recipients(
    action: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    raw = action.get("variable_recipients", {})
    if not isinstance(raw, dict) or any(key not in FIELDS for key in raw):
        raise TemplateRecipientError("Variable recipients must belong to a title or message.")
    result = {}
    for field, entries in raw.items():
        if not isinstance(entries, list):
            raise TemplateRecipientError("Variable recipient settings must be a list.")
        matches = list(AT_TOKEN_PATTERN.finditer(str(action.get(field) or "")))
        seen = set()
        normalized = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise TemplateRecipientError("Invalid variable recipient setting.")
            occurrence, name, targets = (
                entry.get("occurrence"),
                entry.get("name"),
                entry.get("target_ids"),
            )
            if (
                type(occurrence) is not int
                or not 0 <= occurrence < len(matches)
                or name != matches[occurrence].group(1)
                or occurrence in seen
            ):
                raise TemplateRecipientError(
                    "Variable recipient settings no longer match the template."
                )
            if not isinstance(targets, list) or any(
                not isinstance(target, str)
                or not target
                or len(target) > 255
                or target.endswith(":*")
                or not target.startswith(PREFIXES.get(str(action.get("type")), ()))
                for target in targets
            ):
                raise TemplateRecipientError(
                    "Select individual recipients from this delivery channel."
                )
            if str(action.get("type")) not in PREFIXES:
                raise TemplateRecipientError("This channel cannot restrict individual variables.")
            seen.add(occurrence)
            normalized.append(
                {
                    "occurrence": occurrence,
                    "name": name,
                    "target_ids": list(dict.fromkeys(targets)),
                }
            )
        if normalized:
            result[field] = sorted(normalized, key=lambda entry: entry["occurrence"])
    return result


def render_recipient_template(
    template: str,
    variables: dict[str, str],
    restrictions: list[dict[str, Any]],
    recipient: str | None = None,
) -> str:
    by_name = {canonical_key(key): value for key, value in variables.items()}
    rules = {entry["occurrence"]: entry for entry in restrictions}
    occurrence = -1
    omitted = False

    def replace(match: re.Match[str]) -> str:
        nonlocal occurrence, omitted
        occurrence += 1
        rule = rules.get(occurrence)
        if rule is not None and recipient not in rule["target_ids"]:
            omitted = True
            return ""
        return by_name.get(canonical_key(match.group(1)), "")

    rendered = AT_TOKEN_PATTERN.sub(replace, normalize_template_token_spacing(template)).strip()
    if omitted:
        rendered = re.sub(r"[ \t]{2,}", " ", rendered)
        rendered = re.sub(r"[ \t]+([,.;:!?])", r"\1", rendered)
    return rendered


def recipient_content(
    action: dict[str, Any], variables: dict[str, str]
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    restrictions = normalize_variable_recipients(action)

    def render(recipient: str | None) -> dict[str, str]:
        return {
            key: render_recipient_template(
                str(action.get(field) or ""),
                variables,
                restrictions.get(field, []),
                recipient,
            )
            for key, field in zip(("title", "message"), FIELDS)
        }

    targets = {
        target
        for entries in restrictions.values()
        for entry in entries
        for target in entry["target_ids"]
    }
    return render(None), {target: render(target) for target in sorted(targets)}


def content_for_recipient(
    action: dict[str, Any], recipient: str, subject: str = ""
) -> dict[str, str]:
    """Unmatched destinations always receive the copy with restricted values omitted."""
    scoped = action.get("recipient_content")
    if isinstance(scoped, dict) and recipient in scoped:
        return scoped[recipient]
    return {
        "title": str(action.get("title") or ("" if scoped is not None else subject)),
        "message": str(action.get("message") or ""),
    }
