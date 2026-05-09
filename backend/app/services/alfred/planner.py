"""LLM-owned domain planner for Alfred v3."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.ai.providers import ChatMessageInput
from app.ai.tools import AgentTool
from app.ai.tool_groups.metadata import domain_summary
from app.services.chat_contracts import SUPPORTED_INTENTS


PLANNED_PREVIEW_TOOL_NAMES = {
    "calculate_absence_duration",
    "calculate_visit_duration",
    "get_visitor_pass",
    "open_device",
    "open_gate",
    "query_access_events",
    "query_alert_activity",
    "query_anomalies",
    "query_device_states",
    "query_visitor_passes",
    "resolve_human_entity",
    "verify_schedule_access",
}


PLANNER_PROMPT = """You are Alfred's v3 planning brain for IACS. Select tools only; do not answer.
Domain cards are compact: tool_names lists every tool in that domain, while detailed tool cards appear once under their primary domain.
Use the smallest safe tool set. planned_tool_calls may include immediate read-only calls and confirmation-required action previews; the backend forces confirmation fields false before execution.
Do not use keyword rules, regex routing, or hardcoded intent blocks. Reason privately over 2-3 plausible tool-selection candidates, then output only JSON.
Never include private reasoning, candidate lists, chain-of-thought, or evaluation notes.
The acting ReAct model only sees selected tools, so include every tool it may need. For complex questions, select independent read tools together.
Set requested_answer_type to the requested fact shape. Duration answer types must use duration tools; a timestamp alone is not a valid answer to an elapsed-duration request.
Interpret casual wording semantically: departures/exits map to access exit evidence, arrivals/returns map to entry evidence, current-here questions map to presence.
For low-risk access questions, tools that accept a person argument may receive the user's name directly; avoid resolve_human_entity unless exact IDs or ambiguity matter.
Off-site elapsed time uses requested_answer_type=absence_duration and calculate_absence_duration. On-site/stay elapsed time uses requested_answer_type=visit_duration and calculate_visit_duration.
Delivery or supplier arrival intent, including oil delivery, should inspect active/open and resolved alerts with query_anomalies; add analyze_alert_snapshot when visual evidence may identify a truck, tanker, supplier, or Dove Fuels.
Apply active lessons by semantic analogy, not as scripts, keyword triggers, or canned text.
Simple open/closed device-state questions use query_device_states only; malfunction tools are for faults, failures, stuck devices, missed opens, or diagnostics.
Real-world actions/mutations require safety_posture=confirmation_required and the relevant mutation tool. For gate or garage actions, natural target text is enough for an open_device preview with kind=gate or kind=garage_door and action=open/close.
Ask one concise clarification only when ambiguity affects safety, identity, permissions, or the real-world thing being touched.
Never select mutation tools for read-only questions. Never downgrade confirmation-required actions.

