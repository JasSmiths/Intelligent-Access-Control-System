"""Alfred tool catalog entries for this domain."""

from __future__ import annotations

from app.ai.tools import (
    AgentTool,
    get_system_users,
    query_presence,
    resolve_human_entity,
)


def build_tools() -> list[AgentTool]:
    return [
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
                                "items": {"type": "string", "enum": ["person", "vehicle", "group", "device"]},
                                "description": "Optional entity types to search. Defaults to all.",
                            },
                            "include_inactive": {"type": "boolean"},
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                    handler=resolve_human_entity,
                    categories=("General",),
                ),
        AgentTool(
                    name="query_presence",
                    description="Return current presence state for everyone or a named person.",
                    parameters={
                        "type": "object",
                        "properties": {"person": {"type": "string"}},
                        "additionalProperties": False,
                    },
                    handler=query_presence,
                ),
        AgentTool(
                    name="get_system_users",
                    description="Return non-sensitive system account roster for access-control context.",
                    parameters={
                        "type": "object",
                        "properties": {"include_inactive": {"type": "boolean"}},
                        "additionalProperties": False,
                    },
                    handler=get_system_users,
                ),
    ]
