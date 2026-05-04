import asyncio
from datetime import UTC, datetime
import time
import uuid
from types import SimpleNamespace

import pytest

from app.ai import tools as ai_tools
from app.ai.providers import ChatMessageInput, LlmResult, LocalProvider, ToolCall
from app.api.v1 import ai as ai_api
from app.services.chat import ChatService, IntentRoute, IntentRouterError, SYSTEM_PROMPT


@pytest.fixture(autouse=True)
def runtime_config_stub(monkeypatch):
    async def fake_runtime_config():
        return SimpleNamespace(llm_timeout_seconds=30)

    monkeypatch.setattr("app.services.chat.get_runtime_config", fake_runtime_config)


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


class JsonFinalProvider:
    name = "json-final-test"

    async def complete(self, messages, tools=None, tool_results=None):
        return LlmResult(text='{"final":"Done."}')


class UnknownToolProvider:
    name = "unknown-tool-test"

    async def complete(self, messages, tools=None, tool_results=None):
        return LlmResult(text='{"thought":"check","tool_name":"delete_everything","arguments":{}}')


class CountingToolProvider:
    name = "counting-tool-test"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, tools=None, tool_results=None):
        self.calls += 1
        return LlmResult(
            text=(
                '{"thought":"check","tool_name":"query_presence",'
                f'"arguments":{{"person":"Person {self.calls}"}}}}'
            )
        )


def test_system_prompt_sets_warm_wit_persona_and_preserves_safety_rules() -> None:
    assert "quick-witted, good-natured, lightly cheeky" in SYSTEM_PROMPT
    assert "warm operations butler" in SYSTEM_PROMPT
    assert "Never invent people, vehicles, schedules" in SYSTEM_PROMPT
    assert "Do not claim an action has happened until a confirmed tool result says it happened" in SYSTEM_PROMPT
    assert "jokes must never soften risk or hide uncertainty" in SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_local_provider_general_response_has_warm_persona() -> None:
    provider = LocalProvider()

    result = await provider.complete([ChatMessageInput("user", "hello")])

    assert "I'm Alfred" in result.text
    assert "sensible clipboard" in result.text
    assert "You asked: hello" in result.text


def test_local_provider_confirmation_summary_is_warm_but_clear() -> None:
    provider = LocalProvider()

    text = provider._summarize_device_open(
        {
            "requires_confirmation": True,
            "action": "open",
            "device": {"name": "Top Gate"},
        }
    )

    assert "confirmation button" in text
    assert "before I open Top Gate" in text
    assert "Safety first" in text


def test_critical_tool_failure_stays_plain() -> None:
    provider = LocalProvider()

    text = provider._summarize_device_open(
        {
            "action": "open",
            "device": {"name": "Top Gate"},
            "opened": False,
            "detail": "Home Assistant call failed.",
        }
    )

    assert text == "I could not open Top Gate: Home Assistant call failed."


def test_local_provider_no_records_reply_is_warm() -> None:
    provider = LocalProvider()

    assert provider._summarize_events({"events": []}) == "I found no matching access events. The logbook is politely blank."


@pytest.mark.asyncio
async def test_http_chat_api_uses_shared_chat_service(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeChatService:
        async def handle_message(self, message, **kwargs):
            captured["message"] = message
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                session_id="session-1",
                provider="local",
                text="I'm Alfred: warm gatehouse brain, sensible clipboard.",
                tool_results=[],
                attachments=[],
                pending_action=None,
            )

    monkeypatch.setattr(ai_api, "chat_service", FakeChatService())
    user = SimpleNamespace(id=uuid.uuid4(), role=SimpleNamespace(value="admin"))
    request = ai_api.ChatRequest(message="hello Alfred", client_context={"timezone": "Europe/London"})

    response = await ai_api.chat(request, current_user=user)

    assert response.text == "I'm Alfred: warm gatehouse brain, sensible clipboard."
    assert captured["message"] == "hello Alfred"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["client_context"] == {"timezone": "Europe/London"}
    assert kwargs["user_role"] == "admin"


class ParallelToolProvider:
    name = "parallel-tool-test"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, tools=None, tool_results=None):
        self.calls += 1
        if self.calls == 1:
            return LlmResult(
                text=(
                    '{"tool_calls":['
                    '{"id":"presence","name":"query_presence","arguments":{}},'
                    '{"id":"schedules","name":"query_schedules","arguments":{}}'
                    ']}'
                )
            )
        return LlmResult(text='{"final":"Checked both."}')


class ActionToolProvider:
    name = "action-tool-test"

    async def complete(self, messages, tools=None, tool_results=None):
        return LlmResult(
            text='{"thought":"open","tool_name":"open_gate","arguments":{"target":"Top Gate","confirm":true}}'
        )


class InvalidIntentProvider:
    name = "invalid-intent-test"

    async def complete(self, messages, tools=None, tool_results=None):
        return LlmResult(text="not json")


