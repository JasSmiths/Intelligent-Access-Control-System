from sqlalchemy import text

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import Base
from app.db.session import engine
from app.services.settings import seed_dynamic_settings

logger = get_logger(__name__)


async def init_database() -> None:
    """Create schema and seed dynamic settings for this early-phase deployment.

    Alembic migrations should take over once the schema stabilizes. Until then,
    startup creation keeps `docker compose up` useful on a clean machine.
    """

    if settings.auto_create_schema:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE people ADD COLUMN IF NOT EXISTS first_name VARCHAR(80) NOT NULL DEFAULT ''"))
            await conn.execute(text("ALTER TABLE people ADD COLUMN IF NOT EXISTS last_name VARCHAR(80) NOT NULL DEFAULT ''"))
            await conn.execute(text("ALTER TABLE people ADD COLUMN IF NOT EXISTS profile_photo_data_url TEXT"))
            await conn.execute(text("ALTER TABLE people ADD COLUMN IF NOT EXISTS schedule_id UUID"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_people_schedule_id ON people (schedule_id)"))
            await conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        ALTER TABLE people
                        ADD CONSTRAINT fk_people_schedule_id
                        FOREIGN KEY (schedule_id) REFERENCES schedules(id) ON DELETE SET NULL;
                    EXCEPTION
                        WHEN duplicate_object THEN NULL;
                    END $$;
                    """
                )
            )
            await conn.execute(text("ALTER TABLE people ADD COLUMN IF NOT EXISTS garage_door_entity_ids JSONB NOT NULL DEFAULT '[]'::jsonb"))
            await conn.execute(text("ALTER TABLE people ADD COLUMN IF NOT EXISTS home_assistant_mobile_app_notify_service VARCHAR(255)"))
            await conn.execute(
                text(
                    """
                    UPDATE people
                    SET
                        first_name = CASE
                            WHEN first_name = '' THEN split_part(display_name, ' ', 1)
                            ELSE first_name
                        END,
                        last_name = CASE
                            WHEN last_name = '' AND position(' ' in display_name) > 0
                                THEN trim(substr(display_name, position(' ' in display_name) + 1))
                            ELSE last_name
                        END
                    """
                )
            )
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS vehicle_photo_data_url TEXT"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS schedule_id UUID"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS mot_status VARCHAR(80)"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS tax_status VARCHAR(80)"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS mot_expiry DATE"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS tax_expiry DATE"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS last_dvla_lookup_date DATE"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vehicles_schedule_id ON vehicles (schedule_id)"))
            await conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        ALTER TABLE vehicles
                        ADD CONSTRAINT fk_vehicles_schedule_id
                        FOREIGN KEY (schedule_id) REFERENCES schedules(id) ON DELETE SET NULL;
                    EXCEPTION
                        WHEN duplicate_object THEN NULL;
                    END $$;
                    """
                )
            )
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name VARCHAR(80) NOT NULL DEFAULT ''"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name VARCHAR(80) NOT NULL DEFAULT ''"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_photo_data_url TEXT"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS person_id UUID"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_person_id ON users (person_id)"))
            await conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        ALTER TABLE users
                        ADD CONSTRAINT fk_users_person_id
                        FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE SET NULL;
                    EXCEPTION
                        WHEN duplicate_object THEN NULL;
                    END $$;
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    UPDATE users
                    SET
                        first_name = CASE
                            WHEN first_name = '' THEN split_part(full_name, ' ', 1)
                            ELSE first_name
                        END,
                        last_name = CASE
                            WHEN last_name = '' AND position(' ' in full_name) > 0
                                THEN trim(substr(full_name, position(' ' in full_name) + 1))
                            ELSE last_name
                        END
                    """
                )
            )
            await conn.execute(text("ALTER TABLE notification_rules ADD COLUMN IF NOT EXISTS last_fired_at TIMESTAMP WITH TIME ZONE"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notification_rules_last_fired_at ON notification_rules (last_fired_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_logs_timestamp ON audit_logs (timestamp)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_logs_category ON audit_logs (category)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_logs_action ON audit_logs (action)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_logs_actor ON audit_logs (actor)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_logs_actor_user_id ON audit_logs (actor_user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_logs_target_entity ON audit_logs (target_entity)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_logs_target_id ON audit_logs (target_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_logs_outcome ON audit_logs (outcome)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_logs_level ON audit_logs (level)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_logs_trace_id ON audit_logs (trace_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_logs_request_id ON audit_logs (request_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_traces_name ON telemetry_traces (name)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_traces_category ON telemetry_traces (category)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_traces_status ON telemetry_traces (status)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_traces_level ON telemetry_traces (level)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_traces_started_at ON telemetry_traces (started_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_traces_ended_at ON telemetry_traces (ended_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_traces_duration_ms ON telemetry_traces (duration_ms)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_traces_actor ON telemetry_traces (actor)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_traces_source ON telemetry_traces (source)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_traces_registration_number ON telemetry_traces (registration_number)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_traces_access_event_id ON telemetry_traces (access_event_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_spans_span_id ON telemetry_spans (span_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_spans_trace_id ON telemetry_spans (trace_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_spans_parent_span_id ON telemetry_spans (parent_span_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_spans_name ON telemetry_spans (name)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_spans_category ON telemetry_spans (category)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_spans_step_order ON telemetry_spans (step_order)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_spans_started_at ON telemetry_spans (started_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telemetry_spans_status ON telemetry_spans (status)"))
            await conn.execute(text("ALTER TABLE gate_malfunction_states ADD COLUMN IF NOT EXISTS attempt_claim_token VARCHAR(64)"))
            await conn.execute(text("ALTER TABLE gate_malfunction_states ADD COLUMN IF NOT EXISTS attempt_claimed_at TIMESTAMP WITH TIME ZONE"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_gate_malfunction_states_attempt_claim_token ON gate_malfunction_states (attempt_claim_token)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_gate_malfunction_states_attempt_claimed_at ON gate_malfunction_states (attempt_claimed_at)"))
            await conn.execute(
                text(
                    """
                    WITH ranked AS (
                        SELECT
                            id,
                            row_number() OVER (
                                PARTITION BY gate_entity_id
                                ORDER BY opened_at DESC, created_at DESC
                            ) AS row_number
                        FROM gate_malfunction_states
                        WHERE status IN ('ACTIVE', 'FUBAR')
                    )
                    UPDATE gate_malfunction_states AS state
                    SET
                        status = 'RESOLVED',
                        resolved_at = COALESCE(state.resolved_at, now()),
                        next_attempt_scheduled_at = NULL,
                        last_checked_at = now()
                    FROM ranked
                    WHERE state.id = ranked.id
                    AND ranked.row_number > 1
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS ux_gate_malfunction_unresolved_gate
                    ON gate_malfunction_states (gate_entity_id)
                    WHERE status IN ('ACTIVE', 'FUBAR')
                    """
                )
            )
            await conn.execute(text("ALTER TABLE gate_malfunction_timeline_events ADD COLUMN IF NOT EXISTS status VARCHAR(40) NOT NULL DEFAULT 'ok'"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_gate_malfunction_timeline_events_status ON gate_malfunction_timeline_events (status)"))
            await conn.execute(text("ALTER TABLE anomalies ADD COLUMN IF NOT EXISTS resolved_by_user_id UUID"))
            await conn.execute(text("ALTER TABLE anomalies ADD COLUMN IF NOT EXISTS resolution_note TEXT"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_anomalies_resolved_at ON anomalies (resolved_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_anomalies_resolved_by_user_id ON anomalies (resolved_by_user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_anomalies_anomaly_type ON anomalies (anomaly_type)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_anomalies_severity ON anomalies (severity)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_anomalies_created_at ON anomalies (created_at)"))
            await conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        ALTER TABLE anomalies
                        ADD CONSTRAINT fk_anomalies_resolved_by_user_id
                        FOREIGN KEY (resolved_by_user_id) REFERENCES users(id) ON DELETE SET NULL;
                    EXCEPTION
                        WHEN duplicate_object THEN NULL;
                    END $$;
                    """
                )
            )
            await conn.execute(text("ALTER TABLE visitor_passes ADD COLUMN IF NOT EXISTS valid_from TIMESTAMP WITH TIME ZONE"))
            await conn.execute(text("ALTER TABLE visitor_passes ADD COLUMN IF NOT EXISTS valid_until TIMESTAMP WITH TIME ZONE"))
            await conn.execute(text("ALTER TABLE visitor_passes ADD COLUMN IF NOT EXISTS source_reference VARCHAR(255)"))
            await conn.execute(text("ALTER TABLE visitor_passes ADD COLUMN IF NOT EXISTS source_metadata JSONB"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_visitor_passes_valid_from ON visitor_passes (valid_from)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_visitor_passes_valid_until ON visitor_passes (valid_until)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_visitor_passes_source_reference ON visitor_passes (source_reference)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_visitor_passes_status_valid_until ON visitor_passes (status, valid_until)"))
            await conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_visitor_passes_open_departure_lookup
                    ON visitor_passes (number_plate, arrival_time DESC, created_at DESC)
                    WHERE status = 'USED'
                        AND departure_time IS NULL
                        AND number_plate IS NOT NULL
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_visitor_passes_calendar_active_source
                    ON visitor_passes (creation_source, source_reference, status)
                    WHERE creation_source = 'icloud_calendar'
                        AND status IN ('SCHEDULED', 'ACTIVE')
                        AND source_reference IS NOT NULL
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS ux_visitor_passes_source_reference
                    ON visitor_passes (source_reference)
                    WHERE source_reference IS NOT NULL
                    """
                )
            )
        logger.info("database_schema_ready")

    await seed_dynamic_settings()

    if settings.seed_demo_data:
        logger.warning("seed_demo_data_ignored")
