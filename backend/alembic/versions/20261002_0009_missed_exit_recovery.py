"""Resident missed-exit recovery; disabled until explicitly configured."""
from alembic import op

revision = "20261002_0009"
down_revision = "20260920_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE people ADD COLUMN missed_exit_recovery_enabled BOOLEAN NOT NULL DEFAULT FALSE, ADD COLUMN missed_exit_recovery_tracker_entity_id VARCHAR(255)")
    op.execute("CREATE UNIQUE INDEX uq_people_recovery_tracker ON people (missed_exit_recovery_tracker_entity_id) WHERE missed_exit_recovery_tracker_entity_id IS NOT NULL")
    op.execute('''CREATE TABLE resident_recovery_journeys (
      person_id UUID PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
      epoch VARCHAR(36) NOT NULL, binding VARCHAR(64) NOT NULL, coordinate_hash VARCHAR(64),
      latest_at TIMESTAMPTZ, samples JSONB NOT NULL DEFAULT '[]', invalid_reason VARCHAR(80),
      claimed_event_id UUID REFERENCES access_events(id) ON DELETE SET NULL, consumed_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
    op.execute('''CREATE TABLE missed_exit_recovery_attempts (
      id UUID PRIMARY KEY, occurred_at TIMESTAMPTZ NOT NULL,
      owner_id UUID REFERENCES people(id) ON DELETE SET NULL, owner_name VARCHAR(160) NOT NULL,
      vehicle_id UUID REFERENCES vehicles(id) ON DELETE SET NULL, registration_number VARCHAR(32) NOT NULL,
      event_id UUID NOT NULL UNIQUE REFERENCES access_events(id) ON DELETE CASCADE,
      saga_id UUID REFERENCES movement_sagas(id) ON DELETE SET NULL,
      recovery_event_id UUID REFERENCES access_events(id) ON DELETE SET NULL,
      command_id UUID REFERENCES gate_command_records(id) ON DELETE SET NULL,
      action_context_id UUID REFERENCES notification_action_contexts(id) ON DELETE SET NULL,
      method VARCHAR(40) NOT NULL DEFAULT 'none', outcome VARCHAR(40) NOT NULL, reason VARCHAR(120) NOT NULL,
      policy_version INTEGER NOT NULL DEFAULT 1, checks JSONB NOT NULL DEFAULT '{}',
      timeline JSONB NOT NULL DEFAULT '[]', notification JSONB NOT NULL DEFAULT '{}', duration_ms DOUBLE PRECISION,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
    for name in ('occurred_at', 'owner_id', 'registration_number', 'method', 'outcome'):
        op.execute(f"CREATE INDEX ix_missed_exit_recovery_attempts_{name} ON missed_exit_recovery_attempts ({name})")


def downgrade() -> None:
    op.execute('''DO $$ BEGIN IF EXISTS (SELECT 1 FROM missed_exit_recovery_attempts)
      THEN RAISE EXCEPTION 'Recovery audit exists; retain schema and use a compatible hold build'; END IF; END $$''')
    op.execute("DROP TABLE missed_exit_recovery_attempts")
    op.execute("DROP TABLE resident_recovery_journeys")
    op.execute("DROP INDEX uq_people_recovery_tracker")
    op.execute("ALTER TABLE people DROP COLUMN missed_exit_recovery_enabled, DROP COLUMN missed_exit_recovery_tracker_entity_id")
