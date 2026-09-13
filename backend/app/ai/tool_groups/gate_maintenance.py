"""Alfred tool catalog entries for this domain."""

from __future__ import annotations

from typing import Any

from app.ai.tool_groups import gate_maintenance_handlers
from app.ai.tool_groups.metadata import apply_group_metadata
from app.ai.tools import AgentTool
from app.services.type_helpers import as_dict

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
                    handler=gate_maintenance_handlers.query_device_states,
                    example_inputs=(
                        {"target": "Top Gate", "kind": "gate"},
                        {"kind": "garage_door"},
                    ),
                    return_schema={
                        "answer_types": ["device_state"],
                        "records": "devices",
                    },
                ),
        AgentTool(
                    name="get_maintenance_status",
                    description="Return whether global Maintenance Mode is active, who enabled it, when it started, and how long it has been active.",
                    parameters={
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                    handler=gate_maintenance_handlers.get_maintenance_status,
                    example_inputs=({},),
                    return_schema={
                        "answer_types": ["maintenance_status"],
                        "result_keys": ["is_active", "started_at", "reason", "duration_seconds"],
                    },
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
                    handler=gate_maintenance_handlers.get_active_malfunctions,
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
                    handler=gate_maintenance_handlers.get_malfunction_history,
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
                    handler=gate_maintenance_handlers.trigger_manual_malfunction_override,
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
                    handler=gate_maintenance_handlers.enable_maintenance_mode,
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
                    handler=gate_maintenance_handlers.disable_maintenance_mode,
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
                    handler=gate_maintenance_handlers.open_device,
                    example_inputs=(
                        {"target": "Top Gate", "kind": "gate", "action": "open", "confirm": False},
                        {"target": "Main Garage Door", "kind": "garage_door", "action": "close", "confirm": False},
                    ),
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
                    handler=gate_maintenance_handlers.open_device,
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
                    handler=gate_maintenance_handlers.open_gate,
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
                    handler=gate_maintenance_handlers.toggle_maintenance_mode,
                ),
        ],
        categories=TOOL_CATEGORIES,
        button_handler=confirmation_button,
        summary_handler=confirmation_summary,
        status_labels={'query_device_states': 'Checking device states...', 'get_maintenance_status': 'Checking Maintenance Mode...', 'get_active_malfunctions': 'Checking gate malfunction state...', 'get_malfunction_history': 'Reviewing gate malfunction history...', 'trigger_manual_malfunction_override': 'Preparing gate malfunction override...', 'enable_maintenance_mode': 'Preparing Maintenance Mode...', 'disable_maintenance_mode': 'Preparing Maintenance Mode...', 'open_device': 'Preparing device command...', 'command_device': 'Preparing device command...', 'open_gate': 'Preparing gate open command...', 'toggle_maintenance_mode': 'Preparing Maintenance Mode...'},
        success_fields={'enable_maintenance_mode': ('enabled',), 'disable_maintenance_mode': ('disabled',), 'open_device': ('accepted', 'opened', 'closed'), 'command_device': ('accepted', 'opened', 'closed'), 'open_gate': ('opened',), 'toggle_maintenance_mode': ('changed',)},
        finish_after_confirmation=frozenset({'open_gate', 'command_device', 'open_device'}),
        confirmation_required=CONFIRMATION_REQUIRED_TOOLS,
    )


def confirmation_summary(tool_name: str, output: dict[str, Any]) -> str:
    if tool_name in {'open_device', 'command_device', 'open_gate'}:
        device = as_dict(output.get('device'))
        name = device.get('name') or output.get('target') or 'the gate'
        action = 'open' if tool_name == 'open_gate' else str(output.get('action') or 'open')
        past = 'Opened' if action == 'open' else 'Closed'
        success = bool(output.get('opened') if action == 'open' else output.get('closed'))
        return f'{past} {name}. Logged, tidy, and pleasingly uneventful.' if success else f'I could not {action} {name}.'
    if tool_name in {'toggle_maintenance_mode', 'enable_maintenance_mode', 'disable_maintenance_mode'}:
        if output.get('changed') or output.get('enabled') or output.get('disabled'):
            state = output.get('state') or ('enabled' if output.get('enabled') else 'disabled')
            return f'Maintenance Mode is now {state}.'
        return str(output.get('detail') or output.get('error') or 'I did not change Maintenance Mode.')
    return str(output.get("detail") or "Action completed.")


def confirmation_button(tool_name: str, output: dict[str, Any]) -> str:
    if tool_name in {'open_device', 'command_device', 'open_gate'}:
        action = 'open' if tool_name == 'open_gate' else str((output or {}).get('action') or 'open')
        return 'Close' if action == 'close' else 'Open'
    if tool_name in {'toggle_maintenance_mode', 'enable_maintenance_mode', 'disable_maintenance_mode'}:
        return 'Confirm'
    return "Confirm"
