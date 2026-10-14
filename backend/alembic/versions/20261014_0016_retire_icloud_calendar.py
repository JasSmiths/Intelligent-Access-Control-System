"""Retire iCloud Calendar; stop old workers and back up matching DB/files first.

Revision ID: 20261014_0016
Revises: 20261014_0015
No application imports, file deletion, vendor calls or physical effects.
Retirement is irreversible except by restoring the matching backup/image pair.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from alembic import op
import sqlalchemy as sa

revision = "20261014_0016"
down_revision = "20261014_0015"
branch_labels = None
depends_on = None
REASON = "icloud_calendar_retired"
COUNTERS = ("account_count", "events_scanned", "events_matched", "passes_created",
            "passes_updated", "passes_cancelled", "passes_skipped", "historical_coverage_skipped")


def retired(value: Any) -> bool:
    if isinstance(value, dict):
        return any(retired(key) or retired(item) for key, item in value.items())
    if isinstance(value, list):
        return any(retired(item) for item in value)
    return isinstance(value, str) and (
        value == "icloud_calendar" or value.startswith(("integration.icloud_calendar.",
        "icloud_calendar.", "icloud_calendar_", "icloud-calendar", "icloud:")))


def safe_sync(value: Any) -> dict[str, Any]:
    """Archive delivery/checkpoint truth, never account names, credentials or errors."""
    if not isinstance(value, dict):
        return {"unrecognized_history": True}
    result = {key: value[key] for key in COUNTERS if isinstance(value.get(key), int)}
    for key in ("status", "trigger_source", "interrupted", "requires_review", "accepted_any", "delivery"):
        item = value.get(key)
        if isinstance(item, bool):
            result[key] = item
        elif isinstance(item, str):
            allowed = {"ok", "removed", "stale_configuration", "busy", "interrupted", "incomplete",
                       "requires_reauth", "error", "unknown", "running", "completed", "success", "failed",
                       "ui", "workflow", "accepted", "rejected", "not_sent", "partial"}
            result[key] = item if item in allowed else "unrecognized"
    if value.get("error"):
        result["had_error"] = True
    checkpoints = []
    for item in value.get("account_results", []) if isinstance(value.get("account_results"), list) else []:
        if isinstance(item, dict):
            checkpoint = safe_sync(item)
            try:
                checkpoint["account_id"] = str(uuid.UUID(str(item.get("account_id"))))
            except ValueError:
                checkpoint["account_identity_unavailable"] = True
            checkpoints.append(checkpoint)
    if checkpoints:
        result["account_results"] = checkpoints
    return result


def sanitize_receipts(value: Any) -> Any:
    if isinstance(value, list):
        return [sanitize_receipts(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: sanitize_receipts(item) for key, item in value.items()}
    if value.get("type") == "integration.icloud_calendar.sync" or value.get("integration_provider") == "icloud_calendar":
        if "sync" in value:
            result["sync"] = safe_sync(value["sync"])
        if "error" in value:
            result["error"] = REASON if value["error"] else None
        result.pop("disabled_reason", None)
    return result


def audit(bind: Any, entity: str, identity: Any, detail: dict[str, Any]) -> None:
    bind.execute(sa.text("""INSERT INTO audit_logs
        (id,timestamp,category,action,actor,target_entity,target_id,metadata,outcome,level)
        VALUES (:id,now(),'entity_management','system.feature_retirement','schema_retirement',
        :entity,:identity,CAST(:detail AS jsonb),'success','info')"""),
        {"id": uuid.uuid4(), "entity": entity, "identity": str(identity),
         "detail": json.dumps({"reason": REASON, **detail})})


def retire_passes(bind: Any) -> None:
    rows = bind.execute(sa.text("""SELECT id,creation_source,source_reference,source_metadata,
        status,arrival_time,departure_time,arrival_event_id FROM visitor_passes
        WHERE creation_source='icloud_calendar' OR source_reference LIKE 'icloud:%'
        ORDER BY id FOR UPDATE""")).mappings()
    for row in rows:
        identity = row["id"]
        ref = row["source_reference"]
        match = re.fullmatch(r"icloud:([0-9a-fA-F-]{36}):(.+)", ref or "")
        metadata = row["source_metadata"]
        ambiguous = row["creation_source"] != "icloud_calendar" or match is None
        if match:
            try:
                account = str(uuid.UUID(match[1]))
            except ValueError:
                ambiguous = True
                account = ""
            if isinstance(metadata, dict):
                if metadata.get("account_id") is not None and str(metadata["account_id"]) != account:
                    ambiguous = True
                if all(metadata.get(key) is not None for key in ("account_id", "calendar_id", "event_id")):
                    expected = f"icloud:{metadata['account_id']}:{metadata['calendar_id']}:{metadata['event_id']}"[:255]
                    ambiguous |= expected != ref
                elif not re.fullmatch(r".+:.+", match[2]):
                    ambiguous = True
            elif not re.fullmatch(r".+:.+", match[2]):
                ambiguous = True
        if ambiguous:
            raise RuntimeError(f"Calendar retirement blocked: visitor pass {identity} has ambiguous provenance; review offline.")
        reservations = list(bind.execute(sa.text("""SELECT state,access_event_id FROM visitor_pass_reservations
            WHERE visitor_pass_id=:id ORDER BY id FOR UPDATE"""), {"id": identity}).mappings())
        if any(item["state"] in {"reserved", "held"} for item in reservations):
            raise RuntimeError(f"Calendar retirement blocked: visitor pass {identity} has an unresolved reservation; reconcile offline.")
        consumed = [item for item in reservations if item["state"] == "consumed"]
        if row["status"] in {"SCHEDULED", "ACTIVE"} and consumed and (row["arrival_time"] is None or row["arrival_event_id"] is None):
            raise RuntimeError(f"Calendar retirement blocked: visitor pass {identity} has inconsistent consumption history; reconcile offline.")
        if row["status"] in {"SCHEDULED", "ACTIVE"}:
            if row["departure_time"] is not None and row["arrival_time"] is None:
                raise RuntimeError(f"Calendar retirement blocked: visitor pass {identity} has inconsistent visit history; reconcile offline.")
            if row["departure_time"] is not None and row["departure_time"] < row["arrival_time"]:
                raise RuntimeError(f"Calendar retirement blocked: visitor pass {identity} has contradictory visit chronology; reconcile offline.")
            if consumed and not any(item["access_event_id"] == row["arrival_event_id"] for item in consumed):
                raise RuntimeError(f"Calendar retirement blocked: visitor pass {identity} has inconsistent current arrival consumption; reconcile offline.")
            if row["arrival_time"] is not None and row["departure_time"] is None:
                raise RuntimeError(f"Calendar retirement blocked: visitor pass {identity} has an open visit; settle departure offline.")
            bind.execute(sa.text("UPDATE visitor_passes SET status='CANCELLED',updated_at=now() WHERE id=:id"), {"id": identity})
            audit(bind, "VisitorPass", identity, {"cancelled": True, "history_preserved": True})
        # Keep source identity/history, discard obsolete account identifiers and event content.
        if isinstance(metadata, dict):
            sanitized = {key: metadata[key] for key in ("account_id", "calendar_id", "event_id", "event_start", "event_end") if key in metadata}
            sanitized["retired"] = True
            bind.execute(sa.text("UPDATE visitor_passes SET source_metadata=CAST(:metadata AS jsonb) WHERE id=:id"),
                         {"id": identity, "metadata": json.dumps(sanitized)})


def retire_workflows(bind: Any) -> None:
    affected: set[str] = set()
    for table, columns in (("automation_rules", "triggers,conditions,actions"), ("notification_rules", "trigger_event,conditions,actions")):
        for row in bind.execute(sa.text(f"SELECT id,{columns} FROM {table} ORDER BY id FOR UPDATE")).mappings():
            if not retired(dict(row)):
                continue
            affected.add(str(row["id"]))
            schedule = ",next_run_at=NULL,last_error=:reason" if table == "automation_rules" else ""
            bind.execute(sa.text(f"UPDATE {table} SET is_active=false,updated_at=now(){schedule} WHERE id=:id"),
                         {"id": row["id"], "reason": REASON})
            audit(bind, "AutomationRule" if table == "automation_rules" else "NotificationRule", row["id"], {"disabled": True, "plan_preserved": True})
    for table, columns in (("automation_runs", "rule_id,trigger_key,context,trigger_payload,action_plan,action_results"),
                           ("notification_runs", "trigger_event,context,delivery_plan,rules_override")):
        for row in bind.execute(sa.text(f"SELECT id,status,finished_at,{columns} FROM {table} ORDER BY id FOR UPDATE")).mappings():
            payload = dict(row)
            linked = str(payload.get("rule_id")) in affected
            if table == "notification_runs":
                for node in (payload.get("delivery_plan") or []) + (payload.get("rules_override") or []):
                    if isinstance(node, dict):
                        rule = node.get("rule", node)
                        linked |= isinstance(rule, dict) and str(rule.get("id") or rule.get("rule_id")) in affected
            if not (linked or retired(payload)):
                continue
            pending = row["finished_at"] is None or row["status"] in {"queued", "processing", "running", "retrying", "scheduled"}
            if pending:
                bind.execute(sa.text(f"""UPDATE {table} SET status='review_required',review_reason=CAST(:reason AS text),error=CAST(:reason AS text),
                    finished_at=now(),claim_token=NULL,lease_expires_at=NULL,updated_at=now() WHERE id=:id"""),
                    {"id": row["id"], "reason": REASON})
                audit(bind, "AutomationRun" if table == "automation_runs" else "NotificationRun", row["id"], {"held_for_review": True, "plan_and_receipts_preserved": True})
            for column in ("action_plan", "action_results") if table == "automation_runs" else ("delivery_plan",):
                value = payload.get(column)
                sanitized = sanitize_receipts(value)
                if sanitized != value:
                    bind.execute(sa.text(f"UPDATE {table} SET {column}=CAST(:value AS jsonb) WHERE id=:id"),
                                 {"id": row["id"], "value": json.dumps(sanitized)})


def upgrade() -> None:
    bind = op.get_bind()
    retire_passes(bind)  # blockers abort the whole transaction before credential removal
    retire_workflows(bind)
    for row in bind.execute(sa.text("SELECT id,status,trigger_source,account_count,events_scanned,events_matched,passes_created,passes_updated,passes_cancelled,passes_skipped,account_results,error FROM icloud_calendar_sync_runs ORDER BY id")).mappings():
        detail = safe_sync(dict(row))
        detail["execution_retired"] = True
        audit(bind, "RetiredCalendarSyncRun", row["id"], detail)
    bind.execute(sa.text("""UPDATE action_confirmations SET consumed_at=now(),outcome='cancelled',expires_at=now(),updated_at=now()
        WHERE consumed_at IS NULL AND (action LIKE 'icloud_calendar.%' OR action LIKE 'integration.icloud_calendar.%')"""))
    bind.execute(sa.text("DELETE FROM system_settings WHERE key ~ '^icloud_'"))
    audit(bind, "RetiredCalendarAccounts", "all", {"credentials_and_state_removed": True})
    op.drop_index("ix_visitor_passes_calendar_active_source", table_name="visitor_passes")
    op.drop_table("icloud_calendar_sync_runs")
    op.drop_table("icloud_calendar_accounts")


def downgrade() -> None:
    raise RuntimeError("Retired calendar data requires restoration of the matching database/files and image pair.")
