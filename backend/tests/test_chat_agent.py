from datetime import UTC, datetime
import uuid
from types import SimpleNamespace

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

    async def fake_build_messages(session_id, tool_results, selected_tools):
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
        [service._tools["query_presence"]],
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


def test_schedule_delete_planner_understands_named_schedule() -> None:
    service = ChatService()
    lower = "delete the schedule named Jason".lower()
    call = service._planned_schedule_delete_call("delete the schedule named Jason")

    assert service._looks_like_schedule_delete_request(lower)
    assert service._schedule_delete_name_from_message("delete the schedule named Jason") == "Jason"
    assert call.name == "delete_schedule"
    assert call.arguments["schedule_name"] == "Jason"
    assert call.arguments["confirm"] is False
    assert [planned.name for planned in service._plan_tool_calls("delete the schedule named Jason", {}, [])] == ["delete_schedule"]


def test_schedule_delete_direct_text_prompts_for_confirmation() -> None:
    service = ChatService()

    assert (
        service._schedule_delete_direct_text(
            {"requires_confirmation": True, "schedule_name": "Jason", "detail": "Delete the Jason schedule?"}
        )
        == "Delete the Jason schedule?"
    )


def test_assistant_text_cleanup_hides_file_urls_and_markdown() -> None:
    service = ChatService()
    text = (
        "Here is **Top Gate** and [camera-snapshot-back-garden.jpg](/api/chat/files/file-1). "
        "Please provide the Home Assistant cover entity ID."
    )

    cleaned = service._clean_assistant_text(
        text,
        [
            {
                "filename": "camera-snapshot-back-garden.jpg",
                "kind": "image",
                "source": "system_media",
            }
        ],
    )

    assert cleaned == "Here is Top Gate and the snapshot. Please provide the device name."
    assert "/api/chat/files" not in cleaned
    assert "**" not in cleaned
    assert "Home Assistant" not in cleaned
    assert "entity ID" not in cleaned


def test_device_open_planner_extracts_main_garage_door() -> None:
    service = ChatService()
    call = service._planned_device_open_call("open the main garage door")

    assert call.name == "open_device"
    assert call.arguments["target"] == "main garage door"
    assert call.arguments["confirm"] is False


def test_device_status_question_is_not_treated_as_open_request() -> None:
    service = ChatService()
    lower = "is the main garage door open?"

    assert service._looks_like_device_state_request(lower)
    assert not service._looks_like_device_open_request(lower)
    assert [tool.name for tool in service._select_tools_for_request(lower, {}, [], [])] == ["query_device_states"]


def test_camera_snapshot_planner_understands_show_me_camera() -> None:
    service = ChatService()
    call = service._planned_camera_snapshot_call("show me the back garden camera")

    assert call.name == "get_camera_snapshot"
    assert call.arguments["camera_name"] == "back garden"


def test_camera_snapshot_planner_understands_show_me_location() -> None:
    service = ChatService()
    lower = "show me the back garden"
    call = service._planned_camera_snapshot_call(lower)

    assert service._looks_like_camera_snapshot_request(lower)
    assert call.name == "get_camera_snapshot"
    assert call.arguments["camera_name"] == "back garden"


def test_camera_snapshot_planner_does_not_steal_show_me_schedules() -> None:
    service = ChatService()

    assert not service._looks_like_camera_snapshot_request("show me the schedules")


def test_access_event_time_planner_extracts_first_name() -> None:
    service = ChatService()
    call = service._planned_access_event_time_call("what time did steph leave?", {})

    assert call.name == "query_access_events"
    assert call.arguments["person"] == "steph"
    assert call.arguments["day"] == "recent"


def test_leaderboard_tool_is_registered_and_selected() -> None:
    tools = ai_tools.build_agent_tools()
    service = ChatService()

    selected = service._select_tools_for_request("Who is winning Top Charts?", {}, [], [])
    planned = service._plan_tool_calls("Who is winning Top Charts?", {}, [])

    assert "query_leaderboard" in tools
    assert [tool.name for tool in selected] == ["query_leaderboard"]
    assert planned[0].name == "query_leaderboard"
    assert planned[0].arguments["scope"] == "top_known"