Return compact JSON:
{"selected_domains":["Access_Diagnostics"],"selected_tool_names":["query_access_events"],"requested_answer_type":"event_time","planned_tool_calls":[{"name":"query_access_events","arguments_json":"{\"person\":\"Steph\",\"day\":\"today\",\"direction\":\"exit\"}"}],"needs_clarification":false,"clarification_question":"","safety_posture":"read_only","confidence":0.0,"reason":"short"}"""


PLANNER_RESPONSE_SCHEMA = {
    "name": "alfred_planner_selection",
    "schema": {
        "type": "object",
        "properties": {
            "selected_domains": {"type": "array", "items": {"type": "string"}},
            "selected_tool_names": {"type": "array", "items": {"type": "string"}},
            "requested_answer_type": {
                "type": "string",
                "enum": [
                    "general",
                    "event_time",
                    "presence_state",
                    "absence_duration",
                    "visit_duration",
                    "alert_activity",
                    "visitor_pass",
                    "schedule_access",
                    "diagnostic",
                    "action",
                ],
            },
            "planned_tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "arguments_json": {"type": "string"},
                    },
                    "required": ["name", "arguments_json"],
                    "additionalProperties": False,
                },
            },
            "needs_clarification": {"type": "boolean"},
            "clarification_question": {"type": "string"},
            "safety_posture": {"type": "string"},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": [
            "selected_domains",
            "selected_tool_names",
            "requested_answer_type",
            "planned_tool_calls",
            "needs_clarification",
            "clarification_question",
            "safety_posture",
            "confidence",
            "reason",
        ],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class PlannerSelection:
    selected_domains: tuple[str, ...]
    selected_tool_names: tuple[str, ...]
    requested_answer_type: str = "general"
    planned_tool_calls: tuple[ToolCallPlan, ...] = ()
    needs_clarification: bool = False
    clarification_question: str = ""
    safety_posture: str = "read_only"
    confidence: float = 0.5
    reason: str = "LLM v3 planner"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCallPlan:
    name: str
    arguments: dict[str, Any]


def domain_cards(tools: Iterable[AgentTool]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    primary_category_by_tool: dict[str, str] = {}
    for tool in tools:
        categories = tuple(tool.categories or ("General",))
        primary_category = categories[0] if categories else "General"
        for category in categories:
            card = grouped.setdefault(
                category,
                {
                    "domain": category,
                    "tools": [],
                    "tool_names": [],
                    "read_only_tools": 0,
                    "mutation_tools": 0,
                    "summary": domain_summary(category),
                },
            )
            card["tool_names"].append(tool.name)
            if tool.name not in primary_category_by_tool:
                primary_category_by_tool[tool.name] = primary_category
                tool_card = {
                    "name": tool.name,
                    "safety": tool.safety_level,
                    "desc": _compact_description(tool.description),
                }
                if tool.requires_confirmation:
                    tool_card["confirm"] = True
                if tool.name in PLANNED_PREVIEW_TOOL_NAMES or tool.return_schema:
                    tool_card["args"] = _compact_parameter_card(tool.parameters)
                else:
                    required_args = _compact_required_parameter_card(tool.parameters)
                    if required_args:
                        tool_card["args"] = required_args
                if tool.return_schema:
                    tool_card["returns"] = _compact_return_schema(tool.return_schema)
                if tool.required_permissions:
                    tool_card["permissions"] = list(tool.required_permissions)
                if tool.default_limit is not None:
                    tool_card["limit"] = tool.default_limit
                card["tools"].append(tool_card)
            if tool.read_only:
                card["read_only_tools"] += 1
            else:
                card["mutation_tools"] += 1
    return [grouped[key] for key in sorted(grouped)]


async def plan_with_llm(
    provider: Any,
    *,
    message: str,
    actor_context: dict[str, Any],
    memories: list[dict[str, Any]],
    session_memory: dict[str, Any],
    tools: list[AgentTool],
    attachments: list[dict[str, Any]],
    relevant_past_lessons: list[dict[str, Any]] | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> PlannerSelection:
    messages = [
        ChatMessageInput("system", PLANNER_PROMPT),
        ChatMessageInput(
            "user",
            json.dumps(
                {
                    "message": message,
                    "actor_context": actor_context,
                    "memory": memories,
                    "relevant_past_lessons": relevant_past_lessons or [],
                    "session_memory": session_memory,
                    "has_attachments": bool(attachments),
                    "domains": domain_cards(tools),
                },
                separators=(",", ":"),
                default=str,
            ),
        ),
    ]
    result = await _provider_complete(
        provider,
        messages,
        response_schema=PLANNER_RESPONSE_SCHEMA,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    payload = _first_json_object(result.text)
    if not isinstance(payload, dict):
        raise ValueError("Alfred v3 planner returned invalid JSON.")
    return parse_planner_selection(payload, tools)


async def _provider_complete(
    provider: Any,
    messages: list[ChatMessageInput],
    **options: Any,
):
    clean_options = {key: value for key, value in options.items() if value is not None and value != ""}
    try:
        return await provider.complete(messages, **clean_options)
    except TypeError as exc:
        if "unexpected keyword" not in str(exc):
            raise
        return await provider.complete(messages)


def parse_planner_selection(payload: dict[str, Any], tools: Iterable[AgentTool]) -> PlannerSelection:
    available_names = {tool.name for tool in tools}
    available_domains = {category for tool in tools for category in tool.categories}
    raw_domains = payload.get("selected_domains") if isinstance(payload.get("selected_domains"), list) else []
    domains = tuple(
        domain
        for domain in (str(item).strip() for item in raw_domains)
        if domain in available_domains or domain in SUPPORTED_INTENTS
    )
    raw_names = payload.get("selected_tool_names") if isinstance(payload.get("selected_tool_names"), list) else []
    names = tuple(name for name in (str(item).strip() for item in raw_names) if name in available_names)
    raw_calls = payload.get("planned_tool_calls") if isinstance(payload.get("planned_tool_calls"), list) else []
    planned_tool_calls = tuple(
        call
        for call in (_parse_tool_call_plan(item, available_names) for item in raw_calls)
        if call is not None
    )
    requested_answer_type = str(payload.get("requested_answer_type") or "general").strip()
    allowed_answer_types = set(PLANNER_RESPONSE_SCHEMA["schema"]["properties"]["requested_answer_type"]["enum"])
    if requested_answer_type not in allowed_answer_types:
        requested_answer_type = "general"
    try:
        confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    return PlannerSelection(
        selected_domains=domains or ("General",),
        selected_tool_names=names,
        requested_answer_type=requested_answer_type,
        planned_tool_calls=planned_tool_calls,
        needs_clarification=bool(payload.get("needs_clarification")),
        clarification_question=str(payload.get("clarification_question") or "").strip()[:500],
        safety_posture=str(payload.get("safety_posture") or "read_only").strip()[:80] or "read_only",
        confidence=confidence,
        reason=str(payload.get("reason") or "LLM v3 planner").strip()[:240],
        raw=payload,
    )


def tools_for_selection(selection: PlannerSelection, tools: list[AgentTool]) -> list[AgentTool]:
    if selection.selected_tool_names:
        selected = [tool for tool in tools if tool.name in set(selection.selected_tool_names)]
    else:
        selected = []
    deduped: list[AgentTool] = []
    seen: set[str] = set()
    for tool in selected:
        if tool.name in seen:
            continue
        seen.add(tool.name)
        deduped.append(tool)
    return deduped[:32]


def _parse_tool_call_plan(value: Any, available_names: set[str]) -> ToolCallPlan | None:
    if not isinstance(value, dict):
        return None
    name = str(value.get("name") or "").strip()
    if name not in available_names:
        return None
    arguments = value.get("arguments")
    if not isinstance(arguments, dict):
        raw_arguments = value.get("arguments_json")
        if isinstance(raw_arguments, str) and raw_arguments.strip():
            try:
                decoded = json.loads(raw_arguments)
            except json.JSONDecodeError:
                decoded = {}
            arguments = decoded if isinstance(decoded, dict) else {}
        else:
            arguments = {}
    return ToolCallPlan(name=name, arguments=dict(arguments))


def _compact_parameter_card(parameters: dict[str, Any]) -> dict[str, Any]:
    properties = parameters.get("properties") if isinstance(parameters.get("properties"), dict) else {}
    required = parameters.get("required") if isinstance(parameters.get("required"), list) else []
    compact: dict[str, Any] = {"fields": [], "required": [str(item) for item in required[:8]]}
    for name, spec in list(properties.items())[:16]:
        field = str(name)
        if not isinstance(spec, dict):
            compact["fields"].append(field)
            continue
        field_type = str(spec.get("type") or "any")
        if isinstance(spec.get("enum"), list):
            field_type = "|".join(str(item) for item in spec["enum"][:12])
        else:
            items = spec.get("items")
            if isinstance(items, dict) and isinstance(items.get("enum"), list):
                field_type = f"{field_type}<{'|'.join(str(item) for item in items['enum'][:12])}>"
        compact["fields"].append(f"{field}:{field_type}")
    return compact


def _compact_required_parameter_card(parameters: dict[str, Any]) -> dict[str, Any]:
    required = parameters.get("required") if isinstance(parameters.get("required"), list) else []
    if not required:
        return {}
    return {"required": [str(item) for item in required[:8]]}


def _compact_description(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:96]


def _compact_return_schema(schema: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("answer_types", "not_sufficient_for", "fact_kind", "handles"):
        value = schema.get(key)
        if isinstance(value, list):
            compact[key] = [str(item) for item in value[:8]]
        elif value not in (None, "", [], {}):
            compact[key] = value
    return compact


def _first_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    candidates = [match.group(1).strip() for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.I | re.S)]
    candidates.append(text.strip())
    for candidate in candidates:
        start = candidate.find("{")
        if start < 0:
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(candidate)):
            char = candidate[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(candidate[start : index + 1])
                    except json.JSONDecodeError:
                        break
    return None