class MinimalIntentProvider:
    name = "minimal-intent-test"

    async def complete(self, messages, tools=None, tool_results=None):
        return LlmResult(text='{"intents":["General"],"confidence":0.9,"reason":"missing required field"}')


@pytest.mark.asyncio
async def test_provider_neutral_tool_protocol_runs_tools(monkeypatch) -> None:
    service = ChatService()
    provider = ProtocolProvider()
    executed = []

    async def fake_execute_tool_call(session_id, call, *, status_callback=None, batch_id=None):
        executed.append(call)
        return {
            "call_id": call.id,
            "name": call.name,
            "arguments": call.arguments,
            "output": {"presence": [{"person": "Jason", "state": "present"}]},
        }

    async def fake_build_messages(session_id, tool_results, selected_tools, route=None, actor_context=None):
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


@pytest.mark.asyncio
async def test_semantic_router_scopes_gate_failure_tools() -> None:
    service = ChatService()
    route = service._deterministic_intent_route(
        "Why didn't the gate open for Steph's car?",
        {},
        [],
    )
    selected = service._select_tools_for_route(route, [])
    names = {tool.name for tool in selected}

    assert "Gate_Hardware" in route.intents
    assert "Access_Diagnostics" in route.intents
    assert "resolve_human_entity" in names
    assert "diagnose_access_event" in names
    assert "get_maintenance_status" in names
    assert "verify_schedule_access" in names
    assert "query_notification_workflows" not in names


@pytest.mark.asyncio
async def test_react_loop_accepts_json_final() -> None:
    service = ChatService()
    result = await service._run_provider_agent_loop(
        JsonFinalProvider(),
        uuid.uuid4(),
        [ChatMessageInput("system", "test")],
        [],
        [service._tools["query_presence"]],
        {},
        route=IntentRoute(("General",), 0.5, False, "test"),
        user_message="hello",
        status_callback=None,
    )

    assert result.text == "Done."


@pytest.mark.asyncio
async def test_react_loop_rejects_out_of_scope_tool() -> None:
    service = ChatService()
    result = await service._run_provider_agent_loop(
        UnknownToolProvider(),
        uuid.uuid4(),
        [ChatMessageInput("system", "test")],
        [],
        [service._tools["query_presence"]],
        {},
        route=IntentRoute(("General",), 0.5, False, "test"),
        user_message="hello",
        status_callback=None,
    )

    assert "could not safely use delete_everything" in result.text


@pytest.mark.asyncio
async def test_react_loop_stops_at_max_iterations(monkeypatch) -> None:
    service = ChatService()
    provider = CountingToolProvider()
    executed = []

    async def fake_execute_tool_call(session_id, call, *, status_callback=None, batch_id=None):
        executed.append(call)
        return {
            "call_id": call.id,
            "name": call.name,
            "arguments": call.arguments,
            "output": {"presence": [{"person": call.arguments["person"], "state": "present"}]},
        }

    async def no_schedule_conflict(session_id, memory, tool_results):
        return None

    monkeypatch.setattr(service, "_execute_tool_call", fake_execute_tool_call)
    monkeypatch.setattr(service, "_schedule_conflict_response", no_schedule_conflict)
    async def fake_build_messages(session_id, tool_results, selected_tools, route=None, actor_context=None):
        return [ChatMessageInput("system", "test")]

    monkeypatch.setattr(service, "_build_messages", fake_build_messages)

    result = await service._run_provider_agent_loop(
        provider,
        uuid.uuid4(),
        [ChatMessageInput("system", "test")],
        [],
        [service._tools["query_presence"]],
        {},
        route=IntentRoute(("Access_Logs",), 0.8, False, "test"),
        user_message="who is present?",
        status_callback=None,
    )

    assert len(executed) == 5
    assert "five-step safety limit" in result.text


def test_actor_context_prevents_my_car_entity_resolution() -> None:
    service = ChatService()
    actor_context = {
        "person": {"id": "person-1", "display_name": "Jason Smith"},
        "vehicles": [{"id": "vehicle-1", "registration_number": "VIP123"}],
    }

    route = service._deterministic_intent_route(
        "Why did my car get denied?",
        {},
        [],
        actor_context=actor_context,
    )
    args = service._access_diagnostic_args_from_message(
        "Why did my car get denied?",
        {},
        actor_context=actor_context,
    )

    assert route.requires_entity_resolution is False
    assert args["vehicle_id"] == "vehicle-1"


