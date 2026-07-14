"""Narrow Alembic comparison policy for deliberately retained legacy data."""

from typing import Any


RETAINED_LEGACY_COLUMNS = frozenset(
    {
        ("people", "home_assistant_presence_entity_id"),
    }
)


def include_schema_object(
    object_: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """Ignore only reflected legacy columns explicitly retained for data migration."""

    if type_ != "column" or not reflected or compare_to is not None:
        return True
    table_name = str(getattr(getattr(object_, "table", None), "name", ""))
    return (table_name, str(name or "")) not in RETAINED_LEGACY_COLUMNS
