"""Public Alfred tool contracts and registry facade.

The concrete Alfred V3 tool handlers live in ``app.ai.tool_groups`` beside
their domain catalogs. This module keeps the stable public imports used by
ChatService, tests, and extension code.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib import import_module
from types import ModuleType
from typing import Any

from app.ai.tool_groups import _shared
from app.ai.tool_groups._shared import (
    ADMIN_PERMISSION,
    SAFETY_ADMIN_ONLY,
    SAFETY_CONFIRMATION_REQUIRED,
    SAFETY_LEVELS,
    SAFETY_READ_ONLY,
    ToolHandler,
)


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    categories: tuple[str, ...] = ("General",)
    safety_level: str = SAFETY_READ_ONLY
    required_permissions: tuple[str, ...] = ()
    read_only: bool = True
    requires_confirmation: bool = False
    rate_limit: dict[str, Any] | None = None
    example_inputs: tuple[dict[str, Any], ...] = ()
    return_schema: dict[str, Any] | None = None
    default_limit: int | None = None

    def __post_init__(self) -> None:
        categories = tuple(str(category).strip() for category in self.categories if str(category).strip())
        permissions = tuple(
            str(permission).strip().lower()
            for permission in self.required_permissions
            if str(permission).strip()
        )
        safety_level = str(self.safety_level or SAFETY_READ_ONLY).strip().lower()
        if safety_level not in SAFETY_LEVELS:
            safety_level = SAFETY_READ_ONLY
        read_only = bool(self.read_only)
        requires_confirmation = bool(self.requires_confirmation)

        if safety_level == SAFETY_CONFIRMATION_REQUIRED or requires_confirmation or not read_only:
            safety_level = SAFETY_CONFIRMATION_REQUIRED
            read_only = False
            requires_confirmation = True
        elif safety_level == SAFETY_ADMIN_ONLY and ADMIN_PERMISSION not in permissions:
            permissions = (*permissions, ADMIN_PERMISSION)

        object.__setattr__(self, "categories", categories or ("General",))
        object.__setattr__(self, "required_permissions", permissions)
        object.__setattr__(self, "safety_level", safety_level)
        object.__setattr__(self, "read_only", read_only)
        object.__setattr__(self, "requires_confirmation", requires_confirmation)
        object.__setattr__(self, "example_inputs", tuple(self.example_inputs or ()))

    def as_llm_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


def build_agent_tools() -> dict[str, AgentTool]:
    from app.ai.tool_groups.registry import build_grouped_tool_map

    return build_grouped_tool_map()


_HANDLER_MODULE_NAMES = (
    'access_diagnostics_handlers',
    'access_incident_handlers',
    'automations_handlers',
    'compliance_cameras_files_handlers',
    'gate_maintenance_handlers',
    'general_handlers',
    'notifications_handlers',
    'schedules_handlers',
    'system_operations_handlers',
    'visitor_passes_handlers',
)

_FACADE_OVERRIDES: dict[str, Any] = {}


def __getattr__(name: str) -> Any:
    if hasattr(_shared, name):
        value = getattr(_shared, name)
        globals()[name] = value
        return value
    if name.startswith("_"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    for module_name in _HANDLER_MODULE_NAMES:
        module = import_module(f"app.ai.tool_groups.{module_name}")
        _apply_overrides_to_module(module)
        if hasattr(module, name):
            value = getattr(module, name)
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_shared)))


def _apply_overrides_to_module(module: ModuleType) -> None:
    for override_name, override_value in _FACADE_OVERRIDES.items():
        if hasattr(module, override_name):
            setattr(module, override_name, override_value)


def _propagate_facade_override(name: str, value: Any) -> None:
    _FACADE_OVERRIDES[name] = value
    if hasattr(_shared, name):
        setattr(_shared, name, value)
    for module_name in _HANDLER_MODULE_NAMES:
        module = sys.modules.get(f"app.ai.tool_groups.{module_name}")
        if module is not None and hasattr(module, name):
            setattr(module, name, value)


class _ToolFacadeModule(ModuleType):
    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if not name.startswith("__"):
            _propagate_facade_override(name, value)


sys.modules[__name__].__class__ = _ToolFacadeModule
