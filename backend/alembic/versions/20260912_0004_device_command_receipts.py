"""Journal physical target attempts before provider I/O without replaying history."""

from alembic import op

revision = "20260912_0004"
down_revision = "20260912_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE gate_state_observations ADD COLUMN access_device_id UUID")
    op.execute("ALTER TABLE gate_state_observations ADD COLUMN binding_fingerprint VARCHAR(64)")
    op.execute("""CREATE TABLE access_device_command_records (
        id UUID PRIMARY KEY,
        gate_command_id UUID REFERENCES gate_command_records(id) ON DELETE SET NULL,
        target_device_id UUID NOT NULL,
        device_key VARCHAR(120) NOT NULL,
        action VARCHAR(40) NOT NULL,
        intent_id VARCHAR(80) NOT NULL,
        idempotency_key VARCHAR(180) NOT NULL CONSTRAINT uq_device_command_idempotency UNIQUE,
        recovery_version INTEGER NOT NULL DEFAULT 1,
        state VARCHAR(32) NOT NULL,
        binding_snapshot JSONB NOT NULL,
        binding_fingerprint VARCHAR(64) NOT NULL,
        lease_token VARCHAR(80),
        lease_expires_at TIMESTAMPTZ,
        attempted_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        accepted BOOLEAN,
        gate_state VARCHAR(40),
        verification_observation_id UUID REFERENCES gate_state_observations(id) ON DELETE SET NULL,
        verification_evidence JSONB,
        provider_receipts JSONB NOT NULL DEFAULT '[]'::jsonb,
        detail TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_device_command_action CHECK (action IN ('open', 'close')),
        CONSTRAINT ck_device_command_state CHECK
          (state IN ('prepared', 'attempting', 'accepted', 'rejected', 'unknown', 'verified', 'not_sent'))
    )""")
    op.execute("""CREATE UNIQUE INDEX uq_device_command_unresolved_target
                  ON access_device_command_records (target_device_id)
                  WHERE state IN ('prepared', 'attempting', 'accepted', 'unknown')""")
    op.execute("CREATE INDEX ix_device_command_parent_state ON access_device_command_records (gate_command_id, state)")
    op.execute("CREATE INDEX ix_device_command_state_lease ON access_device_command_records (state, lease_expires_at)")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM access_device_command_records) THEN
          RAISE EXCEPTION 'Device command receipts exist; retain additive schema and use a compatible hold build';
        END IF;
    END $$""")
    op.execute("DROP TABLE access_device_command_records")
    op.execute("ALTER TABLE gate_state_observations DROP COLUMN binding_fingerprint")
    op.execute("ALTER TABLE gate_state_observations DROP COLUMN access_device_id")
