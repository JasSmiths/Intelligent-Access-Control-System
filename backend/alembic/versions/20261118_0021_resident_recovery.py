"""Audited exit lookup and optional, scheduled resident expiry notices."""
import sqlalchemy as sa
from alembic import op

revision = "20261118_0021"
down_revision = "20261020_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("people", sa.Column("missed_exit_recovery_expiry_notify_service", sa.String(255)))
    op.add_column("notification_runs", sa.Column("not_before", sa.DateTime(timezone=True)))
    op.create_index("ix_notification_runs_scheduled", "notification_runs", ["not_before", "id"],
                    postgresql_where=sa.text("not_before IS NOT NULL AND claim_token IS NULL AND recovery_version=1 AND status IN ('queued','processing')"))
    op.create_index("ix_access_events_operator_exit_time", "access_events",
                    ["vehicle_id", sa.text("occurred_at DESC"), sa.text("id DESC")],
                    postgresql_where=sa.text("source='operator_reported_backfill' AND direction='EXIT' AND decision='GRANTED'"))


def downgrade() -> None:
    raise RuntimeError("Restore the matching database/image pair to preserve scheduled notification deadlines and recipient settings.")
