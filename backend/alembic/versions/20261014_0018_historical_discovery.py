"""Durable bounded Protect discovery progress; no provider I/O.

Revision ID: 20261014_0018
Revises: 20261014_0017
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20261014_0018'
down_revision = '20261014_0017'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('historical_discovery_progress',
        sa.Column('provider_key', sa.String(64), primary_key=True),
        sa.Column('state', sa.String(24), nullable=False),
        sa.Column('start_ms', sa.BigInteger(), nullable=False),
        sa.Column('end_ms', sa.BigInteger(), nullable=False),
        sa.Column('source', sa.String(120), nullable=False),
        sa.Column('reason', sa.String(500), nullable=False),
        sa.Column('frontier', postgresql.JSONB(), nullable=False),
        sa.Column('page', postgresql.JSONB(), nullable=False),
        sa.Column('page_index', sa.Integer(), nullable=False),
        sa.Column('queued_start_ms', sa.BigInteger()),
        sa.Column('queued_end_ms', sa.BigInteger()),
        sa.Column('queued_source', sa.String(120)),
        sa.Column('queued_reason', sa.String(500)),
        sa.Column('lease_token', sa.Uuid()),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('hold_reason', sa.String(120)),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('pending','leased','completed','held')", name='ck_historical_discovery_state'),
        sa.CheckConstraint('start_ms < end_ms AND page_index >= 0 AND page_index <= jsonb_array_length(page)', name='ck_historical_discovery_bounds'),
        sa.CheckConstraint('jsonb_array_length(frontier) <= 64 AND jsonb_array_length(page) <= 100', name='ck_historical_discovery_size'),
        sa.CheckConstraint('(queued_start_ms IS NULL AND queued_end_ms IS NULL AND queued_source IS NULL AND queued_reason IS NULL) OR (queued_start_ms IS NOT NULL AND queued_end_ms IS NOT NULL AND queued_source IS NOT NULL AND queued_reason IS NOT NULL AND queued_start_ms < queued_end_ms)', name='ck_historical_discovery_queue'))


def downgrade():
    # Discarding a pending/held frontier can silently abandon unseen events.
    raise RuntimeError('Restore a pre-upgrade backup to downgrade historical discovery safely.')
