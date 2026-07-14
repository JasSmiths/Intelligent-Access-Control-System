"""keep open duration visitor departures indexed

Revision ID: 20260713_0001
Revises: 20260624_0001
Create Date: 2026-07-13 00:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "20260713_0001"
down_revision = "20260624_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_visitor_passes_open_departure_lookup")
    op.execute(
        """
        CREATE INDEX ix_visitor_passes_open_departure_lookup
        ON visitor_passes (number_plate, arrival_time DESC, created_at DESC)
        WHERE (
            (status = 'USED' OR pass_type = 'DURATION')
            AND departure_time IS NULL
            AND arrival_time IS NOT NULL
            AND number_plate IS NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_visitor_passes_open_departure_lookup")
    op.execute(
        """
        CREATE INDEX ix_visitor_passes_open_departure_lookup
        ON visitor_passes (number_plate, arrival_time DESC, created_at DESC)
        WHERE (
            (
                status = 'USED'
                OR (pass_type = 'DURATION' AND status = 'ACTIVE')
            )
            AND departure_time IS NULL
            AND arrival_time IS NOT NULL
            AND number_plate IS NOT NULL
        )
        """
    )
