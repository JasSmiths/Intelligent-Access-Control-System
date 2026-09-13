"""Add fenced notification recovery without replaying historical work."""

from alembic import op

revision = "20260912_0001"
down_revision = "20260713_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Initial baseline creates current metadata on new installs. Old databases
    # get the same additive columns here; never backfill recovery eligibility.
    for name, definition in (
        ("recovery_version", "INTEGER"),
        ("claim_token", "UUID"),
        ("lease_expires_at", "TIMESTAMPTZ"),
        ("delivery_plan", "JSONB"),
        ("rules_override", "JSONB"),
        ("review_reason", "VARCHAR(120)"),
        ("claim_count", "INTEGER NOT NULL DEFAULT 0"),
    ):
        op.execute(f"ALTER TABLE notification_runs ADD COLUMN IF NOT EXISTS {name} {definition}")
    op.execute(
        "ALTER TABLE gate_malfunction_notification_outbox ADD COLUMN IF NOT EXISTS recovery_version INTEGER"
    )


def downgrade() -> None:
    # Never erase evidence or eligibility while recovered work exists.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM notification_runs WHERE recovery_version IS NOT NULL)
           OR EXISTS (SELECT 1 FROM gate_malfunction_notification_outbox WHERE recovery_version IS NOT NULL)
        THEN RAISE EXCEPTION 'Notification recovery records exist; retain additive schema for code rollback';
        END IF;
    END $$""")
    for name in (
        "claim_count",
        "review_reason",
        "rules_override",
        "delivery_plan",
        "lease_expires_at",
        "claim_token",
        "recovery_version",
    ):
        op.execute(f"ALTER TABLE notification_runs DROP COLUMN {name}")
    op.execute("ALTER TABLE gate_malfunction_notification_outbox DROP COLUMN recovery_version")
