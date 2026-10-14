"""Closed notification channel contracts, independent of dispatch and persistence."""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.services.workflows.action_definitions import (
    Configuration,
    WorkflowConfigurationError,
    validate_configuration,
)

GateMalfunctionStage = Literal["initial", "30m", "60m", "2hrs", "fubar", "resolved"]
NotificationTargetMode = Literal["all", "many", "selected"]


class NotificationMedia(Configuration):
    attach_camera_snapshot: bool = False
    camera_id: str = ""


class NotificationActionable(Configuration):
    enabled: bool = False
    action: Literal["", "gate.open"] = ""

    @model_validator(mode="after")
    def enabled_action(self) -> NotificationActionable:
        if self.enabled and not self.action:
            raise ValueError("An enabled notification action needs its action.")
        return self


class VariableRecipientRestriction(Configuration):
    occurrence: int = Field(ge=0)
    name: str
    target_ids: list[str]


class VariableRecipients(Configuration):
    title_template: list[VariableRecipientRestriction] | None = None
    message_template: list[VariableRecipientRestriction] | None = None


class NotificationConfiguration(Configuration):
    target_mode: NotificationTargetMode = "all"
    target_ids: list[str] = Field(default_factory=list)
    apprise_revision: str | None = None
    title_template: str = ""
    message_template: str = ""
    gate_malfunction_stages: list[GateMalfunctionStage] = Field(default_factory=list)
    media: NotificationMedia = Field(default_factory=NotificationMedia)
    actionable: NotificationActionable = Field(default_factory=NotificationActionable)
    variable_recipients: VariableRecipients | None = None


    @field_validator("apprise_revision")
    @classmethod
    def valid_apprise_revision(cls, value: str | None) -> str | None:
        if value is not None and (len(value) != 64 or any(c not in "0123456789abcdef" for c in value)):
            raise ValueError("A recipient binding must be an opaque configuration fingerprint.")
        return value


@dataclass(frozen=True)
class ChannelDefinition:
    type: str
    configuration: builtins.type[NotificationConfiguration]
    title_template: str
    message_template: str
    recipient_prefixes: tuple[str, ...] = ()
    spoken: bool = False
    supports_title: bool = True
    supports_media: bool = True
    supports_actionable: bool = False

    def defaults(self, *, editor: bool = False) -> dict[str, Any]:
        value = self.configuration().model_dump(exclude_none=True)
        if editor:
            value.update(title_template=self.title_template, message_template=self.message_template)
        return value


CHANNEL_DEFINITIONS: tuple[ChannelDefinition, ...] = (
    ChannelDefinition(
        "mobile",
        NotificationConfiguration,
        "@FirstName arrived at the gate",
        "@FirstName arrived in the @VehicleName. Gate status: @GateStatus.",
        ("home_assistant_mobile:", "apprise:"),
        supports_actionable=True,
    ),
    ChannelDefinition(
        "voice",
        NotificationConfiguration,
        "",
        "@FirstName has arrived at the gate.",
        ("home_assistant_tts:",),
        spoken=True,
        supports_title=False,
        supports_media=False,
    ),
    ChannelDefinition(
        "in_app",
        NotificationConfiguration,
        "@FirstName arrived at the gate",
        "@FirstName arrived in the @VehicleName.",
    ),
)
CHANNEL_BY_TYPE = {definition.type: definition for definition in CHANNEL_DEFINITIONS}
if len(CHANNEL_BY_TYPE) != len(CHANNEL_DEFINITIONS):
    raise WorkflowConfigurationError("Duplicate notification channel definition.")


def channel_definition(channel: str) -> ChannelDefinition:
    definition = CHANNEL_BY_TYPE.get(channel)
    if definition is None:
        raise WorkflowConfigurationError(f"Unsupported notification channel: {channel}.")
    return definition


