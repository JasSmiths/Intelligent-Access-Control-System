"""Index normalized active registrations for bounded exact plate lookup.

Revision ID: 20261006_0011
Revises: 20261005_0010
"""

from alembic import op
import sqlalchemy as sa

revision = "20261006_0011"
down_revision = "20261005_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_vehicles_normalized_active_plate", "vehicles",
        [sa.text("upper(regexp_replace(registration_number, '[^A-Za-z0-9]', '', 'g'))")],
        postgresql_where=sa.text("is_active IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index("ix_vehicles_normalized_active_plate", table_name="vehicles")
