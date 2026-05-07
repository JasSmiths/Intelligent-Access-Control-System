"""LLM-owned domain planner for Alfred v3."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.ai.providers import ChatMessageInput
from app.ai.tools import AgentTool
from app.services.chat_contracts import SUPPORTED_INTENTS


PLANNER_PROMPT = """You are Alfred's v3 planning brain for IACS.

You receive exact actor context, compact memory, and compact domain cards.
Select only the explicit tool names needed for the next agent loop.
Do not answer the user here. Do not execute tools. Do not use keyword rules, regex routing, or hardcoded intent blocks.
The acting ReAct model will only see the tools you select, so include every tool it may need.
Prefer enough tools to answer correctly; for complex questions, select independent read tools together so the acting model can call them in parallel.
Interpret casual wording semantically. Departure-like meaning ("left this morning", "headed out", "gone", "bolted", "outta here") maps to LPR/access events with direction exit; arrival-like meaning maps to direction entry; current-here questions map to presence.
Arrival/departure questions about a fuzzy person or relationship name require resolve_human_entity plus query_access_events. They may refer to a directory Person or a Visitor Pass visitor; include Visitor Pass lookup tools when the person may be a visitor or entity resolution is uncertain.
Delivery or supplier-arrival questions without a known person/vehicle, such as "When did the oil delivery arrive?", should inspect active/open and resolved Alerts with query_anomalies; include analyze_alert_snapshot when snapshot evidence may be needed to identify a truck, lorry, tanker, or supplier branding.
Active Alfred training lessons in actor context are approved behavioral guidance. Use them to inform tool choice and answer strategy by analogy, never as keyword rules or canned text.
For simple open/closed device-state questions, select query_device_states only. Do not select malfunction tools unless the user asks about faults, failures, stuck devices, missed opens, or diagnostics.
If the user asks for a real-world action or mutation, include the relevant mutation tool but set safety_posture to confirmation_required.
If the request is ambiguous, ask one concise clarification question.

Return only compact JSON:
{"selected_domains":["Access_Diagnostics"],"selected_tool_names":["query_access_events"],"needs_clarification":false,"clarification_question":"","safety_posture":"read_only","confidence":0.0,"reason":"short"}"""


@dataclass(frozen=True)
class PlannerSelection:
    selected_domains: tuple[str, ...]
    selected_tool_names: tuple[str, ...]
    needs_clarification: bool = False
    clarification_question: str = ""
    safety_posture: str = "read_only"
    confidence: float = 0.5
    reason: str = "LLM v3 planner"
    raw: dict[str, Any] = field(default_factory=dict)


def domain_cards(tools: Iterable[AgentTool]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for tool in tools:
        for category in tool.categories or ("General",):
            card = grouped.setdefault(
                category,
                {
                    "domain": category,
                    "tools": [],
                    "read_only_tools": 0,
                    "mutation_tools": 0,
                    "summary": _domain_summary(category),
                },
            )
            card["tools"].append(
                {
                    "name": tool.name,
                    "read_only": tool.read_only,
                    "requires_confirmation": tool.requires_confirmation,
                    "description": tool.description[:360],
                }
            )
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
) -> PlannerSelection:
    result = await provider.complete(
        [
            ChatMessageInput("system", PLANNER_PROMPT),
            ChatMessageInput(
                "user",
                json.dumps(
                    {
                        "message": message,
                        "actor_context": actor_context,
                        "memory": memories,
                        "session_memory": session_memory,
                        "has_attachments": bool(attachments),
                        "domains": domain_cards(tools),
                    },
                    separators=(",", ":"),
                    default=str,
                ),
            ),
        ]
    )
    payload = _first_json_object(result.text)
    if not isinstance(payload, dict):
        raise ValueError("Alfred v3 planner returned invalid JSON.")
    return parse_planner_selection(payload, tools)


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
    try:
        confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    return PlannerSelection(
        selected_domains=domains or ("General",),
        selected_tool_names=names,
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


def _domain_summary(domain: str) -> str:
    summaries = {
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
    return summaries.get(domain, "General IACS context and entity resolution.")
