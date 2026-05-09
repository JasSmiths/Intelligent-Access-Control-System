"""Visitor Pass Alfred tool handlers."""

from __future__ import annotations

from app.ai.tool_groups._facade_handlers import facade_handler

query_visitor_passes = facade_handler("query_visitor_passes")
get_visitor_pass = facade_handler("get_visitor_pass")
create_visitor_pass = facade_handler("create_visitor_pass")
update_visitor_pass = facade_handler("update_visitor_pass")
cancel_visitor_pass = facade_handler("cancel_visitor_pass")
trigger_icloud_sync = facade_handler("trigger_icloud_sync")
