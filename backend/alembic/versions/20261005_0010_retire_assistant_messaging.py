"""Retire assistant/messaging state after a matching database/file backup.

Revision ID: 20261005_0010
Revises: 20261002_0009

This migration performs no provider I/O. Old workers must be stopped before
upgrade. Restore the matching backup and image pair to recover retired data.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from alembic import op
from croniter import croniter
import sqlalchemy as sa

revision = "20261005_0010"
down_revision = "20261002_0009"
branch_labels = None
depends_on = None

RETIRED_TYPES = {
    "discord", "whatsapp", "integration.whatsapp.send_message",
    "ai.phrase_received", "ai.issue_detected", "automation.whatsapp",
    "visitor_pass_arranged", "visitor_pass_timeframe_change_requested",
    "visitor_pass.arranged", "visitor_pass.timeframe_change_requested", "agent_anomaly_alert",
}
RETIRED_VARIABLES = {
    "alfredphrase", "alfredissue", "visitorpasscurrentwindow",
    "visitorpassrequestedwindow", "visitorpassoriginaltime",
    "visitorpassrequestedtime", "visitorpassvisitormessage", "visitormessage",
}
RETIRED_CONTEXTS = {"visitor_conversation_origin", "ephemeral_config", "visitor_origin"}
RETIRED_TABLES = (
    "alfred_eval_examples", "alfred_feedback", "alfred_lessons",
    "alfred_memories", "chat_messages", "alfred_approvals", "chat_sessions",
    "processed_messaging_messages", "messaging_identities",
)


def contains_retired(value: Any) -> bool:
    if isinstance(value, dict):
        if RETIRED_CONTEXTS.intersection(value):
            return True
        if value.get("authority") == "alfred":
            return True
        return any(contains_retired(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_retired(item) for item in value)
    return isinstance(value, str) and (
        value in RETIRED_TYPES or "@alfred" in value.lower()
        or any("@" + name in value.lower() for name in RETIRED_VARIABLES)
    )


def _audit(bind, entity: str, ident: Any, *, removed: bool, converted: bool = False) -> None:
    bind.execute(sa.text("""
        INSERT INTO audit_logs
          (id, timestamp, category, action, actor, target_entity, target_id,
           diff, metadata, outcome, level)
        VALUES (:id, now(), 'entity_management', 'system.feature_retirement',
          'schema_retirement', :entity, :ident, CAST(:diff AS jsonb),
          CAST(:metadata AS jsonb), 'success', 'info')
    """), {
        "id": uuid.uuid4(), "entity": entity, "ident": str(ident),
        "diff": json.dumps({"removed": removed, "disabled": not removed and not converted, "schedule_converted": converted}),
        "metadata": json.dumps({"reason": "assistant_messaging_retired"}),
    })


def _retire_rules(bind) -> set[str]:
    affected: set[str] = set()
    for row in bind.execute(sa.text("SELECT id, trigger_event, conditions, actions FROM notification_rules")).mappings():
        trigger_retired = row["trigger_event"] in RETIRED_TYPES
        conditions = [x for x in row["conditions"] if not contains_retired(x)]
        actions = [x for x in row["actions"] if not contains_retired(x)]
        changed = trigger_retired or conditions != row["conditions"] or actions != row["actions"]
        if not changed:
            continue
        affected.add(str(row["id"]))
        removed = trigger_retired or not actions
        if removed:
            bind.execute(sa.text("DELETE FROM notification_rules WHERE id=:id"), {"id": row["id"]})
        else:
            bind.execute(sa.text("""UPDATE notification_rules SET is_active=false,
                conditions=CAST(:conditions AS jsonb), actions=CAST(:actions AS jsonb),
                updated_at=now() WHERE id=:id"""), {
                    "id": row["id"], "conditions": json.dumps(conditions), "actions": json.dumps(actions),
                })
        _audit(bind, "NotificationRule", row["id"], removed=removed)

    for row in bind.execute(sa.text("SELECT id, triggers, conditions, actions FROM automation_rules")).mappings():
        changed = False
        triggers = []
        for node in row["triggers"]:
            node = dict(node)
            if node.get("type") == "time.ai_text":
                config = dict(node.get("config") or {})
                expression = config.get("cron_expression")
                if not expression or not croniter.is_valid(expression):
                    changed = True
                    continue
                node["type"] = "time.cron"
                config.pop("natural_text", None)
                config.pop("summary", None)
                node["config"] = config
            if contains_retired(node):
                changed = True
                continue
            triggers.append(node)
        conditions = [x for x in row["conditions"] if not contains_retired(x)]
        actions = [x for x in row["actions"] if not contains_retired(x)]
        changed |= conditions != row["conditions"] or actions != row["actions"]
        converted = triggers != row["triggers"]
        if not changed and not converted:
            continue
        if changed:
            affected.add(str(row["id"]))
            for run in bind.execute(sa.text("SELECT id FROM automation_runs WHERE rule_id=:id AND finished_at IS NULL"), {"id": row["id"]}).mappings():
                _mark_review(bind, "automation_runs", run["id"])
        removed = not triggers or not actions
        if removed:
            # Keep historical runs even when their obsolete rule is removed.
            bind.execute(sa.text("UPDATE automation_runs SET rule_id=NULL WHERE rule_id=:id"), {"id": row["id"]})
            bind.execute(sa.text("DELETE FROM automation_rules WHERE id=:id"), {"id": row["id"]})
        else:
            bind.execute(sa.text("""UPDATE automation_rules SET
                triggers=CAST(:triggers AS jsonb), trigger_keys=CAST(:keys AS jsonb),
                conditions=CAST(:conditions AS jsonb), actions=CAST(:actions AS jsonb),
                is_active=CASE WHEN :changed THEN false ELSE is_active END,
                next_run_at=CASE WHEN :changed THEN NULL ELSE next_run_at END,
                last_error=CASE WHEN :changed THEN 'assistant_messaging_retired' ELSE last_error END,
                updated_at=now() WHERE id=:id"""), {
                    "id": row["id"], "triggers": json.dumps(triggers),
                    "keys": json.dumps([x["type"] for x in triggers]),
                    "conditions": json.dumps(conditions), "actions": json.dumps(actions), "changed": changed,
                })
        if changed:
            _audit(bind, "AutomationRule", row["id"], removed=removed)
        elif converted:
            _audit(bind, "AutomationRule", row["id"], removed=False, converted=True)
    return affected


def _retire_runs(bind, affected: set[str]) -> None:
    for table, columns in (
        ("notification_runs", "trigger_event, context, delivery_plan, rules_override"),
        ("automation_runs", "trigger_key, rule_id, context, trigger_payload, action_plan"),
    ):
        rows = bind.execute(sa.text(f"SELECT id, {columns} FROM {table} WHERE finished_at IS NULL")).mappings()
        for row in rows:
            payload = dict(row)
            obsolete = contains_retired(payload) or payload.get("trigger_key") == "time.ai_text"
            obsolete |= str(payload.get("rule_id")) in affected
            for item in (payload.get("delivery_plan") or []) + (payload.get("rules_override") or []):
                rule = item.get("rule", item)
                obsolete |= str(rule.get("id") or rule.get("rule_id")) in affected
            if not obsolete:
                continue
            _mark_review(bind, table, row["id"])


def _mark_review(bind, table: str, ident: Any) -> None:
    bind.execute(sa.text(f"""UPDATE {table} SET status='review_required',
        review_reason='assistant_messaging_retired', error='assistant_messaging_retired',
        finished_at=now(), claim_token=NULL, lease_expires_at=NULL,
        updated_at=now() WHERE id=:id"""), {"id": ident})
    _audit(bind, "NotificationRun" if table == "notification_runs" else "AutomationRun", ident, removed=False)


def upgrade() -> None:
    bind = op.get_bind()
    affected = _retire_rules(bind)
    _retire_runs(bind, affected)
    bind.execute(sa.text("""UPDATE action_confirmations SET consumed_at=now(),
        outcome='cancelled', expires_at=now(), updated_at=now()
        WHERE consumed_at IS NULL AND action ~ '(alfred|discord|whatsapp|ai\\.chat)'"""))
    bind.execute(sa.text("DELETE FROM system_settings WHERE key ~ '^(alfred_|discord_|whatsapp_)'"))
    bind.execute(sa.text("""UPDATE visitor_passes SET source_metadata=(
        SELECT COALESCE(jsonb_object_agg(key, value), '{}'::jsonb)
        FROM jsonb_each(CASE WHEN jsonb_typeof(source_metadata)='object'
          THEN source_metadata ELSE '{}'::jsonb END) WHERE left(key, 9) != 'whatsapp_'
    ) WHERE source_metadata IS NOT NULL AND EXISTS (
        SELECT 1 FROM jsonb_object_keys(CASE WHEN jsonb_typeof(source_metadata)='object'
          THEN source_metadata ELSE '{}'::jsonb END) AS key
        WHERE left(key, 9) = 'whatsapp_'
    )"""))
    for table in RETIRED_TABLES:
        op.drop_table(table)
    bind.execute(sa.text("DROP EXTENSION IF EXISTS vector"))


def downgrade() -> None:
    raise RuntimeError("Retired data requires restoration of the matching database/files and image pair.")
