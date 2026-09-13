"""Add durable occurrence and action checkpoints without replaying old runs."""

from alembic import op

revision = "20260912_0005"
down_revision = "20260912_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE automation_runs
        ADD COLUMN recovery_version INTEGER,
        ADD COLUMN occurrence_key VARCHAR(255),
        ADD COLUMN queued_at TIMESTAMPTZ,
        ADD COLUMN claim_token UUID,
        ADD COLUMN lease_expires_at TIMESTAMPTZ,
        ADD COLUMN claim_count INTEGER NOT NULL DEFAULT 0,
        ADD COLUMN action_plan JSONB,
        ADD COLUMN review_reason VARCHAR(120),
        ADD CONSTRAINT uq_automation_run_occurrence UNIQUE (occurrence_key)
    """)
    op.execute("""CREATE INDEX ix_automation_run_recovery_queue ON automation_runs (queued_at, id)
        WHERE recovery_version = 1 AND status IN ('queued', 'processing')""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM automation_runs WHERE recovery_version IS NOT NULL) THEN
          RAISE EXCEPTION 'Durable automation work exists; retain additive schema and use a compatible hold build';
        END IF;
    END $$""")
    op.execute("DROP INDEX ix_automation_run_recovery_queue")
    op.execute("""ALTER TABLE automation_runs
        DROP CONSTRAINT uq_automation_run_occurrence,
        DROP COLUMN review_reason, DROP COLUMN action_plan, DROP COLUMN claim_count,
        DROP COLUMN lease_expires_at, DROP COLUMN claim_token, DROP COLUMN queued_at,
        DROP COLUMN occurrence_key, DROP COLUMN recovery_version
    """)
