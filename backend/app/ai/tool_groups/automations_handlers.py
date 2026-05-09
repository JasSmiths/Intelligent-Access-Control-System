"""Automation Alfred tool handlers."""

from __future__ import annotations

from app.ai.tool_groups._facade_handlers import facade_handler

query_automation_catalog = facade_handler("query_automation_catalog")
query_automations = facade_handler("query_automations")
get_automation = facade_handler("get_automation")
create_automation = facade_handler("create_automation")
edit_automation = facade_handler("edit_automation")
delete_automation = facade_handler("delete_automation")
enable_automation = facade_handler("enable_automation")
disable_automation = facade_handler("disable_automation")
