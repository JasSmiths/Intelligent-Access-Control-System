"""Deterministic TypeScript projection of the deliberately small workflow schema."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.services.workflows.action_definitions import (
    ACTION_DEFINITIONS,
    Configuration,
    WorkflowConfigurationError,
)
from app.services.workflows.channel_definitions import CHANNEL_DEFINITIONS


class UnsupportedSchema(WorkflowConfigurationError):
    pass


def schema_type(
    schema: dict[str, Any], definitions: dict[str, Any], stack: tuple[str, ...] = ()
) -> str:
    """Fail closed when a future config requires a construct we cannot project."""
    allowed = {
        "$defs",
        "$ref",
        "title",
        "description",
        "default",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "anyOf",
        "minimum",
    }
    if unknown := schema.keys() - allowed:
        raise UnsupportedSchema(f"Unsupported schema keys: {', '.join(sorted(unknown))}.")
    if "$ref" in schema:
        reference = schema["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            raise UnsupportedSchema("Only local configuration schema references are supported.")
        name = reference.removeprefix("#/$defs/")
        if name in stack or name not in definitions:
            raise UnsupportedSchema("Recursive or missing configuration schema reference.")
        return schema_type(definitions[name], definitions, (*stack, name))
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, list) or not values:
            raise UnsupportedSchema("Configuration enums must have values.")
        return " | ".join(json.dumps(value) for value in values)
    if "anyOf" in schema:
        variants = schema["anyOf"]
        if not isinstance(variants, list) or len(variants) != 2 or {"type": "null"} not in variants:
            raise UnsupportedSchema("Only nullable configuration unions are supported.")
        return " | ".join(schema_type(variant, definitions, stack) for variant in variants)
    kind = schema.get("type")
    if "minimum" in schema and kind not in {"integer", "number"}:
        raise UnsupportedSchema("A configuration minimum needs a numeric field.")
    if kind in {"string", "boolean", "null"}:
        return str(kind)
    if kind in {"integer", "number"}:
        return "number"
    if kind == "array" and isinstance(schema.get("items"), dict):
        return f"Array<{schema_type(schema['items'], definitions, stack)}>"
    if kind == "object":
        if schema.get("additionalProperties") is not False:
            raise UnsupportedSchema("Configuration objects must explicitly forbid extra fields.")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise UnsupportedSchema("Configuration properties must be an object.")
        if not properties:
            return "Record<string, never>"
        fields = []
        for key, value in properties.items():
            optional = value.get("default", object()) is None
            projection = schema_type(value, definitions, stack)
            if optional:
                projection = projection.removesuffix(" | null").removeprefix("null | ")
            fields.append(f"{json.dumps(key)}{'?' if optional else ''}: {projection}")
        return "{ " + "; ".join(fields) + " }"
    raise UnsupportedSchema(f"Unsupported configuration schema type: {kind}.")


def configuration_type(configuration: type[Configuration]) -> str:
    schema = configuration.model_json_schema()
    return schema_type(schema, schema.get("$defs", {}))


def json_value(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def render_contracts() -> str:
    action_configs = "\n".join(
        f"  {json.dumps(definition.type)}: {configuration_type(definition.configuration)};"
        for definition in ACTION_DEFINITIONS
    )
    channel_configs = "\n".join(
        f"  {json.dumps(definition.type)}: {configuration_type(definition.configuration)};"
        for definition in CHANNEL_DEFINITIONS
    )
    action_defaults = {definition.type: definition.defaults() for definition in ACTION_DEFINITIONS}
    action_metadata = {
        definition.type: {
            "editor": definition.editor,
            "default_reason": definition.default_reason,
            "execution": definition.execution,
            "effect": definition.effect,
        }
        for definition in ACTION_DEFINITIONS
    }
    channel_metadata = {
        definition.type: {
            "spoken": definition.spoken,
            "supports_title": definition.supports_title,
            "supports_media": definition.supports_media,
            "supports_actionable": definition.supports_actionable,
        }
        for definition in CHANNEL_DEFINITIONS
    }
    channel_defaults = {
        definition.type: definition.defaults(editor=True) for definition in CHANNEL_DEFINITIONS
    }
    return f"""// Generated by python -m app.services.workflows.export_contracts. Do not edit.
export type ActionConfigByType = {{
{action_configs}
}};
export type AutomationActionType = keyof ActionConfigByType;
export type KnownAutomationAction = {{
  [K in AutomationActionType]: {{
    id: string; type: K; config: ActionConfigByType[K]; reason_template?: string;
  }}
}}[AutomationActionType];
export const ACTION_METADATA = {json_value(action_metadata)} as const;
const ACTION_DEFAULTS = {json_value(action_defaults)} satisfies ActionConfigByType;
export function isAutomationActionType(value: string): value is AutomationActionType {{
  return Object.hasOwn(ACTION_METADATA, value);
}}
export function defaultActionConfig<K extends AutomationActionType>(type: K): ActionConfigByType[K] {{
  return structuredClone(ACTION_DEFAULTS[type]) as ActionConfigByType[K];
}}
export type NotificationConfigByType = {{
{channel_configs}
}};
export type NotificationChannelType = keyof NotificationConfigByType;
export type NotificationActionConfig = NotificationConfigByType[NotificationChannelType];
export type KnownNotificationAction = {{
  [K in NotificationChannelType]: {{id: string; type: K}} & NotificationConfigByType[K]
}}[NotificationChannelType];
export const NOTIFICATION_CHANNELS = {json_value([definition.type for definition in CHANNEL_DEFINITIONS])} as const;
export const CHANNEL_METADATA = {json_value(channel_metadata)} as const;
const NOTIFICATION_DEFAULTS = {json_value(channel_defaults)} satisfies NotificationConfigByType;
export function isNotificationChannelType(value: string): value is NotificationChannelType {{
  return Object.hasOwn(NOTIFICATION_DEFAULTS, value);
}}
export function defaultNotificationConfig<K extends NotificationChannelType>(type: K): NotificationConfigByType[K] {{
  return structuredClone(NOTIFICATION_DEFAULTS[type]) as NotificationConfigByType[K];
}}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Reject stale generated workflow contracts."
    )
    parser.add_argument(
        "--stdout", action="store_true", help="Print without modifying the source tree."
    )
    args = parser.parse_args()
    rendered = render_contracts()
    target = Path(__file__).resolve().parents[4] / "frontend/src/api/generated/workflows.ts"
    if args.stdout:
        print(rendered, end="")
    elif args.check:
        if not target.is_file() or target.read_text() != rendered:
            raise SystemExit(
                "Generated workflow contracts are stale; regenerate from backend definitions."
            )
        print("Generated workflow contracts match backend definitions.")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered)


if __name__ == "__main__":
    main()
