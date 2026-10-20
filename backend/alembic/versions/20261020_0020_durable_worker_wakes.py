"""Index compact durable worker deadlines without changing stored authority."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_0020"
down_revision = "20261019_0019"
branch_labels = None
depends_on = None

INDEXES = (
    (
        'lpr_debounce_windows',
        'ix_lpr_window_quiet_due',
        [
            "updated_at",
            "open_ordinal",
        ],
        "state='open'",
    ),
    (
        'lpr_debounce_windows',
        'ix_lpr_window_max_due',
        [
            "first_seen",
            "open_ordinal",
        ],
        "state='open'",
    ),
    (
        'access_completion_work',
        'ix_access_optional_due',
        [
            "due_at",
            "id",
        ],
        "stage IN ('visual','snapshot','visitor_link','zone_shadow') AND state='queued'",
    ),
    (
        'access_completion_work',
        'ix_access_completion_lease_due',
        [
            "stage",
            "GREATEST(due_at,lease_until)",
            "id",
        ],
        "state='leased'",
    ),
    (
        'access_completion_work',
        'ix_access_optional_lease_due',
        [
            "GREATEST(due_at,lease_until)",
            "id",
        ],
        "stage IN ('visual','snapshot','visitor_link','zone_shadow') AND state='leased'",
    ),
    (
        'access_completion_work',
        'ix_access_optional_wait_due',
        [
            "GREATEST(due_at,deadline)",
            "id",
        ],
        "stage IN ('visual','snapshot','zone_shadow') AND state='waiting'",
    ),
    (
        'ha_presence_targets',
        'ix_ha_presence_target_deadline',
        [
            "deadline",
            "target_key",
        ],
        "state='ready' AND barrier_attempt_id IS NULL",
    ),
    (
        'vehicle_information_jobs',
        'ix_information_lease_due',
        [
            "lease_until",
            "event_id",
        ],
        "status IN ('queued','processing')",
    ),
    (
        'vehicle_information_jobs',
        'ix_information_deadline_due',
        [
            "deadline",
            "event_id",
        ],
        "status IN ('queued','processing')",
    ),
    (
        'notification_runs',
        'ix_notification_lease_due',
        [
            "lease_expires_at",
            "id",
        ],
        "recovery_version=1 AND status IN ('queued','processing')",
    ),
    (
        'notification_runs',
        'ix_notification_unclaimed',
        [
            "id",
        ],
        "recovery_version=1 AND status IN ('queued','processing') AND claim_token IS NULL",
    ),
    (
        'automation_runs',
        'ix_automation_lease_due',
        [
            "lease_expires_at",
            "id",
        ],
        "recovery_version=1 AND status IN ('queued','processing')",
    ),
    (
        'automation_runs',
        'ix_automation_unleased',
        [
            "id",
        ],
        (
            "recovery_version=1 AND status IN ('queued','processing') AND (lease_expires_at IS NULL OR "
            "(status='processing' AND claim_token IS NULL))"
        ),
    ),
    (
        'visitor_passes',
        'ix_visitor_pass_lifecycle_start',
        [
            (
                "\nCASE\n"
                "    WHEN valid_from IS NOT NULL AND valid_until IS NOT NULL "
                "THEN (valid_from AT TIME ZONE 'UTC'::text)\n"
                "    ELSE (expected_time AT TIME ZONE 'UTC'::text) - "
                "GREATEST(1, LEAST(COALESCE(NULLIF(window_minutes, 0), 30), 1440))::double precision "
                "* '00:01:00'::interval\nEND"
            ),
            "id",
        ],
        "status='SCHEDULED'",
    ),
    (
        'visitor_passes',
        'ix_visitor_pass_lifecycle_expiry',
        [
            (
                "\nCASE\n"
                "    WHEN valid_from IS NOT NULL AND valid_until IS NOT NULL "
                "THEN (valid_until AT TIME ZONE 'UTC'::text)\n"
                "    ELSE (expected_time AT TIME ZONE 'UTC'::text) + "
                "GREATEST(1, LEAST(COALESCE(NULLIF(window_minutes, 0), 30), 1440))::double precision "
                "* '00:01:00'::interval + '00:00:00.000001'::interval\nEND"
            ),
            "id",
        ],
        "status='ACTIVE'",
    ),
    (
        'gate_command_records',
        'ix_gate_command_lease_due',
        [
            "lease_expires_at",
            "id",
        ],
        "state='LEASED'",
    ),
    (
        'gate_command_records',
        'ix_gate_command_confirmation_due',
        [
            "completed_at",
            "id",
        ],
        "state='ACCEPTED' AND mechanically_confirmed IS FALSE",
    ),
    (
        'movement_sagas',
        'ix_movement_pending_due',
        [
            "updated_at",
            "id",
        ],
        "state='PHYSICAL_COMMAND_PENDING'",
    ),
    (
        'access_device_command_records',
        'ix_device_output_lease_due',
        [
            "lease_expires_at",
            "id",
        ],
        "origin_context IS NOT NULL AND outcome_recorded_at IS NULL AND state IN ('prepared','attempting')",
    ),
    (
        'notification_action_contexts',
        'ix_actionable_dispatch_due',
        [
            "metadata->>'dispatch_lease_expires_at'",
            "id",
        ],
        (
            "action IN ('gate.open','gate.force_open') AND consumed_at IS NOT NULL AND metadata->>'version'='2' AND "
            "outcome IN ('dispatch_pending','attempting','dispatch_revoked')"
        ),
    ),
    (
        'visitor_passes',
        'ix_visitor_pass_invalid_window',
        [
            "id",
        ],
        (
            "status IN ('SCHEDULED','ACTIVE') AND valid_from IS NOT NULL AND valid_until IS NOT NULL AND valid_until "
            "<= valid_from"
        ),
    ),
)



def upgrade() -> None:
    for table, name, fields, predicate in INDEXES:
        columns = ", ".join("(" + field + ")" if " " in field or "->" in field or "(" in field else field for field in fields)
        op.execute(sa.text(f"CREATE INDEX {name} ON {table} ({columns}) WHERE {predicate}"))


def downgrade() -> None:
    raise RuntimeError("Restore the matching pre-upgrade database/image pair instead of downgrading durable worker scheduling.")
