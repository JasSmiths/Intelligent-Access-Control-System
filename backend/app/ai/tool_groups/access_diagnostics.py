"""Alfred tool catalog entries for this domain."""

from __future__ import annotations

from typing import Any

from app.ai.tool_groups import access_diagnostics_handlers, access_incident_handlers
from app.ai.tool_groups.metadata import apply_group_metadata
from app.ai.tools import AgentTool

TOOL_CATEGORIES = {
    "query_access_events": ("Access_Logs", "Access_Diagnostics", "General"),
    "diagnose_access_event": ("Access_Diagnostics",),
    "investigate_access_incident": ("Access_Diagnostics", "Access_Logs", "Gate_Hardware", "Notifications"),
    "query_unifi_protect_events": ("Access_Diagnostics", "Cameras"),
    "backfill_access_event_from_protect": ("Access_Diagnostics",),
    "test_unifi_alarm_webhook": ("Access_Diagnostics", "Cameras"),
    "query_lpr_timing": ("Access_Diagnostics", "Access_Logs"),
    "query_vehicle_detection_history": ("Access_Logs", "Access_Diagnostics", "Compliance_DVLA"),
    "get_telemetry_trace": ("Access_Diagnostics", "Users_Settings"),
    "query_leaderboard": ("Access_Logs", "Compliance_DVLA"),
    "query_anomalies": ("Access_Logs", "Access_Diagnostics", "General"),
    "query_alert_activity": ("Access_Logs", "Access_Diagnostics", "General"),
    "analyze_alert_snapshot": ("Access_Diagnostics", "Cameras"),
    "summarize_access_rhythm": ("Access_Logs", "General"),
    "calculate_visit_duration": ("Access_Logs",),
    "calculate_absence_duration": ("Access_Logs",),
    "trigger_anomaly_alert": ("Access_Logs", "Notifications"),
}

CONFIRMATION_REQUIRED_TOOLS = {
    "backfill_access_event_from_protect",
    "investigate_access_incident",
    "test_unifi_alarm_webhook",
    "trigger_anomaly_alert",
}

DEFAULT_LIMITS = {
    "query_access_events": 10,
    "query_anomalies": 10,
    "query_alert_activity": 25,
    "query_leaderboard": 10,
    "query_lpr_timing": 25,
    "query_unifi_protect_events": 25,
    "get_telemetry_trace": 20,
}


