"""Alfred tool catalog entries for this domain."""

from __future__ import annotations

from app.ai.tools import (
    AgentTool,
    backfill_access_event_from_protect,
    calculate_visit_duration,
    diagnose_access_event,
    get_telemetry_trace,
    investigate_access_incident,
    query_access_events,
    query_anomalies,
    query_leaderboard,
    query_lpr_timing,
    query_unifi_protect_events,
    query_vehicle_detection_history,
    summarize_access_rhythm,
    test_unifi_alarm_webhook,
    trigger_anomaly_alert,
)


def build_tools() -> list[AgentTool]:
    return [
        AgentTool(
                    name="query_access_events",
                    description="Return recent access events, optionally filtered by person, group/category, plate, or day.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "person": {"type": "string"},
                            "person_id": {"type": "string"},
                            "vehicle_id": {"type": "string"},
                            "group": {"type": "string"},
                            "registration_number": {"type": "string"},
                            "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                            "summarize_payload": {"type": "boolean", "description": "Default true. Return compact schedule/gate payload summaries instead of raw payloads."},
                        },
                        "additionalProperties": False,
                    },
                    handler=query_access_events,
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
                    handler=diagnose_access_event,
                ),
        AgentTool(
                    name="investigate_access_incident",
                    description=(
                        "Run a full Alfred access incident investigation. Use this for missing access events, "
                        "nothing logged, no notification, gate/garage failures, schedule denials, and any case "
                        "where diagnose_access_event finds no matching IACS event. It compares IACS records, "
                        "telemetry, Home Assistant gate observations, maintenance mode, schedules, notifications, "
                        "and durable UniFi Protect event/track history before recommending a fix."
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
                    handler=investigate_access_incident,
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
                    handler=query_unifi_protect_events,
                ),
        AgentTool(
                    name="backfill_access_event_from_protect",
                    description=(
                        "Create a missing IACS access event from durable UniFi Protect LPR evidence or explicit "
                        "admin-provided details. Requires Admin and confirm=true. Never opens gates, garage doors, "
                        "or sends normal arrival/departure notifications."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "protect_event_id": {"type": "string"},
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
                    handler=backfill_access_event_from_protect,
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
                    handler=test_unifi_alarm_webhook,
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
                    handler=query_lpr_timing,
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
                    handler=query_vehicle_detection_history,
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
                    handler=get_telemetry_trace,
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
                    handler=query_leaderboard,
                ),
        AgentTool(
                    name="query_anomalies",
                    description="Return recent anomaly records and unresolved alerts.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "severity": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        },
                        "additionalProperties": False,
                    },
                    handler=query_anomalies,
                ),
        AgentTool(
                    name="summarize_access_rhythm",
                    description="Summarize arrivals, exits, denials, and anomalies for a recent period.",
                    parameters={
                        "type": "object",
                        "properties": {"day": {"type": "string", "enum": ["today", "yesterday", "recent"]}},
                        "additionalProperties": False,
                    },
                    handler=summarize_access_rhythm,
                ),
        AgentTool(
                    name="calculate_visit_duration",
                    description="Calculate how long a person or group stayed on site today or recently.",
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
                    handler=calculate_visit_duration,
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
                        "required": ["subject", "severity", "message"],
                        "additionalProperties": False,
                    },
                    handler=trigger_anomaly_alert,
                ),
    ]
