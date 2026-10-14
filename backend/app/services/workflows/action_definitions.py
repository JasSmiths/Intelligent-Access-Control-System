"""Closed automation action contracts. No runtime, ORM, provider or policy imports."""

from __future__ import annotations

import builtins
import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class WorkflowConfigurationError(ValueError):
    """An unsupported type or malformed new/pending configuration."""


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyConfiguration(Configuration):
    pass


class NotificationReference(Configuration):
    notification_rule_id: str = ""
    notification_rule_name: str = ""

    @field_validator("notification_rule_id", "notification_rule_name")
    @classmethod
    def canonical_reference(cls, value: str) -> str:
        return value.strip()


class GarageTargets(Configuration):
    target_entity_ids: list[str] = Field(default_factory=list)

    @field_validator("target_entity_ids")
    @classmethod
    def canonical_targets(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]


@dataclass(frozen=True)
class ActionDefinition:
    type: str
    group: str
    group_label: str
    label: str
    description: str
    configuration: builtins.type[Configuration]
    execution: Literal["transactional", "durable_effect"]
    effect: Literal["configuration", "hardware"]
    editor: Literal["none", "notification_reference", "garage_targets"]
    paused_by_maintenance: bool = False
    continue_after_unknown: bool = False
    default_reason: str = "Automation action from @EventType."
    def defaults(self) -> dict[str, Any]:
        return self.configuration().model_dump()

    def catalog(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "type": self.type,
            "label": self.label,
            "description": self.description,
            "default_config": self.defaults(),
            "editor": self.editor,
        }
        return value


ACTION_DEFINITIONS: tuple[ActionDefinition, ...] = (
    ActionDefinition(
        type="notification.enable",
        group="notifications",
        group_label="Notifications",
        label="Enable Notification",
        description="Enable an existing notification workflow.",
        configuration=NotificationReference,
        execution="transactional",
        effect="configuration",
        editor="notification_reference",
        paused_by_maintenance=True,
        continue_after_unknown=True,
    ),
    ActionDefinition(
        type="notification.disable",
        group="notifications",
        group_label="Notifications",
        label="Disable Notification",
        description="Disable an existing notification workflow.",
        configuration=NotificationReference,
        execution="transactional",
        effect="configuration",
        editor="notification_reference",
        paused_by_maintenance=True,
        continue_after_unknown=True,
    ),
    ActionDefinition(
        type="gate.open",
        group="gate_actions",
        group_label="Gate Actions",
        label="Open the Gate",
        description="Open configured gate entities through the gate controller.",
        configuration=EmptyConfiguration,
        execution="durable_effect",
        effect="hardware",
        editor="none",
        paused_by_maintenance=True,
        default_reason="Automation opened the gate for @DisplayName.",
    ),
    ActionDefinition(
        type="garage_door.open",
        group="garage_door_actions",
        group_label="Garage Door Actions",
        label="Open Garage Door",
        description="Open one or more configured garage door entities.",
        configuration=GarageTargets,
        execution="durable_effect",
        effect="hardware",
        editor="garage_targets",
        paused_by_maintenance=True,
        default_reason="Automation ran @EventType for @DisplayName.",
    ),
    ActionDefinition(
        type="garage_door.close",
        group="garage_door_actions",
        group_label="Garage Door Actions",
        label="Close Garage Door",
        description="Close one or more configured garage door entities.",
        configuration=GarageTargets,
        execution="durable_effect",
        effect="hardware",
        editor="garage_targets",
        paused_by_maintenance=True,
        default_reason="Automation ran @EventType for @DisplayName.",
    ),
    ActionDefinition(
        type="maintenance_mode.enable",
        group="maintenance_mode",
        group_label="Maintenance Mode",
        label="Enable Maintenance Mode",
        description="Enable the global automation kill-switch.",
        configuration=EmptyConfiguration,
        execution="durable_effect",
        effect="configuration",
        editor="none",
        paused_by_maintenance=True,
        default_reason="Automation changed Maintenance Mode: @Subject.",
    ),
    ActionDefinition(
        type="maintenance_mode.disable",
        group="maintenance_mode",
        group_label="Maintenance Mode",
        label="Disable Maintenance Mode",
        description="Disable the global automation kill-switch.",
        configuration=EmptyConfiguration,
        execution="durable_effect",
        effect="configuration",
        editor="none",
        default_reason="Automation changed Maintenance Mode: @Subject.",
    ),
)


def definitions_by_type(
    definitions: tuple[ActionDefinition, ...] = ACTION_DEFINITIONS,
) -> dict[str, ActionDefinition]:
    result = {definition.type: definition for definition in definitions}
    if len(result) != len(definitions):
        raise WorkflowConfigurationError("Duplicate workflow action definition.")
    return result


