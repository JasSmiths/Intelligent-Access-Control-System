"""Access diagnostics Alfred tool handlers."""

from __future__ import annotations

from app.ai.tool_groups._facade_handlers import facade_handler

query_access_events = facade_handler("query_access_events")
diagnose_access_event = facade_handler("diagnose_access_event")
investigate_access_incident = facade_handler("investigate_access_incident")
query_unifi_protect_events = facade_handler("query_unifi_protect_events")
backfill_access_event_from_protect = facade_handler("backfill_access_event_from_protect")
test_unifi_alarm_webhook = facade_handler("test_unifi_alarm_webhook")
query_lpr_timing = facade_handler("query_lpr_timing")
query_vehicle_detection_history = facade_handler("query_vehicle_detection_history")
get_telemetry_trace = facade_handler("get_telemetry_trace")
query_leaderboard = facade_handler("query_leaderboard")
query_anomalies = facade_handler("query_anomalies")
query_alert_activity = facade_handler("query_alert_activity")
analyze_alert_snapshot = facade_handler("analyze_alert_snapshot")
summarize_access_rhythm = facade_handler("summarize_access_rhythm")
calculate_visit_duration = facade_handler("calculate_visit_duration")
calculate_absence_duration = facade_handler("calculate_absence_duration")
trigger_anomaly_alert = facade_handler("trigger_anomaly_alert")