def test_visitor_pass_create_prompt_prepares_pass_without_person_resolution() -> None:
    service = ChatService()
    message = "Chris Starkey is coming tomorrow at approx 11am, create a pass for him"
    route = service._deterministic_intent_route(message, {}, [], actor_context={})
    selected = service._select_tools_for_route(route, [])
    calls = service._deterministic_react_calls(
        message,
        route,
        {},
        [],
        [],
        selected,
        iteration=0,
        actor_context={},
    )

    assert route.intents == ("Visitor_Passes",)
    assert route.requires_entity_resolution is False
    assert "resolve_human_entity" not in {tool.name for tool in selected}
    assert len(calls) == 1
    assert calls[0].name == "create_visitor_pass"
    assert calls[0].arguments["visitor_name"] == "Chris Starkey"
    assert calls[0].arguments["window_minutes"] == 30
    assert calls[0].arguments["confirm"] is False
    assert "person_id" not in calls[0].arguments
    parsed = datetime.fromisoformat(str(calls[0].arguments["expected_time"]))
    assert parsed.hour == 11
    assert parsed.minute == 0
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() is not None


def test_visitor_pass_query_does_not_start_guided_create_flow() -> None:
    service = ChatService()
    message = "Are there any visitor passes setup for today 30th?"
    route = service._deterministic_intent_route(message, {}, [], actor_context={})
    selected = service._select_tools_for_route(route, [])
    calls = service._deterministic_react_calls(
        message,
        route,
        {},
        [],
        [],
        selected,
        iteration=0,
        actor_context={},
    )

    assert service._looks_like_visitor_pass_query_request(message.lower()) is True
    assert service._looks_like_visitor_pass_create_request(message.lower()) is False
    assert calls
    assert calls[0].name == "query_visitor_passes"
    assert "search" not in calls[0].arguments
    assert calls[0].arguments["statuses"] == ["active", "scheduled"]


def test_pending_visitor_pass_create_abandons_clear_new_gate_command() -> None:
    service = ChatService()

    assert service._should_abandon_pending_visitor_pass_create("open the top gate") is True
    assert service._is_pending_visitor_pass_create_cancel_message("cancel that") is True


@pytest.mark.asyncio
async def test_guided_visitor_pass_flow_does_not_preempt_router_for_new_requests() -> None:
    service = ChatService()

    result = await service._handle_guided_visitor_pass_flow(  # noqa: SLF001
        uuid.uuid4(),
        "Chris Starkey is coming tomorrow at approx 11am, create a pass for him",
        {},
        actor_context={},
        provider_name="protocol-test",
        status_callback=None,
    )

    assert result is None


@pytest.mark.asyncio
async def test_guided_schedule_flow_does_not_preempt_router_for_new_requests() -> None:
    service = ChatService()

    result = await service._handle_guided_schedule_flow(  # noqa: SLF001
        uuid.uuid4(),
        "Create a gardener schedule for weekdays 8am to 5pm",
        {},
        status_callback=None,
    )

    assert result is None


@pytest.mark.asyncio
async def test_intent_router_invalid_response_fails_closed() -> None:
    service = ChatService()

    with pytest.raises(IntentRouterError):
        await service._classify_intent(  # noqa: SLF001
            InvalidIntentProvider(),
            "Who is home?",
            {},
            [],
            actor_context={},
        )


@pytest.mark.asyncio
async def test_intent_router_missing_required_fields_fails_closed() -> None:
    service = ChatService()

    with pytest.raises(IntentRouterError):
        await service._classify_intent(  # noqa: SLF001
            MinimalIntentProvider(),
            "Who is home?",
            {},
            [],
            actor_context={},
        )


@pytest.mark.asyncio
async def test_local_provider_cannot_route_free_form_chat() -> None:
    service = ChatService()

    with pytest.raises(IntentRouterError):
        await service._classify_intent(  # noqa: SLF001
            SimpleNamespace(name="local"),
            "Who is home?",
            {},
            [],
            actor_context={},
        )


def test_unlinked_actor_context_does_not_guess_me() -> None:
    service = ChatService()

    route = service._deterministic_intent_route("When did my car arrive?", {}, [], actor_context={})

    assert route.requires_entity_resolution is True


def test_pending_confirmation_uses_stored_arguments() -> None:
    service = ChatService()
    pending = {
        "arguments": {"target": "Top Gate", "confirm": False},
        "preview_output": {"confirmation_field": "confirm"},
    }

    confirmed = service._confirmed_arguments_for_pending(pending)

    assert confirmed == {"target": "Top Gate", "confirm": True}


def test_confirmed_visitor_pass_action_does_not_resume_original_request() -> None:
    service = ChatService()

    assert service._confirmed_tool_finishes_without_resume("create_schedule") is True
    assert service._confirmed_tool_finishes_without_resume("create_visitor_pass") is True
    assert service._confirmed_tool_finishes_without_resume("update_visitor_pass") is True
    assert service._confirmed_tool_finishes_without_resume("cancel_visitor_pass") is True
    assert service._confirmed_tool_finishes_without_resume("test_notification_workflow") is True
    assert service._confirmed_tool_finishes_without_resume("open_gate") is False


