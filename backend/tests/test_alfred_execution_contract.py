"""New operations integrate through catalog metadata and the execution contract."""

import ast
from pathlib import Path
import uuid

import pytest

from app.ai.providers import ToolCall
from app.ai.tools import AgentTool
from app.ai.tool_groups.registry import build_agent_tools
from app.ai.tool_inputs import validate_tool_arguments
from app.services.chat import ChatService


async def noop(arguments):
    return {"created": True}


def test_nested_arguments_are_validated_without_coercion_or_echoing_values():
    schema = {
        "type": "object",
        "properties": {
            "confirm": {"type": "boolean"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 3}},
                    "required": ["n"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["confirm"],
        "additionalProperties": False,
    }
    assert validate_tool_arguments({"confirm": False, "items": [{"n": 2}]}, schema) is None
    for value in [
        {"confirm": "secret-value"},
        {"confirm": False, "extra": "private"},
        {"confirm": False, "items": [{"n": True}]},
        {"confirm": False, "items": [{"n": 4}]},
        {"confirm": False, "items": [{}]},
        {},
    ]:
        error = validate_tool_arguments(value, schema)
        assert error and "secret-value" not in error and "private" not in error


@pytest.mark.parametrize(
    "payload,status",
    [
        ({"created": True}, "succeeded"),
        ({"created": False}, "failed"),
        ({"created": False, "requires_confirmation": True}, "requires_confirmation"),
        ({"created": False, "requires_details": True}, "requires_details"),
        ({"created": True, "accepted": False}, "failed"),
        ({"error": "failure", "error_code": "not_found"}, "failed"),
    ],
)
def test_standard_outcomes(payload, status):
    tool = AgentTool("synthetic", "Test", {}, noop, success_fields=("created",))
    outcome = tool.outcome(payload)
    assert outcome["status"] == status
    assert (outcome["error"] is not None) == (status == "failed")
    if status == "failed":
        assert "completed" not in tool.confirmation_text(payload).lower()


@pytest.mark.parametrize(
    "name,field",
    [
        ("update_schedule", "updated"),
        ("create_notification_workflow", "created"),
        ("update_notification_workflow", "updated"),
        ("delete_notification_workflow", "deleted"),
        ("create_automation", "created"),
        ("edit_automation", "updated"),
    ],
)
def test_failed_domain_result_never_becomes_successful_confirmation(name, field):
    tool = build_agent_tools()[name]
    assert tool.outcome({field: False})["status"] == "failed"
    assert tool.confirmation_text({field: False}) == "The operation did not succeed."


async def test_new_operation_needs_no_chat_presentation_branch(monkeypatch):
    service = ChatService()
    calls = []
    events = []

    async def handler(arguments):
        calls.append(arguments)
        return {"created": True, "record": {"name": "Synthetic"}}

    tool = AgentTool(
        "synthetic_operation",
        "Synthetic operation",
        {
            "type": "object",
            "properties": {"confirm": {"type": "boolean"}},
            "required": ["confirm"],
            "additionalProperties": False,
        },
        handler,
        read_only=False,
        requires_confirmation=True,
        status_label="Synthetic label",
        success_fields=("created",),
        finish_after_confirmation=True,
        summary_handler=lambda name, output: "Saved " + output["record"]["name"],
        button_handler=lambda name, output: "Save record",
    )
    service._tools = {tool.name: tool}

    async def ignore(*args, **kw):
        pass

    monkeypatch.setattr(service, "_append_tool_message", ignore)
    monkeypatch.setattr(service, "_audit_agent_tool_call", lambda *args: None)
    result = await service._execute_tool_call(
        uuid.uuid4(),
        ToolCall("1", tool.name, {"confirm": True}),
        status_callback=lambda event: record_event(events, event),
    )
    assert calls == [{"confirm": True}]
    assert result["outcome"]["status"] == "succeeded"
    assert service._tool_status(tool.name)["label"] == "Synthetic label"
    assert service._confirmation_result_text(tool.name, result["output"]) == "Saved Synthetic"
    assert service._confirmation_button_label(tool.name) == "Save record"
    assert service._confirmed_tool_finishes_without_resume(tool.name)
    result = await service._execute_tool_call(
        uuid.uuid4(), ToolCall("2", tool.name, {"confirm": "true"})
    )
    assert result["outcome"]["error"]["code"] == "invalid_arguments"
    assert len(calls) == 1
    assert events[-1]["status"] == "succeeded"


async def record_event(events, event):
    events.append(event)


async def test_failed_output_streams_failed_and_uses_same_audit_outcome(monkeypatch):
    service = ChatService()
    events = []
    audits = []

    async def fail(arguments):
        return {"updated": False}

    tool = AgentTool("synthetic", "Test", {"type": "object"}, fail, success_fields=("updated",))
    service._tools = {tool.name: tool}

    async def ignore(*args, **kw):
        pass

    monkeypatch.setattr(service, "_append_tool_message", ignore)
    monkeypatch.setattr("app.services.chat.emit_audit_log", lambda **kw: audits.append(kw))
    result = await service._execute_tool_call(
        uuid.uuid4(),
        ToolCall("1", tool.name, {}),
        status_callback=lambda e: record_event(events, e),
    )
    assert result["outcome"]["status"] == events[-1]["status"] == "failed"
    assert audits[-1]["outcome"] == "failed"


def test_chat_presentation_methods_have_no_feature_names():
    root = Path(__file__).parents[1] / "app/services/chat.py"
    methods = {
        "_tool_status",
        "_confirmation_result_text",
        "_confirmation_button_label",
        "_confirmed_tool_finishes_without_resume",
    }
    names = set(build_agent_tools())
    for node in ast.walk(ast.parse(root.read_text())):
        if isinstance(node, ast.FunctionDef) and node.name in methods:
            assert not any(
                isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in names
                for n in ast.walk(node)
            )


def test_new_schema_constraints_cannot_be_silently_ignored():
    from app.ai.tool_inputs import validate_tool_schema

    assert validate_tool_schema(
        {"type": "object", "properties": {"q": {"type": "string", "pattern": "^a"}}}
    )
    assert validate_tool_schema({"type": "mystery"})
    assert all(validate_tool_schema(t.parameters) is None for t in build_agent_tools().values())


@pytest.mark.parametrize("kind", ["timeout", "exception"])
async def test_failed_batch_has_standard_error_once(kind, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    service = ChatService()
    attempts = []

    async def handler(args):
        attempts.append(args)
        if kind == "timeout":
            await asyncio.sleep(1)
        raise ValueError("synthetic failure")

    tool = AgentTool("synthetic", "Synthetic", {"type": "object"}, handler)
    service._tools = {tool.name: tool}

    async def config():
        return SimpleNamespace(llm_timeout_seconds=0.1)

    monkeypatch.setattr("app.services.chat.get_runtime_config", config)
    result = (
        await service._execute_tool_batch(uuid.uuid4(), [ToolCall("1", tool.name, {})], [tool])
    )[0]
    assert len(attempts) == 1
    assert result["outcome"]["status"] == "failed"
    assert result["outcome"]["error"]["code"] == (
        "timeout" if kind == "timeout" else "tool_exception"
    )