ACTION_BY_TYPE = definitions_by_type()


def require_exhaustive_bindings(
    definitions: Mapping[str, object], bindings: Mapping[str, object], *, owner: str
) -> None:
    if missing := (definitions.keys() - bindings.keys()):
        raise WorkflowConfigurationError(
            f"{owner} is missing bindings: {', '.join(sorted(missing))}."
        )
    if unknown := (bindings.keys() - definitions.keys()):
        raise WorkflowConfigurationError(
            f"{owner} has unknown bindings: {', '.join(sorted(unknown))}."
        )


def action_definition(action_type: str) -> ActionDefinition:
    definition = ACTION_BY_TYPE.get(action_type)
    if definition is None:
        raise WorkflowConfigurationError(f"Unsupported automation action: {action_type}.")
    return definition


def validate_configuration(
    configuration: builtins.type[Configuration], value: object
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowConfigurationError("Action configuration must be an object.")
    try:
        return configuration.model_validate(value).model_dump(exclude_none=True)
    except ValidationError as exc:
        fields = sorted(
            {
                ".".join(str(part) for part in error["loc"]) or "config"
                for error in exc.errors(include_input=False)
            }
        )
        raise WorkflowConfigurationError(
            f"Invalid action configuration: {', '.join(fields)}."
        ) from exc


def validate_action(action: object) -> dict[str, Any]:
    """Validate a pending/new action without mutating any stored snapshot."""
    if not isinstance(action, dict):
        raise WorkflowConfigurationError("An automation action must be an object.")
    if action.keys() - {"id", "type", "config", "reason_template"}:
        raise WorkflowConfigurationError("Unknown automation action fields.")
    action_type = action.get("type")
    if not isinstance(action_type, str) or not action_type.strip():
        raise WorkflowConfigurationError("An automation action needs its type.")
    definition = action_definition(action_type)
    if "id" in action and (not isinstance(action["id"], str) or not action["id"].strip()):
        raise WorkflowConfigurationError("An automation action needs a non-empty ID.")
    if not isinstance(action.get("reason_template", ""), str):
        raise WorkflowConfigurationError("An automation reason must be text.")
    return validate_configuration(definition.configuration, action.get("config", {}))


def validate_actions(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise WorkflowConfigurationError("Automation actions must be a list.")
    actions = []
    for index, raw in enumerate(value):
        config = validate_action(raw)
        assert isinstance(raw, dict)
        actions.append(
            {
                "id": raw.get("id", f"action-{index + 1}"),
                "type": raw["type"],
                "config": config,
                "reason_template": raw.get("reason_template", ""),
            }
        )
    return actions


def execution_actions(value: object) -> list[dict[str, Any]]:
    """Keep malformed source work durable so recovery can hold it before attempt."""
    values = value if isinstance(value, list) and value else [value]
    actions = []
    for index, raw in enumerate(values):
        try:
            actions.extend(validate_actions([raw]))
            # IDs omitted in historical rules retain their original list ordinal.
            if isinstance(raw, dict) and "id" not in raw:
                actions[-1]["id"] = f"action-{index + 1}"
        except WorkflowConfigurationError:
            retained: dict[str, Any] = (
                copy.deepcopy(raw)
                if isinstance(raw, dict)
                else {"config": {"unavailable_action": copy.deepcopy(raw)}}
            )
            if not isinstance(retained.get("id"), str) or not retained["id"].strip():
                retained["id"] = f"action-{index + 1}"
            if not isinstance(retained.get("type"), str) or not retained["type"].strip():
                retained["type"] = "unsupported_action_configuration"
            actions.append(retained)
    return actions


def pending_action_error(plan: list[dict[str, Any]]) -> str | None:
    for item in plan:
        if item["state"] == "pending":
            try:
                validate_action(item.get("action"))
            except WorkflowConfigurationError:
                return "unsupported_action_configuration"
    return None


def historical_action_config(action_type: str, config: dict[str, Any]) -> dict[str, Any]:
    """Retain the established read normalization; strict writes use validate_actions."""
    definition = ACTION_BY_TYPE.get(action_type)
    if definition is None:
        return copy.deepcopy(config)
    if definition.configuration is NotificationReference:
        return {key: str(config.get(key) or "").strip() for key in definition.defaults()}
    if definition.configuration is GarageTargets:
        value = config.get("target_entity_ids")
        values = (
            value if isinstance(value, list) else [value] if isinstance(value, (str, bytes)) else []
        )
        return {"target_entity_ids": [str(item).strip() for item in values if str(item).strip()]}
    return {}