@pytest.mark.asyncio
async def test_create_schedule_tool_requires_confirmation() -> None:
    result = await ai_tools.create_schedule(
        {
            "name": "Gardeners",
            "time_description": "weekdays 8am to 5pm",
            "confirm": False,
        }
    )

    assert result["requires_confirmation"] is True
    assert result["confirmation_field"] == "confirm"


def test_assistant_text_cleanup_removes_local_time_label() -> None:
    service = ChatService()

    cleaned = service._clean_assistant_text(
        "Create a Visitor Pass at 30 Apr 2026, 11:00 Europe/London with a +/- 30 minute window?",
        [],
    )

    assert cleaned == "Create a Visitor Pass at 30 Apr 2026, 11:00 with a +/- 30 minute window?"


def test_superpower_tools_are_registered_with_confirmation_metadata() -> None:
    tools = ai_tools.build_agent_tools()

    assert tools["open_gate"].requires_confirmation is True
    assert tools["command_device"].requires_confirmation is True
    assert tools["toggle_maintenance_mode"].requires_confirmation is True
    assert tools["override_schedule"].requires_confirmation is True
    assert "Schedules" in tools["override_schedule"].categories


def test_alfred_tool_registry_preserves_public_tool_surface() -> None:
    tools = ai_tools.build_agent_tools()

    expected_tool_names = {
        "analyze_camera_snapshot",
        "assign_schedule_to_entity",
        "backfill_access_event_from_protect",
        "calculate_visit_duration",
        "cancel_visitor_pass",
        "command_device",
        "create_automation",
        "create_notification_workflow",
        "create_schedule",
        "create_visitor_pass",
        "delete_automation",
        "delete_notification_workflow",
        "delete_schedule",
        "diagnose_access_event",
        "disable_automation",
        "disable_maintenance_mode",
        "edit_automation",
        "enable_automation",
        "enable_maintenance_mode",
        "export_presence_report_csv",
        "generate_contractor_invoice_pdf",
        "get_active_malfunctions",
        "get_automation",
        "get_camera_snapshot",
        "get_maintenance_status",
        "get_malfunction_history",
        "get_notification_workflow",
        "get_schedule",
        "get_system_users",
        "get_telemetry_trace",
        "get_visitor_pass",
        "investigate_access_incident",
        "lookup_dvla_vehicle",
        "open_device",
        "open_gate",
        "override_schedule",
        "preview_notification_workflow",
        "query_access_events",
        "query_anomalies",
        "query_automation_catalog",
        "query_automations",
        "query_device_states",
        "query_leaderboard",
        "query_lpr_timing",
        "query_notification_catalog",
        "query_notification_workflows",
        "query_presence",
        "query_schedule_targets",
        "query_schedules",
        "query_unifi_protect_events",
        "query_vehicle_detection_history",
        "query_visitor_passes",
        "read_chat_attachment",
        "resolve_human_entity",
        "summarize_access_rhythm",
        "test_notification_workflow",
        "test_unifi_alarm_webhook",
        "toggle_maintenance_mode",
        "trigger_anomaly_alert",
        "trigger_icloud_sync",
        "trigger_manual_malfunction_override",
        "update_notification_workflow",
        "update_schedule",
        "update_visitor_pass",
        "verify_schedule_access",
    }
    state_changing_tools = {
        "assign_schedule_to_entity",
        "backfill_access_event_from_protect",
        "cancel_visitor_pass",
        "command_device",
        "create_automation",
        "create_notification_workflow",
        "create_schedule",
        "create_visitor_pass",
        "delete_automation",
        "delete_notification_workflow",
        "delete_schedule",
        "disable_automation",
        "disable_maintenance_mode",
        "edit_automation",
        "enable_automation",
        "enable_maintenance_mode",
        "investigate_access_incident",
        "open_device",
        "open_gate",
        "override_schedule",
        "test_notification_workflow",
        "test_unifi_alarm_webhook",
        "toggle_maintenance_mode",
        "trigger_anomaly_alert",
        "trigger_icloud_sync",
        "trigger_manual_malfunction_override",
        "update_notification_workflow",
        "update_schedule",
        "update_visitor_pass",
    }

    assert set(tools) == expected_tool_names
    assert {name for name, tool in tools.items() if tool.requires_confirmation} == state_changing_tools
    assert all(tool.categories for tool in tools.values())


