"""add durable notification runs

Revision ID: 20260531_0003
Revises: 20260531_0002
Create Date: 2026-05-31 00:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "20260531_0003"
down_revision = "20260531_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_runs (
            id UUID PRIMARY KEY,
            trigger_event VARCHAR(120) NOT NULL,
            subject VARCHAR(255) NOT NULL,
            severity VARCHAR(40) NOT NULL,
            status VARCHAR(40) NOT NULL DEFAULT 'queued',
            context JSONB NOT NULL DEFAULT '{}'::jsonb,
            delivered_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            failures JSONB NOT NULL DEFAULT '[]'::jsonb,
            skipped_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
            queued_at TIMESTAMP WITH TIME ZONE NOT NULL,
            started_at TIMESTAMP WITH TIME ZONE,
            finished_at TIMESTAMP WITH TIME ZONE,
            error TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    for column_name in (
        "trigger_event",
        "subject",
        "severity",
        "status",
        "queued_at",
        "started_at",
        "finished_at",
    ):
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_notification_runs_{column_name} "
            f"ON notification_runs ({column_name})"
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notification_runs_status_queued "
        "ON notification_runs (status, queued_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notification_runs_trigger_queued "
        "ON notification_runs (trigger_event, queued_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_notification_runs_trigger_queued")
    op.execute("DROP INDEX IF EXISTS ix_notification_runs_status_queued")
    for column_name in (
        "finished_at",
        "started_at",
        "queued_at",
        "status",
        "severity",
        "subject",
        "trigger_event",
    ):
        op.execute(f"DROP INDEX IF EXISTS ix_notification_runs_{column_name}")
    op.execute("DROP TABLE IF EXISTS notification_runs")
