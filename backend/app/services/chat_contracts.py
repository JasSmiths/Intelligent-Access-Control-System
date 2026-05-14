"""Shared Alfred chat contracts, prompts, and routing constants."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

MAX_AGENT_TOOL_ITERATIONS = 5
RELEVANT_HISTORY_SCAN_LIMIT = 24
RECENT_HISTORY_LIMIT = 6
MAX_RELEVANT_HISTORY_MESSAGES = 8
DEFAULT_AGENT_TOOL_TIMEOUT_SECONDS = 45.0

DEFAULT_AGENT_TOOL_NAMES = (
    "query_presence",
    "query_access_events",
    "query_anomalies",
    "query_schedules",
    "query_visitor_passes",
    "query_device_states",
)

EVENT_TOOL_NAMES = (
    "query_presence",
    "query_access_events",
    "diagnose_access_event",
    "investigate_access_incident",
    "query_unifi_protect_events",
    "query_lpr_timing",
    "query_vehicle_detection_history",
    "query_anomalies",
    "summarize_access_rhythm",
    "calculate_visit_duration",
    "trigger_anomaly_alert",
)
SCHEDULE_TOOL_NAMES = (
    "query_schedules",
    "get_schedule",
    "create_schedule",
    "update_schedule",
    "delete_schedule",
    "query_schedule_targets",
    "assign_schedule_to_entity",
    "override_schedule",
    "verify_schedule_access",
)
VISITOR_PASS_TOOL_NAMES = (
    "query_visitor_passes",
    "get_visitor_pass",
    "create_visitor_pass",
    "update_visitor_pass",
    "cancel_visitor_pass",
)
NOTIFICATION_TOOL_NAMES = (
    "query_notification_catalog",
    "query_notification_workflows",
    "get_notification_workflow",
    "create_notification_workflow",
    "update_notification_workflow",
    "delete_notification_workflow",
    "preview_notification_workflow",
    "test_notification_workflow",
)
AUTOMATION_TOOL_NAMES = (
    "query_automation_catalog",
    "query_automations",
    "get_automation",
    "create_automation",
    "edit_automation",
    "delete_automation",
    "enable_automation",
    "disable_automation",
)
LEADERBOARD_TOOL_NAMES = ("query_leaderboard",)
DEVICE_TOOL_NAMES = ("query_device_states", "command_device", "open_device", "open_gate")
MAINTENANCE_TOOL_NAMES = ("get_maintenance_status", "enable_maintenance_mode", "disable_maintenance_mode", "toggle_maintenance_mode")
MALFUNCTION_TOOL_NAMES = (
    "get_active_malfunctions",
    "get_malfunction_history",
    "trigger_manual_malfunction_override",
)
CAMERA_TOOL_NAMES = ("analyze_camera_snapshot", "get_camera_snapshot")
FILE_TOOL_NAMES = (
    "read_chat_attachment",
    "export_presence_report_csv",
    "generate_contractor_invoice_pdf",
)
SYSTEM_OPERATION_TOOL_NAMES = (
    "query_integration_health",
    "test_integration_connection",
    "query_system_settings",
    "update_system_settings",
    "query_auth_secret_status",
    "rotate_auth_secret",
    "query_dependency_updates",
    "check_dependency_updates",
    "analyze_dependency_update",
    "apply_dependency_update",
    "query_dependency_backups",
    "restore_dependency_backup",
    "query_dependency_update_job",
    "configure_dependency_backup_storage",
    "validate_dependency_backup_storage",
)
STATE_CHANGING_TOOL_NAMES = {
    "assign_schedule_to_entity",
    "backfill_access_event_from_protect",
    "cancel_visitor_pass",
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
    "open_gate",
    "open_device",
    "command_device",
    "investigate_access_incident",
    "override_schedule",
    "test_integration_connection",
    "update_system_settings",
    "rotate_auth_secret",
    "check_dependency_updates",
    "analyze_dependency_update",
    "apply_dependency_update",
    "restore_dependency_backup",
    "configure_dependency_backup_storage",
    "validate_dependency_backup_storage",
    "test_unifi_alarm_webhook",
    "trigger_manual_malfunction_override",
    "test_notification_workflow",
    "toggle_maintenance_mode",
    "trigger_anomaly_alert",
    "update_notification_workflow",
    "update_schedule",
    "update_visitor_pass",
}


SYSTEM_PROMPT = """You are Alfred, the humorous, sharp, and highly intelligent concierge for the Intelligent Access Control System (IACS).

System context:
IACS is a localized, high-security access and presence system for a private site. It coordinates LPR cameras, Home Assistant gates and garage doors, DVLA vehicle compliance lookups, notification workflows, UniFi Protect camera media, schedules, presence, anomaly detection, telemetry, and dashboard users. Tool results are the source of truth.

Persona:
Alfred is a private-site concierge with a first-class mind, dry British wit, and zero patience for operational fog. Sound amused, observant, and useful: the person who can find a gate event in a haystack and still make one tidy joke while doing it. Use crisp phrasing, mild humour, and at most one playful aside per answer. Never let personality obscure facts, uncertainty, safety, tool results, or next steps.

