"""System operations Alfred tool handlers."""
# ruff: noqa: F403, F405

from __future__ import annotations

from typing import Any

from app.ai.tool_groups._shared import *


async def query_integration_health(arguments: dict[str, Any]) -> dict[str, Any]:
    runtime = await get_runtime_config()
    requested = str(arguments.get("integration") or "all").strip().lower()
    health = {
        "home_assistant": await get_home_assistant_service().status(refresh=False),
        "access_events": get_access_event_service().status(),
        "unifi_protect": await get_unifi_protect_service().status(refresh=False),
        "discord": await get_discord_messaging_service().status(),
        "whatsapp": await get_whatsapp_messaging_service().status(),
        "dvla": {"configured": bool(runtime.dvla_api_key), "endpoint": runtime.dvla_vehicle_enquiry_url},
        "llm": {
            "provider": runtime.llm_provider,
            "openai_configured": bool(runtime.openai_api_key),
            "gemini_configured": bool(runtime.gemini_api_key),
            "anthropic_configured": bool(runtime.anthropic_api_key),
            "ollama_configured": bool(runtime.ollama_base_url),
        },
        "dependency_updates": {
            "backup_storage": await get_dependency_update_service().storage_status(),
        },
    }
    if requested and requested != "all":
        return {"integration": requested, "health": health.get(requested, {"error": "Unknown integration."})}
    return {"integrations": health}


