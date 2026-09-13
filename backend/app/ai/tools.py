"""Alfred tool contracts. No registry, handler, provider, or service dependencies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

ToolSummary = Callable[[str, dict[str, Any]], str]
ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
SAFETY_READ_ONLY = "read_only"
SAFETY_CONFIRMATION_REQUIRED = "confirmation_required"
SAFETY_ADMIN_ONLY = "admin_only"
SAFETY_LEVELS = {SAFETY_READ_ONLY, SAFETY_CONFIRMATION_REQUIRED, SAFETY_ADMIN_ONLY}
ADMIN_PERMISSION = "admin"


class ToolError(TypedDict):
    code: str
    message: str


class ToolOutcome(TypedDict):
    status: Literal["succeeded", "failed", "requires_confirmation", "requires_details"]
    error: ToolError | None


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
    status_label: str = "Running system tool..."
    success_fields: tuple[str, ...] = ()
    finish_after_confirmation: bool = False
    summary_handler: ToolSummary | None = None
    button_handler: ToolSummary | None = None

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


    def execution_metadata(self) -> dict[str, Any]:
        return {"status_label": self.status_label, "success_fields": list(self.success_fields),
                "finish_after_confirmation": self.finish_after_confirmation,
                "summary_owner": f"{self.summary_handler.__module__}.{self.summary_handler.__name__}" if self.summary_handler else None,
                "button_owner": f"{self.button_handler.__module__}.{self.button_handler.__name__}" if self.button_handler else None}

    def outcome(self, output: dict[str, Any]) -> ToolOutcome:
        if output.get("error"):
            return {"status": "failed", "error": {"code": str(output.get("error_code") or "operation_failed"),
                                                    "message": str(output.get("detail") or output["error"])}}
        if output.get("requires_confirmation"):
            return {"status": "requires_confirmation", "error": None}
        if output.get("requires_details"):
            return {"status": "requires_details", "error": None}
        failed = output.get("accepted") is False or output.get("status") in ("failed", "error")
        declared = [output[key] for key in self.success_fields if key in output]
        if self.success_fields and not any(value is True for value in declared):
            failed = True
        if failed:
            return {"status": "failed", "error": {"code": "operation_failed",
                    "message": str(output.get("detail") or "The operation did not succeed.")}}
        return {"status": "succeeded", "error": None}

    def confirmation_text(self, output: dict[str, Any]) -> str:
        outcome = self.outcome(output)
        if outcome["error"] is not None:
            return outcome["error"]["message"]
        if outcome["status"] != "succeeded":
            return str(output.get("detail") or "The operation needs more information or confirmation.")
        if self.summary_handler:
            return self.summary_handler(self.name, output)
        return str(output.get("detail") or "Action completed.")


def failed_outcome(code: str, message: str) -> ToolOutcome:
    return {"status": "failed", "error": {"code": code, "message": message}}
