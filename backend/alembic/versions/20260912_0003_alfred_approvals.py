"""Persist requester-bound Alfred approvals independently of conversation JSON."""

from alembic import op

revision = "20260912_0003"
down_revision = "20260912_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE alfred_approvals (
        id VARCHAR(64) PRIMARY KEY,
        operation_id UUID NOT NULL CONSTRAINT uq_alfred_approval_operation UNIQUE,
        session_id UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
        requester_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
        requester_auth_session_version INTEGER NOT NULL,
        status VARCHAR(20) NOT NULL,
        payload JSONB NOT NULL,
        result JSONB,
        expires_at TIMESTAMPTZ NOT NULL,
        claimed_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_alfred_approval_status CHECK
          (status IN ('pending', 'claimed', 'completed', 'cancelled', 'expired', 'unknown'))
    )""")
    op.execute("CREATE INDEX ix_alfred_approval_requester ON alfred_approvals (session_id, requester_user_id, created_at)")
    op.execute("""CREATE UNIQUE INDEX uq_alfred_approval_pending ON alfred_approvals (session_id, requester_user_id)
                  WHERE status = 'pending' AND requester_user_id IS NOT NULL""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM alfred_approvals) THEN
          RAISE EXCEPTION 'Alfred approvals exist; retain additive schema and use a compatible hold build';
        END IF;
    END $$""")
    op.execute("DROP TABLE alfred_approvals")
