from __future__ import annotations

import re
from typing import Any

AT_TOKEN_PATTERN = re.compile(r"@([A-Za-z][A-Za-z0-9_]*)")


def canonical_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def render_template(template: str, variables: dict[str, str]) -> str:
    by_canonical = {canonical_key(key): value for key, value in variables.items()}
    return AT_TOKEN_PATTERN.sub(lambda match: by_canonical.get(canonical_key(match.group(1)), ""), template).strip()


def referenced_variable_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, str):
        names.update(match.group(1) for match in AT_TOKEN_PATTERN.finditer(value))
    elif isinstance(value, dict):
        for item in value.values():
            names.update(referenced_variable_names(item))
    elif isinstance(value, list):
        for item in value:
            names.update(referenced_variable_names(item))
    return names


def normalize_string_list(value: Any, *, allow_scalar: bool = True) -> list[str]:
    if allow_scalar and isinstance(value, (str, bytes)):
        iterable: list[Any] = [value]
    elif isinstance(value, list):
        iterable = value
    else:
        iterable = []
    return [str(item).strip() for item in iterable if str(item).strip()]


def workflow_action_result(action: dict[str, Any], status: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": action["id"],
        "type": action["type"],
        "status": status,
        **{key: value for key, value in extra.items() if value is not None},
    }