async def test_integration_connection(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("integration connection tests")
    if isinstance(admin, dict):
        return admin
    integration = str(arguments.get("integration") or "").strip().lower()
    if not integration:
        return {"tested": False, "error": "integration is required."}
    if not bool(arguments.get("confirm")):
        return {
            "tested": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": f"{integration} connection",
            "detail": "Testing integrations can contact external providers. Confirm before Alfred runs the test.",
        }
    try:
        if integration == "home_assistant":
            result = await get_home_assistant_service().status(refresh=True)
        elif integration == "unifi_protect":
            result = await get_unifi_protect_service().status(refresh=True)
        elif integration == "discord":
            await get_discord_messaging_service().test_connection({})
            result = {"ok": True}
        elif integration == "whatsapp":
            await get_whatsapp_messaging_service().test_connection({})
            result = {"ok": True}
        elif integration == "apprise":
            runtime = await get_runtime_config()
            result = {"configured": bool(runtime.apprise_urls)}
        elif integration == "dvla":
            runtime = await get_runtime_config()
            result = {"configured": bool(runtime.dvla_api_key), "endpoint": runtime.dvla_vehicle_enquiry_url}
        else:
            return {"tested": False, "integration": integration, "error": "Unknown integration."}
    except Exception as exc:
        return {"tested": True, "integration": integration, "ok": False, "error": str(exc)[:500]}
    return {"tested": True, "integration": integration, "ok": not bool(result.get("error")), "result": result}


async def query_system_settings(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("system setting reads")
    if isinstance(admin, dict):
        return admin
    category = str(arguments.get("category") or "").strip() or None
    rows = await list_settings(category=category)
    return {"settings": rows, "category": category, "redacted": True}


async def update_system_settings(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("system setting updates")
    if isinstance(admin, dict):
        return admin
    values = arguments.get("values")
    if not isinstance(values, dict) or not values:
        return {"updated": False, "error": "values must be a non-empty object."}
    if not bool(arguments.get("confirm")):
        return {
            "updated": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "System Settings",
            "detail": f"Update {len(values)} setting(s)? Secrets stay redacted, but this can change live IACS behavior.",
            "setting_keys": sorted(str(key) for key in values.keys()),
        }
    try:
        rows = await update_settings(values)
    except UnknownDynamicSettingsError as exc:
        return {
            "updated": False,
            "error": str(exc),
            "unknown_keys": exc.unknown_keys,
            "allowed_keys": exc.allowed_keys,
        }
    changed_keys = sorted(str(key) for key in values.keys())
    return {
        "updated": True,
        "changed_keys": changed_keys,
        "settings": [row for row in rows if row.get("key") in changed_keys],
        "redacted": True,
    }


async def query_auth_secret_status(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("auth-secret status")
    if isinstance(admin, dict):
        return admin
    return await auth_secret_security_status()


async def rotate_auth_secret_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("auth-secret rotation")
    if isinstance(admin, dict):
        return admin
    if arguments.get("new_secret"):
        return {"rotated": False, "error": "Alfred only supports generated auth-secret rotation."}
    if not bool(arguments.get("confirm")):
        return {
            "rotated": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Auth Secret",
            "detail": (
                "Rotate the auth root secret? This invalidates existing login sessions and pending action links, "
                "then re-encrypts dynamic secrets."
            ),
        }
    try:
        return await rotate_auth_secret(user=admin, confirmed=True, new_secret=None)
    except AuthSecretRotationError as exc:
        return {"rotated": False, "error": str(exc)}


async def query_alfred_runtime_events(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("Alfred runtime diagnostics")
    if isinstance(admin, dict):
        return admin
    await telemetry.flush()
    config = await get_runtime_config()
    limit = _bounded_int(arguments.get("limit"), default=20, minimum=1, maximum=100)
    hours = _bounded_int(arguments.get("hours"), default=24, minimum=1, maximum=168)
    since = datetime.now(tz=UTC) - timedelta(hours=hours)
    actions = {
        "alfred.chat.http_error",
        "alfred.chat.http_confirm_error",
        "alfred.chat.sse_error",
        "alfred.chat.websocket_error",
        "alfred.chat.websocket_receive_error",
    }
    async with AsyncSessionLocal() as session:
        rows = (
            await session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.category == TELEMETRY_CATEGORY_ALFRED,
                    AuditLog.action.in_(actions),
                    AuditLog.timestamp >= since,
                )
                .order_by(AuditLog.timestamp.desc())
                .limit(limit)
            )
        ).all()
    events = [
        {
            "id": str(row.id),
            "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            "action": row.action,
            "channel": (row.metadata_ or {}).get("channel"),
            "error_type": (row.metadata_ or {}).get("error_type"),
            "message_preview": (row.metadata_ or {}).get("message_preview"),
            "session_id": (row.metadata_ or {}).get("session_id") or row.target_id,
            "provider": (row.metadata_ or {}).get("provider"),
            "outcome": row.outcome,
            "level": row.level,
        }
        for row in rows
    ]
    return {
        "events": events,
        "count": len(events),
        "has_recent_failures": bool(events),
        "window_hours": hours,
        "timezone": config.site_timezone,
        "redacted": True,
    }


async def query_dependency_updates(arguments: dict[str, Any]) -> dict[str, Any]:
    update_only = bool(arguments.get("update_only"))
    packages = await get_dependency_update_service().list_packages(update_only=update_only)
    return {"packages": packages, "count": len(packages), "update_only": update_only}


async def check_dependency_updates(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("dependency update checks")
    if isinstance(admin, dict):
        return admin
    if not bool(arguments.get("confirm")):
        return {
            "checked": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Dependency Updates",
            "detail": "Check configured dependencies against package registries?",
        }
    try:
        return await get_dependency_update_service().check_all_packages(
            direct_only=bool(arguments.get("direct_only")),
            user=admin,
            source="alfred",
        )
    except DependencyUpdateError as exc:
        return {"checked": False, "error": str(exc)}


async def analyze_dependency_update(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("dependency update analysis")
    if isinstance(admin, dict):
        return admin
    dependency_id = _uuid_from_value(arguments.get("dependency_id"))
    if not dependency_id:
        return {"analyzed": False, "error": "dependency_id is required."}
    if not bool(arguments.get("confirm")):
        return {
            "analyzed": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Dependency Analysis",
            "detail": "Analyze this dependency update using release metadata and the configured LLM provider?",
        }
    try:
        return await get_dependency_update_service().analyze_package(
            dependency_id,
            target_version=str(arguments.get("target_version") or "") or None,
            provider=str(arguments.get("provider") or "") or None,
            user=admin,
        )
    except DependencyUpdateError as exc:
        return {"analyzed": False, "error": str(exc)}


async def apply_dependency_update(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("dependency update apply jobs")
    if isinstance(admin, dict):
        return admin
    dependency_id = _uuid_from_value(arguments.get("dependency_id"))
    if not dependency_id:
        return {"started": False, "error": "dependency_id is required."}
    if not bool(arguments.get("confirm")):
        return {
            "started": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Dependency Apply Job",
            "detail": "Apply this dependency update? Alfred will use the existing backup and job pipeline, not shell commands.",
        }
    try:
        return await get_dependency_update_service().start_apply_job(
            dependency_id,
            target_version=str(arguments.get("target_version") or "") or None,
            confirmed=True,
            user=admin,
        )
    except DependencyUpdateError as exc:
        return {"started": False, "error": str(exc)}


async def query_dependency_backups(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("dependency backup reads")
    if isinstance(admin, dict):
        return admin
    dependency_id = _uuid_from_value(arguments.get("dependency_id"))
    backups = await get_dependency_update_service().list_backups(dependency_id)
    return {"backups": backups, "count": len(backups)}


async def restore_dependency_backup(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("dependency backup restore jobs")
    if isinstance(admin, dict):
        return admin
    backup_id = _uuid_from_value(arguments.get("backup_id"))
    if not backup_id:
        return {"started": False, "error": "backup_id is required."}
    if not bool(arguments.get("confirm")):
        return {
            "started": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Dependency Restore Job",
            "detail": "Restore this dependency backup? This uses the existing restore job pipeline.",
        }
    try:
        return await get_dependency_update_service().start_restore_job(backup_id, confirmed=True, user=admin)
    except DependencyUpdateError as exc:
        return {"started": False, "error": str(exc)}


async def query_dependency_update_job(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("dependency job reads")
    if isinstance(admin, dict):
        return admin
    job_id = _uuid_from_value(arguments.get("job_id"))
    if not job_id:
        return {"found": False, "error": "job_id is required."}
    try:
        return await get_dependency_update_service().job_status(job_id)
    except DependencyUpdateError as exc:
        return {"found": False, "error": str(exc)}


async def configure_dependency_backup_storage(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("dependency backup storage configuration")
    if isinstance(admin, dict):
        return admin
    payload = {
        "mode": arguments.get("mode"),
        "mount_source": arguments.get("mount_source"),
        "retention_days": arguments.get("retention_days"),
        "min_free_bytes": arguments.get("min_free_bytes"),
    }
    if "mount_options" in arguments:
        payload["mount_options"] = arguments.get("mount_options")
    if not bool(arguments.get("confirm")):
        return {
            "configured": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Dependency Backup Storage",
            "detail": "Update dependency backup storage? Sensitive mount options stay redacted.",
            "mount_options_configured": bool(arguments.get("mount_options")),
        }
    try:
        return await get_dependency_update_service().save_storage_config(payload, user=admin)
    except DependencyUpdateError as exc:
        return {"configured": False, "error": str(exc)}


async def validate_dependency_backup_storage(arguments: dict[str, Any]) -> dict[str, Any]:
    admin = await _require_admin_user("dependency backup storage validation")
    if isinstance(admin, dict):
        return admin
    if not bool(arguments.get("confirm")):
        return {
            "validated": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Dependency Backup Storage",
            "detail": "Validate backup storage writability and free space?",
        }
    return await get_dependency_update_service().validate_storage()