def build_tools() -> list[AgentTool]:
    return apply_group_metadata(
        [
        AgentTool(
                    name="query_access_events",
                    description=(
                        "LPR/access log lookup for arrivals, exits, denials, and who came or went. "
                        "Use this when the user semantically asks when or whether someone arrived, left, headed out, bolted, "
                        "went, came in, got back, or changed site presence. This returns event timestamps and event lists, "
                        "not elapsed durations. Resolve fuzzy people/vehicles first when needed."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "person": {"type": "string", "description": "Person name only when an exact person_id is not available."},
                            "person_id": {"type": "string", "description": "Exact Person UUID from resolve_human_entity or actor context."},
                            "vehicle_id": {"type": "string", "description": "Exact Vehicle UUID from resolve_human_entity or actor context."},
                            "group": {"type": "string"},
                            "registration_number": {"type": "string"},
                            "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                            "direction": {
                                "type": "string",
                                "enum": ["entry", "exit", "denied"],
                                "description": (
                                    "Use exit for departure meaning such as left, gone, headed out, bolted, or outta here; "
                                    "entry for arrival meaning such as arrived, came in, returned, or got back; denied for rejected access."
                                ),
                            },
                            "decision": {"type": "string", "enum": ["granted", "denied"]},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                            "summarize_payload": {"type": "boolean", "description": "Default true. Return compact schedule/gate payload summaries instead of raw payloads."},
                        },
                        "additionalProperties": False,
                    },
                    handler=access_diagnostics_handlers.query_access_events,
                    example_inputs=(
                        {"person": "Steph", "day": "today", "direction": "exit", "limit": 1},
                        {"registration_number": "PE70DHX", "day": "recent", "decision": "granted"},
                    ),
                    return_schema={
                        "answer_types": ["event_time", "access_event_list"],
                        "not_sufficient_for": ["absence_duration", "visit_duration"],
                        "records": "events",
                    },
                ),
        AgentTool(
                    name="diagnose_access_event",
                    description=(
                        "Explain a specific or latest gate/LPR access event by joining the access event, "
                        "telemetry trace spans, gate action outcome, notification workflow diagnostics, "
                        "nearby LPR timing observations, and same-plate history."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "access_event_id": {"type": "string", "description": "Access event UUID when already known."},
                            "person": {"type": "string", "description": "Person name, for example Steph."},
                            "person_id": {"type": "string", "description": "Exact person UUID when already known."},
                            "vehicle_id": {"type": "string", "description": "Exact vehicle UUID when already known."},
                            "group": {"type": "string", "description": "Group/category name."},
                            "registration_number": {"type": "string", "description": "Plate/VRN to inspect."},
                            "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                            "unknown_only": {
                                "type": "boolean",
                                "description": "When true, resolve the latest event for an unknown/unmatched plate.",
                            },
                            "decision": {"type": "string", "enum": ["granted", "denied"]},
                            "direction": {"type": "string", "enum": ["entry", "exit", "denied"]},
                            "span_limit": {"type": "integer", "minimum": 1, "maximum": 50},
                            "include_trace_payloads": {"type": "boolean"},
                            "summarize_payload": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                    handler=access_diagnostics_handlers.diagnose_access_event,
                    example_inputs=(
                        {"person": "Steph", "day": "today", "direction": "entry"},
                        {"access_event_id": "access-event-uuid", "span_limit": 20},
                    ),
                    return_schema={
                        "answer_types": ["diagnostic"],
                        "handles": ["gate_open_reason", "schedule_denial", "notification_failure"],
                        "result_keys": ["found", "answer_hints", "gate", "schedule", "notifications"],
                    },
                ),
        AgentTool(
                    name="investigate_access_incident",
                    description=(
                        "Run a full Alfred access incident investigation. Use this for missing access events, "
                        "nothing logged, no notification, gate/garage failures, schedule denials, and any case "
                        "where diagnose_access_event finds no matching IACS event. It compares IACS records, "
                        "IACS suppressed vehicle-session reads, telemetry, Home Assistant gate observations, "
                        "maintenance mode, schedules, notifications, and durable UniFi Protect event/track "
                        "history before recommending a fix."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "person": {"type": "string", "description": "Person name, for example Steph."},
                            "person_id": {"type": "string"},
                            "vehicle_id": {"type": "string"},
                            "registration_number": {"type": "string"},
                            "expected_time": {"type": "string", "description": "Expected local or ISO time, for example 07:38 or 2026-04-29T07:38:00."},
                            "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                            "window_minutes": {"type": "integer", "minimum": 1, "maximum": 720},
                            "incident_type": {
                                "type": "string",
                                "enum": ["missing_event", "gate_failure", "garage_failure", "notification_failure", "schedule_denial", "auto"],
                            },
                            "direction": {"type": "string", "enum": ["entry", "exit", "denied"]},
                            "confirm": {"type": "boolean", "description": "Confirm the recommended internal remediation when one is available."},
                        },
                        "additionalProperties": False,
                    },
                    handler=access_incident_handlers.investigate_access_incident,
                    example_inputs=(
                        {"person": "Steph", "day": "today", "direction": "exit", "incident_type": "missing_event"},
                        {"registration_number": "PE70DHX", "expected_time": "07:38", "window_minutes": 20},
                    ),
                    return_schema={
                        "answer_types": ["diagnostic", "repair_preview"],
                        "handles": ["missing_event", "gate_failure", "garage_failure", "notification_failure"],
                        "result_keys": ["root_cause", "confidence", "recommended_action", "requires_confirmation"],
                    },
                ),
        AgentTool(
                    name="query_unifi_protect_events",
                    description=(
                        "Query durable UniFi Protect event history over a specific window and optionally parse "
                        "smartDetectTrack LPR candidates for each event."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "camera_id": {"type": "string"},
                            "camera_name": {"type": "string"},
                            "smart_detect_type": {"type": "string", "description": "Optional smart detect type such as licensePlate or vehicle."},
                            "registration_number": {"type": "string", "description": "Optional plate/VRN candidate to match against track data."},
                            "start": {"type": "string", "description": "ISO datetime window start. Interpreted in site timezone if no timezone is supplied."},
                            "end": {"type": "string", "description": "ISO datetime window end. Interpreted in site timezone if no timezone is supplied."},
                            "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                            "include_tracks": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                    handler=access_incident_handlers.query_unifi_protect_events,
                ),
        AgentTool(
                    name="backfill_access_event_from_protect",
                    description=(
                        "Create a missing IACS access event from durable UniFi Protect LPR evidence, durable "
                        "IACS suppressed-read evidence, or explicit admin-provided details. Requires Admin and "
                        "confirm=true. Never opens gates, garage doors, runs automations, or sends normal "
                        "arrival/departure notifications."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "protect_event_id": {"type": "string"},
                            "evidence_kind": {"type": "string", "enum": ["protect", "suppressed_read"]},
                            "source_access_event_id": {"type": "string", "description": "Access event UUID containing raw_payload.vehicle_session.suppressed_reads evidence."},
                            "suppressed_read_captured_at": {"type": "string", "description": "Captured time of the suppressed read to repair."},
                            "suppression_reason": {"type": "string", "description": "Suppression reason, for example vehicle_session_already_active."},
                            "person": {"type": "string"},
                            "person_id": {"type": "string"},
                            "vehicle_id": {"type": "string"},
                            "registration_number": {"type": "string"},
                            "captured_at": {"type": "string", "description": "Captured local or ISO time when explicit admin details are used."},
                            "expected_time": {"type": "string"},
                            "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                            "direction": {"type": "string", "enum": ["entry", "exit", "denied"]},
                            "decision": {"type": "string", "enum": ["granted", "denied"]},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 100},
                            "reason": {"type": "string"},
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["confirm"],
                        "additionalProperties": False,
                    },
                    handler=access_incident_handlers.backfill_access_event_from_protect,
                ),
        AgentTool(
                    name="test_unifi_alarm_webhook",
                    description=(
                        "Ask UniFi Protect to fire a safe Alarm Manager webhook test when the installed uiprotect "
                        "package supports it. Requires confirm=true because it calls an external system."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "trigger_id": {"type": "string", "description": "UniFi Protect Alarm Manager trigger ID."},
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["trigger_id", "confirm"],
                        "additionalProperties": False,
                    },
                    handler=access_incident_handlers.test_unifi_alarm_webhook,
                ),
        AgentTool(
                    name="query_lpr_timing",
                    description=(
                        "Return recent raw LPR timing observations from webhooks and UniFi Protect, "
                        "including captured-to-received delay where available."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "registration_number": {"type": "string", "description": "Optional plate/VRN filter."},
                            "source": {"type": "string", "description": "Optional source filter, for example webhook or uiprotect_track."},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                            "include_possible_fields": {
                                "type": "boolean",
                                "description": "Include candidate fields that looked like LPR payloads but did not normalize to a plate.",
                            },
                            "include_payload_path": {
                                "type": "boolean",
                                "description": "Include payload path diagnostics. Defaults false to keep observations compact.",
                            },
                        },
                        "additionalProperties": False,
                    },
                    handler=access_diagnostics_handlers.query_lpr_timing,
                ),
        AgentTool(
                    name="query_vehicle_detection_history",
                    description=(
                        "Count how many times a plate has appeared at the gate. Set latest_unknown=true "
                        "to resolve the latest unknown/unmatched vehicle first."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "registration_number": {"type": "string", "description": "Plate/VRN to count."},
                            "latest_unknown": {"type": "boolean", "description": "Resolve and count the latest unknown vehicle."},
                            "period": {"type": "string", "enum": ["all", "today", "yesterday", "recent"]},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                        },
                        "additionalProperties": False,
                    },
                    handler=access_diagnostics_handlers.query_vehicle_detection_history,
                ),
        AgentTool(
                    name="get_telemetry_trace",
                    description="Fetch a compact telemetry trace and bounded span list by trace ID or access event ID.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "trace_id": {"type": "string"},
                            "access_event_id": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                            "summarize_payload": {"type": "boolean", "description": "Default true. Summarize nested payloads instead of returning raw payloads."},
                        },
                        "additionalProperties": False,
                    },
                    handler=access_diagnostics_handlers.get_telemetry_trace,
                ),
        AgentTool(
                    name="query_leaderboard",
                    description="Return the Top Charts leaderboard for known VIP plates and denied unknown Mystery Guest plates.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "scope": {
                                "type": "string",
                                "enum": ["all", "known", "unknown", "top_known"],
                                "description": "Which Top Charts section to return.",
                            },
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                            "enrich_unknowns": {
                                "type": "boolean",
                                "description": "Whether to include live DVLA enrichment for unknown plates.",
                            },
                            "search": {
                                "type": "string",
                                "description": "Optional person, vehicle, or plate text to filter leaderboard rows.",
                            },
                            "person": {"type": "string", "description": "Optional known person name to filter VIP rows."},
                            "registration_number": {"type": "string", "description": "Optional plate to filter known or unknown rows."},
                        },
                        "additionalProperties": False,
                    },
                    handler=access_diagnostics_handlers.query_leaderboard,
                ),
        AgentTool(
                    name="query_anomalies",
                    description=(
                        "Return active/open and resolved alert records, including resolution notes, snapshot metadata, "
                        "linked access-event details, and stored vehicle visual evidence. Use status=all and "
                        "suspected_delivery=true for supplier or delivery questions such as oil deliveries, Dove Fuels, "
                        "or truck/lorry/tanker evidence in alert snapshots."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "severity": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["open", "active", "resolved", "all"],
                                "description": "Alert status to inspect. active is accepted as an alias for open.",
                            },
                            "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                            "search": {
                                "type": "string",
                                "description": "Optional text to search across alert message, registration, resolution note, context, and linked event payload.",
                            },
                            "suspected_delivery": {
                                "type": "boolean",
                                "description": "Set true when looking for likely supplier/delivery visits, including oil deliveries.",
                            },
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        },
                        "additionalProperties": False,
                    },
                    handler=access_diagnostics_handlers.query_anomalies,
                    example_inputs=(
                        {"status": "all", "day": "recent", "search": "oil delivery Dove Fuels truck tanker", "suspected_delivery": True, "limit": 25},
                        {"status": "open", "day": "today", "limit": 10},
                    ),
                    return_schema={
                        "answer_types": ["alert_match", "delivery_alert_match", "alert_activity_empty"],
                        "handles": ["active_alerts", "resolved_alerts", "delivery_alerts"],
                        "records": "alerts",
                    },
                ),
        AgentTool(
                    name="query_alert_activity",
                    description=(
                        "Return alerts raised and alerts resolved in a period. Use this for alert-only questions such as "
                        "what alerts were raised today, what alerts were resolved today, or raised/resolved alert activity. "
                        "This inspects alert/anomaly records only and does not include gate maintenance or malfunction summaries."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "severity": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["open", "active", "resolved", "all"],
                                "description": "Defaults to all for raised/resolved activity.",
                            },
                            "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                            "search": {"type": "string", "description": "Optional alert text, plate, note, or context search."},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        },
                        "additionalProperties": False,
                    },
                    handler=access_diagnostics_handlers.query_alert_activity,
                    example_inputs=(
                        {"day": "today", "status": "all"},
                        {"day": "yesterday", "status": "resolved"},
                    ),
                    return_schema={
                        "answer_types": ["alert_activity"],
                        "handles": ["raised_alerts", "resolved_alerts"],
                        "records": "raised,resolved",
                    },
                ),
        AgentTool(
                    name="analyze_alert_snapshot",
                    description=(
                        "Analyze a retained active or resolved alert snapshot with the active vision-capable provider. "
                        "Use after query_anomalies returns an alert_id and snapshot when visual confirmation is needed, "
                        "for example checking whether a snapshot shows a truck, lorry, tanker, or Dove Fuels branding."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "alert_id": {"type": "string", "description": "Alert/anomaly ID returned by query_anomalies."},
                            "prompt": {"type": "string", "description": "What to inspect in the retained snapshot."},
                            "provider": {"type": "string"},
                        },
                        "required": ["alert_id"],
                        "additionalProperties": False,
                    },
                    handler=access_diagnostics_handlers.analyze_alert_snapshot,
                ),
        AgentTool(
                    name="summarize_access_rhythm",
                    description="Summarize arrivals, exits, denials, and anomalies for a recent period.",
                    parameters={
                        "type": "object",
                        "properties": {"day": {"type": "string", "enum": ["today", "yesterday", "recent"]}},
                        "additionalProperties": False,
                    },
                    handler=access_diagnostics_handlers.summarize_access_rhythm,
                ),
        AgentTool(
                    name="calculate_visit_duration",
                    description=(
                        "Calculate on-site visit duration by pairing an entry with a following exit. "
                        "Use for elapsed on-site duration questions, including how long someone stayed, visited, or was here. "
                        "This returns a duration, not just an arrival timestamp."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "person": {"type": "string"},
                            "person_id": {"type": "string"},
                            "group": {"type": "string"},
                            "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                        },
                        "additionalProperties": False,
                    },
                    handler=access_diagnostics_handlers.calculate_visit_duration,
                    example_inputs=(
                        {"person": "Gardener", "day": "today"},
                        {"group": "contractor", "day": "recent"},
                    ),
                    return_schema={"answer_types": ["visit_duration"], "fact_kind": "elapsed_duration"},
                ),
        AgentTool(
                    name="calculate_absence_duration",
                    description=(
                        "Calculate off-site absence duration by pairing an exit with the next entry. "
                        "Use for elapsed off-site duration questions, including ongoing still-away absence and returned absence. "
                        "This is the inverse of visit duration: exit-to-entry, not entry-to-exit. "
                        "By default, report the latest matched absence; use mode=total only when the user asks for total time out."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "person": {"type": "string"},
                            "person_id": {"type": "string"},
                            "vehicle_id": {"type": "string"},
                            "group": {"type": "string"},
                            "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                            "mode": {"type": "string", "enum": ["latest", "total"]},
                        },
                        "additionalProperties": False,
                    },
                    handler=access_diagnostics_handlers.calculate_absence_duration,
                    example_inputs=(
                        {"person": "Ash", "day": "today", "mode": "latest"},
                        {"person_id": "person-uuid", "day": "today", "mode": "total"},
                    ),
                    return_schema={
                        "answer_types": ["absence_duration"],
                        "fact_kind": "elapsed_duration",
                        "handles": ["ongoing_absence", "returned_absence"],
                    },
                ),
        AgentTool(
                    name="trigger_anomaly_alert",
                    description="Send a contextual anomaly alert notification. Requires confirm=true because this sends real notifications.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "subject": {"type": "string"},
                            "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                            "message": {"type": "string"},
                            "confirm": {"type": "boolean"},
                        },
                        "required": ["subject", "severity", "message", "confirm"],
                        "additionalProperties": False,
                    },
                    handler=access_diagnostics_handlers.trigger_anomaly_alert,
                ),
        ],
        categories=TOOL_CATEGORIES,
        button_handler=confirmation_button,
        summary_handler=confirmation_summary,
        status_labels={'query_access_events': 'Reviewing access events...', 'diagnose_access_event': 'Diagnosing access event...', 'investigate_access_incident': 'Investigating access incident...', 'query_unifi_protect_events': 'Checking UniFi Protect history...', 'backfill_access_event_from_protect': 'Preparing access event backfill...', 'test_unifi_alarm_webhook': 'Preparing Protect webhook test...', 'query_lpr_timing': 'Checking LPR timing...', 'query_vehicle_detection_history': 'Counting vehicle detections...', 'get_telemetry_trace': 'Reading telemetry trace...', 'query_leaderboard': 'Checking Top Charts...', 'query_anomalies': 'Checking anomaly records...', 'summarize_access_rhythm': 'Summarizing site rhythm...', 'calculate_visit_duration': 'Calculating visit duration...', 'calculate_absence_duration': 'Calculating absence duration...', 'trigger_anomaly_alert': 'Preparing alert notification...'},
        success_fields={'test_unifi_alarm_webhook': ('sent',), 'trigger_anomaly_alert': ('sent',)},
        finish_after_confirmation=frozenset(),
        confirmation_required=CONFIRMATION_REQUIRED_TOOLS,
        default_limits=DEFAULT_LIMITS,
    )


