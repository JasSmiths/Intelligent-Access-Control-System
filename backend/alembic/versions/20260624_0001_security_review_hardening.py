"""security review hardening ledgers

Revision ID: 20260624_0001
Revises: 20260531_0003
Create Date: 2026-06-24 00:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "20260624_0001"
down_revision = "20260531_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_session_version INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMP WITH TIME ZONE")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS automation_webhook_nonces (
            id UUID PRIMARY KEY,
            webhook_key VARCHAR(160) NOT NULL,
            source_ip VARCHAR(80) NOT NULL,
            nonce_hash VARCHAR(64) NOT NULL,
            signed_at TIMESTAMP WITH TIME ZONE NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            CONSTRAINT ux_automation_webhook_nonce UNIQUE (webhook_key, nonce_hash)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_automation_webhook_nonces_webhook_key ON automation_webhook_nonces (webhook_key)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_automation_webhook_nonces_source_ip ON automation_webhook_nonces (source_ip)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_automation_webhook_nonces_nonce_hash ON automation_webhook_nonces (nonce_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_automation_webhook_nonces_signed_at ON automation_webhook_nonces (signed_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_automation_webhook_nonces_expires ON automation_webhook_nonces (expires_at)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_messaging_messages (
            id UUID PRIMARY KEY,
            provider VARCHAR(40) NOT NULL,
            provider_message_id VARCHAR(180) NOT NULL,
            provider_channel_id VARCHAR(180),
            author_provider_id VARCHAR(180),
            received_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            CONSTRAINT ux_processed_messaging_message_provider_id UNIQUE (provider, provider_message_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_processed_messaging_messages_provider ON processed_messaging_messages (provider)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_processed_messaging_messages_provider_message_id "
        "ON processed_messaging_messages (provider_message_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_processed_messaging_messages_provider_channel_id "
        "ON processed_messaging_messages (provider_channel_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_processed_messaging_messages_author_provider_id "
        "ON processed_messaging_messages (author_provider_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_processed_messaging_messages_received_at ON processed_messaging_messages (received_at)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS revoked_auth_tokens (
            id UUID PRIMARY KEY,
            jti_hash VARCHAR(64) NOT NULL,
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            CONSTRAINT ux_revoked_auth_tokens_jti_hash UNIQUE (jti_hash)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_revoked_auth_tokens_jti_hash ON revoked_auth_tokens (jti_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_revoked_auth_tokens_user_id ON revoked_auth_tokens (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_revoked_auth_tokens_expires_at ON revoked_auth_tokens (expires_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_revoked_auth_tokens_expires_at")
    op.execute("DROP INDEX IF EXISTS ix_revoked_auth_tokens_user_id")
    op.execute("DROP INDEX IF EXISTS ix_revoked_auth_tokens_jti_hash")
    op.execute("DROP TABLE IF EXISTS revoked_auth_tokens")

    op.execute("DROP INDEX IF EXISTS ix_processed_messaging_messages_received_at")
    op.execute("DROP INDEX IF EXISTS ix_processed_messaging_messages_author_provider_id")
    op.execute("DROP INDEX IF EXISTS ix_processed_messaging_messages_provider_channel_id")
    op.execute("DROP INDEX IF EXISTS ix_processed_messaging_messages_provider_message_id")
    op.execute("DROP INDEX IF EXISTS ix_processed_messaging_messages_provider")
    op.execute("DROP TABLE IF EXISTS processed_messaging_messages")

    op.execute("DROP INDEX IF EXISTS ix_automation_webhook_nonces_expires")
    op.execute("DROP INDEX IF EXISTS ix_automation_webhook_nonces_signed_at")
    op.execute("DROP INDEX IF EXISTS ix_automation_webhook_nonces_nonce_hash")
    op.execute("DROP INDEX IF EXISTS ix_automation_webhook_nonces_source_ip")
    op.execute("DROP INDEX IF EXISTS ix_automation_webhook_nonces_webhook_key")
    op.execute("DROP TABLE IF EXISTS automation_webhook_nonces")

    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS password_changed_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS auth_session_version")
