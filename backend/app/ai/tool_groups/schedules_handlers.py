"""Schedule Alfred tool handlers."""

from __future__ import annotations

from app.ai.tool_groups._facade_handlers import facade_handler

override_schedule = facade_handler("override_schedule")
query_schedules = facade_handler("query_schedules")
get_schedule = facade_handler("get_schedule")
create_schedule = facade_handler("create_schedule")
update_schedule = facade_handler("update_schedule")
delete_schedule = facade_handler("delete_schedule")
query_schedule_targets = facade_handler("query_schedule_targets")
assign_schedule_to_entity = facade_handler("assign_schedule_to_entity")
verify_schedule_access = facade_handler("verify_schedule_access")
