import asyncio
import json
from datetime import UTC, datetime
import time
import uuid
from types import SimpleNamespace

import pytest

from app.ai import tools as ai_tools
from app.ai.providers import ChatMessageInput, LlmResult, LocalProvider, ToolCall
from app.api.v1 import ai as ai_api
from app.services.alfred.feedback import (
    AlfredFeedbackError,
    AlfredFeedbackService,
    DEFAULT_SEEDED_EVAL_EXAMPLES,
    DEFAULT_SEEDED_LESSONS,
    _eval_training_source,
    _feedback_training_source,
    _lesson_training_source,
    _rank_lessons_for_prompt,
    _safe_corrected_answer,
    parse_feedback_command,
)
from app.services.alfred import feedback as feedback_module
from app.services.alfred import embeddings as embeddings_module
from app.services.alfred import memory as alfred_memory_module
from app.services.alfred.planner import (
    PLANNER_PROMPT,
    ToolCallPlan,
    domain_cards,
    parse_planner_selection,
    tools_for_selection,
)
from app.services.alfred.permissions import filter_tools_for_actor
from app.services.alfred.answer_contracts import (
    AnswerDraft,
    extract_answer_artifacts,
    render_answer_from_artifacts,
    select_answer_artifacts,
    verify_answer_draft,
)
from app.ai.tool_groups.registry import ToolRegistryError, _validate_tool
from app.services.alfred.runtime import provider_agent_capability
from app.services.chat import ChatService, IntentRoute, IntentRouterError, SYSTEM_PROMPT
from app.services.chat_contracts import ChatTurnResult


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


class RawJsonAfterToolProvider:
    name = "raw-json-after-tool-test"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, tools=None, tool_results=None):
        self.calls += 1
        if self.calls == 1:
            return LlmResult(
                text="",
                tool_calls=[
                    ToolCall(
                        "hello-fresh-alerts",
                        "query_anomalies",
                        {"status": "all", "search": "hellofresh", "suspected_delivery": True},
                    )
                ],
            )
        return LlmResult(
            text=(
                '[{"alerts":[],"anomalies":[],"count":0,"status":"all",'
                '"day":"recent","search":"hellofresh","suspected_delivery":true,"timezone":"Europe/London"}]'
            )
        )


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
    assert "humorous, sharp, and highly intelligent concierge" in SYSTEM_PROMPT
    assert "dry British wit" in SYSTEM_PROMPT
    assert "find a gate event in a haystack and still make one tidy joke" in SYSTEM_PROMPT
    assert "All intent parsing and tool selection is semantic and LLM-owned" in SYSTEM_PROMPT
    assert "Departure intent means the user is asking about exit evidence" in SYSTEM_PROMPT
    assert "Delivery or supplier arrival intent" in SYSTEM_PROMPT
    assert "Dove Fuels" in SYSTEM_PROMPT
    assert "Never invent people, vehicles, schedules" in SYSTEM_PROMPT
    assert "do not sound like an audit export" in SYSTEM_PROMPT
    assert "never include time zone names" in SYSTEM_PROMPT
    assert "Do not claim an action has happened until a confirmed tool result says it happened" in SYSTEM_PROMPT
    assert "jokes must never soften risk or hide uncertainty" in SYSTEM_PROMPT
    assert "treat them as approved behavioral guidance, not scripts or replacement answers" in SYSTEM_PROMPT
    assert "Do not use keyword rules, regex routing, or hardcoded intent blocks" in PLANNER_PROMPT
    assert "Reason privately" in PLANNER_PROMPT
    assert "2-3 plausible tool-selection candidates" in PLANNER_PROMPT
    assert "Never include private reasoning" in PLANNER_PROMPT
    assert "planned_tool_calls" in PLANNER_PROMPT
    assert "requested_answer_type" in PLANNER_PROMPT
    assert "timestamp alone is not a valid answer" in PLANNER_PROMPT
    assert "oil delivery" in PLANNER_PROMPT
    assert "query_anomalies" in PLANNER_PROMPT
    assert "semantic analogy" in PLANNER_PROMPT


def test_alfred_interactive_model_does_not_follow_openai_nano_default() -> None:
    service = ChatService()
    runtime = SimpleNamespace(
        openai_model="gpt-5.4-nano",
        alfred_interactive_model="",
        alfred_planner_model="gpt-5.4-mini",
        alfred_background_model="gpt-5.4-nano",
        alfred_reasoning_effort="high",
    )

    assert service._interactive_model_for_provider(runtime, "openai") == "gpt-5.4"
    assert service._planner_model_for_provider(runtime, "openai") == "gpt-5.4-mini"
    assert service._background_model_for_provider(runtime, "openai") == "gpt-5.4-nano"


def test_planner_catalog_payload_stays_compact() -> None:
    payload = {
        "message": "open the main garage door",
        "actor_context": {"user": {"role": "admin"}},
        "memory": [],
        "relevant_past_lessons": [],
        "session_memory": {},
        "has_attachments": False,
        "domains": domain_cards(ai_tools.build_agent_tools().values()),
    }
    payload_text = json.dumps(payload, separators=(",", ":"), default=str)

    assert len(PLANNER_PROMPT) + len(payload_text) < 32000


def test_training_lesson_recall_selects_relevant_guidance_without_prompt_stuffing() -> None:
    now = datetime.now(tz=UTC)
    lessons = [
        SimpleNamespace(
            id=uuid.uuid4(),
            scope="site",
            title="Don't confuse get back with exit",
            lesson="When asked 'get back,' treat it as the arrival or return event and query entry evidence.",
            tags=["access-events", "arrival"],
            source_feedback_ids=[],
            confidence=0.94,
            status="active",
            active_at=now,
            created_at=now,
            updated_at=now,
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            scope="site",
            title="Answer departure-time queries with resolved record and exact time",
            lesson="Resolve the person before answering when someone left.",
            tags=["access-events", "departure"],
            source_feedback_ids=[],
            confidence=0.96,
            status="active",
            active_at=now,
            created_at=now,
            updated_at=now,
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            scope="site",
            title="Keep simple device status answers focused",
            lesson="For simple gate or garage state questions, answer the current device state directly.",
            tags=["device-status", "concise-response"],
            source_feedback_ids=[],
            confidence=0.99,
            status="active",
            active_at=now,
            created_at=now,
            updated_at=now,
        ),
    ]

    selected = _rank_lessons_for_prompt("What time did Steph get back this morning?", lessons, limit=2)

    assert [lesson.title for lesson in selected] == ["Don't confuse get back with exit"]


def test_training_sources_describe_user_feedback_reflection_and_seed_data() -> None:
    actor_id = uuid.uuid4()
    user_labels = {str(actor_id): "Jason"}
    feedback_id = uuid.uuid4()
    feedback = SimpleNamespace(
        id=feedback_id,
        actor_user_id=actor_id,
        source_channel="dashboard",
    )

    feedback_source = _feedback_training_source(feedback, user_labels)
    assert feedback_source["label"] == "Jason"
    assert feedback_source["detail"] == "UI"

    linked_lesson = SimpleNamespace(
        created_by_user_id=actor_id,
        source_feedback_ids=[str(feedback_id)],
    )
    assert _lesson_training_source(linked_lesson, {str(feedback_id): feedback}, user_labels) == feedback_source

    reflection_lesson = SimpleNamespace(
        created_by_user_id=actor_id,
        source_feedback_ids=[f"reflection:{uuid.uuid4()}"],
    )
    reflection_source = _lesson_training_source(reflection_lesson, {}, user_labels)
    assert reflection_source["label"] == "Alfred Self Learning"
    assert reflection_source["detail"] == "from Jason's chat"

    seed_lesson = SimpleNamespace(created_by_user_id=None, source_feedback_ids=[])
    seed_source = _lesson_training_source(seed_lesson, {}, {})
    assert seed_source["label"] == "Alfred Seed Data"

    seed_eval = SimpleNamespace(feedback_id=None, metadata_={"seed": "default"})
    eval_source = _eval_training_source(seed_eval, {}, {})
    assert eval_source["label"] == "Alfred Seed Data"
    assert eval_source["detail"] == "Built-in eval"


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
                user_message_id="user-message-1",
                assistant_message_id="assistant-message-1",
            )

    monkeypatch.setattr(ai_api, "chat_service", FakeChatService())
    user = SimpleNamespace(id=uuid.uuid4(), role=SimpleNamespace(value="admin"))
    request = ai_api.ChatRequest(message="hello Alfred", client_context={"timezone": "Europe/London"})

    response = await ai_api.chat(request, current_user=user)

    assert response.text == "I'm Alfred: warm gatehouse brain, sensible clipboard."
    assert response.user_message_id == "user-message-1"
    assert response.assistant_message_id == "assistant-message-1"
    assert captured["message"] == "hello Alfred"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["client_context"] == {"timezone": "Europe/London"}
    assert kwargs["user_role"] == "admin"


