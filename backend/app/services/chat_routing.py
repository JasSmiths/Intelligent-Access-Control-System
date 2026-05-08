"""Deterministic Alfred routing and planning policy.

These helpers intentionally preserve the existing keyword/planner behavior while keeping
that policy separate from the chat orchestration service.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.ai.providers import ToolCall
from app.ai.tools import AgentTool
from app.services.chat_contracts import (
    AUTOMATION_TOOL_NAMES,
    CAMERA_TOOL_NAMES,
    DEFAULT_AGENT_TOOL_NAMES,
    DEFAULT_CHAT_TIMEZONE,
    DEVICE_TOOL_NAMES,
    FILE_TOOL_NAMES,
    LEADERBOARD_TOOL_NAMES,
    MAINTENANCE_TOOL_NAMES,
    MALFUNCTION_TOOL_NAMES,
    NOTIFICATION_TOOL_NAMES,
    SCHEDULE_DAY_ALIASES,
    SCHEDULE_DAY_PATTERN,
    SCHEDULE_TOOL_NAMES,
    SUPPORTED_INTENTS,
    VISITOR_PASS_TOOL_NAMES,
    IntentRoute,
)


class ChatRoutingMixin:
    def _deterministic_intent_route(
            self,
            message: str,
            memory: dict[str, Any],
            attachments: list[dict[str, Any]],
            *,
            actor_context: dict[str, Any] | None = None,
        ) -> IntentRoute:
            lower = message.lower()
            intents: list[str] = []
            if attachments or any(word in lower for word in ["file", "attachment", "download", "csv", "pdf", "export", "invoice"]):
                intents.append("Reports_Files")
            if any(word in lower for word in ["camera", "snapshot", "image", "photo", "picture", "visible", "see"]):
                intents.append("Cameras")
            if any(word in lower for word in ["notification", "notifications", "workflow", "workflows", "template", "apprise"]):
                intents.append("Notifications")
            if any(word in lower for word in ["automation", "automations", "trigger", "condition", "then action", "rule to", "if steph", "if "]):
                intents.append("Automations")
            if any(word in lower for word in ["maintenance", "kill-switch", "kill switch", "disable automation", "resume automation"]):
                intents.append("Maintenance")
            if self._looks_like_visitor_pass_request(lower) or memory.get("pending_visitor_pass_create"):
                intents.append("Visitor_Passes")
            if self._looks_like_calendar_integration_request(lower):
                intents.append("Calendar_Integrations")
            if any(word in lower for word in ["schedule", "schedules", "timeframe", "allowed", "access window"]):
                intents.append("Schedules")
            if any(word in lower for word in ["dvla", "mot", "tax", "compliance", "registration", "plate", "vehicle", "tesla", "car"]):
                intents.append("Compliance_DVLA")
            if any(word in lower for word in ["gate", "garage", "door", "cover", "device", "malfunction", "fubar", "stuck", "open"]):
                intents.append("Gate_Hardware")
            if self._looks_like_missing_access_incident(lower) or self._looks_like_access_diagnostic_request(lower) or any(
                word in lower for word in ["why", "failed", "failure", "didn't", "didnt", "slow", "latency", "delay", "malfunction"]
            ):
                intents.append("Access_Diagnostics")
            if any(
                word in lower
                for word in ["present", "presence", "onsite", "on site", "arrive", "arrival", "arrived", "left", "leave", "exit", "event", "denied", "anomaly", "how long", "duration", "leaderboard", "top charts"]
            ):
                intents.append("Access_Logs")
            if any(word in lower for word in ["user", "users", "account", "accounts", "admin", "setting", "settings", "telemetry", "trace"]):
                intents.append("Users_Settings")
            if memory.get("pending_schedule_create"):
                intents.append("Schedules")
            if memory.get("last_visitor_name") and any(phrase in lower for phrase in ["what car", "which car", "how long", "duration", "stayed", "arrived in"]):
                intents.append("Visitor_Passes")
            if not intents:
                intents.append("General")
            deduped = tuple(dict.fromkeys(intent for intent in intents if intent in SUPPORTED_INTENTS))
            actor_has_vehicle = bool(((actor_context or {}).get("vehicles") or []))
            actor_has_person = bool(((actor_context or {}).get("person") or {}).get("id"))
            pronoun_reference = bool(re.search(r"\b(he|she|they|them|their|it|that|steph|wife|husband|tesla|car|vehicle)\b", lower))
            exact_actor_reference = bool(
                actor_has_person
                and re.search(r"\b(me|myself|mine)\b", lower)
                or actor_has_vehicle
                and re.search(r"\b(my car|my vehicle|my tesla)\b", lower)
            )
            needs_entity = bool(
                pronoun_reference and not exact_actor_reference
                or self._person_name_from_event_time_message(lower)
                or self._registration_from_message(message)
            )
            return IntentRoute(
                intents=deduped,
                confidence=0.72 if deduped != ("General",) else 0.45,
                requires_entity_resolution=needs_entity,
                reason="deterministic keyword and session-memory route",
                source="deterministic",
            )

    def _select_tools_for_route(
            self,
            route: IntentRoute,
            attachments: list[dict[str, Any]],
        ) -> list[AgentTool]:
            intents = set(route.intents or ("General",))
            pure_visitor_pass_route = intents == {"Visitor_Passes"}
            names: set[str] = set() if pure_visitor_pass_route else {"resolve_human_entity"}
            if attachments:
                names.add("read_chat_attachment")
            for name, tool in self._tools.items():
                if pure_visitor_pass_route and name == "resolve_human_entity":
                    continue
                if intents.intersection(tool.categories):
                    if intents == {"General"} and not tool.read_only:
                        continue
                    names.add(name)
            if "Access_Diagnostics" in intents:
                names.update(
                    {
                        "backfill_access_event_from_protect",
                        "diagnose_access_event",
                        "get_maintenance_status",
                        "get_telemetry_trace",
                        "investigate_access_incident",
                        "query_access_events",
                        "query_lpr_timing",
                        "query_unifi_protect_events",
                        "resolve_human_entity",
                        "test_unifi_alarm_webhook",
                        "verify_schedule_access",
                    }
                )
            if "Gate_Hardware" in intents:
                names.update({"get_maintenance_status", "query_device_states"})
            if "Compliance_DVLA" in intents:
                names.update({"lookup_dvla_vehicle", "query_vehicle_detection_history"})
            if "Schedules" in intents:
                names.update({"query_schedules", "query_schedule_targets", "verify_schedule_access"})
            if "Visitor_Passes" in intents:
                names.update(VISITOR_PASS_TOOL_NAMES)
            if "Calendar_Integrations" in intents:
                names.add("trigger_icloud_sync")
            if "Reports_Files" in intents and attachments:
                names.add("read_chat_attachment")
            return [tool for name, tool in self._tools.items() if name in names]

    def _deterministic_react_calls(
            self,
            message: str,
            route: IntentRoute,
            memory: dict[str, Any],
            attachments: list[dict[str, Any]],
            tool_results: list[dict[str, Any]],
            selected_tools: list[AgentTool],
            *,
            iteration: int,
            actor_context: dict[str, Any] | None = None,
        ) -> list[ToolCall]:
            allowed = {tool.name for tool in selected_tools}
            intents = set(route.intents)
            lower = message.lower()
            calls: list[ToolCall] = []

            if iteration == 0 and route.requires_entity_resolution and "resolve_human_entity" in allowed:
                entity_types = self._entity_types_for_route(route)
                query = self._entity_query_from_message(message, memory)
                if query:
                    calls.append(
                        ToolCall(
                            "react-resolve-entity",
                            "resolve_human_entity",
                            {"query": query, "entity_types": entity_types},
                        )
                    )

            if iteration == 0:
                for index, attachment in enumerate(attachments[:4]):
                    if "read_chat_attachment" in allowed:
                        calls.append(
                            ToolCall(
                                f"react-read-attachment-{index}",
                                "read_chat_attachment",
                                {
                                    "file_id": attachment["id"],
                                    "prompt": message or "Summarize this attachment for the user.",
                                },
                            )
                        )

            if iteration > 0 or not calls:
                if (
                    "Access_Diagnostics" in intents
                    and "investigate_access_incident" in allowed
                    and (
                        self._looks_like_missing_access_incident(lower)
                        or self._latest_diagnostic_result_not_found(tool_results)
                    )
                ):
                    args = self._access_incident_args_from_message(message, memory, actor_context=actor_context)
                    calls.append(ToolCall("react-investigate-access-incident", "investigate_access_incident", args))
                elif "Access_Diagnostics" in intents and "diagnose_access_event" in allowed:
                    args = self._access_diagnostic_args_from_message(message, memory, actor_context=actor_context)
                    args.setdefault("summarize_payload", True)
                    args.setdefault("span_limit", 20)
                    calls.append(ToolCall("react-diagnose-access", "diagnose_access_event", args))
                    if self._looks_like_lpr_timing_request(lower) and "query_lpr_timing" in allowed:
                        lpr_args = {
                            key: value
                            for key, value in {
                                "registration_number": args.get("registration_number"),
                                "limit": 25,
                            }.items()
                            if value
                        }
                        calls.append(ToolCall("react-lpr-timing", "query_lpr_timing", lpr_args))
                elif "Gate_Hardware" in intents:
                    if self._looks_like_device_action_request(lower):
                        action = self._device_action_from_message(lower)
                        if action == "open" and "garage" not in lower and "open_gate" in allowed:
                            calls.append(
                                ToolCall(
                                    "react-open-gate",
                                    "open_gate",
                                    {
                                        "target": self._device_target_from_message(lower) or "",
                                        "reason": message,
                                        "confirm": self._explicitly_confirmed_device_action(lower),
                                    },
                                )
                            )
                        elif action == "close" and "command_device" in allowed:
                            calls.append(self._planned_device_action_call(message))
                        elif "command_device" in allowed:
                            calls.append(self._planned_device_action_call(message))
                        elif "open_device" in allowed:
                            calls.append(self._planned_device_open_call(message))
                    elif "query_device_states" in allowed:
                        calls.append(
                            ToolCall(
                                "react-query-device-states",
                                "query_device_states",
                                {"target": self._device_target_from_message(lower) or "", "kind": "all"},
                            )
                        )
                    if any(word in lower for word in ["malfunction", "fubar", "stuck", "recovery", "attempt"]) and "get_active_malfunctions" in allowed:
                        calls.append(ToolCall("react-active-malfunctions", "get_active_malfunctions", {"include_timeline": True}))
                elif "Maintenance" in intents:
                    if "toggle_maintenance_mode" in allowed and any(word in lower for word in ["enable", "turn on", "activate", "start", "disable automation"]):
                        calls.append(
                            ToolCall(
                                "react-enable-maintenance",
                                "toggle_maintenance_mode",
                                {"state": "enabled", "reason": message, "confirm": self._is_confirmation_message(lower)},
                            )
                        )
                    elif "toggle_maintenance_mode" in allowed and any(word in lower for word in ["disable", "turn off", "deactivate", "stop", "resume automation"]):
                        calls.append(
                            ToolCall(
                                "react-disable-maintenance",
                                "toggle_maintenance_mode",
                                {"state": "disabled", "confirm": self._is_confirmation_message(lower)},
                            )
                        )
                    elif "get_maintenance_status" in allowed:
                        calls.append(ToolCall("react-maintenance-status", "get_maintenance_status", {}))
                elif "Calendar_Integrations" in intents:
                    if "trigger_icloud_sync" in allowed:
                        calls.append(
                            ToolCall(
                                "react-trigger-icloud-sync",
                                "trigger_icloud_sync",
                                {"confirm": self._is_confirmation_message(lower)},
                            )
                        )
                elif "Visitor_Passes" in intents:
                    visitor_name = self._visitor_name_from_message(message) or str(memory.get("last_visitor_name") or "")
                    expected_time = self._visitor_expected_time_from_message(message)
                    if self._looks_like_visitor_pass_cancel_request(lower) and "cancel_visitor_pass" in allowed:
                        calls.append(
                            ToolCall(
                                "react-cancel-visitor-pass",
                                "cancel_visitor_pass",
                                {
                                    "visitor_name": visitor_name,
                                    "reason": message,
                                    "confirm": self._is_confirmation_message(lower),
                                },
                            )
                        )
                    elif self._looks_like_visitor_pass_create_request(lower) and "create_visitor_pass" in allowed and visitor_name and expected_time:
                        calls.append(
                            ToolCall(
                                "react-create-visitor-pass",
                                "create_visitor_pass",
                                {
                                    "visitor_name": visitor_name,
                                    "expected_time": expected_time,
                                    "window_minutes": self._visitor_window_from_message(lower) or 30,
                                    "confirm": self._is_confirmation_message(lower),
                                },
                            )
                        )
                    elif "query_visitor_passes" in allowed:
                        query_args: dict[str, Any] = {"limit": 10}
                        if visitor_name and not self._looks_like_visitor_pass_query_request(lower):
                            query_args["search"] = visitor_name
                        elif not any(phrase in lower for phrase in ["what car", "which car", "how long", "duration", "stayed", "arrived in"]):
                            query_args["statuses"] = ["active", "scheduled"]
                        calls.append(ToolCall("react-query-visitor-passes", "query_visitor_passes", query_args))
                elif "Compliance_DVLA" in intents:
                    registration_number = self._registration_from_message(message)
                    if registration_number and "lookup_dvla_vehicle" in allowed:
                        calls.append(ToolCall("react-dvla", "lookup_dvla_vehicle", {"registration_number": registration_number}))
                    elif "query_vehicle_detection_history" in allowed:
                        calls.append(ToolCall("react-vehicle-history", "query_vehicle_detection_history", {"period": "recent", "limit": 10}))
                elif "Access_Logs" in intents:
                    if any(phrase in lower for phrase in ["how long", "duration", "stay", "stayed"]) and "calculate_visit_duration" in allowed:
                        args = self._subject_args(self._subject_from_message(lower, memory, actor_context=actor_context))
                        args["day"] = "today" if "today" in lower else "recent"
                        calls.append(ToolCall("react-duration", "calculate_visit_duration", args))
                    elif "query_access_events" in allowed:
                        args = self._subject_args(self._subject_from_message(lower, memory, actor_context=actor_context))
                        args["day"] = "today" if "today" in lower else "recent"
                        args["limit"] = 10
                        args["summarize_payload"] = True
                        calls.append(ToolCall("react-query-events", "query_access_events", args))
                elif "Schedules" in intents and "query_schedules" in allowed:
                    calls.append(ToolCall("react-query-schedules", "query_schedules", {"include_dependencies": True}))
                elif "Notifications" in intents and "query_notification_workflows" in allowed:
                    calls.append(ToolCall("react-query-notifications", "query_notification_workflows", {"limit": 20}))
                elif "Cameras" in intents and "get_camera_snapshot" in allowed and self._looks_like_camera_snapshot_request(lower):
                    calls.append(self._planned_camera_snapshot_call(message))
                elif "Users_Settings" in intents:
                    if "get_telemetry_trace" in allowed and "trace" in lower:
                        calls.append(ToolCall("react-telemetry", "get_telemetry_trace", {"limit": 20}))
                    elif "get_system_users" in allowed:
                        calls.append(ToolCall("react-users", "get_system_users", {}))
                elif "query_presence" in allowed:
                    calls.append(ToolCall("react-presence", "query_presence", {}))

            fresh: list[ToolCall] = []
            seen = {
                self._tool_call_fingerprint(
                    ToolCall(str(result.get("call_id") or ""), str(result.get("name") or ""), result.get("arguments") if isinstance(result.get("arguments"), dict) else {})
                )
                for result in tool_results
            }
            for call in calls:
                if call.name not in allowed:
                    continue
                fingerprint = self._tool_call_fingerprint(call)
                if fingerprint in seen:
                    continue
                fresh.append(call)
            return fresh[:2]

    def _entity_types_for_route(self, route: IntentRoute) -> list[str]:
            intents = set(route.intents)
            if "Gate_Hardware" in intents:
                return ["device", "person", "vehicle"]
            if "Compliance_DVLA" in intents:
                return ["vehicle", "person"]
            if "Schedules" in intents:
                return ["person", "vehicle", "group", "device"]
            if "Visitor_Passes" in intents:
                return ["person", "vehicle"]
            if "Access_Logs" in intents or "Access_Diagnostics" in intents:
                return ["person", "vehicle", "group"]
            return ["person", "vehicle", "group", "device"]

    def _entity_query_from_message(self, message: str, memory: dict[str, Any]) -> str:
            registration = self._registration_from_message(message)
            if registration:
                return registration
            person_name = self._person_name_from_event_time_message(message.lower())
            if person_name:
                return person_name
            subject = self._subject_from_message(message.lower(), memory)
            if subject.get("person"):
                return subject["person"]
            if subject.get("group"):
                return subject["group"]
            match = re.search(r"\b(?:for|did|didn't|didnt|was|is|has|about)\s+([A-Za-z][A-Za-z' -]{1,40})", message)
            if match:
                return match.group(1).strip(" ?.!'")
            return message.strip()[:80]

    def _select_tools_for_request(
            self,
            message: str,
            memory: dict[str, Any],
            attachments: list[dict[str, Any]],
            tool_results: list[dict[str, Any]],
        ) -> list[AgentTool]:
            lower = message.lower()
            names: set[str] = set()
            leaderboard_request = self._looks_like_leaderboard_request(lower)

            if attachments:
                names.add("read_chat_attachment")

            if self._looks_like_device_action_request(lower):
                names.update(DEVICE_TOOL_NAMES)
            elif self._looks_like_device_state_request(lower):
                names.add("query_device_states")

            if any(word in lower for word in ["gate", "garage", "door", "cover", "device"]):
                names.add("query_device_states")

            if any(word in lower for word in ["malfunction", "fubar", "stuck", "recovery", "retry", "attempt"]):
                names.update(MALFUNCTION_TOOL_NAMES)

            if "what is the gate doing" in lower or "gate doing right now" in lower:
                names.update(("query_device_states", "get_active_malfunctions"))

            if any(word in lower for word in ["maintenance", "kill-switch", "kill switch", "disable automation", "resume automation"]):
                names.update(MAINTENANCE_TOOL_NAMES)

            if not leaderboard_request and any(word in lower for word in ["present", "presence", "onsite", "on site", "here", "who is", "who's"]):
                names.add("query_presence")

            if any(word in lower for word in ["arrive", "arrival", "arrived", "came", "leave", "left", "exit", "exited", "event", "denied", "access log"]):
                names.update(("query_access_events", "query_anomalies"))

            if self._looks_like_missing_access_incident(lower) or self._looks_like_access_diagnostic_request(lower):
                names.update(("diagnose_access_event", "investigate_access_incident", "query_access_events", "query_lpr_timing", "query_unifi_protect_events"))

            if self._looks_like_vehicle_detection_count_request(lower):
                names.update(("query_vehicle_detection_history", "query_access_events", "query_leaderboard"))

            if any(phrase in lower for phrase in ["how long", "duration", "stay", "stayed"]):
                names.update(("calculate_visit_duration", "query_access_events"))

            if any(word in lower for word in ["anomaly", "anomalies", "unauthorized", "unauthorised", "alert"]):
                names.update(("query_anomalies", "trigger_anomaly_alert"))

            if any(word in lower for word in ["summary", "summarize", "summarise", "rhythm", "report"]):
                names.update(("summarize_access_rhythm", "query_access_events"))

            if leaderboard_request:
                names.update(LEADERBOARD_TOOL_NAMES)

            if any(word in lower for word in ["schedule", "schedules", "timeframe", "allowed", "access window"]):
                names.update(SCHEDULE_TOOL_NAMES)

            if memory.get("pending_schedule_create"):
                names.update(SCHEDULE_TOOL_NAMES)

            if self._looks_like_visitor_pass_request(lower) or memory.get("pending_visitor_pass_create") or memory.get("last_visitor_name"):
                names.update(VISITOR_PASS_TOOL_NAMES)

            if any(word in lower for word in ["notification", "notifications", "workflow", "workflows", "template", "apprise"]):
                names.update(NOTIFICATION_TOOL_NAMES)

            if any(word in lower for word in ["automation", "automations", "trigger", "condition", "then action", "rule to"]):
                names.update(AUTOMATION_TOOL_NAMES)

            if any(word in lower for word in ["vehicle", "registration", "reg", "plate", "dvla", "mot", "tax"]):
                names.update(("lookup_dvla_vehicle", "query_access_events"))

            if any(word in lower for word in ["camera", "snapshot", "image", "photo", "picture", "visible", "see"]):
                names.update(CAMERA_TOOL_NAMES)

            if any(word in lower for word in ["file", "attachment", "download", "csv", "pdf", "export", "invoice"]):
                names.update(FILE_TOOL_NAMES)

            if any(word in lower for word in ["user", "users", "account", "accounts", "admin"]):
                names.add("get_system_users")

            for result in tool_results:
                name = str(result.get("name") or "")
                if name:
                    names.add(name)
                output = result.get("output")
                if isinstance(output, dict) and output.get("requires_confirmation"):
                    if name in {"open_device", "command_device"}:
                        names.update(DEVICE_TOOL_NAMES)
                    elif name == "trigger_manual_malfunction_override":
                        names.update(MALFUNCTION_TOOL_NAMES)
                    elif name in MAINTENANCE_TOOL_NAMES:
                        names.update(MAINTENANCE_TOOL_NAMES)
                    elif name in SCHEDULE_TOOL_NAMES:
                        names.update(SCHEDULE_TOOL_NAMES)
                    elif name in VISITOR_PASS_TOOL_NAMES:
                        names.update(VISITOR_PASS_TOOL_NAMES)
                    elif name in NOTIFICATION_TOOL_NAMES:
                        names.update(NOTIFICATION_TOOL_NAMES)
                    elif name in AUTOMATION_TOOL_NAMES:
                        names.update(AUTOMATION_TOOL_NAMES)

            if not names:
                names.update(DEFAULT_AGENT_TOOL_NAMES)

            return [tool for name, tool in self._tools.items() if name in names]

    def _plan_tool_calls(
            self,
            message: str,
            memory: dict[str, Any],
            attachments: list[dict[str, Any]],
        ) -> list[ToolCall]:
            lower = message.lower()
            subject = self._subject_from_message(lower, memory)
            leaderboard_request = self._looks_like_leaderboard_request(lower)
            calls: list[ToolCall] = []

            for index, attachment in enumerate(attachments[:4]):
                calls.append(
                    ToolCall(
                        f"planned-read-attachment-{index}",
                        "read_chat_attachment",
                        {
                            "file_id": attachment["id"],
                            "prompt": message or "Summarize this attachment for the user.",
                        },
                    )
                )

            if self._looks_like_device_action_request(lower):
                action = self._device_action_from_message(lower)
                if action == "open" and "garage" not in lower and "open_gate" in self._tools:
                    calls.append(
                        ToolCall(
                            "planned-open-gate",
                            "open_gate",
                            {
                                "target": self._device_target_from_message(lower) or "",
                                "reason": message,
                                "confirm": self._explicitly_confirmed_device_action(lower),
                            },
                        )
                    )
                elif action == "close" and "command_device" in self._tools:
                    calls.append(self._planned_device_action_call(message))
                else:
                    calls.append(
                        ToolCall(
                            "planned-open-device",
                            "open_device",
                            {
                                "target": self._device_target_from_message(lower) or "",
                                "action": action,
                                "kind": "all",
                                "reason": message,
                                "confirm": self._explicitly_confirmed_device_action(lower),
                            },
                        )
                    )

            if self._looks_like_device_state_request(lower):
                calls.append(
                    ToolCall(
                        "planned-query-device-states",
                        "query_device_states",
                        {"target": self._device_target_from_message(lower) or "", "kind": "all"},
                    )
                )

            if any(word in lower for word in ["maintenance", "kill-switch", "kill switch", "disable automation", "resume automation"]):
                if any(word in lower for word in ["enable", "turn on", "activate", "start", "disable automation"]):
                    calls.append(
                        ToolCall(
                            "planned-enable-maintenance",
                            "toggle_maintenance_mode",
                            {"state": "enabled", "reason": message, "confirm": self._is_confirmation_message(lower)},
                        )
                    )
                elif any(word in lower for word in ["disable", "turn off", "deactivate", "stop", "resume automation"]):
                    calls.append(
                        ToolCall(
                            "planned-disable-maintenance",
                            "toggle_maintenance_mode",
                            {"state": "disabled", "confirm": self._is_confirmation_message(lower)},
                        )
                    )
                else:
                    calls.append(ToolCall("planned-maintenance-status", "get_maintenance_status", {}))

            if any(word in lower for word in ["malfunction", "fubar", "stuck", "recovery", "retry", "attempt"]) or "gate doing right now" in lower:
                calls.append(
                    ToolCall(
                        "planned-active-malfunctions",
                        "get_active_malfunctions",
                        {"include_timeline": any(word in lower for word in ["timeline", "history", "trace", "why"])},
                    )
                )

            if self._looks_like_missing_access_incident(lower):
                calls.append(
                    ToolCall(
                        "planned-access-incident",
                        "investigate_access_incident",
                        self._access_incident_args_from_message(message, memory),
                    )
                )
            elif self._looks_like_access_diagnostic_request(lower):
                diagnostic_args = self._access_diagnostic_args_from_message(message, memory)
                calls.append(ToolCall("planned-access-diagnostics", "diagnose_access_event", diagnostic_args))
                if self._looks_like_lpr_timing_request(lower):
                    lpr_args = {
                        key: value
                        for key, value in {
                            "registration_number": diagnostic_args.get("registration_number"),
                            "limit": 50,
                        }.items()
                        if value
                    }
                    calls.append(ToolCall("planned-lpr-timing", "query_lpr_timing", lpr_args))

            if self._looks_like_vehicle_detection_count_request(lower):
                args: dict[str, Any] = {
                    "period": "all",
                    "limit": 10,
                }
                registration_number = self._registration_from_message(message)
                if registration_number:
                    args["registration_number"] = registration_number
                else:
                    args["latest_unknown"] = self._refers_to_latest_unknown_vehicle(lower)
                calls.append(ToolCall("planned-detection-history", "query_vehicle_detection_history", args))

            if self._looks_like_schedule_delete_request(lower):
                calls.append(self._planned_schedule_delete_call(message))

            if not leaderboard_request and any(word in lower for word in ["present", "here", "onsite", "on site", "who is"]):
                calls.append(ToolCall("planned-query-presence", "query_presence", self._subject_args(subject)))

            if any(word in lower for word in ["arrive", "arrival", "arrived", "came", "left", "leave", "exit", "exited", "event", "denied"]):
                args = self._subject_args(subject)
                person_name = self._person_name_from_event_time_message(lower)
                if person_name:
                    args["person"] = person_name
                args["day"] = "today" if "today" in lower else "recent"
                calls.append(ToolCall("planned-query-events", "query_access_events", args))

            if any(word in lower for word in ["how long", "duration", "stay", "stayed"]):
                args = self._subject_args(subject)
                args["day"] = "today" if "today" in lower or memory else "recent"
                calls.append(ToolCall("planned-duration", "calculate_visit_duration", args))

            if any(word in lower for word in ["anomaly", "anomalies", "alert", "unauthorized"]):
                calls.append(ToolCall("planned-query-anomalies", "query_anomalies", {"limit": 25}))

            if ("send" in lower or "trigger" in lower) and "alert" in lower:
                calls.append(
                    ToolCall(
                        "planned-trigger-alert",
                        "trigger_anomaly_alert",
                        {
                            "subject": memory.get("last_subject") or "Manual AI alert",
                            "severity": "warning",
                            "message": message,
                        },
                    )
                )

            registration_number = self._registration_from_message(message)
            if registration_number and self._is_vehicle_lookup_request(lower):
                calls.append(
                    ToolCall(
                        "planned-dvla-lookup",
                        "lookup_dvla_vehicle",
                        {"registration_number": registration_number},
                    )
                )

            if any(word in lower for word in ["camera", "snapshot", "image"]) and any(
                word in lower for word in ["analyze", "analyse", "look", "see", "visible", "describe"]
            ):
                camera_name = self._camera_name_from_message(message)
                if camera_name:
                    calls.append(
                        ToolCall(
                            "planned-camera-analysis",
                            "analyze_camera_snapshot",
                            {"camera_name": camera_name, "prompt": message},
                        )
                    )

            if any(word in lower for word in ["summary", "summarize", "rhythm", "report"]):
                calls.append(
                    ToolCall(
                        "planned-summary",
                        "summarize_access_rhythm",
                        {"day": "today" if "today" in lower else "recent"},
                    )
                )

            if leaderboard_request:
                calls.append(self._planned_leaderboard_call(message))

            if "presence" in lower and any(word in lower for word in ["csv", "export", "download", "spreadsheet"]):
                calls.append(
                    ToolCall(
                        "planned-presence-csv",
                        "export_presence_report_csv",
                        {"day": "today" if "today" in lower else "recent"},
                    )
                )

            if "invoice" in lower and "contractor" in lower:
                calls.append(
                    ToolCall(
                        "planned-contractor-invoice",
                        "generate_contractor_invoice_pdf",
                        {"contractor_name": memory.get("last_subject") or "Contractor", "day": "today"},
                    )
                )

            if any(word in lower for word in ["snapshot", "image", "camera"]) and any(
                word in lower for word in ["attach", "fetch", "get", "send", "show", "latest"]
            ):
                camera_name = self._camera_name_from_message(message)
                if camera_name:
                    calls.append(
                        ToolCall(
                            "planned-camera-snapshot",
                            "get_camera_snapshot",
                            {"camera_name": camera_name},
                        )
                    )

            if not calls:
                calls.append(ToolCall("planned-query-presence", "query_presence", {}))
            return calls

    def _preplanned_context_calls(
            self,
            message: str,
            memory: dict[str, Any],
            _attachments: list[dict[str, Any]],
        ) -> list[ToolCall]:
            """Run safe read-only context tools before hosted providers answer.

            Native function calling is still available, but questions that are
            clearly about access-event causality need the deep diagnostic record
            loaded deterministically. This avoids a provider taking the shallow
            access-log path and claiming latency or notification data is missing.
            """

            lower = message.lower()
            calls: list[ToolCall] = []
            if self._looks_like_access_diagnostic_request(lower):
                diagnostic_args = self._access_diagnostic_args_from_message(message, memory)
                calls.append(ToolCall("preplanned-access-diagnostics", "diagnose_access_event", diagnostic_args))
                if self._looks_like_lpr_timing_request(lower):
                    lpr_args = {
                        key: value
                        for key, value in {
                            "registration_number": diagnostic_args.get("registration_number"),
                            "limit": 50,
                        }.items()
                        if value
                    }
                    calls.append(ToolCall("preplanned-lpr-timing", "query_lpr_timing", lpr_args))

            if self._looks_like_vehicle_detection_count_request(lower):
                args: dict[str, Any] = {"period": "all", "limit": 10}
                registration_number = self._registration_from_message(message)
                if registration_number:
                    args["registration_number"] = registration_number
                else:
                    args["latest_unknown"] = self._refers_to_latest_unknown_vehicle(lower)
                calls.append(ToolCall("preplanned-detection-history", "query_vehicle_detection_history", args))

            return calls

    def _planned_device_open_call(self, message: str) -> ToolCall:
            lower = message.lower()
            target = self._device_target_from_message(lower) or ""
            return ToolCall(
                "planned-open-device",
                "open_device",
                {
                    "target": target,
                    "action": "open",
                    "kind": "all",
                    "reason": message,
                    "confirm": self._explicitly_confirmed_device_open(lower),
                },
            )

    def _planned_device_action_call(self, message: str) -> ToolCall:
            lower = message.lower()
            action = self._device_action_from_message(lower)
            target = self._device_target_from_message(lower) or ""
            tool_name = "command_device" if "command_device" in self._tools else "open_device"
            return ToolCall(
                f"planned-{action}-device",
                tool_name,
                {
                    "target": target,
                    "action": action,
                    "kind": "all",
                    "reason": message,
                    "confirm": self._explicitly_confirmed_device_action(lower),
                },
            )

    def _planned_schedule_delete_call(self, message: str) -> ToolCall:
            return ToolCall(
                "planned-delete-schedule",
                "delete_schedule",
                {
                    "schedule_name": self._schedule_delete_name_from_message(message) or self._schedule_name_from_message(message) or "",
                    "confirm": False,
                },
            )

    def _planned_camera_snapshot_call(self, message: str) -> ToolCall:
            return ToolCall(
                "planned-camera-snapshot",
                "get_camera_snapshot",
                {"camera_name": self._camera_name_from_message(message) or ""},
            )

    def _planned_access_event_time_call(self, message: str, memory: dict[str, Any]) -> ToolCall:
            lower = message.lower()
            args: dict[str, Any] = {"limit": 50, "day": self._day_from_message(lower)}
            person_name = self._person_name_from_event_time_message(lower)
            if person_name:
                args["person"] = person_name
            elif memory.get("last_person"):
                args["person"] = memory["last_person"]
            subject = self._subject_from_message(lower, memory)
            args.update(self._subject_args(subject))
            return ToolCall("planned-access-event-time", "query_access_events", args)

    def _planned_leaderboard_call(self, message: str) -> ToolCall:
            lower = message.lower()
            scope = "all"
            if any(phrase in lower for phrase in ["mystery", "unknown", "stranger", "denied"]):
                scope = "unknown"
            elif any(phrase in lower for phrase in ["vip", "known", "family", "leader", "winner", "winning", "top spot", "number one", "#1"]):
                scope = "top_known" if any(phrase in lower for phrase in ["leader", "winner", "winning", "top spot", "number one", "#1"]) else "known"

            args: dict[str, Any] = {
                "scope": scope,
                "limit": self._leaderboard_limit_from_message(lower),
                "enrich_unknowns": scope in {"all", "unknown"},
            }
            registration_number = self._registration_from_message(message)
            if registration_number:
                args["registration_number"] = registration_number
            return ToolCall("planned-query-leaderboard", "query_leaderboard", args)

    def _device_open_direct_text(self, output: dict[str, Any]) -> str:
            device = output.get("device") if isinstance(output.get("device"), dict) else {}
            name = str(device.get("name") or output.get("target") or "that device").strip()
            action = str(output.get("action") or "open")
            if output.get("requires_details"):
                return str(output.get("detail") or f"Which gate or garage door should I {action}?")
            if output.get("requires_confirmation"):
                return f"Please confirm before I {action} {name}. I'll keep the cape off this one until you press the button."
            success = bool(output.get("opened") if action == "open" else output.get("closed"))
            if success:
                return f"{'Opened' if action == 'open' else 'Closed'} {name}. Logged, tidy, and pleasingly uneventful."
            return str(output.get("detail") or output.get("error") or f"I could not {action} {name}.")

    def _schedule_delete_direct_text(self, output: dict[str, Any]) -> str:
            schedule = output.get("schedule") if isinstance(output.get("schedule"), dict) else {}
            name = str(schedule.get("name") or output.get("schedule_name") or "that schedule").strip()
            if output.get("requires_confirmation"):
                return str(output.get("detail") or f"Delete the {name} schedule? Use the confirmation button to continue.")
            if output.get("deleted"):
                return f"Deleted {name}."
            if output.get("dependencies"):
                return f"I cannot delete {name} because it is still assigned. Remove its assignments first, then try again."
            return str(output.get("detail") or output.get("error") or f"I could not delete {name}.")

    def _camera_snapshot_direct_text(self, output: dict[str, Any]) -> str:
            if output.get("fetched"):
                return "Here's the latest snapshot."
            camera = output.get("camera") or "that camera"
            detail = str(output.get("error") or "I could not fetch the snapshot.")
            return f"I couldn't fetch {camera}: {detail}"

    def _access_event_time_direct_text(self, message: str, output: dict[str, Any]) -> str:
            events = output.get("events") if isinstance(output.get("events"), list) else []
            lower = message.lower()
            direction = "exit" if any(word in lower for word in ["leave", "left", "exit", "exited"]) else "entry"
            matching = [event for event in events if event.get("direction") == direction]
            event = matching[0] if matching else (events[0] if events else None)
            if not event:
                person = self._person_name_from_event_time_message(lower)
                subject = f" for {person.title()}" if person else ""
                action = "leave" if direction == "exit" else "arrive"
                return f"I couldn't find a recent {action} event{subject}."
            person_name = event.get("person") or event.get("registration_number") or "They"
            verb = "left" if event.get("direction") == "exit" else "arrived"
            occurred_at = self._chat_time_from_iso(str(event.get("occurred_at") or ""))
            return f"{person_name} {verb} at {occurred_at}." if occurred_at else f"{person_name} {verb} recently."

    def _chat_time_from_iso(self, value: str) -> str | None:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(ZoneInfo(DEFAULT_CHAT_TIMEZONE)).strftime("%H:%M")

    def _subject_from_message(
            self,
            lower: str,
            memory: dict[str, Any],
            *,
            actor_context: dict[str, Any] | None = None,
        ) -> dict[str, str]:
            actor_subject = self._actor_subject_from_message(lower, actor_context or {})
            if actor_subject:
                return actor_subject
            if "gardener" in lower:
                return {"group": "gardener"}
            if "contractor" in lower:
                return {"group": "contractor"}
            if any(token in lower.split() for token in ["they", "them", "he", "she", "their"]):
                if memory.get("last_group"):
                    return {"group": memory["last_group"]}
                if memory.get("last_person"):
                    return {"person": memory["last_person"]}
            return {}

    def _actor_subject_from_message(self, lower: str, actor_context: dict[str, Any]) -> dict[str, str]:
            person = actor_context.get("person") if isinstance(actor_context.get("person"), dict) else {}
            vehicles = actor_context.get("vehicles") if isinstance(actor_context.get("vehicles"), list) else []
            if re.search(r"\b(my car|my vehicle|my tesla)\b", lower) and vehicles:
                vehicle = next((item for item in vehicles if isinstance(item, dict)), None)
                if vehicle and vehicle.get("id"):
                    return {"vehicle_id": str(vehicle["id"])}
                if vehicle and vehicle.get("registration_number"):
                    return {"registration_number": str(vehicle["registration_number"])}
            if re.search(r"\b(me|myself|mine)\b", lower) and person.get("id"):
                return {"person_id": str(person["id"]), "person": str(person.get("display_name") or "")}
            return {}

    def _subject_args(self, subject: dict[str, str]) -> dict[str, str]:
            if "vehicle_id" in subject:
                return {"vehicle_id": subject["vehicle_id"]}
            if "person_id" in subject:
                return {"person_id": subject["person_id"]}
            if "registration_number" in subject:
                return {"registration_number": subject["registration_number"]}
            if "group" in subject:
                return {"group": subject["group"]}
            if "person" in subject:
                return {"person": subject["person"]}
            return {}

    def _registration_from_message(self, message: str) -> str | None:
            for match in re.finditer(r"\b[A-Z0-9][A-Z0-9 -]{1,10}[A-Z0-9]\b", message.upper()):
                candidate = re.sub(r"[^A-Z0-9]", "", match.group(0))
                raw = match.group(0).lower()
                if re.search(r"\b(?:back|came|come|return|returned|arrive|arrived)\s+at\s+\d", raw):
                    continue
                if " " in raw and re.search(r"\b(?:but|he|she|they|them|wasnt|wasn't|not|notification|fired)\b", raw):
                    continue
                if re.fullmatch(r"\d+(?:MS|S|SEC|SECS|SECOND|SECONDS|MILLISECOND|MILLISECONDS)", candidate):
                    continue
                if re.fullmatch(r"AT\d{1,4}", candidate):
                    continue
                if re.match(r"^\d{1,2}(?:AM|PM)", candidate):
                    continue
                if 2 <= len(candidate) <= 8 and any(char.isalpha() for char in candidate) and any(char.isdigit() for char in candidate):
                    return candidate
            return None

    def _camera_name_from_message(self, message: str) -> str | None:
            patterns = (
                r"(?:show|get|fetch|send)\s+(?:me\s+)?(?:the\s+)?([A-Za-z0-9 _.-]{2,80}?\s+camera)\b",
                r"(?:camera|snapshot|image|photo|picture)\s+(?:called|named|from|of)?\s*([A-Za-z0-9 _.-]{2,80})",
                r"(?:latest\s+)?(?:snapshot|image|photo|picture)\s+(?:from|of)\s+(?:the\s+)?([A-Za-z0-9 _.-]{2,80})",
                r"(?:show|get|fetch|send)\s+(?:me\s+)?(?:the\s+)?([A-Za-z0-9 _.-]{2,80})",
            )
            for pattern in patterns:
                match = re.search(pattern, message, re.IGNORECASE)
                if not match:
                    continue
                camera_name = self._clean_camera_name(match.group(1))
                if camera_name:
                    return camera_name
            return None

    def _clean_camera_name(self, value: str) -> str | None:
            cleaned = value.strip(" .")
            cleaned = re.sub(r"\b(?:please|thanks|thank you)\b.*$", "", cleaned, flags=re.IGNORECASE).strip(" .")
            cleaned = re.sub(r"^(?:latest|current|live)\s+", "", cleaned, flags=re.IGNORECASE).strip(" .")
            cleaned = re.sub(r"^(?:snapshot|image|photo|picture)\s+(?:from|of)\s+", "", cleaned, flags=re.IGNORECASE).strip(" .")
            cleaned = re.sub(r"\s+(?:camera|cam|snapshot|image|photo|picture)$", "", cleaned, flags=re.IGNORECASE).strip(" .")
            if not cleaned:
                return None
            if self._is_non_camera_show_target(cleaned.lower()):
                return None
            return cleaned

    def _is_non_camera_show_target(self, lower: str) -> bool:
            blocked_terms = {
                "schedule",
                "schedules",
                "notification",
                "notifications",
                "workflow",
                "workflows",
                "presence",
                "people",
                "person",
                "users",
                "events",
                "logs",
                "report",
                "reports",
                "settings",
            }
            return any(term in lower.split() for term in blocked_terms)

    def _day_from_message(self, lower: str) -> str:
            if "yesterday" in lower:
                return "yesterday"
            if "today" in lower:
                return "today"
            return "recent"

    def _person_name_from_event_time_message(self, lower: str) -> str | None:
            patterns = [
                r"(?:what time did|when did|did|has|have)\s+([a-z][a-z .'-]{1,40}?)\s+(?:leave|left|exit|exited|arrive|arrived|come|came)\b",
                r"\b([a-z][a-z .'-]{1,40}?)\s+(?:left|exited|arrived|came)\b",
            ]
            for pattern in patterns:
                match = re.search(pattern, lower)
                if not match:
                    continue
                name = re.sub(r"\b(the|a|an|person|user|resident|visitor|contractor)\b", "", match.group(1))
                name = re.sub(r"\s+", " ", name).strip(" ?.")
                if name:
                    return name
            return None

    def _is_vehicle_lookup_request(self, lower: str) -> bool:
            lookup_phrases = [
                "lookup details",
                "look up details",
                "lookup vehicle",
                "look up vehicle",
                "vehicle details",
                "details on",
                "details for",
                "check vehicle",
                "check registration",
                "dvla",
                "vehicle enquiry",
                "mot",
                "tax status",
                "taxed",
            ]
            return any(phrase in lower for phrase in lookup_phrases)

    def _looks_like_leaderboard_request(self, lower: str) -> bool:
            terms = [
                "leaderboard",
                "leader board",
                "top charts",
                "top chart",
                "vip lounge",
                "mystery guests",
                "mystery guest",
                "read count",
                "detectiion",
                "detectiions",
                "detection",
                "detections",
                "most detected",
                "most detections",
                "most reads",
                "top spot",
                "number one",
                "#1",
                "winner",
                "overtake",
                "overtaken",
            ]
            if any(term in lower for term in terms):
                return True
            return bool(
                re.search(r"\b(?:who|what|which)\b.*\b(?:leading|lead|leader|top)\b", lower)
                and re.search(r"\b(?:plate|plates|car|vehicle|vehicles|vip|known|unknown)\b", lower)
            )

    def _leaderboard_limit_from_message(self, lower: str) -> int:
            match = re.search(r"\btop\s+(\d{1,3})\b", lower)
            if match:
                return max(1, min(int(match.group(1)), 100))
            return 25

    def _looks_like_visitor_pass_request(self, lower: str) -> bool:
            return (
                "visitor pass" in lower
                or "guest pass" in lower
                or bool(re.search(r"\b(?:create|make|add|book|set\s*up|setup)\s+(?:a\s+)?pass\b", lower))
                or bool(re.search(r"\bpass\s+(?:for|to)\b", lower))
                or bool(re.search(r"\bpass\b.*\b(?:coming|arriving|visiting|expected|tomorrow|today|tonight)\b", lower))
                or "visitor coming" in lower
                or "guest coming" in lower
                or "visitor arriving" in lower
                or "guest arriving" in lower
                or "expected visitor" in lower
                or "expected guest" in lower
                or bool(re.search(r"\b(visitor|guest)\b.*\b(pass|coming|arriving|expect|expected|cancel|delete|remove|revoke|visit)\b", lower))
            )

    def _looks_like_calendar_integration_request(self, lower: str) -> bool:
            calendar_reference = "icloud" in lower or "calendar" in lower or "calendars" in lower
            sync_reference = any(word in lower for word in ["sync", "check", "scan", "refresh", "update", "pull"])
            gate_reference = any(phrase in lower for phrase in ["open gate", "gate pass", "visitor pass", "passes"])
            return calendar_reference and (sync_reference or gate_reference)

    def _looks_like_visitor_pass_create_request(self, lower: str) -> bool:
            if self._looks_like_visitor_pass_cancel_request(lower):
                return False
            if self._looks_like_visitor_pass_query_request(lower):
                return False
            return self._looks_like_visitor_pass_request(lower) and any(
                phrase in lower
                for phrase in [
                    "coming",
                    "arriving",
                    "expecting",
                    "expect ",
                    "create",
                    "new",
                    "add",
                    "book",
                    "set up",
                    "setup",
                    "make",
                    "coming over",
                ]
            )

    def _looks_like_visitor_pass_cancel_request(self, lower: str) -> bool:
            return self._looks_like_visitor_pass_request(lower) and bool(re.search(r"\b(cancel|delete|remove|revoke)\b", lower))

    def _looks_like_visitor_pass_query_request(self, lower: str) -> bool:
            if not self._looks_like_visitor_pass_request(lower):
                return False
            if re.search(r"\b(?:are there|any|do we have|have we got|show|list|check|find|lookup|what|which)\b", lower):
                return True
            status_query = re.search(
                r"\b(?:visitor passes|guest passes|passes)\b.*\b(?:today|tomorrow|tonight|active|scheduled|setup|set up)\b",
                lower,
            )
            create_marker = re.search(r"\b(?:create|make|add|book|set\s*up|setup|expect|expecting)\b", lower)
            return bool(status_query and not create_marker)

    def _is_pending_visitor_pass_create_cancel_message(self, lower: str) -> bool:
            return bool(re.fullmatch(r"(?:no|cancel|cancel that|cancel this|stop|never mind|nevermind|forget it|abort)", lower))

    def _should_abandon_pending_visitor_pass_create(self, lower: str) -> bool:
            if self._looks_like_visitor_pass_query_request(lower):
                return True
            if self._looks_like_device_action_request(lower) or self._looks_like_device_state_request(lower):
                return True
            if self._looks_like_schedule_create_request(lower) or self._looks_like_calendar_integration_request(lower):
                return True
            return bool(
                re.search(r"\b(?:status|help|show|list|check|what|why|when|where|open|close|shut)\b", lower)
                and not self._looks_like_visitor_pass_create_request(lower)
            )

    def _visitor_name_from_message(self, message: str) -> str | None:
            patterns = [
                r"(?:called|named)\s+([A-Za-z][A-Za-z' -]{1,48})",
                r"(?:visitor|guest)\s+(?:called|named)\s+([A-Za-z][A-Za-z' -]{1,48})",
                r"(?:expecting|expect|having|have)\s+(?:a\s+)?(?:visitor|guest)?\s*(?:called|named)?\s+([A-Za-z][A-Za-z' -]{1,48})",
                r"\b([A-Za-z][A-Za-z' -]{1,48})\s+(?:is\s+|'s\s+)?(?:coming|arriving|visiting)\b",
                r"(?:visitor|guest)\s+([A-Za-z][A-Za-z' -]{1,48})(?:\s+(?:at|on|tomorrow|today|tonight|coming|arriving)\b|$)",
                r"\bfor\s+([A-Za-z][A-Za-z' -]{1,48})(?:\s+(?:at|on|tomorrow|today|tonight)\b|$)",
                r"^([A-Za-z][A-Za-z' -]{1,48})(?:\s+(?:at|on|tomorrow|today|tonight|in)\b|$)",
            ]
            for pattern in patterns:
                match = re.search(pattern, message, flags=re.IGNORECASE)
                if not match:
                    continue
                cleaned = self._clean_visitor_name_candidate(match.group(1))
                if cleaned:
                    return cleaned
            return None

    def _clean_visitor_name_candidate(self, value: str) -> str | None:
            name = re.split(
                r"\b(?:at|around|about|on|today|tomorrow|tonight|this|next|in|from|for|with|coming|arriving|visiting)\b",
                value,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            name = re.sub(r"\b(?:a|an|the|visitor|guest|is|will|be|called|named)\b", " ", name, flags=re.IGNORECASE)
            name = " ".join(name.strip(" .,!?'\"").split())
            if not name or name.lower() in {
                "coming",
                "arriving",
                "visiting",
                "today",
                "tomorrow",
                "tonight",
                "pass",
                "passes",
                "pass setup",
                "passes setup",
                "setup",
                "set up",
            }:
                return None
            return name[:80]

    def _visitor_expected_time_from_message(
            self,
            message: str,
            timezone_name: str = DEFAULT_CHAT_TIMEZONE,
        ) -> str | None:
            text = message.strip()
            iso_match = re.search(r"\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?", text)
            if iso_match:
                raw = iso_match.group(0).replace(" ", "T")
                try:
                    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    parsed = None
                if parsed:
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
                    return parsed.astimezone(ZoneInfo(timezone_name)).isoformat()

            timezone = ZoneInfo(timezone_name)
            now = datetime.now(tz=timezone)
            lower = text.lower()
            relative = re.search(r"\bin\s+(\d{1,3})\s*(minute|minutes|mins|min|hour|hours|hrs|hr)\b", lower)
            if relative:
                amount = int(relative.group(1))
                unit = relative.group(2)
                delta = timedelta(hours=amount) if unit.startswith(("hour", "hr")) else timedelta(minutes=amount)
                return (now + delta).isoformat()

            time_match = re.search(
                r"\b(?:at|around|about|by)?\s*(?:approx(?:imately)?|roughly|circa|about|around)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
                lower,
            )
            if not time_match:
                time_match = re.search(
                    r"\b(?:at|around|about|by)\s*(?:approx(?:imately)?|roughly|circa|about|around)?\s*(\d{1,2}):(\d{2})\b",
                    lower,
                )
            if not time_match:
                if re.search(r"\b(noon|midday)\b", lower):
                    hour, minute = 12, 0
                elif "midnight" in lower:
                    hour, minute = 0, 0
                else:
                    return None
            else:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2) or 0)
                meridiem = time_match.group(3)
                if meridiem == "pm" and hour != 12:
                    hour += 12
                elif meridiem == "am" and hour == 12:
                    hour = 0
                if hour > 23 or minute > 59:
                    return None

            days_offset = 1 if "tomorrow" in lower else 0
            expected = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days_offset)
            if days_offset == 0 and "today" not in lower and expected <= now:
                expected = expected + timedelta(days=1)
            return expected.isoformat()

    def _visitor_window_from_message(self, lower: str) -> int | None:
            match = re.search(r"(?:\+/-|plus/minus|window|within|for)\s*(\d{1,3})\s*(?:minutes|minute|mins|min|m)?", lower)
            if not match:
                return None
            minutes = int(match.group(1))
            if minutes in {30, 60, 90, 120, 180}:
                return minutes
            return max(1, min(minutes, 180))

    def _looks_like_schedule_create_request(self, lower: str) -> bool:
            return "schedule" in lower and any(word in lower for word in ["create", "new", "add", "make"])

    def _looks_like_schedule_delete_request(self, lower: str) -> bool:
            return "schedule" in lower and bool(re.search(r"\b(delete|remove)\b", lower))

    def _is_confirmation_message(self, lower: str) -> bool:
            return bool(re.search(r"\b(yes|confirm|confirmed|update|replace|proceed|go ahead|do it|approved|approve)\b", lower))

    def _is_rejection_message(self, lower: str) -> bool:
            return bool(re.search(r"\b(no|cancel|stop|leave|unchanged|do not|don't)\b", lower))

    def _refers_to_previous_timeframe(self, lower: str) -> bool:
            return any(phrase in lower for phrase in ["already told", "told you", "as i said", "same as before", "previous"])

    def _schedule_name_from_message(self, message: str) -> str | None:
            patterns = [
                r"(?:called|named)\s+['\"]?([A-Za-z0-9 _.-]{2,80})['\"]?",
                r"schedule\s+(?:for\s+)?['\"]?([A-Za-z0-9 _.-]{2,80})['\"]?",
            ]
            for pattern in patterns:
                match = re.search(pattern, message, re.IGNORECASE)
                if not match:
                    continue
                name = self._clean_schedule_name(match.group(1))
                name = re.split(
                    rf"\b(?:{SCHEDULE_DAY_PATTERN}|weekday|weekend|every day|daily|24/7)\b",
                    name,
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )[0].strip()
                if name and name.lower() not in {"a new", "new", "called", "named"}:
                    return name
            return None

    def _schedule_delete_name_from_message(self, message: str) -> str | None:
            patterns = (
                r"(?:delete|remove)\s+(?:the\s+)?schedule\s+(?:called|named)\s+['\"]?([A-Za-z0-9 _.-]{2,80})['\"]?",
                r"(?:delete|remove)\s+(?:the\s+)?schedule\s+['\"]?([A-Za-z0-9 _.-]{2,80})['\"]?",
                r"(?:delete|remove)\s+['\"]?([A-Za-z0-9 _.-]{2,80})['\"]?\s+schedule\b",
            )
            for pattern in patterns:
                match = re.search(pattern, message, re.IGNORECASE)
                if not match:
                    continue
                name = self._clean_schedule_name(match.group(1))
                name = re.sub(r"^(?:called|named)\s+", "", name, flags=re.IGNORECASE).strip()
                if name:
                    return name
            return None

    def _clean_schedule_name(self, message: str) -> str:
            name = re.sub(r"\s+", " ", message.strip(" .?\"'"))
            return name[:120].strip()

    def _parse_schedule_time_blocks(self, message: str) -> dict[str, list[dict[str, str]]] | None:
            lower = message.lower()
            if any(token in lower for token in ["24/7", "24-7", "24 hours", "all day every day"]):
                return {
                    str(day): [{"start": "00:00", "end": "24:00"}]
                    for day in range(7)
                }

            days = self._schedule_days_from_message(lower)
            time_range = self._schedule_time_range_from_message(lower)
            if not days or not time_range:
                return None

            start, end = time_range
            blocks = {str(day): [] for day in range(7)}
            for day in days:
                blocks[str(day)].append({"start": start, "end": end})
            return blocks

    def _schedule_days_from_message(self, lower: str) -> list[int]:
            if any(phrase in lower for phrase in ["weekday", "week day", "workday", "work day"]):
                return list(range(5))
            if any(phrase in lower for phrase in ["weekend", "saturday and sunday", "sat and sun"]):
                return [5, 6]
            if any(phrase in lower for phrase in ["every day", "daily", "all week", "each day", "mon-sun", "monday to sunday"]):
                return list(range(7))

            range_match = re.search(
                rf"\b({SCHEDULE_DAY_PATTERN})\b"
                r"\s*(?:-|to|through|until|thru)\s*"
                rf"\b({SCHEDULE_DAY_PATTERN})\b",
                lower,
            )
            if range_match:
                start = self._schedule_day_index(range_match.group(1))
                end = self._schedule_day_index(range_match.group(2))
                if start is not None and end is not None:
                    if start <= end:
                        return list(range(start, end + 1))
                    return list(range(start, 7)) + list(range(0, end + 1))

            days: list[int] = []
            for token in re.findall(rf"\b({SCHEDULE_DAY_PATTERN})\b", lower):
                day = self._schedule_day_index(token)
                if day is not None and day not in days:
                    days.append(day)
            return days

    def _schedule_day_index(self, value: str) -> int | None:
            normalized = value.lower()[:3]
            return SCHEDULE_DAY_ALIASES.get(value.lower(), SCHEDULE_DAY_ALIASES.get(normalized))

    def _schedule_time_range_from_message(self, lower: str) -> tuple[str, str] | None:
            match = re.search(
                r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|to|until|through|thru)\s*"
                r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
                lower,
            )
            if not match:
                return None

            start = self._schedule_minute_from_parts(match.group(1), match.group(2), match.group(3))
            end = self._schedule_minute_from_parts(match.group(4), match.group(5), match.group(6))
            if start is None or end is None:
                return None
            if end <= start and not match.group(3) and not match.group(6) and int(match.group(4)) <= 12:
                end += 12 * 60
            if start < 0 or end > 24 * 60 or end <= start:
                return None
            if start % 30 or end % 30:
                return None
            return self._format_schedule_minute(start), self._format_schedule_minute(end)

    def _schedule_minute_from_parts(self, hour_text: str, minute_text: str | None, meridiem: str | None) -> int | None:
            hour = int(hour_text)
            minute = int(minute_text or "0")
            if minute not in {0, 30}:
                return None
            if meridiem:
                if hour < 1 or hour > 12:
                    return None
                if meridiem == "am":
                    hour = 0 if hour == 12 else hour
                else:
                    hour = 12 if hour == 12 else hour + 12
            if hour < 0 or hour > 24:
                return None
            return hour * 60 + minute

    def _format_schedule_minute(self, minute: int) -> str:
            if minute == 24 * 60:
                return "24:00"
            return f"{minute // 60:02d}:{minute % 60:02d}"

    def _chat_schedule_summary(self, time_blocks: dict[str, list[dict[str, str]]]) -> str:
            selected_slots = 0
            active_days = 0
            for intervals in time_blocks.values():
                day_slots = 0
                for interval in intervals:
                    start = self._parse_schedule_summary_minute(str(interval["start"]))
                    end = self._parse_schedule_summary_minute(str(interval["end"]))
                    day_slots += max(0, (end - start) // 30)
                if day_slots:
                    active_days += 1
                    selected_slots += day_slots
            if not selected_slots:
                return "no allowed time"
            if selected_slots == 48 * 7:
                return "24/7"
            hours = selected_slots / 2
            display_hours = int(hours) if hours.is_integer() else round(hours, 1)
            return f"{display_hours}h across {active_days} day{'s' if active_days != 1 else ''}"

    def _parse_schedule_summary_minute(self, value: str) -> int:
            if value in {"24:00", "23:59"}:
                return 24 * 60
            hour, minute = value.split(":")
            return int(hour) * 60 + int(minute)

    def _looks_like_device_state_request(self, lower: str) -> bool:
            device_words = ["gate", "door", "garage", "cover"]
            if not any(word in lower for word in device_words):
                return False
            return bool(
                re.search(r"\b(?:is|are|was|were)\b[^?]*\b(?:open|closed|opening|closing|locked|unlocked)\b", lower)
                or re.search(r"\b(?:state|status)\b", lower)
                or re.search(r"\b(?:what(?:'s| is)|check)\b", lower)
            )

    def _looks_like_device_action_request(self, lower: str) -> bool:
            return self._looks_like_device_open_request(lower) or self._looks_like_device_close_request(lower)

    def _looks_like_device_open_request(self, lower: str) -> bool:
            if not any(word in lower for word in ["gate", "garage", "door", "cover"]):
                return False
            if re.search(r"\b(?:is|are|was|were)\b[^?]*\bopen\b", lower):
                return False
            return bool(
                re.search(r"^\s*(?:please\s+)?(?:confirm(?:ed)?\s+)?open\s+(?:the\s+|my\s+)?", lower)
                or re.search(r"\b(?:can|could|would)\s+you\s+open\s+(?:the\s+|my\s+)?", lower)
            )

    def _looks_like_device_close_request(self, lower: str) -> bool:
            if not any(word in lower for word in ["garage", "door", "cover"]):
                return False
            if re.search(r"\b(?:is|are|was|were)\b[^?]*\bclosed?\b", lower):
                return False
            return bool(
                re.search(r"^\s*(?:please\s+)?(?:confirm(?:ed)?\s+)?(?:close|shut)\s+(?:the\s+|my\s+)?", lower)
                or re.search(r"\b(?:can|could|would)\s+you\s+(?:close|shut)\s+(?:the\s+|my\s+)?", lower)
            )

    def _looks_like_access_diagnostic_request(self, lower: str) -> bool:
            diagnostic_terms = [
                "why",
                "why didn't",
                "why didnt",
                "did not",
                "didn't",
                "didnt",
                "slow",
                "slower",
                "longer",
                "latency",
                "timing",
                "took",
                "recognise",
                "recognize",
                "debug",
                "diagnose",
                "diagnostic",
                "notification",
                "notify",
                "notified",
                "alert",
                "failed",
                "failure",
                "problem",
                "issue",
                "reason",
                "cause",
                "explain",
            ]
            access_terms = [
                "lpr",
                "number plate",
                "numberplate",
                "plate",
                "scan",
                "read",
                "recognition",
                "process",
                "processing",
                "detection",
                "detected",
                "arrival",
                "arrivals",
                "entry",
                "entries",
                "event",
                "gate open",
                "gate",
                "vehicle",
                "car",
                "unknown",
                "stranger",
                "visitor",
                "access event",
                "access log",
            ]
            return any(term in lower for term in diagnostic_terms) and any(
                term in lower for term in access_terms
            )

    def _looks_like_missing_access_incident(self, lower: str) -> bool:
            phrases = [
                "nothing logged",
                "nothing was logged",
                "not logged",
                "didn't log",
                "didnt log",
                "did not log",
                "wasn't logged",
                "wasnt logged",
                "no event",
                "no access event",
                "no log",
                "missing event",
                "not recorded",
                "wasn't recorded",
                "wasnt recorded",
                "no notification",
                "not notified",
                "wasn't let in",
                "wasnt let in",
                "not let in",
                "didn't get in",
                "didnt get in",
                "why wasn't",
                "why wasnt",
            ]
            if any(phrase in lower for phrase in phrases):
                return any(
                    term in lower
                    for term in ["left", "leave", "exit", "arrived", "arrival", "came", "return", "returned", "back", "entry", "gate", "lpr", "plate", "car", "vehicle", "notification", "logged", "recorded"]
                )
            return False

    def _latest_diagnostic_result_not_found(self, tool_results: list[dict[str, Any]]) -> bool:
            for result in reversed(tool_results):
                if result.get("name") != "diagnose_access_event":
                    continue
                output = result.get("output")
                return isinstance(output, dict) and not bool(output.get("found"))
            return False

    def _looks_like_vehicle_detection_count_request(self, lower: str) -> bool:
            if not any(phrase in lower for phrase in ["how many times", "how often", "count"]):
                return False
            return any(word in lower for word in ["car", "vehicle", "plate", "gate", "detected", "detection"])

    def _looks_like_lpr_timing_request(self, lower: str) -> bool:
            return any(
                term in lower
                for term in [
                    "lpr",
                    "plate",
                    "number plate",
                    "scan",
                    "recognise",
                    "recognize",
                    "recognition",
                    "process",
                    "processing",
                    "slow",
                    "slower",
                    "longer",
                    "latency",
                    "timing",
                    "took",
                    "ms",
                    "millisecond",
                    "milliseconds",
                ]
            )

    def _refers_to_latest_unknown_vehicle(self, lower: str) -> bool:
            return any(
                phrase in lower
                for phrase in [
                    "unknown",
                    "mystery",
                    "stranger",
                    "that car",
                    "that vehicle",
                    "that plate",
                    "the car",
                    "the vehicle",
                    "last car",
                    "latest car",
                    "last vehicle",
                    "latest vehicle",
                    "last detection",
                    "latest detection",
                ]
            )

    def _access_diagnostic_args_from_message(
            self,
            message: str,
            memory: dict[str, Any],
            *,
            actor_context: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            lower = message.lower()
            args: dict[str, Any] = {"day": self._day_from_message(lower)}
            actor_subject = self._actor_subject_from_message(lower, actor_context or {})
            args.update(self._subject_args(actor_subject))
            registration_number = self._registration_from_message(message)
            if registration_number:
                args["registration_number"] = registration_number
            person_name = self._person_name_from_diagnostic_message(lower)
            if person_name:
                args["person"] = person_name
            elif any(token in lower.split() for token in ["they", "them", "he", "she", "their"]) and memory.get("last_person"):
                args["person"] = memory["last_person"]
            if any(word in lower for word in ["unknown", "mystery", "stranger", "unauthorized", "unauthorised"]):
                args["unknown_only"] = True
                args["decision"] = "denied"
            if any(word in lower for word in ["exit", "exited", "leave", "left", "leaving"]):
                args["direction"] = "exit"
            elif any(word in lower for word in ["entry", "entries", "enter", "arrival", "arrivals", "arrive", "arrived", "arriving", "came", "come", "returned", "return", "back"]):
                args["direction"] = "entry"
            return args

    def _access_incident_args_from_message(
            self,
            message: str,
            memory: dict[str, Any],
            *,
            actor_context: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            lower = message.lower()
            args = self._access_diagnostic_args_from_message(message, memory, actor_context=actor_context)
            args.pop("span_limit", None)
            args.pop("summarize_payload", None)
            args["incident_type"] = self._incident_type_from_message(lower)
            if not any(key in args for key in ("person", "person_id", "vehicle_id", "registration_number")):
                person_name = self._person_name_from_event_time_message(lower)
                if person_name:
                    args["person"] = person_name
            expected_time = self._expected_time_from_message(lower)
            if expected_time:
                args["expected_time"] = expected_time
                args["window_minutes"] = 20
            if "this morning" in lower or "today" in lower:
                args["day"] = "today"
            if "yesterday" in lower:
                args["day"] = "yesterday"
            return args

    def _incident_type_from_message(self, lower: str) -> str:
            if any(phrase in lower for phrase in ["nothing logged", "nothing was logged", "not logged", "didn't log", "didnt log", "no event", "missing event", "not recorded"]):
                return "missing_event"
            if "notification" in lower or "notified" in lower or "notify" in lower:
                return "notification_failure"
            if "garage" in lower:
                return "garage_failure"
            if "schedule" in lower or "denied" in lower or "outside" in lower:
                return "schedule_denial"
            if "gate" in lower and any(word in lower for word in ["open", "failed", "failure", "didn't", "didnt"]):
                return "gate_failure"
            return "auto"

    def _expected_time_from_message(self, lower: str) -> str | None:
            match = re.search(r"\b(?:at|around|about|by)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", lower)
            if not match:
                match = re.search(r"\b(\d{1,2}:\d{2}\s*(?:am|pm)?)\b", lower)
            if not match:
                return None
            return re.sub(r"\s+", "", match.group(1))

    def _person_name_from_diagnostic_message(self, lower: str) -> str | None:
            patterns = [
                r"\b(?:why\s+(?:did|does)\s+)?([a-z][a-z .'-]{1,40}?)(?:'s|s)\s+(?:latest|last)\s+(?:lpr|plate|detection|event|arrival|entry|scan|read)\b",
                r"\bwhy\s+(?:didn'?t|did not)\s+(?:the\s+)?gate\s+open\s+for\s+([a-z][a-z .'-]{1,40})",
                r"\bfor\s+([a-z][a-z .'-]{1,40})\b",
                r"\b([a-z][a-z .'-]{1,40}?)(?:'s|s)\s+(?:latest|last)\s+(?:lpr|plate|detection|event|arrival|entry|scan|read)\b",
            ]
            for pattern in patterns:
                match = re.search(pattern, lower)
                if not match:
                    continue
                name = re.sub(
                    r"\b(why|did|does|the|a|an|latest|last|unknown|vehicle|car|gate|notification|detection|plate|lpr)\b",
                    "",
                    match.group(1),
                )
                name = re.sub(r"\s+", " ", name).strip(" ?.'")
                if name:
                    return name
            return None

    def _looks_like_access_event_time_request(self, lower: str) -> bool:
            return bool(
                re.search(
                    r"\b(?:what time did|when did|did|has|have)\s+[a-z][a-z .'-]{1,40}?\s+"
                    r"(?:leave|left|exit|exited|arrive|arrived|come|came)\b",
                    lower,
                )
                or re.search(r"\b[a-z][a-z .'-]{1,40}?\s+(?:left|exited|arrived|came)\b", lower)
            )

    def _looks_like_camera_snapshot_request(self, lower: str) -> bool:
            if any(word in lower for word in ["analyze", "analyse", "describe", "visible", "see if", "look for"]):
                return False
            if any(word in lower for word in ["camera", "snapshot", "image", "photo", "picture"]):
                return bool(re.search(r"\b(?:show|get|fetch|send|latest)\b", lower))
            if not re.search(r"\b(?:show|get|fetch|send)\s+(?:me\s+)?(?:the\s+)?[a-z0-9 _.-]{2,80}\b", lower):
                return False
            camera_name = self._camera_name_from_message(lower)
            if not camera_name:
                return False
            camera_like_terms = {
                "back",
                "front",
                "side",
                "garden",
                "drive",
                "driveway",
                "yard",
                "patio",
                "gate",
                "garage",
                "entrance",
                "door",
                "parking",
                "courtyard",
            }
            return bool(camera_like_terms.intersection(camera_name.lower().split()))

    def _explicitly_confirmed_device_open(self, lower: str) -> bool:
            return self._explicitly_confirmed_device_action(lower)

    def _explicitly_confirmed_device_action(self, lower: str) -> bool:
            return bool(
                re.search(r"\b(confirm|confirmed|authorise|authorize|approved|yes)\b", lower)
                and re.search(r"\b(open|close|shut)\b", lower)
            )

    def _device_action_from_message(self, lower: str) -> str:
            return "close" if self._looks_like_device_close_request(lower) else "open"

    def _device_target_from_message(self, lower: str) -> str | None:
            patterns = [
                r"(?:confirm|confirmed|authorise|authorize|approved|yes,?\s*)?\s*(?:open|close|shut)\s+(?:the\s+|my\s+)?([a-z0-9 _.-]*?(?:gate|door|garage)[a-z0-9 _.-]*?)(?:\s+please|\.|\?|$)",
                r"(?:state|status)\s+(?:of|for)\s+(?:the\s+)?([a-z0-9 _.-]{2,80})",
                r"(?:is|are|check)\s+(?:the\s+)?([a-z0-9 _.-]*?(?:gate|door|garage)[a-z0-9 _.-]*?)(?:\s+(?:open|closed|opening|closing|locked|unlocked)|\?|$)",
                r"(?:what(?:'s| is))\s+(?:the\s+)?([a-z0-9 _.-]*?(?:gate|door|garage)[a-z0-9 _.-]*?)(?:\s+(?:state|status|doing)|\?|$)",
            ]
            for pattern in patterns:
                match = re.search(pattern, lower)
                if match:
                    target = re.sub(
                        r"\b(open|close|shut|closed|opening|closing|locked|unlocked|state|status|doing|please)\b",
                        "",
                        match.group(1),
                    )
                    return re.sub(r"\s+", " ", target).strip(" ?.") or None
            return None
