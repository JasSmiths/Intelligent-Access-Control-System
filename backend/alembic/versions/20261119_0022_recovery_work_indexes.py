"""Index outstanding admissions and time-sensitive resident phone requests."""
from alembic import op
import sqlalchemy as sa

revision = "20261119_0022"
down_revision = "20261118_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_people_mobile_destination", "people", ["home_assistant_mobile_app_notify_service"],
                    postgresql_where=sa.text("home_assistant_mobile_app_notify_service IS NOT NULL"))
    op.create_index("ix_access_events_visitor_pass_time", "access_events",
                    [sa.text("((raw_payload -> 'visitor_pass'::text) ->> 'id'::text)"), sa.text("occurred_at DESC"), sa.text("id DESC")],
                    postgresql_where=sa.text("((raw_payload -> 'visitor_pass'::text) ->> 'id'::text) IS NOT NULL"))
    op.create_index("ix_movement_sagas_pending_admission", "movement_sagas", ["id"],
                    postgresql_where=sa.text("admission_status='pending'"))
    op.create_index("ix_notification_runs_active_location", "notification_runs", ["queued_at", "id"],
                    postgresql_where=sa.text("recovery_version=1 AND status IN ('queued','processing') "
                                             "AND trigger_event='resident_location_refresh'"))


def downgrade() -> None:
    op.drop_index("ix_people_mobile_destination", table_name="people")
    op.drop_index("ix_access_events_visitor_pass_time", table_name="access_events")
    op.drop_index("ix_notification_runs_active_location", table_name="notification_runs")
    op.drop_index("ix_movement_sagas_pending_admission", table_name="movement_sagas")