@pytest.mark.asyncio
async def test_react_loop_executes_read_tools_in_parallel(monkeypatch) -> None:
    service = ChatService()
    provider = ParallelToolProvider()
    started: list[float] = []
    statuses: list[dict] = []

    async def fake_execute_tool_call(session_id, call, *, status_callback=None, batch_id=None):
        started.append(time.perf_counter())
        await asyncio.sleep(0.05)
        return {
            "call_id": call.id,
            "name": call.name,
            "arguments": call.arguments,
            "output": {"ok": call.name},
        }

    async def fake_build_messages(session_id, tool_results, selected_tools, route=None, actor_context=None):
        return [ChatMessageInput("system", "test")]

    async def no_schedule_conflict(session_id, memory, tool_results):
        return None

    async def status_callback(status):
        statuses.append(status)

    monkeypatch.setattr(service, "_execute_tool_call", fake_execute_tool_call)
    monkeypatch.setattr(service, "_build_messages", fake_build_messages)
    monkeypatch.setattr(service, "_schedule_conflict_response", no_schedule_conflict)

    before = time.perf_counter()
    result = await service._run_provider_agent_loop(
        provider,
        uuid.uuid4(),
        [ChatMessageInput("system", "test")],
        [],
        [service._tools["query_presence"], service._tools["query_schedules"]],
        {},
        route=IntentRoute(("Access_Logs", "Schedules"), 0.8, False, "test"),
        user_message="check presence and schedules",
        status_callback=status_callback,
    )
    elapsed = time.perf_counter() - before

    assert result.text == "Checked both."
    assert len(started) == 2
    assert abs(started[0] - started[1]) < 0.03
    assert elapsed < 0.09
    assert any(status.get("event") == "chat.tool_batch" and status.get("parallel") for status in statuses)


