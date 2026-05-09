"""Notification Alfred tool handlers."""

from __future__ import annotations

from app.ai.tool_groups._facade_handlers import facade_handler

query_notification_catalog = facade_handler("query_notification_catalog")
query_notification_workflows = facade_handler("query_notification_workflows")
get_notification_workflow = facade_handler("get_notification_workflow")
create_notification_workflow = facade_handler("create_notification_workflow")
update_notification_workflow = facade_handler("update_notification_workflow")
delete_notification_workflow = facade_handler("delete_notification_workflow")
preview_notification_workflow = facade_handler("preview_notification_workflow")
test_notification_workflow = facade_handler("test_notification_workflow")
