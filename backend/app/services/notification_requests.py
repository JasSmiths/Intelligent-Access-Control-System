"""Authority and configuration binding for explicitly confirmed notification work.

The notification store remains the only executor. These checks run inside its
attempt transaction; no provider is called here and credentials are never saved.
"""

import json
import uuid
from dataclasses import asdict

from sqlalchemy import select

from app.models import ActionConfirmation, AlfredApproval, MaintenanceModeState
from app.services.action_confirmations import confirmation_token_hash
from app.services.mutation_context import MutationError, load_active_admin


CONFIG_KEYS = {
    "voice": ("home_assistant_url", "home_assistant_token", "home_assistant_tts_service",
              "home_assistant_default_media_player"),
    "mobile": ("home_assistant_url", "home_assistant_token", "apprise_urls"),
    "whatsapp": ("whatsapp_enabled", "whatsapp_access_token", "whatsapp_phone_number_id",
                 "whatsapp_graph_api_version", "whatsapp_visitor_pass_template_name",
                 "whatsapp_visitor_pass_template_language"),
    "discord": ("discord_bot_token", "discord_channel_allowlist", "discord_guild_allowlist",
                "discord_default_notification_channel_id"),
    "in_app": (),
}


def configuration_binding(config, plan) -> str:
    keys = set()
    for item in plan:
        action = item.get("action")
        if action is not None:
            kind = action.get("type")
            if kind not in CONFIG_KEYS:
                raise ValueError("Unsupported confirmed notification channel")
            keys.update(CONFIG_KEYS[kind])
    values = {key: getattr(config, key) for key in sorted(keys)}
    return confirmation_token_hash(json.dumps(values, sort_keys=True, separators=(",", ":")))


def ephemeral_configuration_binding(config) -> str:
    return confirmation_token_hash(json.dumps(asdict(config), sort_keys=True, separators=(",", ":")))


async def confirmed_origin(session, *, actor_user_id, auth_version, operation_id, authority, action):
    user = await load_active_admin(session, actor_user_id, auth_version=auth_version, lock=True)
    operation = uuid.UUID(str(operation_id))
    if authority == "api":
        approval = await session.get(ActionConfirmation, operation)
        valid = (approval is not None and approval.actor_user_id == user.id
                 and approval.action == action and approval.outcome == "consumed"
                 and approval.consumed_at is not None)
    elif authority == "alfred":
        approval = await session.scalar(select(AlfredApproval).where(AlfredApproval.operation_id == operation))
        valid = (approval is not None and approval.requester_user_id == user.id
                 and approval.requester_auth_session_version == user.auth_session_version
                 and approval.status == "claimed" and approval.claimed_at is not None)
        if action == "notification_rule.test":
            valid = valid and approval.payload.get("tool_name") == "test_notification_workflow"
    else:
        valid = False
    if not valid:
        raise MutationError("confirmation_required", "A claimed requester-bound approval is required.")
    return user, {"user_id": str(user.id), "auth_version": user.auth_session_version,
                  "operation_id": str(operation), "authority": authority, "action": action}


async def confirmed_attempt_denial(session, payload, run_id, *, plan, runtime_config,
                                   ephemeral_config=None) -> str | None:
    origin = payload.get("confirmed_delivery")
    if origin is None:
        return None
    if not isinstance(origin, dict):
        return "confirmed_delivery_invalid"
    if type(origin.get("auth_version")) is not int:
        return "confirmed_delivery_invalid"
    try:
        operation = uuid.UUID(str(origin["operation_id"]))
        if uuid.uuid5(operation, "notification-delivery") != run_id:
            return "confirmed_delivery_identity_mismatch"
        await load_active_admin(session, origin["user_id"], auth_version=origin["auth_version"], lock=True)
        if origin.get("action") == "announcement.say":
            maintenance = await session.get(MaintenanceModeState, 1, populate_existing=True)
            if maintenance is not None and maintenance.is_active:
                return "maintenance_mode_active"
        if origin.get("ephemeral_config"):
            if ephemeral_config is None:
                return "ephemeral_configuration_unavailable"
            if ephemeral_configuration_binding(ephemeral_config) != origin.get("configuration_binding"):
                return "notification_configuration_changed"
        elif configuration_binding(runtime_config, plan) != origin.get("configuration_binding"):
            return "notification_configuration_changed"
    except (MutationError, ValueError, TypeError, KeyError, AttributeError):
        return "confirmed_actor_no_longer_authorized"
    return None
