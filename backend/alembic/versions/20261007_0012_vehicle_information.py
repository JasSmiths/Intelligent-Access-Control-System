"""Add vehicle information provenance, snapshots and arrival refresh jobs.

Revision ID: 20261007_0012
Revises: 20261006_0011
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261007_0012"
down_revision = "20261006_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("mot_source", sa.String(12), nullable=True),
        sa.Column("mot_expiry_kind", sa.String(16), nullable=True),
        sa.Column("mot_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mot_valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("information_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("information_outcome", postgresql.JSONB(), nullable=True),
    ):
        op.add_column("vehicles", column)
    op.create_table("vehicle_information_snapshots",
        sa.Column("vehicle_id", sa.Uuid(), sa.ForeignKey("vehicles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("registration_number", sa.String(32), nullable=False),
        sa.Column("dvla", postgresql.JSONB(), nullable=True),
        sa.Column("dvsa", postgresql.JSONB(), nullable=True))
    op.create_table("vehicle_information_jobs",
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("access_events.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("vehicle_id", sa.Uuid(), sa.ForeignKey("vehicles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("registration_number", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(40), nullable=True))
    op.create_index("ix_vehicle_information_jobs_ready", "vehicle_information_jobs", ["status", "created_at", "event_id"])


def downgrade() -> None:
    # Completed enrichment history and pending work must not silently disappear.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM vehicle_information_jobs) OR EXISTS (SELECT 1 FROM vehicle_information_snapshots)")):
        raise RuntimeError("Populated vehicle information requires a matching backup/source restore")
    op.drop_table("vehicle_information_jobs")
    op.drop_table("vehicle_information_snapshots")
    for name in ("information_outcome", "information_checked_at", "mot_valid_until", "mot_checked_at", "mot_expiry_kind", "mot_source"):
        op.drop_column("vehicles", name)
