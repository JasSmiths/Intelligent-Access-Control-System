"""add automation webhook hardening metadata and LPR ingest queue

Revision ID: 20260531_0002
Revises: 20260509_0001
Create Date: 2026-05-31 00:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "20260531_0002"
down_revision = "20260509_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS lpr_ingest_events (
            id UUID PRIMARY KEY,
            idempotency_key VARCHAR(180) NOT NULL UNIQUE,
            source VARCHAR(120) NOT NULL,
            registration_number VARCHAR(32) NOT NULL,
            captured_at TIMESTAMP WITH TIME ZONE NOT NULL,
            received_at TIMESTAMP WITH TIME ZONE NOT NULL,
            normalized_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(40) NOT NULL DEFAULT 'pending',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            processing_started_at TIMESTAMP WITH TIME ZONE,
            processed_at TIMESTAMP WITH TIME ZONE,
            last_error TEXT,
            movement_saga_id UUID REFERENCES movement_sagas(id) ON DELETE SET NULL,
            access_event_id UUID REFERENCES access_events(id) ON DELETE SET NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_lpr_ingest_events_idempotency_key "
        "ON lpr_ingest_events (idempotency_key)"
    )
    for column_name in (
        "source",
        "registration_number",
        "captured_at",
        "received_at",
        "status",
        "processing_started_at",
        "processed_at",
        "movement_saga_id",
        "access_event_id",
    ):
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_lpr_ingest_events_{column_name} "
            f"ON lpr_ingest_events ({column_name})"
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lpr_ingest_events_status_received "
        "ON lpr_ingest_events (status, received_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lpr_ingest_events_source_captured "
        "ON lpr_ingest_events (source, captured_at)"
    )

    op.execute(
        "ALTER TABLE automation_webhook_senders "
        "ADD COLUMN IF NOT EXISTS key_strength VARCHAR(40) NOT NULL DEFAULT 'legacy'"
    )
    op.execute(
        "ALTER TABLE automation_webhook_senders "
        "ADD COLUMN IF NOT EXISTS hmac_required BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE automation_webhook_senders "
        "ADD COLUMN IF NOT EXISTS allowed_source_ips JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute("ALTER TABLE automation_webhook_senders ADD COLUMN IF NOT EXISTS last_nonce VARCHAR(160)")
    op.execute(
        "ALTER TABLE automation_webhook_senders "
        "ADD COLUMN IF NOT EXISTS last_signature_at TIMESTAMP WITH TIME ZONE"
    )
    op.execute(
        "ALTER TABLE automation_webhook_senders "
        "ADD COLUMN IF NOT EXISTS rate_window_started_at TIMESTAMP WITH TIME ZONE"
    )
    op.execute(
        "ALTER TABLE automation_webhook_senders "
        "ADD COLUMN IF NOT EXISTS rate_window_count INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE automation_webhook_senders "
        "ADD COLUMN IF NOT EXISTS rejected_count INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_automation_webhook_senders_key_strength "
        "ON automation_webhook_senders (key_strength)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_automation_webhook_senders_hmac_required "
        "ON automation_webhook_senders (hmac_required)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_automation_webhook_senders_last_nonce "
        "ON automation_webhook_senders (last_nonce)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_automation_webhook_senders_last_signature_at "
        "ON automation_webhook_senders (last_signature_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_automation_webhook_senders_rate_window_started_at "
        "ON automation_webhook_senders (rate_window_started_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_lpr_ingest_events_source_captured")
    op.execute("DROP INDEX IF EXISTS ix_lpr_ingest_events_status_received")
    for column_name in (
        "access_event_id",
        "movement_saga_id",
        "processed_at",
        "processing_started_at",
        "status",
        "received_at",
        "captured_at",
        "registration_number",
        "source",
        "idempotency_key",
    ):
        op.execute(f"DROP INDEX IF EXISTS ix_lpr_ingest_events_{column_name}")
    op.execute("DROP TABLE IF EXISTS lpr_ingest_events")

    op.execute("DROP INDEX IF EXISTS ix_automation_webhook_senders_rate_window_started_at")
    op.execute("DROP INDEX IF EXISTS ix_automation_webhook_senders_last_signature_at")
    op.execute("DROP INDEX IF EXISTS ix_automation_webhook_senders_last_nonce")
    op.execute("DROP INDEX IF EXISTS ix_automation_webhook_senders_hmac_required")
    op.execute("DROP INDEX IF EXISTS ix_automation_webhook_senders_key_strength")
    for column_name in (
        "rejected_count",
        "rate_window_count",
        "rate_window_started_at",
        "last_signature_at",
        "last_nonce",
        "allowed_source_ips",
        "hmac_required",
        "key_strength",
    ):
        op.execute(f"ALTER TABLE automation_webhook_senders DROP COLUMN IF EXISTS {column_name}")
