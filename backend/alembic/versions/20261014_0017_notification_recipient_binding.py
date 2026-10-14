"""Hold ambiguous legacy notification recipients; never infer a new binding.

Stop old workers and back up the matching database/images before upgrading.
No provider/runtime imports, credential reads or network calls. Historical plans
and destination receipts remain byte-for-byte intact. Downgrade cannot safely
restore prior activation/claim authority; use the matching backup if required.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from alembic import op
import sqlalchemy as sa

revision = "20261014_0017"
down_revision = "20261014_0016"
branch_labels = None
depends_on = None
REASON = "recipient_binding_required"


def ambiguous(value: Any) -> bool:
    if isinstance(value, list):
        return any(ambiguous(item) for item in value)
    if not isinstance(value, dict):
        return False
    if isinstance(value.get("type"), str) and value["type"] in {"mobile", "voice"}:
        mode = value.get("target_mode", "all")
        targets = value.get("target_ids", [])
        if mode != "all" and (not isinstance(targets, list) or not targets
                              or any(not isinstance(item, str) or item.endswith(":*") for item in targets)):
            return True
        references = list(targets) if mode != "all" and isinstance(targets, list) else []
        restrictions = value.get("variable_recipients")
        if isinstance(restrictions, dict):
            for entries in restrictions.values():
                if isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, dict) and isinstance(entry.get("target_ids"), list):
                            references.extend(entry["target_ids"])
        if any(isinstance(item, str) and item.startswith("apprise:") for item in references):
            return True
        if value.get("frozen_apprise_indexes"):
            return True
    return any(ambiguous(item) for item in value.values())


def audit(bind: Any, entity: str, identity: Any, detail: dict[str, Any]) -> None:
    bind.execute(sa.text("""INSERT INTO audit_logs
        (id,timestamp,category,action,actor,target_entity,target_id,metadata,outcome,level)
        VALUES (:id,now(),'entity_management','system.recipient_binding_hold','schema.migration',
        :entity,:identity,CAST(:detail AS jsonb),'success','warning')"""),
        {"id": uuid.uuid4(), "entity": entity, "identity": str(identity),
         "detail": json.dumps({"reason": REASON, **detail})})


def upgrade() -> None:
    bind = op.get_bind()
    affected_events = set()
    rules = bind.execute(sa.text("SELECT id,trigger_event,actions,is_active FROM notification_rules ORDER BY id FOR UPDATE")).mappings()
    for rule in rules:
        if not ambiguous(rule["actions"]):
            continue
        if rule["is_active"]:
            affected_events.add(rule["trigger_event"])
        bind.execute(sa.text("UPDATE notification_rules SET is_active=false,updated_at=now() WHERE id=:id"), {"id": rule["id"]})
        audit(bind, "NotificationRule", rule["id"], {"disabled": True, "definition_preserved": True})
    runs = bind.execute(sa.text("""SELECT id,trigger_event,status,finished_at,review_reason,delivery_plan,rules_override
        FROM notification_runs WHERE status <> 'review_required'
        AND (finished_at IS NULL OR status IN ('queued','processing','retrying','running')) ORDER BY id FOR UPDATE""")).mappings()
    for run in runs:
        if run["status"] == "review_required":
            continue  # Preserve already-held uncertainty and its original reason.
        pending = run["finished_at"] is None or run["status"] in {"queued", "processing", "retrying", "running"}
        if not pending:
            continue
        plan = run["delivery_plan"]
        captured = isinstance(plan, list) and any(
            isinstance(item, dict) and item.get("state") in {"pending", "attempting", "unknown"}
            and ambiguous(item.get("action")) for item in plan)
        override = ambiguous(run["rules_override"])
        # Before planning there is no captured rule/condition decision. Hold
        # potentially affected queued events rather than silently omit a step.
        unplanned = not plan and run["rules_override"] is None and run["trigger_event"] in affected_events
        if not (captured or override or unplanned):
            continue
        reason = run["review_reason"] or REASON
        bind.execute(sa.text("""UPDATE notification_runs SET status='review_required',review_reason=CAST(:reason AS text),error=CAST(:reason AS text),
            finished_at=now(),claim_token=NULL,lease_expires_at=NULL,updated_at=now() WHERE id=:id"""),
            {"id": run["id"], "reason": reason})
        audit(bind, "NotificationRun", run["id"], {"held_for_review": True,
            "plan_and_receipts_preserved": True, "unplanned_event": unplanned})


def downgrade() -> None:
    raise RuntimeError("Recipient-binding holds require deliberate review or restoration of the matching pre-upgrade database/image pair.")
