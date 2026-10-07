"""current SQLAlchemy metadata baseline

Revision ID: 20260531_0000
Revises:
Create Date: 2026-05-31 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

from app.db.base import Base
from app import models as _models  # noqa: F401 - import registers mapped models


revision = "20260531_0000"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, checkfirst=True)
