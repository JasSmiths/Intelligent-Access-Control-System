"""System operations Alfred tool handlers."""

from __future__ import annotations

from app.ai.tool_groups._facade_handlers import facade_handler

query_integration_health = facade_handler("query_integration_health")
test_integration_connection = facade_handler("test_integration_connection")
query_system_settings = facade_handler("query_system_settings")
update_system_settings = facade_handler("update_system_settings")
query_auth_secret_status = facade_handler("query_auth_secret_status")
rotate_auth_secret_tool = facade_handler("rotate_auth_secret_tool")
query_alfred_runtime_events = facade_handler("query_alfred_runtime_events")
query_dependency_updates = facade_handler("query_dependency_updates")
check_dependency_updates = facade_handler("check_dependency_updates")
analyze_dependency_update = facade_handler("analyze_dependency_update")
apply_dependency_update = facade_handler("apply_dependency_update")
query_dependency_backups = facade_handler("query_dependency_backups")
restore_dependency_backup = facade_handler("restore_dependency_backup")
query_dependency_update_job = facade_handler("query_dependency_update_job")
configure_dependency_backup_storage = facade_handler("configure_dependency_backup_storage")
validate_dependency_backup_storage = facade_handler("validate_dependency_backup_storage")
