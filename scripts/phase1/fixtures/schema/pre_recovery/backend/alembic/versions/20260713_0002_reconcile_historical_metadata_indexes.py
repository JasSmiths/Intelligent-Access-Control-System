"""reconcile indexes omitted by the mutable schema baseline

Revision ID: 20260713_0002
Revises: 20260713_0001
Create Date: 2026-07-13 18:15:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "20260713_0002"
down_revision = "20260713_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Converge both historical and freshly created baseline schemas."""

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_vehicle_person_assignments_person_id
        ON vehicle_person_assignments (person_id)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_visitor_passes_source_reference
        ON visitor_passes (source_reference)
        WHERE source_reference IS NOT NULL
        """
    )
    op.execute("DROP INDEX IF EXISTS ix_visitor_passes_source_reference")
    op.execute(
        """
        CREATE INDEX ix_visitor_passes_source_reference
        ON visitor_passes (source_reference)
        """
    )


def downgrade() -> None:
    """Restore the historical baseline index shape."""

    op.execute("DROP INDEX IF EXISTS ix_visitor_passes_source_reference")
    op.execute(
        """
        CREATE UNIQUE INDEX ix_visitor_passes_source_reference
        ON visitor_passes (source_reference)
        """
    )
    op.execute("DROP INDEX IF EXISTS ux_visitor_passes_source_reference")
    op.execute("DROP INDEX IF EXISTS ix_vehicle_person_assignments_person_id")
