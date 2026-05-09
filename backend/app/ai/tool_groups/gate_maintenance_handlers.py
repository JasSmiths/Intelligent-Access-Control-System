"""Gate and maintenance Alfred tool handlers."""

from __future__ import annotations

from app.ai.tool_groups._facade_handlers import facade_handler

query_device_states = facade_handler("query_device_states")
get_maintenance_status = facade_handler("get_maintenance_status")
get_active_malfunctions = facade_handler("get_active_malfunctions")
get_malfunction_history = facade_handler("get_malfunction_history")
trigger_manual_malfunction_override = facade_handler("trigger_manual_malfunction_override")
enable_maintenance_mode = facade_handler("enable_maintenance_mode")
disable_maintenance_mode = facade_handler("disable_maintenance_mode")
open_device = facade_handler("open_device")
open_gate = facade_handler("open_gate")
toggle_maintenance_mode = facade_handler("toggle_maintenance_mode")