def validate_notification_action(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowConfigurationError("A notification action must be an object.")
    channel = value.get("type")
    if not isinstance(channel, str):
        raise WorkflowConfigurationError("A notification action needs its channel.")
    definition = channel_definition(channel)
    if "id" in value and (not isinstance(value["id"], str) or not value["id"].strip()):
        raise WorkflowConfigurationError("A notification action needs a non-empty ID.")
    config = {key: item for key, item in value.items() if key not in {"id", "type"}}
    return validate_configuration(definition.configuration, config)


def validate_notification_actions(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise WorkflowConfigurationError("Notification actions must be a list.")
    actions = []
    for index, raw in enumerate(value):
        config = validate_notification_action(raw)
        assert isinstance(raw, dict)
        actions.append({"id": raw.get("id", f"action-{index + 1}"), "type": raw["type"], **config})
    return actions


def pending_notification_error(plan: object) -> str | None:
    """Validate explicit rendered/literal adapters without rewriting frozen data."""
    if not isinstance(plan, list):
        return "execution_snapshot_unavailable"
    states = {"pending", "attempting", "accepted", "failed", "skipped", "unknown"}
    for item in plan:
        if not isinstance(item, dict) or not isinstance(item.get("state"), str) or item["state"] not in states:
            return "execution_snapshot_unavailable"
        if item["state"] != "pending":
            continue
        action = item.get("action")
        if not isinstance(action, dict) or not isinstance(item.get("rule"), dict):
            return "unsupported_action_configuration"
        try:
            definition = channel_definition(str(action.get("type") or ""))
            if action.get("delivery_mode") == "literal":
                # These existing producers identify the action by ordered run
                # index: supervised manual tests, actionable and resident output.
                allowed = {
                    "id",
                    "type",
                    "delivery_mode",
                    "target",
                    "title",
                    "message",
                    "configured_default",
                    "actionable_output",
                    "resident_recovery_output",
                }
                if (
                    definition.type not in {"mobile", "voice"}
                    or action.keys() - allowed
                    or any(
                        not isinstance(action.get(key), str)
                        for key in ("target", "title", "message")
                    )
                    or not action["target"]
                    or (
                        "id" in action
                        and (not isinstance(action["id"], str) or not action["id"].strip())
                    )
                    or (
                        "configured_default" in action
                        and type(action["configured_default"]) is not bool
                    )
                    or any(
                        key in action and not isinstance(action[key], dict)
                        for key in ("actionable_output", "resident_recovery_output")
                    )
                ):
                    return "unsupported_action_configuration"
                continue
            fields = definition.configuration.model_fields
            rendered = {
                "id",
                "type",
                "title",
                "message",
                "snapshot",
                "recipient_content",
                "frozen_voice_targets",
                "frozen_mobile_targets",
                "frozen_apprise_indexes",
            }
            if (
                action.keys() - fields.keys() - rendered
                or not isinstance(action.get("id"), str)
                or not action["id"].strip()
                or any(
                    key in action and not isinstance(action[key], str)
                    for key in ("title", "message")
                )
                or (
                    "snapshot" in action
                    and action["snapshot"] is not None
                    and not isinstance(action["snapshot"], dict)
                )
                or (
                    "recipient_content" in action
                    and not isinstance(action["recipient_content"], dict)
                )
                or any(
                    key in action
                    and (
                        not isinstance(action[key], list)
                        or any(not isinstance(value, str) for value in action[key])
                    )
                    for key in ("frozen_voice_targets", "frozen_mobile_targets")
                )
                or (
                    "frozen_apprise_indexes" in action
                    and (
                        not isinstance(action["frozen_apprise_indexes"], list)
                        or any(
                            type(value) is not int or value < 0
                            for value in action["frozen_apprise_indexes"]
                        )
                    )
                )
            ):
                return "unsupported_action_configuration"
            validate_configuration(
                definition.configuration,
                {key: action[key] for key in fields if key in action},
            )
        except WorkflowConfigurationError:
            return "unsupported_action_configuration"
    return None
