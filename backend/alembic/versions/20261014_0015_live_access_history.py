"""Index bounded live history without scanning intervening backfills.

Revision ID: 20261014_0015
Revises: 20261008_0014
"""
from alembic import op
import sqlalchemy as sa

revision = "20261014_0015"
down_revision = "20261008_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    predicate = sa.text("decision = 'GRANTED' AND direction IN ('ENTRY','EXIT') "
        "AND lower(coalesce(source, '')) NOT LIKE '%backfill%' "
        "AND (raw_payload IS NULL OR jsonb_typeof(raw_payload) <> 'object' OR NOT (raw_payload ? 'backfill'))")
    for identity in ("person_id", "vehicle_id"):
        op.create_index(f"ix_access_events_live_{identity}_time", "access_events",
            [identity, sa.text("occurred_at DESC"), sa.text("id DESC")], postgresql_where=predicate)


def downgrade() -> None:
    for identity in ("person_id", "vehicle_id"):
        op.drop_index(f"ix_access_events_live_{identity}_time", table_name="access_events")