def confirmation_summary(tool_name: str, output: dict[str, Any]) -> str:
    if tool_name in {'backfill_access_event_from_protect', 'investigate_access_incident'}:
        if output.get('backfilled'):
            return f"Backfilled the {output.get('direction') or 'access'} event for {output.get('registration_number') or 'that plate'} at {output.get('occurred_at_display') or output.get('occurred_at')}. Presence {('was' if output.get('presence_updated') else 'was not')} updated."
        return str(output.get('detail') or output.get('error') or 'I did not backfill the access event.')
    if tool_name == 'test_unifi_alarm_webhook':
        if output.get('sent'):
            return 'Sent the UniFi Protect Alarm Manager webhook test and checked for a matching IACS webhook trace.'
        return str(output.get('detail') or output.get('error') or 'I did not send the UniFi Protect webhook test.')
    if tool_name == 'trigger_anomaly_alert':
        if output.get('sent'):
            return f"Sent the anomaly alert: {output.get('title') or 'Alert'}."
        return str(output.get('detail') or output.get('error') or 'I did not send the anomaly alert.')
    return str(output.get("detail") or "Action completed.")


def confirmation_button(tool_name: str, output: dict[str, Any]) -> str:
    if tool_name in {'backfill_access_event_from_protect', 'investigate_access_incident'}:
        return 'Backfill event'
    if tool_name == 'test_unifi_alarm_webhook':
        return 'Send test'
    return "Confirm"