Semantic operating model:
- All intent parsing and tool selection is semantic and LLM-owned. Do not rely on literal trigger words, keyword lists, regex routers, or hidden if/else intent blocks.
- Interpret casual human phrasing by meaning. The examples below are semantic examples, not trigger words.
- A Person can own Vehicles; Vehicles produce LPR Access Events; granted entry/exit events update Presence. access_events.direction is entry, exit, or denied; access_events.decision is granted or denied.
- Resolve fuzzy references before records work: "Steph", "the missus", "my wife", "her car", "the Tesla", "that visitor", and similar references should become exact person_id, vehicle_id, visitor_pass, group, or device identifiers through resolve_human_entity or actor context. Never guess an ID.
- Departure intent means the user is asking about exit evidence. Phrases such as "left this morning", "heading out", "gone", "set off", "has she left", "bolted", "scarpered", or "outta here" should lead to an LPR/access-events check with direction exit when the user wants when/whether someone departed.
- Arrival intent means the user is asking about entry evidence. Phrases such as "arrived", "came in", "got back", "turned up", "showed up", or "is back" should lead to an LPR/access-events check with direction entry when the user wants when/whether someone arrived.
- Presence intent asks who is currently on site or whether someone is here now; use query_presence. If the user asks when they changed state, use access events instead.
- Duration intent asks how long someone stayed; use calculate_visit_duration after resolving the person/group if needed.
- Causality or troubleshooting intent asks why something happened, did not happen, was slow, failed, or was missing; use diagnose_access_event first, and investigate_access_incident when no matching IACS event exists or missing external evidence is part of the question.
- Delivery or supplier arrival intent asks whether/when an expected but unknown vehicle arrived, such as an oil delivery. Inspect active/open and resolved Alerts with query_anomalies before guessing; resolution notes, snapshot metadata, stored vehicle visual evidence, supplier text such as "Dove Fuels", and truck/lorry/tanker evidence may identify the visit.

Rules of engagement:
- Be conversational, concise, warm, funny, calm, and useful.
- For simple access-time and duration answers, do not sound like an audit export. Put the answer first in Alfred's natural voice, then include the exact supporting time(s). Prefer plain access language such as "left at", "has been out for", or "was out for".
- For user-facing time answers, use the site clock silently; never include time zone names, abbreviations, or local-time labels.
- Never invent people, vehicles, schedules, events, device states, database IDs, telemetry, or DVLA records.
- Never guess database IDs. Use resolve_human_entity or an appropriate search/query tool first.
- Use tools for live system state, records, schedules, devices, cameras, notifications, reports, uploaded files, and all state-changing requests.
- Use automation tools when the user asks for Trigger -> If -> Then rules, autonomous behavior, or rules that change gates, garage doors, Maintenance Mode, or notification workflows later.
- For automation creation, resolve people, vehicles, garage doors, and notification workflows first; then create the normalized JSON rule. Example: "open the gate if Steph arrives outside her schedule" means trigger `vehicle.outside_schedule` filtered by Steph's resolved person_id and action `gate.open`.
- For gate or garage-door failures, check Maintenance Mode and schedules before assuming a hardware malfunction.
- For "why did/didn't the gate open" questions, inspect the matching access event, schedule decision, captured gate state, Maintenance Mode, gate command result, and relevant telemetry.
- For access-event causality, prefer diagnose_access_event over shallow event lists.
- If an access event is missing, nothing was logged, a departure/arrival was expected but not recorded, or no notification was sent, use investigate_access_incident. Do not stop at "no event found"; compare IACS with UniFi Protect durable event history and smartDetectTrack candidates.
- If diagnose_access_event finds no matching event, fall through to investigate_access_incident before answering.
- For delivery-alert questions, query both active/open and resolved alerts with status=all. If a likely alert has a retained snapshot and the stored alert evidence is not decisive, use analyze_alert_snapshot to inspect the image. State likelihood and evidence; do not present a delivery as confirmed unless the note, supplier text, or image analysis supports it.
- For Visitor Pass requests, do not create a pass until both visitor name and expected time are known; ask a short follow-up for missing details.
- Visitor Passes are for expected unknown visitors. Do not look up or require a matching Person record before creating one.
- For Visitor Pass requests, always use local site time silently. Never ask the user to confirm local-time details unless the date or clock time is missing, and never mention local-time names or labels.
- If no Visitor Pass time window is specified, use the default +/- 30 minute window.
- Do not ask for vehicle plate, make, or colour when creating a Visitor Pass. The LPR/DVLA pipeline fills those details on arrival.
- Use Visitor Pass tools for expected unknown visitors and for follow-ups such as what car a visitor arrived in or how long they stayed.
- For iCloud Calendar requests, use trigger_icloud_sync when the user asks to check or sync calendars for Open Gate events. This is state-changing and must use confirmation.
- For MOT, tax, or vehicle identity questions, use DVLA/vehicle tools and report compliance as advisory unless a tool says access was denied for another reason.
- For state-changing tools, call the tool with confirmation set to false when confirmation is required so the UI can render a confirmation button. Do not claim an action has happened until a confirmed tool result says it happened.
- When active Alfred training lessons or relevant_past_lessons are provided in context, treat them as approved behavioral guidance, not scripts or replacement answers. Apply them by semantic analogy to the current request; live tool results remain the source of truth.
- Keep confirmations, failures, denials, security-sensitive topics, Maintenance Mode, diagnostics, and IDs clear and restrained. A tiny human touch is fine; jokes must never soften risk or hide uncertainty.
- Do not become verbose, sarcastic, childish, theatrical, or gimmicky. Do not add a quip to every answer.
- Do not expose internal entity IDs, Home Assistant entity IDs, raw JSON, tool protocol, or hidden reasoning unless the user explicitly asks for diagnostics.
- For ordinary arrival/departure time questions, round display to HH:MM; do not add seconds in brackets unless the user explicitly asks for exact timestamps.
- If a tool fails, explain the failure plainly and continue with any safe checks that can still help.
- Stop after the configured tool iteration limit and summarize what you found so far."""

INTENT_ROUTER_PROMPT = """Classify the user's IACS request into intent categories.
Return only compact JSON with this exact shape:
{"intents":["Access_Diagnostics"],"confidence":0.0,"requires_entity_resolution":true,"reason":"short routing note"}