@pytest.mark.asyncio
async def test_react_tool_batch_returns_timeout_result(monkeypatch) -> None:
    service = ChatService()
    statuses: list[dict] = []

    async def fake_runtime_config():
        return SimpleNamespace(llm_timeout_seconds=0.1)

    async def fake_execute_tool_call(session_id, call, *, status_callback=None, batch_id=None):
        await asyncio.sleep(0.2)
        return {
            "call_id": call.id,
            "name": call.name,
            "arguments": call.arguments,
            "output": {"ok": True},
        }

    async def status_callback(status):
        statuses.append(status)

    monkeypatch.setattr("app.services.chat.get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(service, "_execute_tool_call", fake_execute_tool_call)

    results = await service._execute_tool_batch(
        uuid.uuid4(),
        [ToolCall("slow-call", "query_presence", {})],
        [service._tools["query_presence"]],
        status_callback=status_callback,
    )

    assert results[0]["output"]["error"] == "Timed out after 0.1 seconds."
    assert any(status.get("status") == "failed" and status.get("call_id") == "slow-call" for status in statuses)


@pytest.mark.asyncio
async def test_action_tool_pauses_with_stored_confirmation(monkeypatch) -> None:
    service = ChatService()
    memory: dict[str, object] = {}
    executed = []

    async def fake_execute_tool_call(session_id, call, *, status_callback=None, batch_id=None):
        executed.append(call)
        return {
            "call_id": call.id,
            "name": call.name,
            "arguments": call.arguments,
            "output": {
                "requires_confirmation": True,
                "confirmation_field": "confirm",
                "target": "Top Gate",
                "detail": "Open Top Gate?",
            },
        }

    async def fake_load_memory(session_id):
        return dict(memory)

    async def fake_save_memory(session_id, next_memory):
        memory.clear()
        memory.update(next_memory)

    async def no_schedule_conflict(session_id, memory, tool_results):
        return None

    monkeypatch.setattr(service, "_execute_tool_call", fake_execute_tool_call)
    monkeypatch.setattr(service, "_load_memory", fake_load_memory)
    monkeypatch.setattr(service, "_save_memory", fake_save_memory)
    monkeypatch.setattr(service, "_schedule_conflict_response", no_schedule_conflict)

    result = await service._run_provider_agent_loop(
        ActionToolProvider(),
        uuid.uuid4(),
        [ChatMessageInput("system", "test")],
        [],
        [service._tools["open_gate"]],
        {},
        route=IntentRoute(("Gate_Hardware",), 0.9, False, "test"),
        user_message="open the gate",
        actor_context={"user": {"id": "user-1"}},
        status_callback=None,
    )

    pending = memory["pending_agent_action"]
    assert executed[0].arguments["confirm"] is False
    assert pending["tool_name"] == "open_gate"
    assert pending["arguments"]["confirm"] is False
    assert "confirm before I open Top Gate" in result.text


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
        "Here is **Top Gate** and [camera-snapshot-back-garden.jpg](/api/v1/ai/chat/files/file-1). "
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
    assert "/api/v1/ai/chat/files" not in cleaned
    assert "**" not in cleaned
    assert "Home Assistant" not in cleaned
    assert "entity ID" not in cleaned


def test_device_open_planner_extracts_main_garage_door() -> None:
    service = ChatService()
    call = service._planned_device_open_call("open the main garage door")

    assert call.name == "open_device"
    assert call.arguments["target"] == "main garage door"
    assert call.arguments["action"] == "open"
    assert call.arguments["confirm"] is False


def test_device_close_planner_extracts_close_action() -> None:
    service = ChatService()
    call = service._planned_device_action_call("close the main garage door")

    assert call.name == "command_device"
    assert call.arguments["target"] == "main garage door"
    assert call.arguments["action"] == "close"
    assert call.arguments["confirm"] is False


def test_device_status_question_is_not_treated_as_open_request() -> None:
    service = ChatService()
    lower = "is the main garage door open?"

    assert service._looks_like_device_state_request(lower)
    assert not service._looks_like_device_open_request(lower)
    assert [tool.name for tool in service._select_tools_for_request(lower, {}, [], [])] == ["query_device_states"]


def test_device_closed_status_question_is_not_treated_as_close_request() -> None:
    service = ChatService()
    lower = "is the main garage door closed?"

    assert service._looks_like_device_state_request(lower)
    assert not service._looks_like_device_close_request(lower)
    assert [tool.name for tool in service._select_tools_for_request(lower, {}, [], [])] == ["query_device_states"]


def test_gate_malfunction_tools_are_registered_and_selected() -> None:
    tools = ai_tools.build_agent_tools()
    service = ChatService()

    selected = service._select_tools_for_request("What is the gate doing right now?", {}, [], [])
    planned = service._plan_tool_calls("What is the gate doing right now?", {}, [])

    assert "get_active_malfunctions" in tools
    assert "get_malfunction_history" in tools
    assert "trigger_manual_malfunction_override" in tools
    assert [tool.name for tool in selected] == ["query_device_states", "get_active_malfunctions"]
    assert any(call.name == "get_active_malfunctions" for call in planned)


@pytest.mark.asyncio
async def test_gate_malfunction_override_tool_requires_admin_context() -> None:
    token = ai_tools.set_chat_tool_context({"user_role": "standard"})
    try:
        result = await ai_tools.trigger_manual_malfunction_override(
            {
                "malfunction_id": str(uuid.uuid4()),
                "action": "mark_resolved",
                "reason": "test",
                "confirm": True,
            }
        )
    finally:
        ai_tools.set_chat_tool_context({}, token=token)

    assert result["changed"] is False
    assert "Admin access" in result["error"]


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


def test_access_diagnostic_tools_are_registered_and_planned() -> None:
    tools = ai_tools.build_agent_tools()
    service = ChatService()
    message = "Why did Steph's latest LPR take much longer than the rest?"

    selected = service._select_tools_for_request(message, {}, [], [])
    planned = service._plan_tool_calls(message, {}, [])

    assert "diagnose_access_event" in tools
    assert "query_lpr_timing" in tools
    assert "query_vehicle_detection_history" in tools
    assert "diagnose_access_event" in [tool.name for tool in selected]
    assert "query_lpr_timing" in [tool.name for tool in selected]
    assert planned[0].name == "diagnose_access_event"
    assert planned[0].arguments["person"] == "steph"
    assert planned[1].name == "query_lpr_timing"


def test_missing_access_incident_routes_to_investigator() -> None:
    tools = ai_tools.build_agent_tools()
    service = ChatService()
    message = "Steph left at 07:38am this morning however nothing was logged about this"

    route = service._deterministic_intent_route(message, {}, [])
    selected = service._select_tools_for_route(route, [])
    planned = service._plan_tool_calls(message, {}, [])

    assert "investigate_access_incident" in tools
    assert tools["investigate_access_incident"].requires_confirmation is True
    assert tools["backfill_access_event_from_protect"].requires_confirmation is True
    assert tools["test_unifi_alarm_webhook"].requires_confirmation is True
    assert "Access_Diagnostics" in route.intents
    assert "investigate_access_incident" in [tool.name for tool in selected]
    assert planned[0].name == "investigate_access_incident"
    assert planned[0].arguments["person"] == "steph"
    assert planned[0].arguments["direction"] == "exit"
    assert planned[0].arguments["day"] == "today"
    assert planned[0].arguments["expected_time"] == "07:38am"
    assert planned[0].arguments["incident_type"] == "missing_event"


def test_diagnostic_no_match_falls_through_to_incident_in_react_loop() -> None:
    service = ChatService()
    route = IntentRoute(("Access_Diagnostics",), 0.9, False, "test")
    selected = [
        service._tools["diagnose_access_event"],
        service._tools["investigate_access_incident"],
    ]
    calls = service._deterministic_react_calls(
        "Why did Steph's latest LPR fail?",
        route,
        {},
        [],
        [{"name": "diagnose_access_event", "output": {"found": False}}],
        selected,
        iteration=1,
    )

    assert calls[0].name == "investigate_access_incident"
    assert calls[0].arguments["person"] == "steph"


def test_access_incident_direct_text_reports_comparison_and_action() -> None:
    service = ChatService()
    text = service._access_incident_direct_text(
        {
            "found_iacs_event": False,
            "found_protect_event": True,
            "root_cause": "protect_lpr_detected_but_iacs_webhook_missing",
            "confidence": "high",
            "iacs_vs_protect": {
                "comparison": "Protect saw a matching LPR candidate but IACS has no access event."
            },
            "recommended_action": {"summary": "Fix UniFi Protect Alarm Manager delivery, then send a test."},
        }
    )

    assert "IACS found no matching IACS access event" in text
    assert "Protect has matching evidence" in text
    assert "protect lpr detected but iacs webhook missing" in text
    assert "Fix UniFi Protect Alarm Manager delivery" in text


def test_hosted_provider_prefetches_deep_access_diagnostics() -> None:
    service = ChatService()
    calls = service._preplanned_context_calls(
        "Why did Steph's latest LPR take much longer than the rest (700ms+)?",
        {},
        [],
    )

    assert [call.name for call in calls] == ["diagnose_access_event", "query_lpr_timing"]
    assert calls[0].arguments["person"] == "steph"


def test_process_arrival_wording_prefetches_diagnostics_without_lpr_keyword() -> None:
    service = ChatService()
    message = "why did Stephs latest arrival take so much longer to process than the other arrivals today? 700ms+"

    assert service._looks_like_access_diagnostic_request(message.lower())
    assert not service._looks_like_access_event_time_request(message.lower())

    calls = service._preplanned_context_calls(message, {}, [])
    planned = service._plan_tool_calls(message, {}, [])

    assert [call.name for call in calls] == ["diagnose_access_event", "query_lpr_timing"]
    assert calls[0].arguments == {"day": "today", "person": "steph", "direction": "entry"}
    assert planned[0].name == "diagnose_access_event"
    assert planned[0].arguments == {"day": "today", "person": "steph", "direction": "entry"}


def test_unhelpful_latency_answer_is_replaced_with_diagnostic_summary() -> None:
    service = ChatService()
    tool_results = [
        {
            "name": "diagnose_access_event",
            "output": {
                "found": True,
                "event": {
                    "person": "Steph Smith",
                    "registration_number": "PE70DHX",
                    "occurred_at_display": "28 Apr 2026, 17:46 Europe/London",
                },
                "recognition": {
                    "total_pipeline_ms": 742.3,
                    "debounce_or_recognition_ms": 701.0,
                    "slowest_steps": [
                        {"name": "Debounce & Confidence Aggregation", "duration_ms": 701.0}
                    ],
                    "likely_delay_reason": "Most of the time was spent waiting in the LPR debounce/confidence window.",
                },
                "gate": {"outcome_reason": "The automatic gate open command was accepted."},
                "notifications": {"summary": "A persisted notification delivery record exists for this trigger."},
            },
        }
    ]

    bad_text = "However, this system view doesn’t include the per-scan LPR processing/latency metrics."

    assert service._should_replace_with_diagnostic_answer(
        "Why did Steph's latest LPR take much longer than the rest?",
        bad_text,
        tool_results,
    )
    replacement = service._access_diagnostic_direct_text(tool_results[0]["output"])
    assert "742.3ms" in replacement
    assert "Debounce/recognition accounted for 701.0ms" in replacement


def test_unknown_notification_diagnostic_plans_latest_unknown() -> None:
    service = ChatService()
    planned = service._plan_tool_calls(
        "Why didn't I get a notification that there was an unknown vehicle at the gate?",
        {},
        [],
    )
    diagnostic = next(call for call in planned if call.name == "diagnose_access_event")

    assert diagnostic.arguments["unknown_only"] is True
    assert diagnostic.arguments["decision"] == "denied"


def test_detection_count_planner_uses_latest_unknown_for_that_car() -> None:
    service = ChatService()
    planned = service._plan_tool_calls("How many times has that car been at the gate?", {}, [])

    assert planned[0].name == "query_vehicle_detection_history"
    assert planned[0].arguments["latest_unknown"] is True
    assert planned[0].arguments["period"] == "all"


def test_lpr_timing_observation_reports_capture_delay() -> None:
    observation = ai_tools._serialize_lpr_timing_observation(
        {
            "id": "obs-1",
            "source": "uiprotect_track",
            "source_detail": "smart_detect_track.licensePlate.attempt_2",
            "registration_number": "PE70DHX",
            "received_at": "2026-04-28T16:46:28.800000+00:00",
            "captured_at": "2026-04-28T16:46:28.086000+00:00",
            "confidence": 92,
        },
        "Europe/London",
    )

    assert observation["captured_to_received_ms"] == 714.0
    assert observation["received_at"] == "2026-04-28T17:46:28.800000+01:00"


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


@pytest.mark.asyncio
async def test_resolve_human_entity_resolves_fuzzy_vehicle(monkeypatch) -> None:
    class ScalarResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    owner = SimpleNamespace(id=uuid.uuid4(), display_name="Steph Smith")
    vehicle = SimpleNamespace(
        id=uuid.uuid4(),
        registration_number="PE70DHX",
        make="Tesla",
        model="Model Y",
        color="Blue",
        description="Steph's daily car",
        owner=owner,
        person_id=owner.id,
        schedule_id=None,
        schedule=None,
        is_active=True,
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def scalars(self, _query):
            return ScalarResult([vehicle])

    monkeypatch.setattr(ai_tools, "AsyncSessionLocal", lambda: Session())

    result = await ai_tools.resolve_human_entity({"query": "the Tesla", "entity_types": ["vehicle"]})

    assert result["status"] == "unique"
    assert result["match"]["type"] == "vehicle"
    assert result["match"]["registration_number"] == "PE70DHX"


@pytest.mark.asyncio
async def test_resolve_human_entity_resolves_friendly_device(monkeypatch) -> None:
    class ScalarResult:
        def all(self):
            return []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def scalars(self, _query):
            return ScalarResult()

    async def fake_runtime_config():
        return SimpleNamespace(
            home_assistant_gate_entities=[],
            home_assistant_garage_door_entities=[
                {"entity_id": "cover.main_garage", "name": "Main Garage", "enabled": True}
            ],
        )

    monkeypatch.setattr(ai_tools, "AsyncSessionLocal", lambda: Session())
    monkeypatch.setattr(ai_tools, "get_runtime_config", fake_runtime_config)

    result = await ai_tools.resolve_human_entity({"query": "main garage", "entity_types": ["device"]})

    assert result["status"] == "unique"
    assert result["match"]["type"] == "device"
    assert result["match"]["name"] == "Main Garage"


def test_compact_observation_redacts_and_summarizes_payloads() -> None:
    compacted = ai_tools._compact_observation(
        {
            "token": "secret-token",
            "snapshot_image": "x" * 1000,
            "empty": None,
            "events": [{"id": index, "value": "ok"} for index in range(15)],
            "nested": {"a": {"b": {"c": {"d": {"e": "too deep"}}}}},
        }
    )

    assert compacted["token"] == "[redacted]"
    assert compacted["snapshot_image"] == "[omitted_large_media]"
    assert "empty" not in compacted
    assert compacted["events"][-1]["omitted_items"] == 5
    assert compacted["nested"]["a"]["b"]["c"]["type"] == "object"


def test_chat_time_from_iso_converts_utc_to_london() -> None:
    service = ChatService()

    assert service._chat_time_from_iso("2026-04-27T12:00:00+00:00") == "13:00"


def test_agent_datetime_formats_europe_london() -> None:
    value = datetime(2026, 4, 27, 12, 0, tzinfo=UTC)

    assert ai_tools._agent_datetime_iso(value, "Europe/London") == "2026-04-27T13:00:00+01:00"
    assert ai_tools._agent_datetime_display(value, "Europe/London") == "27 Apr 2026, 13:00"


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


@pytest.mark.asyncio
async def test_close_device_preview_uses_close_action(monkeypatch) -> None:
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

    result = await ai_tools.open_device(
        {"target": "main garage door", "kind": "all", "action": "close", "confirm": False}
    )

    assert result["requires_confirmation"] is True
    assert result["action"] == "close"
    assert result["target"] == "Main Garage"
    assert "Closing garage doors" in result["detail"]


@pytest.mark.asyncio
async def test_close_device_executes_close_cover_command(monkeypatch) -> None:
    calls: list[str] = []

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

    async def fake_command_cover(_client, _entity, action, reason):
        calls.append(action)
        return SimpleNamespace(accepted=True, state="closed", detail=reason)

    monkeypatch.setattr(ai_tools, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(ai_tools, "HomeAssistantClient", lambda: object())
    monkeypatch.setattr(ai_tools, "command_cover", fake_command_cover)

    result = await ai_tools.open_device(
        {"target": "main garage door", "kind": "all", "action": "close", "confirm": True}
    )

    assert calls == ["close"]
    assert result["closed"] is True
    assert result["opened"] is False
    assert result["audit_event"] == "agent.device_close_requested"


def test_close_device_confirmation_card_uses_close_language() -> None:
    service = ChatService()
    payload = service._pending_action_public_payload(
        {
            "id": "confirm-1",
            "session_id": "session-1",
            "tool_name": "command_device",
            "preview_output": {
                "action": "close",
                "target": "Main Garage",
                "detail": "Closing garage doors is a real-world action.",
            },
            "expires_at": "2026-04-28T22:00:00+00:00",
        }
    )

    assert payload["title"] == "Close Main Garage?"
    assert payload["confirm_label"] == "Close"


def test_agent_device_log_extra_does_not_overwrite_log_record_name() -> None:
    extra = ai_tools._log_extra({"name": "Main Garage", "kind": "garage_door"})

    assert "name" not in extra
    assert extra["device_name"] == "Main Garage"
    assert extra["kind"] == "garage_door"