@pytest.mark.asyncio
async def test_query_leaderboard_filters_rows(monkeypatch) -> None:
    class FakeLeaderboardService:
        async def get_leaderboard(self, *, limit, enrich_unknowns):
            assert limit == 10
            assert enrich_unknowns is True
            return {
                "generated_at": "2026-04-27T12:00:00+00:00",
                "top_known": {
                    "rank": 1,
                    "registration_number": "VIP123",
                    "display_name": "Steph Smith",
                    "vehicle_name": "Silver Ford Transit",
                    "read_count": 7,
                    "person": {"display_name": "Steph Smith"},
                    "vehicle": {"registration_number": "VIP123", "make": "Ford", "model": "Transit", "color": "Silver"},
                },
                "known": [
                    {
                        "rank": 1,
                        "registration_number": "VIP123",
                        "display_name": "Steph Smith",
                        "vehicle_name": "Silver Ford Transit",
                        "read_count": 7,
                        "person": {"display_name": "Steph Smith"},
                        "vehicle": {"registration_number": "VIP123", "make": "Ford", "model": "Transit", "color": "Silver"},
                    },
                    {
                        "rank": 2,
                        "registration_number": "OTHER1",
                        "display_name": "Jason Smith",
                        "vehicle_name": "Blue Tesla",
                        "read_count": 5,
                        "person": {"display_name": "Jason Smith"},
                        "vehicle": {"registration_number": "OTHER1", "make": "Tesla", "model": "Model Y", "color": "Blue"},
                    },
                ],
                "unknown": [
                    {
                        "rank": 1,
                        "registration_number": "MYSTERY1",
                        "read_count": 3,
                        "dvla": {"label": "White Ford Transit", "display_vehicle": {"make": "Ford", "colour": "White"}},
                    }
                ],
            }

    monkeypatch.setattr(ai_tools, "get_leaderboard_service", lambda: FakeLeaderboardService())

    result = await ai_tools.query_leaderboard({"scope": "all", "limit": 10, "search": "Steph", "enrich_unknowns": True})

    assert result["top_known"]["display_name"] == "Steph Smith"
    assert result["known_count"] == 1
    assert result["unknown_count"] == 0
    assert result["known"][0]["registration_number"] == "VIP123"


def test_person_record_match_accepts_first_name_and_punctuation() -> None:
    assert ai_tools._person_record_matches({"display_name": "Steph Smith", "group": "Family"}, "steph?")


def test_chat_time_from_iso_converts_utc_to_london() -> None:
    service = ChatService()

    assert service._chat_time_from_iso("2026-04-27T12:00:00+00:00") == "13:00"


def test_agent_datetime_formats_europe_london() -> None:
    value = datetime(2026, 4, 27, 12, 0, tzinfo=UTC)

    assert ai_tools._agent_datetime_iso(value, "Europe/London") == "2026-04-27T13:00:00+01:00"
    assert ai_tools._agent_datetime_display(value, "Europe/London") == "27 Apr 2026, 13:00 Europe/London"


@pytest.mark.asyncio
async def test_open_device_resolves_friendly_garage_name_before_confirmation(monkeypatch) -> None:
    async def fake_runtime_config():
        return SimpleNamespace(
            home_assistant_gate_entities=[],
            home_assistant_garage_door_entities=[
                {
                    "entity_id": "cover.internal_main_garage",
                    "name": "Main Garage",
                    "enabled": True,
                }
            ],
        )

    monkeypatch.setattr(ai_tools, "get_runtime_config", fake_runtime_config)

    result = await ai_tools.open_device({"target": "main garage door", "kind": "all", "confirm": False})

    assert result["requires_confirmation"] is True
    assert result["target"] == "Main Garage"
    assert result["device"]["name"] == "Main Garage"
    assert "entity" not in result["detail"].lower()
