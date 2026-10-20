"""Scheduling projections for the existing silent movement recovery owner."""
from __future__ import annotations

from app.services.durable_readiness import readiness
from app.services.durable_work import WorkReadiness
from app.services.workflow_session import SessionFactory


async def movement_readiness(sessions: SessionFactory, *, include_ready: bool = True) -> WorkReadiness:
    # A completed no-progress sweep ignores already-due held rows while retaining
    # future timers. Held uncertainty must never become an automatic resend loop.
    future = "" if include_ready else " AND lease_expires_at > t.now"
    completed_future = "" if include_ready else " AND completed_at + INTERVAL '20 seconds' > t.now"
    saga_future = "" if include_ready else " AND updated_at + INTERVAL '20 seconds' > t.now"
    reservation_future = "" if include_ready else " AND dispatch_deadline + INTERVAL '1 microsecond' > t.now"
    branches: tuple[str, ...] = (
        "SELECT lease_expires_at FROM gate_command_records,t WHERE state='LEASED'" + future + " ORDER BY lease_expires_at,id LIMIT 1",
        "SELECT completed_at + INTERVAL '20 seconds' FROM gate_command_records,t WHERE state='ACCEPTED' AND mechanically_confirmed IS FALSE" + completed_future + " ORDER BY completed_at,id LIMIT 1",
        "SELECT updated_at + INTERVAL '20 seconds' FROM movement_sagas,t WHERE state='PHYSICAL_COMMAND_PENDING'" + saga_future + " ORDER BY updated_at,id LIMIT 1",
        "SELECT dispatch_deadline + INTERVAL '1 microsecond' FROM visitor_pass_reservations,t WHERE state='reserved'" + reservation_future + " ORDER BY dispatch_deadline,id LIMIT 1",
        "SELECT lease_expires_at FROM access_device_command_records,t WHERE origin_context IS NOT NULL AND outcome_recorded_at IS NULL AND state IN ('prepared','attempting')" + future + " ORDER BY lease_expires_at,id LIMIT 1",
    )
    lease = "metadata->>'dispatch_lease_expires_at'"
    valid = f"COALESCE(({lease}) ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}T\\d{{2}}:\\d{{2}}:\\d{{2}}\\.\\d{{6}}\\+00:00$',false) AND pg_input_is_valid({lease},'timestamp with time zone')"
    output = "action IN ('gate.open','gate.force_open') AND consumed_at IS NOT NULL AND metadata->>'version'='2' AND outcome IN ('dispatch_pending','attempting','dispatch_revoked')"
    # Valid owned values are fixed-width UTC strings; invalid retained values are
    # immediate review candidates and never cast before pg_input_is_valid.
    branches += (f"SELECT CASE WHEN {valid} THEN ({lease})::timestamptz ELSE t.now END FROM notification_action_contexts,t WHERE {output}" + ("" if include_ready else f" AND {valid} AND ({lease}) > to_char(t.now AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US') || '+00:00'") + f" ORDER BY ({lease}) NULLS FIRST,id LIMIT 1",)
    if include_ready:
        branches += (
            "SELECT t.now FROM t WHERE EXISTS (SELECT 1 FROM gate_command_records WHERE requires_reconciliation IS TRUE OR state='RECONCILIATION_REQUIRED')",
            "SELECT t.now FROM t WHERE EXISTS (SELECT 1 FROM movement_sagas WHERE reconciliation_required IS TRUE)",
            "SELECT t.now FROM t WHERE EXISTS (SELECT 1 FROM access_device_command_records WHERE origin_context IS NOT NULL AND outcome_recorded_at IS NULL AND (state NOT IN ('prepared','attempting') OR lease_expires_at IS NULL))",
        )
    return await readiness(sessions, branches)
