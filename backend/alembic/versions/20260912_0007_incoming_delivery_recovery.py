"""Add inbox recovery and immutable access-device delivery origin.

Historical dedupe markers and unattributed device commands remain inert. No
message is claimed, replied to, or replayed by this additive schema change.
"""
from alembic import op

revision = "20260912_0007"
down_revision = "20260912_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE processed_messaging_messages
      ADD COLUMN recovery_version INTEGER, ADD COLUMN state VARCHAR(24),
      ADD COLUMN envelope JSONB, ADD COLUMN routing_context JSONB,
      ADD COLUMN available_at TIMESTAMPTZ, ADD COLUMN batch_id UUID,
      ADD COLUMN claim_token UUID, ADD COLUMN lease_expires_at TIMESTAMPTZ,
      ADD COLUMN claimed_at TIMESTAMPTZ, ADD COLUMN handled_at TIMESTAMPTZ,
      ADD COLUMN reply_plan JSONB, ADD COLUMN result JSONB, ADD COLUMN review_reason TEXT,
      ADD CONSTRAINT ck_incoming_message_recovery CHECK (recovery_version IS NULL OR
        (recovery_version = 1 AND state IS NOT NULL AND state IN ('received','processing','handled','review_required')
         AND envelope IS NOT NULL AND jsonb_typeof(envelope) = 'object'
         AND routing_context IS NOT NULL AND jsonb_typeof(routing_context) = 'object' AND available_at IS NOT NULL))""")
    op.execute("CREATE INDEX ix_incoming_message_eligible ON processed_messaging_messages (state,available_at,created_at) WHERE recovery_version = 1")
    op.execute("CREATE INDEX ix_incoming_message_batch ON processed_messaging_messages (batch_id)")
    op.execute("""ALTER TABLE access_device_command_records
      ADD COLUMN origin_context JSONB, ADD COLUMN outcome_recorded_at TIMESTAMPTZ,
      ADD CONSTRAINT ck_device_command_origin CHECK (origin_context IS NULL OR
        (jsonb_typeof(origin_context) = 'object' AND COALESCE(origin_context->>'kind' = 'automatic_access_garage', FALSE))),
      ADD CONSTRAINT ck_device_command_output_origin CHECK (outcome_recorded_at IS NULL OR origin_context IS NOT NULL)""")
    op.execute("CREATE INDEX ix_device_command_pending_output ON access_device_command_records (created_at,id) WHERE origin_context IS NOT NULL AND outcome_recorded_at IS NULL")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM processed_messaging_messages WHERE recovery_version IS NOT NULL)
         OR EXISTS (SELECT 1 FROM access_device_command_records WHERE origin_context IS NOT NULL OR outcome_recorded_at IS NOT NULL) THEN
        RAISE EXCEPTION 'Incoming or access delivery recovery records exist; retain schema and use a compatible hold build';
      END IF;
    END $$""")
    op.execute("DROP INDEX ix_device_command_pending_output")
    op.execute("""ALTER TABLE access_device_command_records DROP CONSTRAINT ck_device_command_output_origin,
      DROP CONSTRAINT ck_device_command_origin, DROP COLUMN outcome_recorded_at, DROP COLUMN origin_context""")
    op.execute("DROP INDEX ix_incoming_message_batch")
    op.execute("DROP INDEX ix_incoming_message_eligible")
    op.execute("""ALTER TABLE processed_messaging_messages DROP CONSTRAINT ck_incoming_message_recovery,
      DROP COLUMN review_reason, DROP COLUMN result, DROP COLUMN reply_plan, DROP COLUMN handled_at,
      DROP COLUMN claimed_at, DROP COLUMN lease_expires_at, DROP COLUMN claim_token, DROP COLUMN batch_id,
      DROP COLUMN available_at, DROP COLUMN routing_context, DROP COLUMN envelope, DROP COLUMN state, DROP COLUMN recovery_version""")