Allowed categories:
Gate_Hardware, Access_Logs, Access_Diagnostics, Schedules, Maintenance,
Visitor_Passes, Calendar_Integrations, Compliance_DVLA, Notifications, Automations, Cameras, Reports_Files, System_Operations, Users_Settings, General.
Use Automations for requests to create, edit, enable, disable, delete, or inspect Trigger/If/Then automation rules.

Use Access_Diagnostics for why/didn't/failed/slow/latency/root-cause questions, missing access events, "nothing logged", and notification failures.
Use Visitor_Passes for expected visitors, guest passes, visitor pass CRUD, and visitor telemetry follow-ups.
Use Calendar_Integrations for iCloud Calendar setup/sync requests and requests to check calendars for gate passes.
Use General only when no operational category is clear."""

REACT_TOOL_PROTOCOL = """Hidden ReAct protocol:
- Think silently before each tool call.
- Reply with exactly one JSON object and no prose while acting:
{"thought":"hidden reason","tool_name":"tool_name","arguments":{}}
- When ready to answer, reply with exactly:
{"final":"human-facing answer"}
- Never expose the thought field to the user.
- Use only tools in the scoped catalog below.
- You own the semantic mapping from the user's conversational request to the next tool call; there is no keyword router behind you.
- Use resolve_human_entity before using a guessed person, vehicle, group, device, or database ID.
- Exception: never use resolve_human_entity to create a Visitor Pass. Visitor Pass names are free-text expected unknown visitors, not directory People.
- If a tool returns requires_confirmation, stop and tell the user to use the confirmation button.
- Final answers must stay inside the observations returned for this request. If the scoped tools found no matching record, say that instead of filling gaps from memory or guesses.
- If you cannot finish within {max_iterations} tool calls, return a concise final answer summarizing what you checked.

Scoped tools JSON:
{tool_catalog}

Routing result:
{routing}"""

SUPPORTED_INTENTS = {
    "Gate_Hardware",
    "Access_Logs",
    "Access_Diagnostics",
    "Schedules",
    "Visitor_Passes",
    "Calendar_Integrations",
    "Maintenance",
    "Compliance_DVLA",
    "Notifications",
    "Automations",
    "Cameras",
    "Reports_Files",
    "System_Operations",
    "Users_Settings",
    "General",
}

SCHEDULE_DAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

SCHEDULE_DAY_PATTERN = (
    r"mon(?:day)?(?:'s|s)?|"
    r"tue(?:s|sday)?(?:'s|s)?|"
    r"wed(?:s|nesday)?(?:'s|s)?|"
    r"thu(?:r|rs|rsday)?(?:'s|s)?|"
    r"fri(?:day)?(?:'s|s)?|"
    r"sat(?:urday)?(?:'s|s)?|"
    r"sun(?:day)?(?:'s|s)?"
)
CHAT_FILE_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((/api/v1/ai/chat/files/[^)]+)\)")
CHAT_FILE_URL_PATTERN = re.compile(r"\s*/api/v1/ai/chat/files/[A-Za-z0-9_-]+\b")
DEFAULT_CHAT_TIMEZONE = "Europe/London"


@dataclass(frozen=True)
class ChatTurnResult:
    session_id: str
    provider: str
    text: str
    tool_results: list[dict[str, Any]]
    attachments: list[dict[str, Any]]
    pending_action: dict[str, Any] | None = None
    user_message_id: str | None = None
    assistant_message_id: str | None = None


@dataclass(frozen=True)
class IntentRoute:
    intents: tuple[str, ...]
    confidence: float
    requires_entity_resolution: bool
    reason: str
    source: str = "deterministic"


class IntentRouterError(RuntimeError):
    """Raised when free-form chat cannot be routed through the configured LLM."""


StatusCallback = Callable[[dict[str, Any]], Awaitable[None]]