@pytest.mark.asyncio
async def test_feedback_api_delegates_to_learning_service(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeFeedbackService:
        async def submit_feedback(self, **kwargs):
            captured.update(kwargs)
            return {"feedback": {"id": "feedback-1"}, "corrected_answer": ""}

    monkeypatch.setattr(ai_api, "alfred_feedback_service", FakeFeedbackService())
    user = SimpleNamespace(id=uuid.uuid4(), role=SimpleNamespace(value="admin"))
    request = ai_api.AlfredFeedbackRequest(
        assistant_message_id="assistant-message-1",
        rating="up",
        source_channel="dashboard",
    )

    response = await ai_api.submit_feedback(request, current_user=user)

    assert response["feedback"]["id"] == "feedback-1"
    assert captured["assistant_message_id"] == "assistant-message-1"
    assert captured["rating"] == "up"
    assert captured["user"] is user


def test_feedback_repair_does_not_accept_placeholder_answers() -> None:
    assert (
        _safe_corrected_answer(
            "Stu left at [time].",
            ideal_answer="Stu left at [time].",
            turn_snapshot={"tool_results": []},
        )
        == ""
    )
    assert (
        _safe_corrected_answer(
            "Stu left at 10:35.",
            ideal_answer="Stu left at 10:35.",
            turn_snapshot={"tool_results": []},
        )
        == "Stu left at 10:35."
    )


@pytest.mark.asyncio
async def test_feedback_thumbs_down_requires_reason_before_database_access() -> None:
    with pytest.raises(AlfredFeedbackError):
        await AlfredFeedbackService().submit_feedback(
            assistant_message_id=str(uuid.uuid4()),
            rating="down",
            reason="",
            ideal_answer="",
            source_channel="dashboard",
            actor_user_id=str(uuid.uuid4()),
            actor_role="admin",
        )


@pytest.mark.asyncio
async def test_alfred_v3_planner_receives_actor_context_before_tooling(monkeypatch) -> None:
    service = ChatService()
    provider = V3PlannerProvider()
    session_id = uuid.uuid4()
    saved_assistant: list[str] = []
    lesson_recall_kwargs: dict[str, str | None] = {}

    class Memory:
        async def recall(self, **_kwargs):
            return [{"scope": "user", "title": "Call me Jas", "content": {"note": "Prefers short answers."}}]

        async def semantic_search(self, *_args, **_kwargs):
            return [
                {
                    "source_type": "lesson",
                    "title": "Presence phrasing",
                    "lesson": "Treat am I home as a current presence check.",
                    "score": 0.87,
                }
            ]

        async def remember_from_turn(self, *_args, **_kwargs):
            return 0

    class Feedback:
        async def recall_active_lessons(self, **kwargs):
            lesson_recall_kwargs.update(kwargs)
            return [{"title": "Short answers", "lesson": "Keep routine answers concise."}]

    async def fake_execute_tool_call(_session_id, call, *, status_callback=None, batch_id=None):
        return {
            "call_id": call.id,
            "name": call.name,
            "arguments": call.arguments,
            "output": {"presence": [{"person": "Jas", "state": "present"}]},
        }

    async def fake_build_messages(_session_id, tool_results, selected_tools, route=None, actor_context=None):
        return [
            ChatMessageInput("system", json.dumps({"actor_context": actor_context, "tools": [tool.name for tool in selected_tools]})),
            ChatMessageInput("user", "Am I home?"),
        ]

    async def fake_append_message(_session_id, role, content, **_kwargs):
        if role == "assistant":
            saved_assistant.append(content)
        return uuid.uuid4()

    async def no_schedule_conflict(_session_id, _memory, _tool_results):
        return None

    monkeypatch.setattr("app.services.chat.alfred_memory_service", Memory())
    monkeypatch.setattr("app.services.chat.alfred_feedback_service", Feedback())
    monkeypatch.setattr(service, "_execute_tool_call", fake_execute_tool_call)
    monkeypatch.setattr(service, "_build_agent_messages", fake_build_messages)
    monkeypatch.setattr(service, "_append_message", fake_append_message)
    monkeypatch.setattr(service, "_update_memory", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(service, "_pending_action_for_response", lambda *_args, **_kwargs: asyncio.sleep(0, result=None))
    monkeypatch.setattr(service, "_schedule_conflict_response", no_schedule_conflict)
    monkeypatch.setattr("app.services.chat.event_bus.publish", lambda *_args, **_kwargs: asyncio.sleep(0))

    result = await service._handle_message_v3(
        provider,
        SimpleNamespace(llm_provider="openai", openai_api_key="key"),
        session_id,
        "Check my presence",
        {},
        [],
        {
            "user": {"id": "user-1", "role": "admin", "username": "jas"},
            "person": {"id": "person-1", "display_name": "Jas"},
            "vehicles": [{"id": "vehicle-1", "registration_number": "VIP123"}],
        },
        status_callback=None,
    )

    planner_payload = json.loads(provider.messages[0][1].content)
    assert planner_payload["actor_context"]["user"]["username"] == "jas"
    assert planner_payload["actor_context"]["vehicles"][0]["registration_number"] == "VIP123"
    assert planner_payload["actor_context"]["alfred_lessons"][0]["title"] == "Short answers"
    assert planner_payload["actor_context"]["relevant_past_lessons"][0]["title"] == "Presence phrasing"
    assert planner_payload["actor_context"]["tool_access"] == {
        "role": "admin",
        "visible_tool_count": len(service._tools),
        "can_prepare_state_changes": True,
        "state_changes_require_admin_confirmation": True,
        "visible_tools_are_permission_filtered": True,
    }
    assert planner_payload["relevant_past_lessons"][0]["source_type"] == "lesson"
    assert planner_payload["memory"][0]["title"] == "Call me Jas"
    assert lesson_recall_kwargs["message"] == "Check my presence"
    assert result.text == "You are present."
    assert saved_assistant == ["You are present."]


@pytest.mark.asyncio
async def test_alfred_v3_fails_closed_for_local_provider(monkeypatch) -> None:
    service = ChatService()
    statuses: list[dict[str, object]] = []

    async def fake_direct_response(session_id, text, **kwargs):
        return ChatTurnResult(str(session_id), kwargs.get("provider", "provider_error"), text, [], [])

    async def status_callback(status):
        statuses.append(status)

    monkeypatch.setattr(service, "_direct_response", fake_direct_response)

    result = await service._handle_message_v3(
        SimpleNamespace(name="local"),
        SimpleNamespace(llm_provider="local"),
        uuid.uuid4(),
        "Who is home?",
        {},
        [],
        {"user": {"id": "user-1", "role": "admin"}},
        status_callback=status_callback,
    )

    assert result.provider == "provider_error"
    assert "requires a configured hosted LLM provider" in result.text
    assert "I did not run any system action" in result.text
    assert statuses[-1]["event"] == "chat.agent_state"
    assert statuses[-1]["phase"] == "provider_error"
    assert statuses[-1]["agents_running"] == 0


@pytest.mark.asyncio
async def test_alfred_v3_persona_answers_chit_chat_with_new_concierge_voice(monkeypatch) -> None:
    provider = V3SimulatedSemanticProvider(
        planner_json=(
            '{"selected_domains":["General"],'
            '"selected_tool_names":[],'
            '"needs_clarification":false,'
            '"safety_posture":"read_only",'
            '"confidence":0.91,'
            '"reason":"chit-chat, no tools needed"}'
        ),
        agent_steps=[
            '{"final":"Awake, calibrated, and regrettably still overqualified for small talk. What needs finding?"}'
        ],
    )

    result, executed, selected_tool_history = await _run_simulated_v3_turn(
        monkeypatch,
        provider=provider,
        message="Are you awake Alfred?",
        tool_outputs={},
    )

    assert executed == []
    assert selected_tool_history[0] == []
    assert provider.agent_tool_catalogs == [[]]
    assert "overqualified for small talk" in result.text
    assert "What needs finding?" in result.text


@pytest.mark.asyncio
async def test_alfred_v3_semantically_maps_steph_leave_to_lpr_exit_logs(monkeypatch) -> None:
    provider = V3SimulatedSemanticProvider(
        planner_json=(
            '{"selected_domains":["Access_Logs"],'
            '"selected_tool_names":["resolve_human_entity","query_access_events"],'
            '"needs_clarification":false,'
            '"safety_posture":"read_only",'
            '"confidence":0.96,'
            '"reason":"semantic departure question requires entity resolution and access-event exit lookup"}'
        ),
        agent_steps=[
            ToolCall("resolve-steph", "resolve_human_entity", {"query": "Steph", "entity_types": ["person", "vehicle"]}),
            ToolCall(
                "query-steph-exit",
                "query_access_events",
                {"person_id": "person-steph", "vehicle_id": "vehicle-steph", "day": "today", "direction": "exit", "limit": 1},
            ),
            '{"final":"Steph left at 07:42. The Tesla made its escape with unusual punctuality."}',
        ],
    )

    result, executed, selected_tool_history = await _run_simulated_v3_turn(
        monkeypatch,
        provider=provider,
        message="When did Steph leave this morning?",
        tool_outputs={
            "resolve_human_entity": {
                "status": "unique",
                "match": {
                    "type": "person",
                    "id": "person-steph",
                    "display_name": "Steph",
                    "vehicles": [{"id": "vehicle-steph", "registration_number": "PE70DHX"}],
                },
            },
            "query_access_events": {
                "events": [
                    {
                        "person": "Steph",
                        "person_id": "person-steph",
                        "vehicle_id": "vehicle-steph",
                        "direction": "exit",
                        "decision": "granted",
                        "occurred_at_display": "07:42",
                    }
                ],
                "count": 1,
            },
        },
    )

    assert selected_tool_history[0] == ["resolve_human_entity", "query_access_events"]
    assert [call.name for call in executed] == ["resolve_human_entity", "query_access_events"]
    assert executed[0].arguments == {"query": "Steph", "entity_types": ["person", "vehicle"]}
    assert executed[1].arguments["vehicle_id"] == "vehicle-steph"
    assert executed[1].arguments["direction"] == "exit"
    assert provider.planner_requests[0]["message"] == "When did Steph leave this morning?"
    assert "left at 07:42" in result.text
    assert "unusual punctuality" in result.text


@pytest.mark.asyncio
async def test_alfred_v3_semantically_routes_missus_bolted_without_keyword_router(monkeypatch) -> None:
    provider = V3SimulatedSemanticProvider(
        planner_json=(
            '{"selected_domains":["Access_Logs"],'
            '"selected_tool_names":["resolve_human_entity","query_access_events"],'
            '"needs_clarification":false,'
            '"safety_posture":"read_only",'
            '"confidence":0.94,'
            '"reason":"relationship reference plus idiomatic departure maps to resolved person and exit logs"}'
        ),
        agent_steps=[
            ToolCall("resolve-missus", "resolve_human_entity", {"query": "the missus", "entity_types": ["person", "vehicle"]}),
            ToolCall(
                "query-missus-exit",
                "query_access_events",
                {"person_id": "person-steph", "vehicle_id": "vehicle-steph", "day": "today", "direction": "exit", "limit": 1},
            ),
            '{"final":"Yes. Steph bolted at 07:42, in the strictly LPR-approved sense of the word."}',
        ],
    )

    result, executed, _selected_tool_history = await _run_simulated_v3_turn(
        monkeypatch,
        provider=provider,
        message="Has the missus bolted yet?",
        tool_outputs={
            "resolve_human_entity": {
                "status": "unique",
                "match": {
                    "type": "person",
                    "id": "person-steph",
                    "display_name": "Steph",
                    "vehicles": [{"id": "vehicle-steph", "registration_number": "PE70DHX"}],
                },
            },
            "query_access_events": {
                "events": [
                    {
                        "person": "Steph",
                        "person_id": "person-steph",
                        "vehicle_id": "vehicle-steph",
                        "direction": "exit",
                        "decision": "granted",
                        "occurred_at_display": "07:42",
                    }
                ],
                "count": 1,
            },
        },
        actor_context={
            "user": {"id": "user-1", "role": "admin", "username": "jas"},
            "person": {"id": "person-jas", "display_name": "Jas"},
        },
    )

    assert [call.name for call in executed] == ["resolve_human_entity", "query_access_events"]
    assert executed[0].arguments["query"] == "the missus"
    assert executed[1].arguments == {
        "person_id": "person-steph",
        "vehicle_id": "vehicle-steph",
        "day": "today",
        "direction": "exit",
        "limit": 1,
    }
    assert provider.planner_requests[0]["message"] == "Has the missus bolted yet?"
    assert "Steph bolted at 07:42" in result.text


@pytest.mark.asyncio
async def test_alfred_v3_semantically_routes_oil_delivery_to_active_and_resolved_alerts(monkeypatch) -> None:
    provider = V3SimulatedSemanticProvider(
        planner_json=(
            '{"selected_domains":["Access_Diagnostics"],'
            '"selected_tool_names":["query_anomalies","analyze_alert_snapshot"],'
            '"needs_clarification":false,'
            '"safety_posture":"read_only",'
            '"confidence":0.93,'
            '"reason":"unknown supplier arrival should inspect open/resolved alerts and retained snapshot evidence"}'
        ),
        agent_steps=[
            ToolCall(
                "query-oil-alerts",
                "query_anomalies",
                {
                    "status": "all",
                    "day": "recent",
                    "search": "oil delivery Dove Fuels truck lorry tanker",
                    "suspected_delivery": True,
                    "limit": 25,
                },
            ),
            ToolCall(
                "inspect-alert-snapshot",
                "analyze_alert_snapshot",
                {
                    "alert_id": "alert-oil-1",
                    "prompt": "Does this retained alert snapshot show an oil/fuel delivery vehicle, truck, lorry, tanker, or Dove Fuels branding?",
                },
            ),
            '{"final":"The oil delivery likely arrived at 09:18. The resolved alert note says Dove Fuels, and the snapshot analysis saw a fuel lorry. Tiny mystery, neatly filed."}',
        ],
    )

    result, executed, selected_tool_history = await _run_simulated_v3_turn(
        monkeypatch,
        provider=provider,
        message="When did the oil delivery arrive?",
        tool_outputs={
            "query_anomalies": {
                "alerts": [
                    {
                        "id": "alert-oil-1",
                        "status": "resolved",
                        "created_at_display": "07 May 2026, 09:18",
                        "resolution_note": "Oil delivery from Dove Fuels.",
                        "delivery_indicators": [
                            "Text evidence mentions Dove Fuels.",
                            "Stored visual evidence reports vehicle type: Truck.",
                        ],
                        "snapshot": {"url": "/api/v1/alerts/alert-oil-1/snapshot"},
                    }
                ],
                "count": 1,
            },
            "analyze_alert_snapshot": {
                "alert_id": "alert-oil-1",
                "analysis": "The image shows a fuel lorry with Dove Fuels branding.",
            },
        },
    )

    assert selected_tool_history[0] == ["query_anomalies", "analyze_alert_snapshot"]
    assert [call.name for call in executed] == ["query_anomalies", "analyze_alert_snapshot"]
    assert executed[0].arguments["status"] == "all"
    assert executed[0].arguments["suspected_delivery"] is True
    assert executed[1].arguments["alert_id"] == "alert-oil-1"
    assert provider.planner_requests[0]["message"] == "When did the oil delivery arrive?"
    assert "likely arrived at 09:18" in result.text
    assert "Dove Fuels" in result.text


@pytest.mark.asyncio
@pytest.mark.alfred_critical
async def test_alfred_v3_duration_turn_cannot_finish_with_timestamp_only_tools(monkeypatch) -> None:
    provider = V3SimulatedSemanticProvider(
        planner_json=(
            '{"selected_domains":["Access_Logs"],'
            '"selected_tool_names":["query_presence","query_access_events"],'
            '"requested_answer_type":"absence_duration",'
            '"planned_tool_calls":['
            '{"name":"query_presence","arguments_json":"{\\"person\\":\\"Ash\\"}"},'
            '{"name":"query_access_events","arguments_json":"{\\"person\\":\\"Ash\\",\\"day\\":\\"today\\",\\"direction\\":\\"exit\\"}"}'
            "],"
            '"needs_clarification":false,'
            '"safety_posture":"read_only",'
            '"confidence":0.83,'
            '"reason":"bad historical plan: timestamp and presence only"}'
        ),
        agent_steps=[],
    )

    result, executed, _selected_tool_history = await _run_simulated_v3_turn(
        monkeypatch,
        provider=provider,
        message="how long has ash been out today?",
        tool_outputs={
            "query_presence": {
                "presence": [{"person": "Ash Smith", "state": "absent"}],
            },
            "query_access_events": {
                "events": [
                    {
                        "person": "Ash Smith",
                        "direction": "exit",
                        "decision": "granted",
                        "occurred_at": "2026-05-09T19:21:00+01:00",
                        "occurred_at_display": "09 May 2026, 19:21",
                    }
                ],
                "answer_artifacts": [
                    {
                        "domain": "access_logs",
                        "answer_type": "latest_departure",
                        "subject_label": "Ash",
                        "primary_fact": {
                            "id": "access.latest_exit",
                            "label": "Latest departure",
                            "value": "2026-05-09T19:21:00+01:00",
                            "display_value": "19:21",
                            "kind": "datetime",
                            "source": "access_events",
                            "must_appear": True,
                        },
                    }
                ],
            },
            "calculate_absence_duration": {
                "subject": "Ash Smith",
                "absence_seconds": 540,
                "absence_display": "9 mins",
                "status": "returned",
                "answer_artifacts": [
                    {
                        "domain": "access_logs",
                        "answer_type": "absence_duration",
                        "subject_label": "Ash",
                        "primary_fact": {
                            "id": "absence.duration.latest",
                            "label": "Latest absence duration",
                            "value": 540,
                            "display_value": "9 mins",
                            "kind": "duration",
                            "source": "access_events",
                            "must_appear": True,
                        },
                        "source_records": [
                            {
                                "left_at": "19:21",
                                "returned_at": "19:30",
                                "seconds": 540,
                                "mode": "latest",
                            }
                        ],
                    }
                ],
            },
        },
    )

    assert [call.name for call in executed] == [
        "query_presence",
        "query_access_events",
        "calculate_absence_duration",
    ]
    assert executed[-1].arguments == {"person": "Ash", "day": "today", "mode": "latest"}
    assert "9 mins" in result.text
    assert "from 19:21 to 19:30" in result.text
    assert result.text != "Ash left at 19:21."
    assert "left at 19:21" not in result.text


@pytest.mark.asyncio
@pytest.mark.alfred_critical
async def test_alfred_v3_planned_confirmation_action_skips_react_loop(monkeypatch) -> None:
    provider = V3SimulatedSemanticProvider(
        planner_json=(
            '{"selected_domains":["Gate_Hardware"],'
            '"selected_tool_names":["open_device"],'
            '"requested_answer_type":"action",'
            '"planned_tool_calls":['
            '{"name":"open_device","arguments_json":"{\\"target\\":\\"main garage door\\",\\"kind\\":\\"garage_door\\",\\"action\\":\\"open\\",\\"confirm\\":true}"}'
            "],"
            '"needs_clarification":false,'
            '"safety_posture":"confirmation_required",'
            '"confidence":0.94,'
            '"reason":"clear garage-door action"}'
        ),
        agent_steps=[],
    )

    result, executed, selected_tool_history = await _run_simulated_v3_turn(
        monkeypatch,
        provider=provider,
        message="open the main garage door",
        tool_outputs={
            "open_device": {
                "opened": False,
                "accepted": False,
                "action": "open",
                "requires_confirmation": True,
                "target": "Main Garage",
                "device": {"name": "Main Garage", "kind": "garage_door"},
                "confirmation_field": "confirm",
                "detail": "Opening gates and garage doors is a real-world action. Use the chat confirmation action before I continue.",
            },
        },
    )

    assert selected_tool_history == []
    assert provider.agent_tool_catalogs == []
    assert [(call.name, call.arguments) for call in executed] == [
        ("open_device", {"target": "main garage door", "kind": "garage_door", "action": "open", "confirm": False})
    ]
    assert "Please confirm before I open Main Garage" in result.text


def test_standard_users_do_not_see_mutation_or_admin_tools() -> None:
    service = ChatService()

    names = {
        tool.name
        for tool in filter_tools_for_actor(
            service._tools.values(),
            {"user": {"id": "user-1", "role": "standard"}},
        )
    }

    assert "query_presence" in names
    assert "open_gate" not in names
    assert "update_system_settings" not in names
    assert "query_auth_secret_status" not in names
    assert "query_alfred_runtime_events" not in names


def test_local_provider_is_reported_as_non_agent_capable() -> None:
    status = provider_agent_capability(SimpleNamespace(llm_provider="local"), "local")

    assert status["configured"] is True
    assert status["agent_capable"] is False
    assert status["reason"] == "local_provider_non_agent"


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


class V3PlannerProvider:
    name = "openai"

    def __init__(self) -> None:
        self.messages: list[list[ChatMessageInput]] = []
        self.calls = 0

    async def complete(self, messages, tools=None, tool_results=None):
        self.calls += 1
        self.messages.append(messages)
        if self.calls == 1:
            return LlmResult(
                text=(
                    '{"selected_domains":["Access_Logs"],'
                    '"selected_tool_names":["query_presence"],'
                    '"needs_clarification":false,'
                    '"safety_posture":"read_only",'
                    '"confidence":0.95,'
                    '"reason":"presence check"}'
                )
            )
        if self.calls == 2:
            return LlmResult(
                text="",
                tool_calls=[ToolCall("presence", "query_presence", {"person": "me"})],
            )
        return LlmResult(text='{"final":"You are present."}')


class V3SimulatedSemanticProvider:
    name = "openai"

    def __init__(self, *, planner_json: str, agent_steps: list[ToolCall | str]) -> None:
        self.planner_json = planner_json
        self.agent_steps = list(agent_steps)
        self.planner_requests: list[dict[str, object]] = []
        self.agent_tool_catalogs: list[list[str]] = []

    async def complete(self, messages, tools=None, tool_results=None):
        if tools is None and messages and messages[0].content.startswith("You are Alfred's v3 planning brain"):
            self.planner_requests.append(json.loads(messages[1].content))
            return LlmResult(text=self.planner_json)

        self.agent_tool_catalogs.append([tool["name"] for tool in tools or []])
        if not self.agent_steps:
            raise AssertionError("Simulated provider received more agent calls than expected.")
        step = self.agent_steps.pop(0)
        if isinstance(step, ToolCall):
            return LlmResult(text="", tool_calls=[step])
        return LlmResult(text=step)


class DualActionPreviewProvider:
    name = "action-preview-test"

    async def complete(self, messages, tools=None, tool_results=None):
        return LlmResult(
            text=(
                '{"tool_calls":['
                '{"id":"schedule","name":"create_schedule","arguments":{"name":"A","confirm":true}},'
                '{"id":"pass","name":"create_visitor_pass","arguments":{"visitor_name":"Chris","expected_time":"2026-05-05T11:00:00+01:00","confirm":true}}'
                ']}'
            )
        )


async def _run_simulated_v3_turn(
    monkeypatch,
    *,
    provider: V3SimulatedSemanticProvider,
    message: str,
    tool_outputs: dict[str, dict[str, object]],
    actor_context: dict[str, object] | None = None,
) -> tuple[ChatTurnResult, list[ToolCall], list[list[str]]]:
    service = ChatService()
    executed: list[ToolCall] = []
    selected_tool_history: list[list[str]] = []

    def forbidden_deterministic_routing(*_args, **_kwargs):
        raise AssertionError("Deterministic keyword routing was invoked in a v3 semantic-routing test.")

    async def fake_execute_tool_call(_session_id, call, *, status_callback=None, batch_id=None):
        executed.append(call)
        return {
            "call_id": call.id,
            "name": call.name,
            "arguments": call.arguments,
            "output": tool_outputs[call.name],
        }

    async def fake_build_messages(_session_id, tool_results, selected_tools, route=None, actor_context=None):
        selected_tool_history.append([tool.name for tool in selected_tools])
        return [
            ChatMessageInput("system", SYSTEM_PROMPT),
            ChatMessageInput(
                "user",
                json.dumps(
                    {
                        "message": message,
                        "route": route.intents if route else [],
                        "tool_results": tool_results,
                        "actor_context": actor_context or {},
                    },
                    default=str,
                ),
            ),
        ]

    async def fake_append_message(_session_id, _role, _content, **_kwargs):
        return uuid.uuid4()

    async def fake_store_pending_agent_action(
        session_id,
        pending_result,
        _tool_results,
        _route,
        _selected_tools,
        **_kwargs,
    ):
        pending = {
            "id": "confirm-test",
            "session_id": str(session_id),
            "tool_name": str(pending_result.get("name") or ""),
            "preview_output": pending_result.get("output") if isinstance(pending_result.get("output"), dict) else {},
            "expires_at": "2026-05-09T12:10:00+00:00",
        }
        return service._pending_action_public_payload(pending)

    async def no_schedule_conflict(_session_id, _memory, _tool_results):
        return None

    class Memory:
        async def recall(self, **_kwargs):
            return []

        async def remember_from_turn(self, *_args, **_kwargs):
            return 0

    class Feedback:
        async def recall_active_lessons(self, **_kwargs):
            return []

    monkeypatch.setattr(service, "_deterministic_react_calls", forbidden_deterministic_routing)
    monkeypatch.setattr(service, "_select_tools_for_route", forbidden_deterministic_routing)
    monkeypatch.setattr(service, "_execute_tool_call", fake_execute_tool_call)
    monkeypatch.setattr(service, "_build_agent_messages", fake_build_messages)
    monkeypatch.setattr(service, "_append_message", fake_append_message)
    monkeypatch.setattr(service, "_store_pending_agent_action", fake_store_pending_agent_action)
    monkeypatch.setattr(service, "_update_memory", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(service, "_pending_action_for_response", lambda *_args, **_kwargs: asyncio.sleep(0, result=None))
    monkeypatch.setattr(service, "_schedule_conflict_response", no_schedule_conflict)
    monkeypatch.setattr("app.services.chat.alfred_memory_service", Memory())
    monkeypatch.setattr("app.services.chat.alfred_feedback_service", Feedback())
    monkeypatch.setattr("app.services.chat.event_bus.publish", lambda *_args, **_kwargs: asyncio.sleep(0))

    result = await service._handle_message_v3(
        provider,
        SimpleNamespace(llm_provider="openai", openai_api_key="key", openai_model="semantic-test"),
        uuid.uuid4(),
        message,
        {},
        [],
        actor_context or {"user": {"id": "user-1", "role": "admin"}},
        status_callback=None,
    )
    return result, executed, selected_tool_history


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
async def test_react_loop_does_not_expose_raw_tool_json_after_empty_alert_search(monkeypatch) -> None:
    service = ChatService()

    async def fake_execute_tool_batch(_session_id, calls, _selected_tools, **_kwargs):
        call = calls[0]
        return [
            {
                "call_id": call.id,
                "name": call.name,
                "arguments": call.arguments,
                "output": {
                    "alerts": [],
                    "anomalies": [],
                    "count": 0,
                    "status": "all",
                    "day": "recent",
                    "search": "hellofresh",
                    "suspected_delivery": True,
                    "timezone": "Europe/London",
                },
            }
        ]

    async def fake_build_agent_messages(_session_id, _tool_results, _selected_tools, route=None, actor_context=None):
        return [ChatMessageInput("system", "test")]

    async def no_schedule_conflict(_session_id, _memory, _tool_results):
        return None

    monkeypatch.setattr(service, "_execute_tool_batch", fake_execute_tool_batch)
    monkeypatch.setattr(service, "_build_agent_messages", fake_build_agent_messages)
    monkeypatch.setattr(service, "_schedule_conflict_response", no_schedule_conflict)

    result = await service._run_provider_agent_loop(
        RawJsonAfterToolProvider(),
        uuid.uuid4(),
        [ChatMessageInput("system", "test")],
        [],
        [service._tools["query_anomalies"]],
        {},
        route=IntentRoute(("Access_Diagnostics",), 0.9, False, "delivery alert search"),
        user_message="When did the hello fresh delivery arrive?",
        status_callback=None,
    )

    assert not result.text.strip().startswith("[")
    assert "couldn't find any matching active or resolved alerts for hellofresh" in result.text


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


@pytest.mark.alfred_critical
def test_confirmed_terminal_actions_do_not_resume_original_request() -> None:
    service = ChatService()

    assert service._confirmed_tool_finishes_without_resume("open_gate") is True
    assert service._confirmed_tool_finishes_without_resume("open_device") is True
    assert service._confirmed_tool_finishes_without_resume("command_device") is True
    assert service._confirmed_tool_finishes_without_resume("create_schedule") is True
    assert service._confirmed_tool_finishes_without_resume("create_visitor_pass") is True
    assert service._confirmed_tool_finishes_without_resume("update_visitor_pass") is True
    assert service._confirmed_tool_finishes_without_resume("cancel_visitor_pass") is True
    assert service._confirmed_tool_finishes_without_resume("test_notification_workflow") is True


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


def test_time_label_behavior_is_training_not_cleanup() -> None:
    service = ChatService()

    assert not hasattr(service, "_strip_local_time_labels")
    assert "never include time zone names" in SYSTEM_PROMPT
    assert any(item["title"] == "Use the site clock silently" for item in DEFAULT_SEEDED_LESSONS)


def test_assistant_text_cleanup_removes_redundant_seconds_parentheses() -> None:
    service = ChatService()

    cleaned = service._clean_assistant_text("Steph left this morning at 07:38 (07:38:12).", [])

    assert cleaned == "Steph left this morning at 07:38."


def test_noop_malfunction_guidance_is_a_lesson_not_keyword_filter() -> None:
    service = ChatService()
    seeded_text = " ".join(item["lesson"] for item in DEFAULT_SEEDED_LESSONS)

    assert not hasattr(service, "_should_suppress_tool_result_for_prompt")
    assert not hasattr(service, "_humanize_robotic_absence_summary")
    assert "Do not mention inactive malfunctions" in seeded_text
    assert "exit with their next entry" in seeded_text
    assert "duration first in Alfred's natural voice" in seeded_text
    assert any(item["title"] == "Investigate missing access as a full chain" for item in DEFAULT_SEEDED_LESSONS)
    assert any(item["title"] == "Distinguish absence duration from visit duration" for item in DEFAULT_SEEDED_LESSONS)
    assert any(item["title"] == "Keep duration answers human" for item in DEFAULT_SEEDED_LESSONS)
    assert any(item["title"] == "Use the site clock silently" for item in DEFAULT_SEEDED_LESSONS)
    assert any(item["metadata"]["seed"] == "sylv_absence_duration_exit_to_entry" for item in DEFAULT_SEEDED_EVAL_EXAMPLES)


def test_timezone_auto_learning_uses_prompt_guidance_not_sanitizer() -> None:
    assert not hasattr(feedback_module, "lesson_text_without_timezone_directives")
    assert "Never create lessons that tell Alfred to mention time zones" in feedback_module.FEEDBACK_ANALYSIS_PROMPT
    assert "Never create lessons that tell Alfred to mention time zones" in feedback_module.TURN_REFLECTION_PROMPT


@pytest.mark.asyncio
async def test_default_eval_example_seed_is_idempotent(monkeypatch) -> None:
    rows = []

    class FakeSeedSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def scalar(self, _query):
            return rows[0] if rows else None

        def add(self, row):
            rows.append(row)

        async def commit(self):
            return None

    monkeypatch.setattr(feedback_module, "AsyncSessionLocal", lambda: FakeSeedSession())

    await AlfredFeedbackService().seed_default_eval_examples()
    await AlfredFeedbackService().seed_default_eval_examples()

    assert len(rows) == 1
    assert rows[0].prompt == DEFAULT_SEEDED_EVAL_EXAMPLES[0]["prompt"]
    assert rows[0].scope == "site"
    assert rows[0].metadata_["seed"] == "ash_1818_suppressed_read_incident"


def test_messaging_feedback_commands_parse_without_keyword_routing() -> None:
    assert parse_feedback_command("thumbs up") == {"rating": "up", "reason": "", "ideal_answer": ""}
    assert parse_feedback_command("thumbs down Too much detail. ideal: Just say the gate is closed.") == {
        "rating": "down",
        "reason": "Too much detail.",
        "ideal_answer": "Just say the gate is closed.",
    }
    assert parse_feedback_command("is the top gate open") is None


def test_visitor_pass_resolution_direct_text_reports_arrival_time() -> None:
    service = ChatService()

    text = service._entity_resolution_direct_text(
        {
            "status": "unique",
            "match": {
                "type": "visitor_pass",
                "visitor_name": "Stu",
                "display_name": "Stu",
                "arrival_time": "2026-05-04T10:12:45+01:00",
            },
        }
    )

    assert text == "Stu's Visitor Pass shows arrival at 10:12."


def test_query_visitor_pass_fallback_prioritizes_arrival_time() -> None:
    service = ChatService()

    text = service._fallback_text(
        [
            {
                "name": "query_visitor_passes",
                "output": {
                    "visitor_passes": [
                        {
                            "visitor_name": "Stu",
                            "arrival_time": "2026-05-04T10:12:45+01:00",
                            "vehicle_summary": "Blue Ford - AB12CDE",
                        }
                    ]
                },
            }
        ]
    )

    assert text == "Stu arrived at 10:12."


def test_calculate_visit_duration_fallback_never_returns_raw_json() -> None:
    service = ChatService()
    output = {
        "duration_seconds": 820,
        "duration_human": "13m",
        "intervals": [
            {
                "entry": "2026-05-09T16:44:13.005000+01:00",
                "entry_display": "09 May 2026, 16:44",
                "exit": "still_present",
                "exit_display": None,
            }
        ],
        "matched_events": 2,
        "timezone": "Europe/London",
    }

    text = service._fallback_text([{"name": "calculate_visit_duration", "output": output}])

    assert text == "The matched visit has lasted 13m since 09 May 2026, 16:44, and is still marked open."
    assert not text.lstrip().startswith(("[", "{"))
    assert "duration_seconds" not in text


@pytest.mark.asyncio
async def test_calculate_absence_duration_pairs_exit_to_next_entry(monkeypatch) -> None:
    async def fake_query_access_events(arguments):
        assert arguments["person"] == "Sylv"
        return {
            "timezone": "Europe/London",
            "events": [
                {
                    "person": "Sylvia Smith",
                    "direction": "exit",
                    "decision": "granted",
                    "occurred_at": "2026-05-09T16:30:33+01:00",
                },
                {
                    "person": "Sylvia Smith",
                    "direction": "entry",
                    "decision": "granted",
                    "occurred_at": "2026-05-09T16:44:13+01:00",
                },
            ],
        }

    monkeypatch.setattr(ai_tools, "query_access_events", fake_query_access_events)

    result = await ai_tools.calculate_absence_duration({"person": "Sylv", "day": "today"})

    assert result["subject"] == "Sylvia Smith"
    assert result["absence_seconds"] == 820
    assert result["absence_human"] == "13m"
    assert result["total_absence_seconds"] == 820
    assert result["total_absence_human"] == "13m"
    assert result["mode"] == "latest"
    assert result["status"] == "returned"
    assert result["answer_hints"]
    assert "Sylvia Smith was out for 13m" in result["answer_hints"][0]
    assert result["intervals"] == [
        {
            "exit": "2026-05-09T16:30:33+01:00",
            "exit_display": "09 May 2026, 16:30",
            "entry": "2026-05-09T16:44:13+01:00",
            "entry_display": "09 May 2026, 16:44",
            "seconds": 820,
            "duration_human": "13m",
        }
    ]
    assert result["primary_interval"] == result["intervals"][0]


@pytest.mark.asyncio
async def test_calculate_absence_duration_defaults_to_latest_interval_not_recent_total(monkeypatch) -> None:
    async def fake_query_access_events(arguments):
        assert arguments["person"] == "Sylv"
        return {
            "timezone": "Europe/London",
            "events": [
                {
                    "person": "Sylvia Smith",
                    "direction": "exit",
                    "decision": "granted",
                    "occurred_at": "2026-05-08T10:00:00+01:00",
                },
                {
                    "person": "Sylvia Smith",
                    "direction": "entry",
                    "decision": "granted",
                    "occurred_at": "2026-05-08T11:00:00+01:00",
                },
                {
                    "person": "Sylvia Smith",
                    "direction": "exit",
                    "decision": "granted",
                    "occurred_at": "2026-05-09T15:54:42.335000+01:00",
                },
                {
                    "person": "Sylvia Smith",
                    "direction": "entry",
                    "decision": "granted",
                    "occurred_at": "2026-05-09T16:44:13.005000+01:00",
                },
            ],
        }

    monkeypatch.setattr(ai_tools, "query_access_events", fake_query_access_events)

    latest = await ai_tools.calculate_absence_duration({"person": "Sylv", "day": "recent"})

    assert latest["absence_seconds"] == 2970
    assert latest["absence_human"] == "49m"
    assert latest["total_absence_seconds"] == 6570
    assert latest["total_absence_human"] == "1h 49m"
    assert latest["primary_interval"]["exit_display"] == "09 May 2026, 15:54"
    assert latest["primary_interval"]["entry_display"] == "09 May 2026, 16:44"
    assert "Sylvia Smith was out for 49m, from 09 May 2026, 15:54 to 09 May 2026, 16:44" in latest["answer_hints"][0]
    assert "1h 49m" not in latest["answer_hints"][0]

    total = await ai_tools.calculate_absence_duration({"person": "Sylv", "day": "recent", "mode": "total"})

    assert total["absence_seconds"] == 6570
    assert total["absence_human"] == "1h 49m"
    assert total["total_absence_human"] == "1h 49m"
    assert "Sylvia Smith was out for 1h 49m in total across 2 matched absences" in total["answer_hints"][0]
    assert "The latest matched absence was 49m" in total["answer_hints"][0]


@pytest.mark.asyncio
async def test_absence_duration_artifact_uses_latest_interval_and_requested_name(monkeypatch) -> None:
    async def fake_query_access_events(arguments):
        return {
            "timezone": "Europe/London",
            "events": [
                {
                    "person": "Sylvia Smith",
                    "direction": "exit",
                    "decision": "granted",
                    "occurred_at": "2026-05-08T10:00:00+01:00",
                },
                {
                    "person": "Sylvia Smith",
                    "direction": "entry",
                    "decision": "granted",
                    "occurred_at": "2026-05-08T11:00:00+01:00",
                },
                {
                    "person": "Sylvia Smith",
                    "direction": "exit",
                    "decision": "granted",
                    "occurred_at": "2026-05-09T15:54:42.335000+01:00",
                },
                {
                    "person": "Sylvia Smith",
                    "direction": "entry",
                    "decision": "granted",
                    "occurred_at": "2026-05-09T16:44:13.005000+01:00",
                },
            ],
        }

    monkeypatch.setattr(ai_tools, "query_access_events", fake_query_access_events)

    result = await ai_tools.calculate_absence_duration({"person": "Sylv", "day": "recent"})
    artifacts = extract_answer_artifacts([{"name": "calculate_absence_duration", "output": result}])

    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.subject_label == "Sylv"
    assert artifact.primary_fact
    assert artifact.primary_fact.id == "absence.duration.latest"
    assert artifact.primary_fact.value == 2970
    assert artifact.primary_fact.display_value == "50 mins"
    fallback = render_answer_from_artifacts(artifacts)
    assert fallback == "Sylv was out for 50 mins, from 15:54 to 16:44."
    assert "1h 49m" not in fallback
    assert "(Sylvia Smith)" not in fallback
    assert "Europe/London" not in fallback


def test_answer_verifier_rejects_duration_not_in_artifact() -> None:
    artifact = {
        "answer_artifacts": [
            {
                "domain": "access_logs",
                "answer_type": "absence_duration",
                "subject_label": "Sylv",
                "primary_fact": {
                    "id": "absence.duration.latest",
                    "label": "Latest absence duration",
                    "value": 2970,
                    "display_value": "50 mins",
                    "kind": "duration",
                    "source": "access_events",
                    "must_appear": True,
                },
                "source_records": [{"left_at": "15:54", "returned_at": "16:44"}],
                "fallback_text": "Sylv was out for 50 mins, from 15:54 to 16:44.",
            }
        ]
    }
    artifacts = extract_answer_artifacts([{"name": "calculate_absence_duration", "output": artifact}])
    draft = AnswerDraft(
        answer_text="Sylv was out for 1h 49m, from 15:54 to 16:44.",
        fact_ids_used=["absence.duration.latest"],
        style="natural_concise",
        confidence=0.8,
        needs_clarification=False,
    )

    result = verify_answer_draft(draft, artifacts)

    assert result.approved is False
    assert any("display value" in reason for reason in result.reasons)


def test_answer_artifact_selection_keeps_absence_separate_from_visitor_pass() -> None:
    artifacts = extract_answer_artifacts(
        [
            {
                "name": "calculate_absence_duration",
                "output": {
                    "answer_artifacts": [
                        {
                            "domain": "access_logs",
                            "answer_type": "absence_duration",
                            "subject_label": "Ash",
                            "primary_fact": {
                                "id": "absence.duration.latest",
                                "label": "Latest absence duration",
                                "value": 8320,
                                "display_value": "2h 19m",
                                "kind": "duration",
                                "source": "access_events",
                                "must_appear": True,
                            },
                            "source_records": [{"left_at": "15:21", "returned_at": "17:40"}],
                            "fallback_text": "Ash was out for 2h 19m, from 15:21 to 17:40.",
                        }
                    ]
                },
            },
            {
                "name": "query_visitor_passes",
                "output": {
                    "answer_artifacts": [
                        {
                            "domain": "visitor_passes",
                            "answer_type": "visitor_pass",
                            "subject_label": "Ash",
                            "primary_fact": {
                                "id": "visitor.status",
                                "label": "Visitor pass status",
                                "value": "cancelled",
                                "display_value": "cancelled",
                                "kind": "status",
                                "source": "visitor_passes",
                                "must_appear": True,
                            },
                            "fallback_text": "Ash has a cancelled visitor pass.",
                        }
                    ]
                },
            },
        ]
    )

    selected = select_answer_artifacts(artifacts)
    text = render_answer_from_artifacts(artifacts)

    assert [artifact.answer_type for artifact in selected] == ["absence_duration"]
    assert text == "Ash was out for 2h 19m, from 15:21 to 17:40."
    assert "visitor pass" not in text


@pytest.mark.asyncio
async def test_calculate_absence_duration_ongoing_includes_human_answer_hint(monkeypatch) -> None:
    async def fake_query_access_events(arguments):
        assert arguments["person"] == "Ash"
        return {
            "timezone": "Europe/London",
            "events": [
                {
                    "person": "Ash",
                    "direction": "exit",
                    "decision": "granted",
                    "occurred_at": "2026-05-09T15:50:00+01:00",
                },
            ],
        }

    monkeypatch.setattr(ai_tools, "query_access_events", fake_query_access_events)
    monkeypatch.setattr(
        ai_tools,
        "_agent_now",
        lambda _timezone_name=None: datetime.fromisoformat("2026-05-09T17:40:00+01:00"),
    )

    result = await ai_tools.calculate_absence_duration({"person": "Ash", "day": "today"})

    assert result["subject"] == "Ash"
    assert result["absence_human"] == "1h 50m"
    assert result["status"] == "still_away"
    assert result["as_of_display"] == "09 May 2026, 17:40"
    assert result["answer_hints"]
    hint = result["answer_hints"][0]
    assert "Ash has been out for 1h 50m since 09 May 2026, 15:50. Still marked away as of 09 May 2026, 17:40" in hint
    assert "avoid robotic audit-log phrasing" in hint


def test_calculate_absence_duration_fallback_uses_natural_language() -> None:
    service = ChatService()
    output = {
        "subject": "Sylvia Smith",
        "absence_seconds": 820,
        "absence_human": "13m",
        "intervals": [
            {
                "exit": "2026-05-09T16:30:33+01:00",
                "exit_display": "09 May 2026, 16:30",
                "entry": "2026-05-09T16:44:13+01:00",
                "entry_display": "09 May 2026, 16:44",
            }
        ],
        "matched_events": 2,
        "status": "returned",
        "timezone": "Europe/London",
    }

    text = service._fallback_text([{"name": "calculate_absence_duration", "output": output}])

    assert text == "Sylvia Smith was out for 13m, from 09 May 2026, 16:30 to 09 May 2026, 16:44. Neat little there-and-back loop."
    assert not text.lstrip().startswith(("[", "{"))
    assert "absence_seconds" not in text


def test_calculate_absence_duration_fallback_keeps_ongoing_absence_human() -> None:
    service = ChatService()
    output = {
        "subject": "Ash",
        "absence_seconds": 6600,
        "absence_human": "1h 50m",
        "intervals": [
            {
                "exit": "2026-05-09T15:50:00+01:00",
                "exit_display": "09 May 2026, 15:50",
                "entry": "still_away",
                "entry_display": None,
            }
        ],
        "matched_events": 1,
        "status": "still_away",
        "timezone": "Europe/London",
        "as_of": "2026-05-09T17:40:00+01:00",
        "as_of_display": "09 May 2026, 17:40",
    }

    text = service._fallback_text([{"name": "calculate_absence_duration", "output": output}])

    assert text == "Ash has been out for 1h 50m since 09 May 2026, 15:50. Still marked away as of 17:40, so the clock's still running."
    assert "latest logged departure" not in text
    assert "That means" not in text
    assert "absence_seconds" not in text


def test_absence_persona_uses_training_not_phrase_humanizer() -> None:
    service = ChatService()
    seeded_text = " ".join(item["lesson"] for item in DEFAULT_SEEDED_LESSONS)

    assert not hasattr(service, "_humanize_robotic_absence_summary")
    assert "duration first in Alfred's natural voice" in seeded_text
    assert "do not sound like an audit export" in SYSTEM_PROMPT


def test_assistant_text_cleanup_blocks_raw_json_tool_payloads() -> None:
    service = ChatService()
    raw_json = json.dumps(
        [
            {
                "duration_seconds": 820,
                "duration_human": "13m",
                "intervals": [
                    {
                        "entry": "2026-05-09T16:44:13.005000+01:00",
                        "entry_display": "09 May 2026, 16:44",
                        "exit": "still_present",
                        "exit_display": None,
                    }
                ],
                "matched_events": 2,
                "timezone": "Europe/London",
            }
        ]
    )

    for response_text in (raw_json, json.dumps(raw_json)):
        cleaned = service._clean_assistant_text(response_text, [])

        assert cleaned == "The matched visit has lasted 13m since 09 May 2026, 16:44, and is still marked open."
        assert not cleaned.lstrip().startswith(("[", "{", '"[', '"{'))
        assert "duration_seconds" not in cleaned


@pytest.mark.asyncio
async def test_alfred_memory_recall_serializes_rows_before_session_closes(monkeypatch) -> None:
    class Result:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class Row:
        def __init__(self, owner_session):
            self._session = owner_session
            self.id = uuid.uuid4()
            self.scope = "user"
            self.kind = "preference"
            self.title = "Short answers"
            self.content = {"note": "Keep it concise."}
            self.tags = ["style"]
            self.confidence = 0.8
            self.last_used_at = None
            self.created_at = datetime.now(tz=UTC)
            self.owner_user_id = uuid.uuid4()

        @property
        def updated_at(self):
            assert not self._session.closed, "memory row was serialized after the DB session closed"
            return datetime.now(tz=UTC)

    class Session:
        def __init__(self):
            self.closed = False
            self.row = Row(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            self.closed = True

        async def scalars(self, _query):
            return Result([self.row])

        async def commit(self):
            return None

        async def refresh(self, _row):
            return None

    monkeypatch.setattr(alfred_memory_module, "AsyncSessionLocal", Session)

    rows = await alfred_memory_module.AlfredMemoryService().recall(
        user_id=str(uuid.uuid4()),
        user_role="admin",
        session_id=str(uuid.uuid4()),
    )

    assert rows[0]["title"] == "Short answers"
    assert rows[0]["updated_at"]


@pytest.mark.asyncio
async def test_semantic_search_returns_redis_cache_hit_without_embedding(monkeypatch) -> None:
    cached = [{"source_type": "memory", "source": "semantic", "title": "Cached answer"}]

    async def cache_get(_key):
        return cached

    async def fail_embedding(*_args, **_kwargs):
        raise AssertionError("embedding generation should be skipped on cache hit")

    monkeypatch.setattr(alfred_memory_module, "_semantic_cache_get", cache_get)
    monkeypatch.setattr(alfred_memory_module, "generate_embedding", fail_embedding)

    results = await alfred_memory_module.AlfredMemoryService().semantic_search(
        "gate status concise",
        actor_id=str(uuid.uuid4()),
    )

    assert results == cached


@pytest.mark.asyncio
async def test_semantic_search_falls_back_to_lexical_with_user_scope(monkeypatch) -> None:
    now = datetime.now(tz=UTC)
    actor_id = uuid.uuid4()
    queries: list[str] = []
    cached: list[tuple[str, list[dict]]] = []

    async def no_embedding(*_args, **_kwargs):
        return None

    async def cache_miss(_key):
        return None

    async def cache_set(key, rows):
        cached.append((key, rows))

    class ScalarResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class Session:
        calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def scalars(self, query):
            queries.append(str(query))
            self.calls += 1
            if self.calls == 1:
                return ScalarResult(
                    [
                        SimpleNamespace(
                            id=uuid.uuid4(),
                            scope="user",
                            kind="preference",
                            title="Gate status style",
                            content={"note": "Keep gate status answers concise."},
                            tags=["device-status"],
                            confidence=0.8,
                        )
                    ]
                )
            return ScalarResult(
                [
                    SimpleNamespace(
                        id=uuid.uuid4(),
                        scope="site",
                        title="Gate status answers",
                        lesson="Answer gate status questions directly from current device state.",
                        tags=["device-status"],
                        confidence=0.9,
                        status="active",
                        active_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                ]
            )

    monkeypatch.setattr(alfred_memory_module, "generate_embedding", no_embedding)
    monkeypatch.setattr(alfred_memory_module, "AsyncSessionLocal", Session)
    monkeypatch.setattr(alfred_memory_module, "_semantic_cache_get", cache_miss)
    monkeypatch.setattr(alfred_memory_module, "_semantic_cache_set", cache_set)

    results = await alfred_memory_module.AlfredMemoryService().semantic_search(
        "gate status concise",
        actor_id=str(actor_id),
    )

    assert results
    assert {result["source_type"] for result in results} == {"memory", "lesson"}
    assert all(result["source"] == "lexical" for result in results)
    assert any("owner_user_id" in query for query in queries)
    assert cached == [(alfred_memory_module._semantic_search_cache_key("gate status concise", limit=5, actor_uuid=actor_id), results)]


@pytest.mark.asyncio
async def test_openai_embedding_provider_success_and_failure(monkeypatch) -> None:
    vector = [0.01] * embeddings_module.ALFRED_EMBEDDING_DIMENSION

    async def fake_runtime_config():
        return SimpleNamespace(
            alfred_semantic_memory_enabled=True,
            alfred_embedding_provider="openai",
            alfred_embedding_dimension=embeddings_module.ALFRED_EMBEDDING_DIMENSION,
            alfred_embedding_model="text-embedding-3-small",
            openai_api_key="key",
            openai_base_url="https://api.openai.test/v1",
            llm_timeout_seconds=30,
        )

    class SuccessResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"embedding": vector}]}

    class FailureResponse:
        status_code = 500
        text = "boom"

        def json(self):
            return {}

    class Client:
        response = SuccessResponse()

        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return self.response

    monkeypatch.setattr(embeddings_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(embeddings_module.httpx, "AsyncClient", Client)

    assert await embeddings_module.generate_embedding("hello") == vector
    Client.response = FailureResponse()
    assert await embeddings_module.generate_embedding("hello") is None


@pytest.mark.asyncio
async def test_background_memory_uses_configured_background_model() -> None:
    captured: list[str | None] = []

    class BackgroundProvider:
        async def complete(self, _messages, **kwargs):
            captured.append(kwargs.get("model"))
            return LlmResult(text='{"memories":[]}')

    created = await alfred_memory_module.AlfredMemoryService().remember_from_turn(
        BackgroundProvider(),
        user_message="open the gate",
        assistant_text="Please confirm before I open the gate.",
        tool_results=[{"name": "open_device"}],
        actor_context={"user": {"id": str(uuid.uuid4()), "role": "admin"}},
        session_id=str(uuid.uuid4()),
        model_name="gpt-5.4-nano",
    )

    assert created == 0
    assert captured == ["gpt-5.4-nano"]


@pytest.mark.asyncio
async def test_reflection_lessons_follow_learning_mode(monkeypatch) -> None:
    captured: list[dict[str, object]] = []
    captured_models: list[str | None] = []

    class ReflectionProvider:
        async def complete(self, *_args, **kwargs):
            captured_models.append(kwargs.get("model"))
            return LlmResult(
                text=(
                    '{"reflection":{"went_well":"Used source tools.",'
                    '"could_improve":"Be more direct.",'
                    '"key_lesson":"For routine gate status checks, answer directly from the device-state tool.",'
                    '"title":"Gate status reflection",'
                    '"tags":["device-status"],'
                    '"confidence":0.77}}'
                )
            )

    async def fake_create_lesson(self, **kwargs):
        captured.append(kwargs)
        return {
            "status": "active" if kwargs["learning_mode"] == "auto_learn" else "pending",
            "lesson": kwargs["analysis"]["lesson"]["lesson"],
        }

    monkeypatch.setattr(AlfredFeedbackService, "_create_lesson_from_analysis", fake_create_lesson)
    service = AlfredFeedbackService()

    for mode in ("review_then_learn", "auto_learn"):
        async def fake_runtime_config(mode=mode):
            return SimpleNamespace(alfred_reflection_enabled=True, alfred_learning_mode=mode)

        monkeypatch.setattr(feedback_module, "get_runtime_config", fake_runtime_config)
        lesson = await service.reflect_on_turn(
            ReflectionProvider(),
            user_message="Is the gate open?",
            assistant_text="The gate is closed.",
            tool_results=[{"name": "query_device_states", "output": {"devices": [{"state": "closed"}]}}],
            actor_context={"user": {"id": str(uuid.uuid4()), "role": "admin"}},
            session_id=str(uuid.uuid4()),
            provider_name="openai",
            model_name="gpt-test",
        )
        expected_status = "active" if mode == "auto_learn" else "pending"
        assert lesson and lesson["status"] == expected_status

    assert [item["learning_mode"] for item in captured] == ["review_then_learn", "auto_learn"]
    assert captured_models == ["gpt-test", "gpt-test"]


def test_reflection_learning_has_timezone_training_instruction_without_regex_sanitizer() -> None:
    assert "Never create lessons that tell Alfred to mention time zones" in feedback_module.TURN_REFLECTION_PROMPT
    assert not hasattr(feedback_module, "TIMEZONE_DIRECTIVE_FRAGMENT_RE")


def test_superpower_tools_are_registered_with_confirmation_metadata() -> None:
    tools = ai_tools.build_agent_tools()

    assert tools["open_gate"].requires_confirmation is True
    assert tools["command_device"].requires_confirmation is True
    assert tools["toggle_maintenance_mode"].requires_confirmation is True
    assert tools["override_schedule"].requires_confirmation is True
    assert "Schedules" in tools["override_schedule"].categories


def test_resolve_human_entity_schema_includes_visitor_passes() -> None:
    tools = ai_tools.build_agent_tools()
    schema = tools["resolve_human_entity"].parameters

    enum = schema["properties"]["entity_types"]["items"]["enum"]

    assert "visitor_pass" in enum


def test_alfred_tool_registry_preserves_public_tool_surface() -> None:
    tools = ai_tools.build_agent_tools()

    expected_tool_names = {
        "analyze_alert_snapshot",
        "analyze_camera_snapshot",
        "analyze_dependency_update",
        "apply_dependency_update",
        "assign_schedule_to_entity",
        "backfill_access_event_from_protect",
        "calculate_absence_duration",
        "calculate_visit_duration",
        "cancel_visitor_pass",
        "check_dependency_updates",
        "command_device",
        "configure_dependency_backup_storage",
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
        "query_alert_activity",
        "query_anomalies",
        "query_alfred_runtime_events",
        "query_automation_catalog",
        "query_automations",
        "query_auth_secret_status",
        "query_dependency_backups",
        "query_dependency_update_job",
        "query_dependency_updates",
        "query_device_states",
        "query_integration_health",
        "query_leaderboard",
        "query_lpr_timing",
        "query_notification_catalog",
        "query_notification_workflows",
        "query_presence",
        "query_schedule_targets",
        "query_schedules",
        "query_system_settings",
        "query_unifi_protect_events",
        "query_vehicle_detection_history",
        "query_visitor_passes",
        "read_chat_attachment",
        "resolve_human_entity",
        "restore_dependency_backup",
        "rotate_auth_secret",
        "summarize_access_rhythm",
        "test_integration_connection",
        "test_notification_workflow",
        "test_unifi_alarm_webhook",
        "toggle_maintenance_mode",
        "trigger_anomaly_alert",
        "trigger_icloud_sync",
        "trigger_manual_malfunction_override",
        "update_system_settings",
        "update_notification_workflow",
        "update_schedule",
        "update_visitor_pass",
        "validate_dependency_backup_storage",
        "verify_schedule_access",
    }
    state_changing_tools = {
        "analyze_dependency_update",
        "apply_dependency_update",
        "assign_schedule_to_entity",
        "backfill_access_event_from_protect",
        "cancel_visitor_pass",
        "check_dependency_updates",
        "command_device",
        "configure_dependency_backup_storage",
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
        "restore_dependency_backup",
        "rotate_auth_secret",
        "test_integration_connection",
        "test_notification_workflow",
        "test_unifi_alarm_webhook",
        "toggle_maintenance_mode",
        "trigger_anomaly_alert",
        "trigger_icloud_sync",
        "trigger_manual_malfunction_override",
        "update_system_settings",
        "update_notification_workflow",
        "update_schedule",
        "update_visitor_pass",
        "validate_dependency_backup_storage",
    }

    assert set(tools) == expected_tool_names
    assert {name for name, tool in tools.items() if tool.requires_confirmation} == state_changing_tools
    assert all(tool.categories for tool in tools.values())
    assert all(tool.safety_level for tool in tools.values())
    assert all(isinstance(tool.required_permissions, tuple) for tool in tools.values())
    assert {
        name for name, tool in tools.items() if tool.safety_level == ai_tools.SAFETY_CONFIRMATION_REQUIRED
    } == state_changing_tools
    assert tools["update_schedule"].parameters["required"] == ["confirm"]
    assert tools["assign_schedule_to_entity"].parameters["required"] == ["entity_type", "confirm"]
    assert "confirm" in tools["trigger_anomaly_alert"].parameters["required"]


def test_alfred_registry_metadata_drives_permissions_and_planner_cards() -> None:
    tools = ai_tools.build_agent_tools()

    standard_visible = filter_tools_for_actor(
        tools.values(),
        {"user": {"id": "user-standard", "role": "standard"}},
    )
    assert standard_visible
    assert all(tool.read_only for tool in standard_visible)
    assert all(not tool.requires_confirmation for tool in standard_visible)
    assert all("admin" not in tool.required_permissions for tool in standard_visible)
    assert "get_system_users" not in {tool.name for tool in standard_visible}

    cards = {card["domain"]: card for card in domain_cards(tools.values())}
    schedule_tools = {tool["name"]: tool for tool in cards["Schedules"]["tools"]}
    system_tools = {tool["name"]: tool for tool in cards["Users_Settings"]["tools"]}

    assert schedule_tools["update_schedule"]["safety"] == ai_tools.SAFETY_CONFIRMATION_REQUIRED
    assert schedule_tools["query_schedule_targets"]["limit"] == 25
    assert system_tools["get_system_users"]["safety"] == ai_tools.SAFETY_ADMIN_ONLY
    assert system_tools["get_system_users"]["permissions"] == ["admin"]
    access_tools = {tool["name"]: tool for tool in cards["Access_Logs"]["tools"]}
    assert any(field.startswith("mode:") for field in access_tools["calculate_absence_duration"]["args"]["fields"])
    assert access_tools["query_access_events"]["examples"][0]["direction"] == "exit"
    assert access_tools["calculate_absence_duration"]["returns"]["answer_types"] == ["absence_duration"]
    assert "absence_duration" in access_tools["query_access_events"]["returns"]["not_sufficient_for"]
    assert access_tools["query_anomalies"]["returns"]["records"] == "alerts"
    diagnostic_tools = {tool["name"]: tool for tool in cards["Access_Diagnostics"]["tools"]}
    assert "missing_event" in diagnostic_tools["investigate_access_incident"]["returns"]["handles"]
    visitor_tools = {tool["name"]: tool for tool in cards["Visitor_Passes"]["tools"]}
    assert "visitor_departure" in visitor_tools["query_visitor_passes"]["returns"]["answer_types"]
    gate_tools = {tool["name"]: tool for tool in cards["Gate_Hardware"]["tools"]}
    assert gate_tools["query_device_states"]["returns"]["answer_types"] == ["device_state"]
    assert gate_tools["open_device"]["examples"][0]["confirm"] is False


@pytest.mark.asyncio
async def test_query_integration_health_includes_access_event_worker_status(monkeypatch) -> None:
    async def fake_runtime_config():
        return SimpleNamespace(
            dvla_api_key="",
            dvla_vehicle_enquiry_url="https://dvla.example.test",
            llm_provider="local",
            openai_api_key="",
            gemini_api_key="",
            anthropic_api_key="",
            ollama_base_url="",
        )

    class AsyncStatusService:
        def __init__(self, payload):
            self.payload = payload

        async def status(self, **_kwargs):
            return self.payload

    class FakeDependencyUpdateService:
        async def storage_status(self):
            return {"status": "ok"}

    monkeypatch.setattr(ai_tools, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(
        ai_tools,
        "get_access_event_service",
        lambda: SimpleNamespace(
            status=lambda: {
                "status": "degraded",
                "worker_running": True,
                "queue_depth": 2,
                "pending_windows": 1,
                "last_error": "RuntimeError: database connection reset",
            }
        ),
    )
    monkeypatch.setattr(ai_tools, "get_home_assistant_service", lambda: AsyncStatusService({"configured": False}))
    monkeypatch.setattr(ai_tools, "get_unifi_protect_service", lambda: AsyncStatusService({"configured": False}))
    monkeypatch.setattr(ai_tools, "get_discord_messaging_service", lambda: AsyncStatusService({"configured": False}))
    monkeypatch.setattr(ai_tools, "get_whatsapp_messaging_service", lambda: AsyncStatusService({"enabled": False}))
    monkeypatch.setattr(ai_tools, "get_dependency_update_service", lambda: FakeDependencyUpdateService())

    result = await ai_tools.query_integration_health({"integration": "access_events"})

    assert result["integration"] == "access_events"
    assert result["health"]["status"] == "degraded"
    assert result["health"]["queue_depth"] == 2
    assert "database connection reset" in result["health"]["last_error"]


def test_planner_selection_parses_direct_read_tool_calls() -> None:
    tools = ai_tools.build_agent_tools()
    payload = {
        "selected_domains": ["Access_Logs"],
        "selected_tool_names": ["calculate_absence_duration"],
        "requested_answer_type": "absence_duration",
        "planned_tool_calls": [
            {
                "name": "calculate_absence_duration",
                "arguments_json": '{"person":"Ash","day":"today","mode":"latest"}',
            }
        ],
        "needs_clarification": False,
        "clarification_question": "",
        "safety_posture": "read_only",
        "confidence": 0.94,
        "reason": "direct source-of-truth duration check",
    }

    selection = parse_planner_selection(payload, tools.values())

    assert selection.selected_tool_names == ("calculate_absence_duration",)
    assert selection.requested_answer_type == "absence_duration"
    assert selection.planned_tool_calls == (
        ToolCallPlan("calculate_absence_duration", {"person": "Ash", "day": "today", "mode": "latest"}),
    )


def test_tools_for_selection_uses_planned_calls_when_selected_names_are_missing() -> None:
    tools = ai_tools.build_agent_tools()
    selection = parse_planner_selection(
        {
            "selected_domains": ["Access_Logs"],
            "selected_tool_names": [],
            "requested_answer_type": "absence_duration",
            "planned_tool_calls": [
                {
                    "name": "calculate_absence_duration",
                    "arguments_json": '{"person":"Ash","day":"today","mode":"latest"}',
                }
            ],
            "needs_clarification": False,
            "clarification_question": "",
            "safety_posture": "read_only",
            "confidence": 0.9,
            "reason": "planner supplied executable call but omitted the redundant name list",
        },
        tools.values(),
    )

    selected = tools_for_selection(selection, list(tools.values()))

    assert [tool.name for tool in selected] == ["calculate_absence_duration"]


def test_tools_for_selection_falls_back_to_non_general_domains_only() -> None:
    tools = ai_tools.build_agent_tools()
    access_selection = parse_planner_selection(
        {
            "selected_domains": ["Access_Logs"],
            "selected_tool_names": [],
            "requested_answer_type": "general",
            "planned_tool_calls": [],
            "needs_clarification": False,
            "clarification_question": "",
            "safety_posture": "read_only",
            "confidence": 0.72,
            "reason": "partial planner output still identified the operational domain",
        },
        tools.values(),
    )
    general_selection = parse_planner_selection(
        {
            "selected_domains": ["General"],
            "selected_tool_names": [],
            "requested_answer_type": "general",
            "planned_tool_calls": [],
            "needs_clarification": False,
            "clarification_question": "",
            "safety_posture": "read_only",
            "confidence": 0.72,
            "reason": "chit-chat does not need tools",
        },
        tools.values(),
    )

    assert "query_access_events" in {tool.name for tool in tools_for_selection(access_selection, list(tools.values()))}
    assert tools_for_selection(general_selection, list(tools.values())) == []


def test_planned_read_tool_calls_are_safe_and_sanitized() -> None:
    service = ChatService()
    selection = parse_planner_selection(
        {
            "selected_domains": ["Access_Logs"],
            "selected_tool_names": ["calculate_absence_duration"],
            "requested_answer_type": "absence_duration",
            "planned_tool_calls": [
                {
                    "name": "calculate_absence_duration",
                    "arguments_json": '{"person":"Ash","day":"today","mode":"latest","unexpected":"drop"}',
                }
            ],
            "needs_clarification": False,
            "clarification_question": "",
            "safety_posture": "read_only",
            "confidence": 0.9,
            "reason": "direct duration check",
        },
        service._tools.values(),
    )

    calls = service._planned_read_tool_calls(
        selection,
        [service._tools["calculate_absence_duration"]],
        actor_context={"user": {"id": "admin-1", "role": "admin"}},
    )

    assert [(call.name, call.arguments) for call in calls] == [
        ("calculate_absence_duration", {"person": "Ash", "day": "today", "mode": "latest"})
    ]


@pytest.mark.asyncio
@pytest.mark.alfred_critical
async def test_planned_duration_answer_repairs_timestamp_only_tool_selection(monkeypatch) -> None:
    service = ChatService()
    selection = parse_planner_selection(
        {
            "selected_domains": ["Access_Logs"],
            "selected_tool_names": ["query_access_events"],
            "requested_answer_type": "absence_duration",
            "planned_tool_calls": [
                {
                    "name": "query_access_events",
                    "arguments_json": '{"person":"Ash","day":"today","direction":"exit"}',
                }
            ],
            "needs_clarification": False,
            "clarification_question": "",
            "safety_posture": "read_only",
            "confidence": 0.9,
            "reason": "mistaken timestamp-only plan",
        },
        service._tools.values(),
    )
    tool_results = [
        {
            "name": "query_access_events",
            "arguments": {"person": "Ash", "day": "today", "direction": "exit"},
            "output": {
                "answer_artifacts": [
                    {
                        "domain": "access_logs",
                        "answer_type": "latest_departure",
                        "subject_label": "Ash",
                        "primary_fact": {
                            "id": "access.latest_exit",
                            "label": "Latest departure",
                            "value": "2026-05-09T19:21:00+01:00",
                            "display_value": "19:21",
                            "kind": "datetime",
                            "source": "access_events",
                            "must_appear": True,
                        },
                    }
                ]
            },
        }
    ]
    captured: list[ToolCall] = []

    async def fake_execute_tool_batch(_session_id, calls, _selected_tools, **_kwargs):
        captured.extend(calls)
        return [
            {
                "name": "calculate_absence_duration",
                "arguments": calls[0].arguments,
                "output": {
                    "answer_artifacts": [
                        {
                            "domain": "access_logs",
                            "answer_type": "absence_duration",
                            "subject_label": "Ash",
                            "primary_fact": {
                                "id": "absence.duration.latest",
                                "label": "Latest absence duration",
                                "value": 300,
                                "display_value": "5 mins",
                                "kind": "duration",
                                "source": "access_events",
                                "must_appear": True,
                            },
                        }
                    ]
                },
            }
        ]

    monkeypatch.setattr(service, "_execute_tool_batch", fake_execute_tool_batch)

    repaired = await service._repair_missing_planned_answer_type(
        selection,
        tool_results,
        uuid.uuid4(),
        status_callback=None,
        actor_context={"user": {"id": "admin-1", "role": "admin"}},
    )

    assert [(call.name, call.arguments) for call in captured] == [
        ("calculate_absence_duration", {"person": "Ash", "day": "today", "mode": "latest"})
    ]
    assert repaired[0]["name"] == "calculate_absence_duration"


def test_general_tool_handlers_are_extracted_behind_stable_facade() -> None:
    tools = ai_tools.build_agent_tools()

    assert tools["resolve_human_entity"].handler.__module__ == "app.ai.tool_groups.general_handlers"
    assert tools["query_presence"].handler.__module__ == "app.ai.tool_groups.general_handlers"
    assert tools["get_system_users"].handler.__module__ == "app.ai.tool_groups.general_handlers"
    assert callable(ai_tools.resolve_human_entity)
    assert callable(ai_tools.query_presence)
    assert callable(ai_tools.get_system_users)


def test_tool_group_catalogs_use_domain_handler_modules() -> None:
    tools = ai_tools.build_agent_tools()

    expected_modules = {
        "query_device_states": "app.ai.tool_groups.gate_maintenance_handlers",
        "create_visitor_pass": "app.ai.tool_groups.visitor_passes_handlers",
        "calculate_absence_duration": "app.ai.tool_groups.access_diagnostics_handlers",
        "query_access_events": "app.ai.tool_groups.access_diagnostics_handlers",
        "query_alert_activity": "app.ai.tool_groups.access_diagnostics_handlers",
        "lookup_dvla_vehicle": "app.ai.tool_groups.compliance_cameras_files_handlers",
        "create_notification_workflow": "app.ai.tool_groups.notifications_handlers",
        "create_automation": "app.ai.tool_groups.automations_handlers",
        "update_schedule": "app.ai.tool_groups.schedules_handlers",
        "query_system_settings": "app.ai.tool_groups.system_operations_handlers",
    }

    for tool_name, module_name in expected_modules.items():
        assert tools[tool_name].handler.__module__ == module_name


def test_alfred_registry_rejects_confirmation_tools_without_confirmation_field() -> None:
    async def noop_handler(_arguments):
        return {}

    unsafe_tool = ai_tools.AgentTool(
        name="unsafe_mutation",
        description="Unsafe mutation missing confirm.",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        handler=noop_handler,
        categories=("Test",),
        safety_level=ai_tools.SAFETY_CONFIRMATION_REQUIRED,
    )

    with pytest.raises(ToolRegistryError, match="confirmation field"):
        _validate_tool(unsafe_tool)


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
    started_batch = next(
        status
        for status in statuses
        if status.get("event") == "chat.tool_batch" and status.get("status") == "started"
    )
    completed_batch = next(
        status
        for status in statuses
        if status.get("event") == "chat.tool_batch" and status.get("status") == "completed"
    )
    assert started_batch["phase"] == "using_tools"
    assert started_batch["agents_running"] == 1
    assert started_batch["active_tool_calls"] == 2
    assert completed_batch["active_tool_calls"] == 0
    assert completed_batch["completed_tool_steps"] == 2


@pytest.mark.asyncio
async def test_react_loop_executes_unconfirmed_action_previews_in_parallel(monkeypatch) -> None:
    service = ChatService()
    started: list[float] = []
    statuses: list[dict] = []
    memory: dict[str, object] = {}

    async def fake_execute_tool_call(session_id, call, *, status_callback=None, batch_id=None):
        started.append(time.perf_counter())
        await asyncio.sleep(0.05)
        return {
            "call_id": call.id,
            "name": call.name,
            "arguments": call.arguments,
            "output": {
                "requires_confirmation": True,
                "confirmation_field": "confirm",
                "target": call.name,
                "detail": f"Confirm {call.name}?",
            },
        }

    async def fake_load_memory(session_id):
        return dict(memory)

    async def fake_save_memory(session_id, next_memory):
        memory.clear()
        memory.update(next_memory)

    async def no_schedule_conflict(session_id, memory, tool_results):
        return None

    async def status_callback(status):
        statuses.append(status)

    monkeypatch.setattr(service, "_execute_tool_call", fake_execute_tool_call)
    monkeypatch.setattr(service, "_load_memory", fake_load_memory)
    monkeypatch.setattr(service, "_save_memory", fake_save_memory)
    monkeypatch.setattr(service, "_schedule_conflict_response", no_schedule_conflict)

    before = time.perf_counter()
    result = await service._run_provider_agent_loop(
        DualActionPreviewProvider(),
        uuid.uuid4(),
        [ChatMessageInput("system", "test")],
        [],
        [service._tools["create_schedule"], service._tools["create_visitor_pass"]],
        {},
        route=IntentRoute(("Schedules", "Visitor_Passes"), 0.8, False, "test"),
        user_message="prepare a schedule and pass",
        actor_context={"user": {"id": "user-1", "role": "admin"}},
        status_callback=status_callback,
    )
    elapsed = time.perf_counter() - before

    assert len(started) == 2
    assert abs(started[0] - started[1]) < 0.03
    assert elapsed < 0.09
    assert memory["pending_agent_action"]["tool_name"] in {"create_schedule", "create_visitor_pass"}
    assert "Confirm" in result.text
    assert any(status.get("event") == "chat.tool_batch" and status.get("parallel") for status in statuses)
    assert any(
        status.get("event") == "chat.confirmation_required"
        and status.get("phase") == "awaiting_confirmation"
        and status.get("agents_running") == 0
        for status in statuses
    )


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
        actor_context={"user": {"id": "user-1", "role": "admin"}},
        status_callback=None,
    )

    pending = memory["pending_agent_action"]
    assert executed[0].arguments["confirm"] is False
    assert pending["tool_name"] == "open_gate"
    assert pending["arguments"]["confirm"] is False
    assert "confirm before I open Top Gate" in result.text


@pytest.mark.asyncio
@pytest.mark.alfred_critical
async def test_confirmed_open_gate_finishes_without_reprompt(monkeypatch) -> None:
    service = ChatService()
    session_id = uuid.uuid4()
    pending = {
        "id": "confirm-open-gate",
        "tool_name": "open_gate",
        "arguments": {"target": "Top Gate", "confirm": False},
        "preview_output": {
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Top Gate",
            "detail": "Open Top Gate?",
        },
        "tool_results": [
            {
                "call_id": "preview-open-gate",
                "name": "open_gate",
                "arguments": {"target": "Top Gate", "confirm": False},
                "output": {"requires_confirmation": True, "target": "Top Gate"},
            }
        ],
        "selected_tools": ["open_gate"],
        "provider": "local",
        "route": {"intents": ["Gate_Hardware"], "confidence": 0.9, "requires_entity_resolution": False},
        "actor_context": {"user": {"id": "admin-1", "role": "admin"}},
    }
    executed: list[ToolCall] = []
    cleared = False

    async def fake_runtime_config():
        return SimpleNamespace(llm_provider="local", llm_timeout_seconds=30)

    async def fake_load_pending_agent_action(_session_id, *, confirmation_id, user_id):
        assert confirmation_id == "confirm-open-gate"
        return pending

    async def fake_clear_pending_agent_action(_session_id):
        nonlocal cleared
        cleared = True

    async def fake_execute_tool_call(_session_id, call, *, status_callback=None, batch_id=None):
        executed.append(call)
        return {
            "call_id": call.id,
            "name": call.name,
            "arguments": call.arguments,
            "output": {
                "opened": True,
                "accepted": True,
                "action": "open",
                "target": "Top Gate",
                "device": {"name": "Top Gate", "kind": "gate"},
            },
        }

    async def fail_resume(*_args, **_kwargs):
        raise AssertionError("confirmed gate commands must not resume the ReAct loop")

    async def fake_append_message(*_args, **_kwargs):
        return uuid.uuid4()

    async def noop_async(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.services.chat.get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(service, "_load_pending_agent_action", fake_load_pending_agent_action)
    monkeypatch.setattr(service, "_clear_pending_agent_action", fake_clear_pending_agent_action)
    monkeypatch.setattr(service, "_execute_tool_call", fake_execute_tool_call)
    monkeypatch.setattr(service, "_run_provider_agent_loop", fail_resume)
    monkeypatch.setattr(service, "_append_message", fake_append_message)
    monkeypatch.setattr(service, "_update_memory", noop_async)
    monkeypatch.setattr("app.services.chat.event_bus.publish", noop_async)

    result = await service._handle_pending_action_decision(
        session_id,
        confirmation_id="confirm-open-gate",
        decision="confirm",
        user_id="admin-1",
        user_role="admin",
        client_context={},
        status_callback=None,
    )

    assert cleared is True
    assert [(call.name, call.arguments) for call in executed] == [
        ("open_gate", {"target": "Top Gate", "confirm": True})
    ]
    assert result.text == "Opened Top Gate. Logged, tidy, and pleasingly uneventful."
    assert [item["name"] for item in result.tool_results] == ["open_gate"]
    assert result.tool_results[0]["output"].get("requires_confirmation") is not True


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


def test_ash_style_access_incident_routes_to_suppressed_read_investigation() -> None:
    service = ChatService()
    message = "Ash came back at 18:18 but he wasnt let in and no notification fired"

    route = service._deterministic_intent_route(message, {}, [])
    planned = service._plan_tool_calls(message, {}, [])

    assert "Access_Diagnostics" in route.intents
    assert planned[0].name == "investigate_access_incident"
    assert planned[0].arguments["person"] == "ash"
    assert planned[0].arguments["direction"] == "entry"
    assert planned[0].arguments["expected_time"] == "18:18"
    assert planned[0].arguments["incident_type"] == "notification_failure"


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


def test_suppressed_read_extraction_and_root_cause_chain() -> None:
    person_id = uuid.uuid4()
    vehicle_id = uuid.uuid4()
    source_event_id = uuid.uuid4()
    source_event = SimpleNamespace(
        id=source_event_id,
        person_id=person_id,
        vehicle_id=vehicle_id,
        registration_number="AGS7X",
        direction=ai_tools.AccessDirection.EXIT,
        decision=ai_tools.AccessDecision.GRANTED,
        occurred_at=datetime(2026, 5, 8, 18, 5, tzinfo=UTC),
        vehicle=SimpleNamespace(
            registration_number="AGS7X",
            owner=SimpleNamespace(display_name="Ash"),
        ),
        raw_payload={
            "vehicle_session": {
                "suppressed_reads": [
                    {
                        "registration_number": "AGS7X",
                        "detected_registration_number": "AGS7X",
                        "captured_at": "2026-05-08T18:18:00+00:00",
                        "confidence": 0.99,
                        "source": "ubiquiti_lpr",
                        "gate_state": "closed",
                        "reason": "vehicle_session_already_active",
                        "matched_by": "registration_number",
                    }
                ]
            }
        },
    )

    suppressed = ai_tools._incident_suppressed_read_payloads_from_event(
        source_event,
        subject_summary={"person_id": str(person_id), "vehicle_id": str(vehicle_id), "plates": ["AGS7X"], "person": "Ash"},
        plates=["AGS7X"],
        start=datetime(2026, 5, 8, 18, 0, tzinfo=UTC),
        end=datetime(2026, 5, 8, 18, 30, tzinfo=UTC),
        direction="entry",
        timezone_name="Europe/London",
    )
    root = ai_tools._incident_root_cause(
        found_iacs=False,
        protect={"available": True, "events": []},
        traces=[],
        suppressed_reads=suppressed,
        incident_type="notification_failure",
    )

    assert len(suppressed) == 1
    assert suppressed[0]["source_access_event_id"] == str(source_event_id)
    assert suppressed[0]["reason"] == "vehicle_session_already_active"
    assert suppressed[0]["inferred_direction"] == "entry"
    assert root["root_cause"] == "iacs_read_suppressed_as_active_vehicle_session"


def test_suppressed_read_incident_builds_backfill_candidate_args() -> None:
    person_id = str(uuid.uuid4())
    vehicle_id = str(uuid.uuid4())
    source_event_id = str(uuid.uuid4())

    args = ai_tools._backfill_args_from_incident(
        subject={"summary": {"person_id": person_id, "vehicle_id": vehicle_id, "plates": ["AGS7X"]}},
        protect={},
        suppressed_reads=[
            {
                "source_access_event_id": source_event_id,
                "registration_number": "AGS7X",
                "captured_at": "2026-05-08T18:18:00+01:00",
                "inferred_direction": "entry",
                "reason": "vehicle_session_already_active",
                "confidence": 0.99,
                "backfill_repairable": True,
            }
        ],
        arguments={},
        root_cause="iacs_read_suppressed_as_active_vehicle_session",
    )

    assert args is not None
    assert args["evidence_kind"] == "suppressed_read"
    assert args["source_access_event_id"] == source_event_id
    assert args["suppression_reason"] == "vehicle_session_already_active"
    assert args["decision"] == "granted"


def test_access_incident_direct_text_reports_suppressed_read_chain() -> None:
    service = ChatService()

    text = service._access_incident_direct_text(
        {
            "found_iacs_event": False,
            "found_iacs_suppressed_read": True,
            "root_cause": "iacs_read_suppressed_as_active_vehicle_session",
            "diagnostic_chain": [
                {"stage": "camera_webhook", "detail": "IACS suppressed-read history contains the matching LPR read."},
                {"stage": "access_event", "detail": "IACS received the read at 18:18 but suppressed it as vehicle_session_already_active."},
                {"stage": "gate_command", "detail": "No gate command ran because no access event was finalized."},
                {"stage": "notification", "detail": "Notifications never ran because notification workflows are evaluated after finalized access events."},
                {"stage": "root_cause", "detail": "iacs_read_suppressed_as_active_vehicle_session"},
            ],
            "iacs": {
                "suppressed_reads": [
                    {
                        "reason": "vehicle_session_already_active",
                        "source_access_event_id": "event-1",
                    }
                ]
            },
            "detail": "Confirm to backfill the access event and update presence only.",
        }
    )

    assert "Camera/webhook" in text
    assert "IACS received the plate read, but suppressed it as `vehicle_session_already_active`" in text
    assert "no access event was finalized and notifications never ran" in text
    assert "Repair: Confirm to backfill" in text


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
async def test_query_anomalies_can_search_resolved_delivery_alert_notes_and_visual_evidence(
    monkeypatch,
    tmp_path,
) -> None:
    alert_id = uuid.uuid4()
    event_id = uuid.uuid4()
    observed_at = datetime.now(tz=UTC)
    snapshot_path = tmp_path / "alert-snapshots" / f"{alert_id}.jpg"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_bytes(b"jpeg")
    event = SimpleNamespace(
        id=event_id,
        registration_number="DOVE123",
        direction=SimpleNamespace(value="entry"),
        decision=SimpleNamespace(value="denied"),
        source="uiprotect",
        occurred_at=observed_at,
        raw_payload={
            "vehicle_visual_detection": {
                "observed_vehicle_type": "Truck",
                "observed_vehicle_color": "White",
                "vehicle_type_confidence": 88,
            },
            "best": {"supplier": "Dove Fuels"},
        },
    )
    alert = SimpleNamespace(
        id=alert_id,
        event_id=event_id,
        event=event,
        anomaly_type=SimpleNamespace(value="unauthorized_plate"),
        severity=SimpleNamespace(value="warning"),
        message="Unauthorised Plate, Access Denied",
        context={
            "registration_number": "DOVE123",
            "snapshot": {
                "url": f"/api/v1/alerts/{alert_id}/snapshot",
                "captured_at": observed_at.isoformat(),
                "content_type": "image/jpeg",
                "bytes": 4,
            },
        },
        created_at=observed_at,
        resolved_at=observed_at,
        resolution_note="Resolved as oil delivery from Dove Fuels.",
        resolved_by=None,
    )

    class ScalarResult:
        def all(self):
            return [alert]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def scalars(self, _query):
            return ScalarResult()

    async def fake_runtime_config():
        return SimpleNamespace(site_timezone="Europe/London")

    monkeypatch.setattr("app.services.alert_snapshots.settings.data_dir", tmp_path)
    monkeypatch.setattr(ai_tools, "AsyncSessionLocal", lambda: Session())
    monkeypatch.setattr(ai_tools, "get_runtime_config", fake_runtime_config)

    result = await ai_tools.query_anomalies(
        {
            "status": "all",
            "search": "oil delivery",
            "suspected_delivery": True,
            "limit": 10,
        }
    )

    assert result["count"] == 1
    record = result["alerts"][0]
    assert record["status"] == "resolved"
    assert record["resolution_note"] == "Resolved as oil delivery from Dove Fuels."
    assert record["snapshot"]["url"] == f"/api/v1/alerts/{alert_id}/snapshot"
    assert record["event"]["vehicle_visual_detection"]["observed_vehicle_type"] == "Truck"
    assert record["possible_delivery"] is True
    assert "Text evidence mentions Dove Fuels." in record["delivery_indicators"]
    assert "Stored visual evidence reports vehicle type: Truck." in record["delivery_indicators"]


@pytest.mark.asyncio
async def test_query_anomalies_matches_compacted_hello_fresh_supplier_search(monkeypatch) -> None:
    observed_at = datetime.now(tz=UTC)
    alert = SimpleNamespace(
        id=uuid.uuid4(),
        event_id=None,
        event=None,
        anomaly_type=SimpleNamespace(value="unauthorized_plate"),
        severity=SimpleNamespace(value="warning"),
        message="Unauthorised Plate, Access Denied",
        context={"registration_number": "HFRESH1"},
        created_at=observed_at,
        resolved_at=observed_at,
        resolution_note="Resolved as Hello Fresh delivery.",
        resolved_by=None,
    )

    class ScalarResult:
        def all(self):
            return [alert]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def scalars(self, _query):
            return ScalarResult()

    async def fake_runtime_config():
        return SimpleNamespace(site_timezone="Europe/London")

    monkeypatch.setattr(ai_tools, "AsyncSessionLocal", lambda: Session())
    monkeypatch.setattr(ai_tools, "get_runtime_config", fake_runtime_config)

    result = await ai_tools.query_anomalies(
        {
            "status": "all",
            "search": "hellofresh",
            "suspected_delivery": True,
            "limit": 10,
        }
    )

    assert result["count"] == 1
    assert result["alerts"][0]["resolution_note"] == "Resolved as Hello Fresh delivery."
    assert "Text evidence mentions HelloFresh." in result["alerts"][0]["delivery_indicators"]
    assert "Text evidence mentions a delivery." in result["alerts"][0]["delivery_indicators"]


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
async def test_resolve_human_entity_resolves_visitor_pass(monkeypatch) -> None:
    visitor_pass = SimpleNamespace(
        id=uuid.uuid4(),
        visitor_name="Stu",
        number_plate="STU123",
        vehicle_make="Ford",
        vehicle_colour="Blue",
        status=SimpleNamespace(value="active"),
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    class VisitorPassService:
        async def refresh_statuses(self, *, session, publish):
            return False

        async def list_passes(self, session, *, statuses=None, search=None, limit=10):
            assert search == "Stu"
            return [visitor_pass]

    async def fake_runtime_config():
        return SimpleNamespace(site_timezone="Europe/London")

    monkeypatch.setattr(ai_tools, "AsyncSessionLocal", lambda: Session())
    monkeypatch.setattr(ai_tools, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(ai_tools, "get_visitor_pass_service", lambda: VisitorPassService())
    monkeypatch.setattr(
        ai_tools,
        "_visitor_pass_agent_payload",
        lambda pass_, timezone_name: {
            "id": str(pass_.id),
            "visitor_name": pass_.visitor_name,
            "arrival_time": "2026-05-04T10:12:45+01:00",
        },
    )

    result = await ai_tools.resolve_human_entity({"query": "Stu", "entity_types": ["visitor_pass"]})

    assert result["status"] == "unique"
    assert result["match"]["type"] == "visitor_pass"
    assert result["match"]["visitor_name"] == "Stu"
    assert result["match"]["arrival_time"] == "2026-05-04T10:12:45+01:00"


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
