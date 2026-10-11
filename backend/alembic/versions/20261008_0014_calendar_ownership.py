"""Calendar account fences and immutable resumable run context.

Revision ID: 20261008_0014
Revises: 20261007_0013

Cutover requires stopping and draining every old calendar/auth worker first:
older code ignores these fences. Historical empty run contexts are read-only.
Populated new ownership state requires verified backup/compatible-image
fix-forward; downgrade never erases accepted or uncertain checkpoints.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261008_0014"
down_revision = "20261007_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("icloud_calendar_accounts", sa.Column("sync_generation", sa.BigInteger(),
                                                       nullable=False, server_default="0"))
    op.add_column("icloud_calendar_accounts", sa.Column("sync_lease_token", sa.Uuid(), nullable=True))
    op.add_column("icloud_calendar_accounts", sa.Column("sync_lease_until", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_icloud_calendar_sync_generation", "icloud_calendar_accounts", "sync_generation >= 0")
    op.create_check_constraint("ck_icloud_calendar_sync_lease_pair", "icloud_calendar_accounts", "(sync_lease_token IS NULL) = (sync_lease_until IS NULL)")
    op.add_column("icloud_calendar_sync_runs", sa.Column("run_context", postgresql.JSONB(), nullable=False,
                                                       server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    # Refuse before any DDL; even expired tokens/accepted terminal context may
    # carry evidence an older worker would ignore or repeat.
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM icloud_calendar_accounts WHERE sync_generation <> 0 "
        "OR sync_lease_token IS NOT NULL OR sync_lease_until IS NOT NULL) "
        "OR EXISTS (SELECT 1 FROM icloud_calendar_sync_runs WHERE run_context <> '{}'::jsonb)"
    )):
        raise RuntimeError("Calendar ownership state requires a verified matching backup/source restore or compatible-image fix-forward; downgrade would discard fencing or accepted/resumable evidence.")
    op.drop_column("icloud_calendar_sync_runs", "run_context")
    op.drop_constraint("ck_icloud_calendar_sync_lease_pair", "icloud_calendar_accounts", type_="check")
    op.drop_constraint("ck_icloud_calendar_sync_generation", "icloud_calendar_accounts", type_="check")
    op.drop_column("icloud_calendar_accounts", "sync_lease_until")
    op.drop_column("icloud_calendar_accounts", "sync_lease_token")
    op.drop_column("icloud_calendar_accounts", "sync_generation")
