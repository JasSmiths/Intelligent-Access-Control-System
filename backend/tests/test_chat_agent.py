import uuid

import pytest

from app.ai import tools as ai_tools
from app.ai.providers import ChatMessageInput, LlmResult
from app.services.chat import ChatService


class ProtocolProvider:
    name = "protocol-test"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, tools=None, tool_results=None):
        self.calls += 1
        if self.calls == 1:
            return LlmResult(
                text=(
                    "IACS_TOOL_CALLS:\n"
                    '{"tool_calls":[{"id":"call_1","name":"query_presence","arguments":{"state":"present"}}]}'
                )
            )
        return LlmResult(text="Alfred found two people present.")


@pytest.mark.asyncio
async def test_provider_neutral_tool_protocol_runs_tools(monkeypatch) -> None:
    service = ChatService()
    provider = ProtocolProvider()
    executed = []

    async def fake_execute_tool_call(session_id, call, *, status_callback=None):
        executed.append(call)
        return {
            "call_id": call.id,
            "name": call.name,
            "arguments": call.arguments,
            "output": {"presence": [{"person": "Jason", "state": "present"}]},
        }

    async def fake_build_messages(session_id, tool_results):
        return [
            ChatMessageInput("system", "test"),
            ChatMessageInput("user", f"Tool results: {tool_results}"),
        ]

    async def no_schedule_conflict(session_id, memory, tool_results):
        return None

    monkeypatch.setattr(service, "_execute_tool_call", fake_execute_tool_call)
    monkeypatch.setattr(service, "_build_messages", fake_build_messages)
    monkeypatch.setattr(service, "_schedule_conflict_response", no_schedule_conflict)

    result = await service._run_provider_agent_loop(
        provider,
        uuid.uuid4(),
        [ChatMessageInput("system", "test"), ChatMessageInput("user", "Who is present?")],
        [],
        {},
        status_callback=None,
    )

    assert provider.calls == 2
    assert result.text == "Alfred found two people present."
    assert [(call.name, call.arguments) for call in executed] == [
        ("query_presence", {"state": "present"})
    ]


def test_natural_schedule_time_description_normalizes_to_time_blocks() -> None:
    blocks = ai_tools._time_blocks_from_agent_arguments(
        {
            "name": "Gardener",
            "time_description": "Wednesdays and Fridays 6am to 7pm",
        }
    )

    assert blocks["2"] == [{"start": "06:00", "end": "19:00"}]
    assert blocks["4"] == [{"start": "06:00", "end": "19:00"}]
    assert all(not blocks[str(day)] for day in [0, 1, 3, 5, 6])
