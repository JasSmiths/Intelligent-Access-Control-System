"""Alfred tool catalog entries for this domain."""

from __future__ import annotations

from app.ai.tool_groups import general_handlers
from app.ai.tool_groups.metadata import admin_permissions, apply_group_metadata
from app.ai.tools import AgentTool

TOOL_CATEGORIES = {
    "resolve_human_entity": ("General",),
    "query_presence": ("Access_Logs", "General"),
    "get_system_users": ("Users_Settings",),
}

REQUIRED_PERMISSIONS = admin_permissions("get_system_users")


def build_tools() -> list[AgentTool]:
    return apply_group_metadata(
        [
        AgentTool(
                    name="resolve_human_entity",
                    description=(
                        "Resolve fuzzy human text such as a person name, vehicle description, plate, group, "
                        "or friendly gate/garage name to exact IACS IDs before technical tool calls."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Natural language entity reference, for example Steph, the Tesla, PE70, or main garage."},
                            "entity_types": {
                                "type": "array",
                                "items": {"type": "string", "enum": ["person", "vehicle", "group", "device", "visitor_pass"]},
                                "description": "Optional entity types to search. Defaults to all.",
                            },
                            "include_inactive": {"type": "boolean"},
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                    handler=general_handlers.resolve_human_entity,
                    categories=("General",),
                    example_inputs=(
                        {"query": "the missus", "entity_types": ["person", "vehicle"]},
                        {"query": "main garage", "entity_types": ["device"]},
                    ),
                    return_schema={
                        "answer_types": ["entity_resolution"],
                        "result_keys": ["status", "match", "matches"],
                    },
                ),
        AgentTool(
                    name="query_presence",
                    description="Return current presence state for everyone or a named person. This returns state only, not elapsed duration.",
                    parameters={
                        "type": "object",
                        "properties": {"person": {"type": "string"}},
                        "additionalProperties": False,
                    },
                    handler=general_handlers.query_presence,
                    example_inputs=(
                        {"person": "Steph"},
                        {},
                    ),
                    return_schema={
                        "answer_types": ["presence_state"],
                        "not_sufficient_for": ["absence_duration", "visit_duration"],
                        "records": "presence",
                    },
                ),
        AgentTool(
                    name="get_system_users",
                    description="Return non-sensitive system account roster for access-control context.",
                    parameters={
                        "type": "object",
                        "properties": {"include_inactive": {"type": "boolean"}},
                        "additionalProperties": False,
                    },
                    handler=general_handlers.get_system_users,
                ),
        ],
        categories=TOOL_CATEGORIES,
        status_labels={'resolve_human_entity': 'Resolving system entity...', 'query_presence': 'Checking presence logs...', 'get_system_users': 'Checking user directory...'},
        success_fields={},
        finish_after_confirmation=frozenset(),
        required_permissions=REQUIRED_PERMISSIONS,
    )
