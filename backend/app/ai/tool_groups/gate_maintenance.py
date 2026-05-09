"""Alfred tool catalog entries for this domain."""

from __future__ import annotations

from app.ai.tools import AgentTool
from app.ai.tool_groups.gate_maintenance_handlers import (
    disable_maintenance_mode,
    enable_maintenance_mode,
    get_active_malfunctions,
    get_maintenance_status,
    get_malfunction_history,
    open_device,
    open_gate,
    query_device_states,
    toggle_maintenance_mode,
    trigger_manual_malfunction_override,
)
from app.ai.tool_groups.metadata import apply_group_metadata


TOOL_CATEGORIES = {
    "query_device_states": ("Gate_Hardware", "General"),
    "get_maintenance_status": ("Maintenance", "Gate_Hardware", "Access_Diagnostics"),
    "get_active_malfunctions": ("Gate_Hardware", "Access_Diagnostics"),
    "get_malfunction_history": ("Gate_Hardware", "Access_Diagnostics"),
    "trigger_manual_malfunction_override": ("Gate_Hardware",),
    "enable_maintenance_mode": ("Maintenance",),
    "disable_maintenance_mode": ("Maintenance",),
    "open_device": ("Gate_Hardware",),
    "command_device": ("Gate_Hardware",),
    "open_gate": ("Gate_Hardware",),
    "toggle_maintenance_mode": ("Maintenance",),
}

CONFIRMATION_REQUIRED_TOOLS = {
    "command_device",
    "disable_maintenance_mode",
    "enable_maintenance_mode",
    "open_device",
    "open_gate",
    "toggle_maintenance_mode",
    "trigger_manual_malfunction_override",
}


def build_tools() -> list[AgentTool]:
    return apply_group_metadata(
        [
        AgentTool(
                    name="query_device_states",
                    description="Return current states for configured gates, doors, and garage doors.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "target": {
                                "type": "string",
                                "description": "Optional friendly device name, for example Top Gate, Back Door, or Main Garage Door.",
                            },
                            "kind": {
                                "type": "string",
                                "enum": ["all", "gate", "door", "garage_door"],
                                "description": "Optional device kind filter.",
                            },
                        },
                        "additionalProperties": False,
                    },
                    handler=query_device_states,
                ),
        AgentTool(
                    name="get_maintenance_status",
                    description="Return whether global Maintenance Mode is active, who enabled it, when it started, and how long it has been active.",
                    parameters={
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                    handler=get_maintenance_status,
                ),
        AgentTool(
                    name="get_active_malfunctions",
                    description="Return active or FUBAR gate malfunctions, including attempt counts, next retry times, status, and optional timeline.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "include_timeline": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                    handler=get_active_malfunctions,
                ),
        AgentTool(
                    name="get_malfunction_history",
                    description="Return historical gate malfunctions, optionally filtered by active, resolved, or fubar status.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "status": {"type": "string", "enum": ["active", "resolved", "fubar"]},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                            "include_timeline": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                    handler=get_malfunction_history,
                ),
        AgentTool(
                    name="trigger_manual_malfunction_override",
                    description="Manually recheck, run a recovery attempt now, mark resolved, or mark FUBAR for a gate malfunction. State-changing actions require confirm=true.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "malfunction_id": {"type": "string"},
                            "action": {
                                "type": "string",
                                "enum": ["recheck_live_state", "run_attempt_now", "mark_resolved", "mark_fubar"],
                            },
                            "reason": {"type": "string"},
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["malfunction_id", "action", "confirm"],
                        "additionalProperties": False,
                    },
                    handler=trigger_manual_malfunction_override,
                ),
        AgentTool(
                    name="enable_maintenance_mode",
                    description="Enable global Maintenance Mode. This disables automated actions and requires confirm=true.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "reason": {"type": "string", "description": "Human-readable reason to audit and use in notifications."},
                            "confirm": {"type": "boolean", "description": "Must be true before Maintenance Mode is enabled."},
                        },
                        "required": ["confirm"],
                        "additionalProperties": False,
                    },
                    handler=enable_maintenance_mode,
                ),
        AgentTool(
                    name="disable_maintenance_mode",
                    description="Disable global Maintenance Mode and resume automated actions. Requires confirm=true.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "confirm": {"type": "boolean", "description": "Must be true before Maintenance Mode is disabled."},
                        },
                        "required": ["confirm"],
                        "additionalProperties": False,
                    },
                    handler=disable_maintenance_mode,
                ),
        AgentTool(
                    name="open_device",
                    description=(
                        "Open or close a configured gate or garage door. "
                        "Opening gates and garage doors requires confirm=true; closing is supported for configured garage doors."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "target": {
                                "type": "string",
                                "description": "Friendly device name, for example Top Gate or Main Garage Door.",
                            },
                            "entity_id": {"type": "string", "description": "Optional internal device identifier when already known."},
                            "action": {"type": "string", "enum": ["open", "close"], "description": "Device action. Defaults to open."},
                            "kind": {
                                "type": "string",
                                "enum": ["all", "gate", "garage_door"],
                                "description": "Optional openable device kind filter.",
                            },
                            "reason": {"type": "string", "description": "Human-readable audit reason."},
                            "confirm": {
                                "type": "boolean",
                                "description": "Must be true before the device will be opened.",
                            },
                        },
                        "required": ["confirm"],
                        "additionalProperties": False,
                    },
                    handler=open_device,
                ),
        AgentTool(
                    name="command_device",
                    description=(
                        "Open or close a configured gate or garage door. Use action=close for garage door close requests. "
                        "This is a real-world side effect and requires confirm=true."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "target": {
                                "type": "string",
                                "description": "Friendly device name, for example Top Gate or Main Garage Door.",
                            },
                            "entity_id": {"type": "string", "description": "Optional internal device identifier when already known."},
                            "action": {"type": "string", "enum": ["open", "close"], "description": "Device action to perform."},
                            "kind": {
                                "type": "string",
                                "enum": ["all", "gate", "garage_door"],
                                "description": "Optional device kind filter.",
                            },
                            "reason": {"type": "string", "description": "Human-readable audit reason."},
                            "confirm": {
                                "type": "boolean",
                                "description": "Must be true before the device command will be executed.",
                            },
                        },
                        "required": ["action", "confirm"],
                        "additionalProperties": False,
                    },
                    handler=open_device,
                ),
        AgentTool(
                    name="open_gate",
                    description="Open a configured gate. This is a real-world side effect and requires confirm=true.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "target": {
                                "type": "string",
                                "description": "Optional friendly gate name. If omitted, the only configured gate is used.",
                            },
                            "reason": {"type": "string", "description": "Human-readable audit reason."},
                            "confirm": {"type": "boolean", "description": "Must be true before the gate will be opened."},
                        },
                        "required": ["confirm"],
                        "additionalProperties": False,
                    },
                    handler=open_gate,
                ),
        AgentTool(
                    name="toggle_maintenance_mode",
                    description="Enable or disable global Maintenance Mode. This is state-changing and requires confirm=true.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "state": {"type": "string", "enum": ["enabled", "disabled", "on", "off", "true", "false"]},
                            "reason": {"type": "string", "description": "Human-readable reason for enabling Maintenance Mode."},
                            "confirm": {"type": "boolean", "description": "Must be true before Maintenance Mode changes."},
                        },
                        "required": ["state", "confirm"],
                        "additionalProperties": False,
                    },
                    handler=toggle_maintenance_mode,
                ),
        ],
        categories=TOOL_CATEGORIES,
        confirmation_required=CONFIRMATION_REQUIRED_TOOLS,
    )
