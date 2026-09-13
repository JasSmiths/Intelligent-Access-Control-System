"""Converge fresh and historical authentication session defaults without row changes."""

from alembic import op

revision = "20260912_0002"
down_revision = "20260912_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The security revision established DEFAULT 0 on historical upgrades. The
    # former mutable baseline could skip that ALTER on fresh installations.
    op.execute("ALTER TABLE users ALTER COLUMN auth_session_version SET DEFAULT 0")


def downgrade() -> None:
    # Keep the historical security default for compatible older writers. There
    # is no single prior default to restore, and no row data needs reversing.
    pass
