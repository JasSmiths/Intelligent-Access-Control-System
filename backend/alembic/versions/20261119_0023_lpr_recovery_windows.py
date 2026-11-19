"""Allow unrelated plate reads to progress during a resident evidence wait."""
import sqlalchemy as sa
from alembic import op

revision = "20261119_0023"
down_revision = "20261119_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("lpr_recovery_windows",
        sa.Column("window_id", sa.Uuid(), sa.ForeignKey("lpr_debounce_windows.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False), sa.Column("vehicle_id", sa.Uuid(), nullable=False),
        sa.Column("open_ordinal", sa.BigInteger(), nullable=False), sa.Column("phone_epoch", sa.String(36)),
        sa.Column("park_ordinal", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("after_window_id", sa.Uuid(), sa.ForeignKey("lpr_debounce_windows.id", ondelete="RESTRICT")),
        sa.Column("state", sa.String(16), nullable=False),
        sa.CheckConstraint("state IN ('waiting','following','released')", name="ck_lpr_recovery_window_state"))
    op.create_index("ix_lpr_recovery_state_ordinal", "lpr_recovery_windows", ["state", "open_ordinal"])
    op.create_index("ix_lpr_recovery_owner", "lpr_recovery_windows", ["owner_id", "park_ordinal"])
    op.create_index("ix_lpr_recovery_vehicle", "lpr_recovery_windows", ["vehicle_id", "park_ordinal"])
    op.add_column("lpr_debounce_windows", sa.Column("wait_for_recovery", sa.Uuid()))
    op.create_foreign_key("fk_lpr_window_wait_for_recovery", "lpr_debounce_windows", "lpr_recovery_windows",
                          ["wait_for_recovery"], ["window_id"], ondelete="SET NULL")
    op.create_index("ix_lpr_window_recovery_dependency", "lpr_debounce_windows", ["wait_for_recovery"],
                    postgresql_where=sa.text("wait_for_recovery IS NOT NULL"))
    op.create_table("lpr_recovery_blocked_reads",
        sa.Column("ingest_id", sa.Uuid(), sa.ForeignKey("lpr_ingest_events.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("window_id", sa.Uuid(), sa.ForeignKey("lpr_recovery_windows.window_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("ready_ordinal", sa.BigInteger(), nullable=False))
    op.create_index("ix_lpr_recovery_blocked_order", "lpr_recovery_blocked_reads", ["window_id", "ready_ordinal"])


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM lpr_recovery_windows) THEN
        RAISE EXCEPTION 'Complete the pending LPR recovery windows before downgrade';
        END IF; END $$""")
    op.drop_table("lpr_recovery_blocked_reads")
    op.drop_column("lpr_debounce_windows", "wait_for_recovery")
    op.drop_table("lpr_recovery_windows")
