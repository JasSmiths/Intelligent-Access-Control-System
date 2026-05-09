"""Shared helpers for declarative Alfred tool metadata."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

from app.ai.tools import (
    ADMIN_PERMISSION,
    SAFETY_ADMIN_ONLY,
    SAFETY_CONFIRMATION_REQUIRED,
    SAFETY_READ_ONLY,
    AgentTool,
)

DOMAIN_SUMMARIES = {
    "Access_Diagnostics": "Root-cause LPR, access-event, gate, notification, and telemetry questions.",
    "Access_Logs": "Presence, access events, anomalies, visit durations, and leaderboards.",
    "Automations": "Trigger/If/Then rules and automation runs.",
    "Calendar_Integrations": "iCloud Calendar Open Gate sync.",
    "Cameras": "UniFi Protect events, snapshots, and camera analysis.",
    "Compliance_DVLA": "Vehicle identity and MOT/tax advisory lookup.",
    "Gate_Hardware": "Device states and gate/garage commands. Malfunction tools are only for fault/failure/diagnostic requests.",
    "Maintenance": "Maintenance Mode state and changes.",
    "Notifications": "Notification catalogs, workflows, previews, and tests.",
    "Reports_Files": "Attachments and generated CSV/PDF reports.",
    "Schedules": "Schedules, assignments, and temporary overrides.",
    "System_Operations": "Settings, provider health, auth-secret status, and dependency update operations.",
    "Users_Settings": "User and settings context.",
    "Visitor_Passes": "Visitor Pass creation, update, cancellation, and visit questions.",
}


def apply_group_metadata(
    tools: Iterable[AgentTool],
    *,
    categories: Mapping[str, tuple[str, ...]],
    confirmation_required: set[str] | frozenset[str] = frozenset(),
    default_limits: Mapping[str, int] | None = None,
    required_permissions: Mapping[str, tuple[str, ...]] | None = None,
) -> list[AgentTool]:
    """Attach explicit group-owned metadata to tool definitions."""

    limits = default_limits or {}
    permissions_by_name = required_permissions or {}
    annotated: list[AgentTool] = []
    for tool in tools:
        if tool.name not in categories:
            raise ValueError(f"{tool.name}: tool group metadata must declare categories.")
        permissions = permissions_by_name.get(tool.name, tool.required_permissions)
        requires_confirmation = tool.name in confirmation_required or tool.requires_confirmation
        safety_level = _safety_level(requires_confirmation=requires_confirmation, permissions=permissions)
        annotated.append(
            replace(
                tool,
                categories=categories.get(tool.name, tool.categories),
                safety_level=safety_level,
                required_permissions=permissions,
                read_only=not requires_confirmation,
                requires_confirmation=requires_confirmation,
                default_limit=limits.get(tool.name, tool.default_limit),
            )
        )
    return annotated


def admin_permissions(*tool_names: str) -> dict[str, tuple[str, ...]]:
    return {tool_name: (ADMIN_PERMISSION,) for tool_name in tool_names}


def domain_summary(domain: str) -> str:
    return DOMAIN_SUMMARIES.get(domain, "General IACS context and entity resolution.")


def _safety_level(*, requires_confirmation: bool, permissions: tuple[str, ...]) -> str:
    if requires_confirmation:
        return SAFETY_CONFIRMATION_REQUIRED
    if ADMIN_PERMISSION in permissions:
        return SAFETY_ADMIN_ONLY
    return SAFETY_READ_ONLY
