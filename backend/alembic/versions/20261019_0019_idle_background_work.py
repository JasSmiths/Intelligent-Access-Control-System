"""Persist pending completion accounting and index ready LPR work.

Historical completion receipts are recognized once during the upgrade. Runtime
recovery thereafter reads only the pending queue, independently of audit retention.
"""

from alembic import op
import sqlalchemy as sa

revision = "20261019_0019"
down_revision = "20261014_0018"
branch_labels = None
depends_on = None

BACKFILL_PENDING_AUDITS = """
    UPDATE automation_runs AS run
    SET completion_audit_pending = true
    WHERE run.recovery_version = 1
      AND run.status IN ('success', 'failed', 'skipped', 'review_required')
      AND NOT EXISTS (
          SELECT 1 FROM audit_logs AS audit
          WHERE audit.category = 'automation_engine'
            AND audit.action IN ('automation_rule.success', 'automation_rule.skipped',
                                 'automation_rule.failed', 'automation_rule.review_required')
            AND audit.metadata ->> 'run_id' = run.id::varchar
      )
"""


def upgrade() -> None:
    op.add_column(
        "automation_runs",
        sa.Column("completion_audit_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Unlike the old ordered/limited recovery query, this whole-set anti-join
    # permits a batched hash plan. NOT IN can instead repeatedly scan a materialized
    # receipt set when it exceeds work_mem. NULL/missing metadata never matches.
    # Preserve historical counters/receipts and never import a dispatch owner.
    op.execute(sa.text(BACKFILL_PENDING_AUDITS))
    op.create_check_constraint(
        "ck_automation_run_pending_audit_terminal", "automation_runs",
        "NOT completion_audit_pending OR (recovery_version IS NOT DISTINCT FROM 1 "
        "AND status IN ('success', 'failed', 'skipped', 'review_required'))",
    )
    op.create_index(
        "ix_automation_run_pending_audit", "automation_runs", ["finished_at", "id"],
        postgresql_where=sa.text("completion_audit_pending IS TRUE"),
    )
    op.create_index(
        "ix_lpr_ingest_observation_ready", "lpr_ingest_events", ["observation_state", "ready_ordinal"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "Completion accounting survives audit purge; restore the matching pre-upgrade "
        "database/image pair instead of discarding its durable checkpoint."
    )
