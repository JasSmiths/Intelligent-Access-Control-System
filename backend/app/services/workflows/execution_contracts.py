"""Checked internal workflow values with unchanged durable JSON formats.

Runtime execution crosses the JSON boundary once. Variable provider metadata is
retained, but action identity, state and result certainty are checked explicitly.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, NotRequired, TypedDict, cast

AutomationStatus = Literal["success", "queued", "failed", "skipped", "unknown"]
CheckpointState = Literal["succeeded", "failed", "skipped", "unknown"]
NotificationState = Literal["pending", "attempting", "accepted", "failed", "skipped", "unknown"]


class WorkflowAction(TypedDict):
    id: str
    type: str
    config: NotRequired[dict[str, Any]]
    reason_template: NotRequired[str]


class AutomationExecution(TypedDict):
    run_id: str
    claim_token: str
    index: int
    operation_id: str
    idempotency_key: str
    target_plan: NotRequired[dict[str, Any]]
    target_plans: NotRequired[list[dict[str, Any]]]
    expires_at: NotRequired[datetime | None]
    automatic_entry_policy: NotRequired[bool]


def checked_execution(value: object) -> AutomationExecution:
    if not isinstance(value, dict):
        raise TypeError("An automation execution checkpoint must be an object.")
    for key in ("run_id", "claim_token", "operation_id"):
        if not isinstance(value.get(key), str):
            raise TypeError(f"An automation execution checkpoint needs its {key}.")
        try:
            uuid.UUID(value[key])
        except ValueError as exc:
            raise ValueError(f"An automation execution checkpoint has an invalid {key}.") from exc
    if type(value.get("index")) is not int or value["index"] < 0:
        raise ValueError("An automation execution checkpoint needs a non-negative action index.")
    if not isinstance(value.get("idempotency_key"), str) or not value["idempotency_key"].strip():
        raise ValueError("An automation execution checkpoint needs its idempotency key.")
    if "target_plan" in value and not isinstance(value["target_plan"], dict):
        raise ValueError("An automation target plan must be an object.")
    if "target_plans" in value and (
        not isinstance(value["target_plans"], list)
        or any(not isinstance(plan, dict) for plan in value["target_plans"])
    ):
        raise ValueError("Automation target plans must be an ordered list of objects.")
    if value.get("expires_at") is not None:
        expires = value["expires_at"]
        if (
            not isinstance(expires, datetime)
            or expires.tzinfo is None
            or expires.utcoffset() is None
        ):
            raise ValueError("An automation hardware deadline must include a timezone.")
    if "automatic_entry_policy" in value and type(value["automatic_entry_policy"]) is not bool:
        raise ValueError("An automation admission policy must be a boolean.")
    return cast(AutomationExecution, value)


class NotificationPlanItem(TypedDict):
    state: NotificationState
    action: NotRequired[dict[str, Any]]
    rule: NotRequired[dict[str, Any]]
    rule_origin: NotRequired[dict[str, str]]
    reason: NotRequired[str]


@dataclass(frozen=True)
class NotificationActionOutcome:
    delivered: bool
    skipped: bool = False
    reason: str = ""
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def checked_action(value: object) -> WorkflowAction:
    """Reject malformed persisted actions before domain/provider dispatch."""
    if not isinstance(value, dict):
        raise TypeError("A workflow action must be an object.")
    for key in ("id", "type"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"A workflow action needs a non-empty {key}.")
    if "config" in value and not isinstance(value["config"], dict):
        raise ValueError("Workflow action configuration must be an object.")
    return cast(WorkflowAction, value)


def checked_notification_plan(value: object) -> list[NotificationPlanItem]:
    if not isinstance(value, list):
        raise TypeError("A notification delivery plan must be an ordered list.")
    states = {"pending", "attempting", "accepted", "failed", "skipped", "unknown"}
    for item in value:
        if not isinstance(item, dict) or item.get("state") not in states:
            raise ValueError("A notification plan needs valid action checkpoint states.")
        if item["state"] == "pending":
            action = item.get("action")
            if (
                not isinstance(action, dict)
                or not isinstance(action.get("type"), str)
                or not action["type"].strip()
            ):
                raise ValueError("A pending notification action needs its channel type.")
            if "id" in action and (not isinstance(action["id"], str) or not action["id"].strip()):
                raise ValueError("A notification action ID must be a non-empty string.")
            if not isinstance(item.get("rule"), dict):
                raise ValueError("A pending notification action needs its rendered rule.")
    return cast(list[NotificationPlanItem], value)


@dataclass(frozen=True)
class AutomationActionResult:
    """Validated outcome without narrowing provider receipt metadata."""

    action_id: str
    action_type: str
    status: AutomationStatus
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(
        cls, value: object, *, action: WorkflowAction | None = None
    ) -> AutomationActionResult:
        if not isinstance(value, dict):
            raise TypeError("An automation action result must be an object.")
        identity = checked_action(value)
        if action is not None and (identity["id"], identity["type"]) != (
            action["id"],
            action["type"],
        ):
            raise ValueError("An automation result cannot change its action identity.")
        status = value.get("status")
        if status not in {"success", "queued", "failed", "skipped", "unknown"}:
            raise ValueError("An automation action result needs a supported status.")
        return cls(
            identity["id"],
            identity["type"],
            cast(AutomationStatus, status),
            copy.deepcopy(
                {key: part for key, part in value.items() if key not in {"id", "type", "status"}}
            ),
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.action_id,
            "type": self.action_type,
            "status": self.status,
            **copy.deepcopy(self.details),
        }

    @property
    def checkpoint_state(self) -> CheckpointState:
        if self.status == "skipped":
            return "skipped"
        if (
            self.status == "unknown"
            or self.details.get("delivery") == "unknown"
            or self.details.get("requires_reconciliation")
            or self.details.get("requires_review")
        ):
            return "unknown"
        if self.status == "failed":
            return "failed"
        return "succeeded"
