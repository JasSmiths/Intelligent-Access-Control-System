"""Add explicit admission evidence and visitor reservation identity.

Legacy rows stay unclassified. This migration does not consume, release,
reclassify or replay any historical pass or access event.
"""
from alembic import op

revision = "20260912_0006"
down_revision = "20260912_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE movement_sagas
      ADD COLUMN admission_status VARCHAR(24), ADD COLUMN admission_evidence JSONB,
      ADD CONSTRAINT ck_movement_sagas_admission_status CHECK
        (admission_status IS NULL OR admission_status IN ('pending','verified','not_required','denied','historical'))""")
    op.execute("""CREATE TABLE visitor_pass_reservations (
      id UUID PRIMARY KEY, visitor_pass_id UUID NOT NULL, access_event_id UUID NOT NULL,
      intent_id UUID NOT NULL, gate_command_id UUID, pass_type VARCHAR(20) NOT NULL,
      normalized_plate VARCHAR(32) NOT NULL, state VARCHAR(20) NOT NULL,
      dispatch_deadline TIMESTAMPTZ NOT NULL, completed_at TIMESTAMPTZ,
      completion_reason TEXT, verification_evidence JSONB,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT uq_visitor_reservation_event UNIQUE (access_event_id),
      CONSTRAINT uq_visitor_reservation_intent UNIQUE (intent_id),
      CONSTRAINT ck_visitor_reservation_state CHECK (state IN ('reserved','held','consumed','released')),
      CONSTRAINT ck_visitor_reservation_pass_type CHECK (pass_type IN ('one-time','duration'))
    )""")
    op.execute("""CREATE UNIQUE INDEX uq_visitor_reservation_unavailable ON visitor_pass_reservations (visitor_pass_id)
      WHERE state IN ('reserved','held') OR (state = 'consumed' AND pass_type = 'one-time')""")
    op.execute("CREATE INDEX ix_visitor_reservation_state_deadline ON visitor_pass_reservations (state, dispatch_deadline)")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM visitor_pass_reservations)
         OR EXISTS (SELECT 1 FROM movement_sagas WHERE admission_status IS NOT NULL OR admission_evidence IS NOT NULL) THEN
        RAISE EXCEPTION 'Admission or visitor reservation records exist; retain schema and use a compatible hold build';
      END IF;
    END $$""")
    op.execute("DROP TABLE visitor_pass_reservations")
    op.execute("""ALTER TABLE movement_sagas DROP CONSTRAINT ck_movement_sagas_admission_status,
      DROP COLUMN admission_evidence, DROP COLUMN admission_status""")
