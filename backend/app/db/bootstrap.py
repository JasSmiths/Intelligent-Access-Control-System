from sqlalchemy import text

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import Base
from app.db.session import engine
from app.services.alfred.feedback import alfred_feedback_service
from app.services.auth_secret_management import migrate_encrypted_payloads_for_active_auth_secret
from app.services.settings import seed_dynamic_settings

logger = get_logger(__name__)


async def init_database() -> None:
    """Create schema and seed dynamic settings for this early-phase deployment.

    Alembic migrations should take over once the schema stabilizes. Until then,
    startup creation keeps `docker compose up` useful on a clean machine.
    """

    if settings.auto_create_schema:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE people ADD COLUMN IF NOT EXISTS first_name VARCHAR(80) NOT NULL DEFAULT ''"))
            await conn.execute(text("ALTER TABLE people ADD COLUMN IF NOT EXISTS last_name VARCHAR(80) NOT NULL DEFAULT ''"))
            await conn.execute(text("ALTER TABLE people ADD COLUMN IF NOT EXISTS pronouns VARCHAR(24)"))
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
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS fuel_type VARCHAR(80)"))
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
            await conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS vehicle_person_assignments (
                        vehicle_id UUID NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
                        person_id UUID NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                        PRIMARY KEY (vehicle_id, person_id)
                    )
                    """
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vehicle_person_assignments_person_id ON vehicle_person_assignments (person_id)"))
            await conn.execute(
                text(
                    """
                    INSERT INTO vehicle_person_assignments (vehicle_id, person_id)
                    SELECT id, person_id
                    FROM vehicles
                    WHERE person_id IS NOT NULL
                    ON CONFLICT DO NOTHING
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    WITH derived_vehicle_people AS (
                        SELECT
                            vehicles.id AS vehicle_id,
                            CASE
                                WHEN count(vehicle_person_assignments.person_id) = 1
                                    THEN min(vehicle_person_assignments.person_id::text)::uuid
                                ELSE NULL
                            END AS derived_person_id
                        FROM vehicles
                        LEFT JOIN vehicle_person_assignments
                            ON vehicle_person_assignments.vehicle_id = vehicles.id
                        GROUP BY vehicles.id
                    )
                    UPDATE vehicles
                    SET person_id = derived_vehicle_people.derived_person_id
                    FROM derived_vehicle_people
                    WHERE vehicles.id = derived_vehicle_people.vehicle_id
                    """
                )
            )
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name VARCHAR(80) NOT NULL DEFAULT ''"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name VARCHAR(80) NOT NULL DEFAULT ''"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_photo_data_url TEXT"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS mobile_phone_number VARCHAR(40)"))
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
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notification_rules_trigger_active_created ON notification_rules (trigger_event, is_active, created_at DESC)"))
            await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_action_confirmations_token_hash ON action_confirmations (token_hash)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_action_confirmations_action ON action_confirmations (action)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_action_confirmations_payload_hash ON action_confirmations (payload_hash)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_action_confirmations_actor_user_id ON action_confirmations (actor_user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_action_confirmations_target_entity ON action_confirmations (target_entity)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_action_confirmations_target_id ON action_confirmations (target_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_action_confirmations_expires_at ON action_confirmations (expires_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_action_confirmations_consumed_at ON action_confirmations (consumed_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_action_confirmations_outcome ON action_confirmations (outcome)"))
            await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_notification_action_contexts_token_hash ON notification_action_contexts (token_hash)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notification_action_contexts_action ON notification_action_contexts (action)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notification_action_contexts_notify_service ON notification_action_contexts (notify_service)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notification_action_contexts_registration_number ON notification_action_contexts (registration_number)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notification_action_contexts_access_event_id ON notification_action_contexts (access_event_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notification_action_contexts_telemetry_trace_id ON notification_action_contexts (telemetry_trace_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notification_action_contexts_person_id ON notification_action_contexts (person_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notification_action_contexts_actor_user_id ON notification_action_contexts (actor_user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notification_action_contexts_parent_context_id ON notification_action_contexts (parent_context_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notification_action_contexts_expires_at ON notification_action_contexts (expires_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notification_action_contexts_consumed_at ON notification_action_contexts (consumed_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notification_action_contexts_outcome ON notification_action_contexts (outcome)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_automation_rules_trigger_keys_gin ON automation_rules USING gin (trigger_keys)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_automation_rules_active_next_run ON automation_rules (is_active, next_run_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_automation_rules_active_created ON automation_rules (is_active, created_at DESC)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_automation_runs_rule_started ON automation_runs (rule_id, started_at DESC)"))
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
            await conn.execute(text("ALTER TABLE access_events ADD COLUMN IF NOT EXISTS snapshot_path VARCHAR(512)"))
            await conn.execute(text("ALTER TABLE access_events ADD COLUMN IF NOT EXISTS snapshot_content_type VARCHAR(80)"))
            await conn.execute(text("ALTER TABLE access_events ADD COLUMN IF NOT EXISTS snapshot_bytes INTEGER"))
            await conn.execute(text("ALTER TABLE access_events ADD COLUMN IF NOT EXISTS snapshot_width INTEGER"))
            await conn.execute(text("ALTER TABLE access_events ADD COLUMN IF NOT EXISTS snapshot_height INTEGER"))
            await conn.execute(text("ALTER TABLE access_events ADD COLUMN IF NOT EXISTS snapshot_captured_at TIMESTAMP WITH TIME ZONE"))
            await conn.execute(text("ALTER TABLE access_events ADD COLUMN IF NOT EXISTS snapshot_camera VARCHAR(120)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_access_events_created_at ON access_events (created_at)"))
            await conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_access_events_decision_occurred_person_vehicle
                    ON access_events (decision, occurred_at DESC, person_id, vehicle_id)
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_access_events_snapshot_created_at
                    ON access_events (created_at)
                    WHERE snapshot_path IS NOT NULL
                    """
                )
            )
            await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_report_exports_report_number ON report_exports (report_number)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_report_exports_report_type ON report_exports (report_type)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_report_exports_person_id ON report_exports (person_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_report_exports_created_by_user_id ON report_exports (created_by_user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_report_exports_person_created ON report_exports (person_id, created_at)"))
            await conn.execute(text("ALTER TABLE gate_malfunction_states ADD COLUMN IF NOT EXISTS attempt_claim_token VARCHAR(64)"))
            await conn.execute(text("ALTER TABLE gate_malfunction_states ADD COLUMN IF NOT EXISTS attempt_claimed_at TIMESTAMP WITH TIME ZONE"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_gate_malfunction_states_attempt_claim_token ON gate_malfunction_states (attempt_claim_token)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_gate_malfunction_states_attempt_claimed_at ON gate_malfunction_states (attempt_claimed_at)"))
            await conn.execute(text("ALTER TABLE gate_malfunction_notification_outbox ADD COLUMN IF NOT EXISTS stage VARCHAR(40) NOT NULL DEFAULT 'initial'"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_gate_malfunction_notification_outbox_stage ON gate_malfunction_notification_outbox (stage)"))
            await conn.execute(
                text(
                    """
                    ALTER TABLE gate_malfunction_notification_outbox
                    DROP CONSTRAINT IF EXISTS uq_gate_malfunction_notification_trigger
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    UPDATE gate_malfunction_notification_outbox
                    SET stage = CASE trigger
                        WHEN 'gate_malfunction_30m' THEN '30m'
                        WHEN 'gate_malfunction_60m' THEN '60m'
                        WHEN 'gate_malfunction_2hrs' THEN '2hrs'
                        WHEN 'gate_malfunction_fubar' THEN 'fubar'
                        ELSE COALESCE(NULLIF(stage, ''), 'initial')
                    END,
                    trigger = 'gate_malfunction'
                    WHERE trigger LIKE 'gate_malfunction%'
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    WITH ranked AS (
                        SELECT
                            id,
                            row_number() OVER (
                                PARTITION BY malfunction_id, stage
                                ORDER BY occurred_at DESC, created_at DESC, id DESC
                            ) AS rank
                        FROM gate_malfunction_notification_outbox
                    )
                    DELETE FROM gate_malfunction_notification_outbox AS outbox
                    USING ranked
                    WHERE outbox.id = ranked.id
                      AND ranked.rank > 1
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM pg_constraint
                            WHERE conname = 'uq_gate_malfunction_notification_stage'
                            AND conrelid = 'gate_malfunction_notification_outbox'::regclass
                        ) THEN
                            ALTER TABLE gate_malfunction_notification_outbox
                            ADD CONSTRAINT uq_gate_malfunction_notification_stage
                            UNIQUE (malfunction_id, stage);
                        END IF;
                    EXCEPTION
                        WHEN duplicate_object OR duplicate_table THEN NULL;
                    END $$;
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    UPDATE notification_rules
                    SET
                        trigger_event = 'gate_malfunction',
                        actions = (
                            SELECT jsonb_agg(
                                CASE
                                    WHEN action ? 'gate_malfunction_stages' THEN action
                                    ELSE jsonb_set(action, '{gate_malfunction_stages}', jsonb_build_array(
                                        CASE notification_rules.trigger_event
                                            WHEN 'gate_malfunction_30m' THEN '30m'
                                            WHEN 'gate_malfunction_60m' THEN '60m'
                                            WHEN 'gate_malfunction_2hrs' THEN '2hrs'
                                            WHEN 'gate_malfunction_fubar' THEN 'fubar'
                                            ELSE 'initial'
                                        END
                                    ))
                                END
                            )
                            FROM jsonb_array_elements(actions) AS action
                        )
                    WHERE trigger_event IN (
                        'gate_malfunction_initial',
                        'gate_malfunction_30m',
                        'gate_malfunction_60m',
                        'gate_malfunction_2hrs',
                        'gate_malfunction_fubar'
                    )
                    """
                )
            )
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
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_anomalies_open_created_at ON anomalies (created_at DESC) WHERE resolved_at IS NULL"))
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
            await conn.execute(text("ALTER TABLE visitor_passes ADD COLUMN IF NOT EXISTS pass_type VARCHAR(20) NOT NULL DEFAULT 'ONE_TIME'"))
            await conn.execute(text("ALTER TABLE visitor_passes ADD COLUMN IF NOT EXISTS visitor_phone VARCHAR(40)"))
            await conn.execute(text("ALTER TABLE visitor_passes ADD COLUMN IF NOT EXISTS source_reference VARCHAR(255)"))
            await conn.execute(text("ALTER TABLE visitor_passes ADD COLUMN IF NOT EXISTS source_metadata JSONB"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_visitor_passes_pass_type ON visitor_passes (pass_type)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_visitor_passes_visitor_phone ON visitor_passes (visitor_phone)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_visitor_passes_valid_from ON visitor_passes (valid_from)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_visitor_passes_valid_until ON visitor_passes (valid_until)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_visitor_passes_source_reference ON visitor_passes (source_reference)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_visitor_passes_visitor_phone_status ON visitor_passes (visitor_phone, status)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_visitor_passes_status_valid_until ON visitor_passes (status, valid_until)"))
            await conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_visitor_passes_duration_phone_window
                    ON visitor_passes (visitor_phone, status, valid_from, valid_until)
                    WHERE pass_type = 'DURATION'
                        AND visitor_phone IS NOT NULL
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    DO $$
                    DECLARE
                        current_predicate TEXT;
                    BEGIN
                        SELECT pg_get_expr(indexes.indpred, indexes.indrelid)
                        INTO current_predicate
                        FROM pg_index indexes
                        JOIN pg_class classes ON classes.oid = indexes.indexrelid
                        WHERE classes.relname = 'ix_visitor_passes_open_departure_lookup';

                        IF FOUND AND (
                            current_predicate IS NULL
                            OR current_predicate NOT ILIKE '%pass_type%'
                        ) THEN
                            DROP INDEX IF EXISTS ix_visitor_passes_open_departure_lookup;
                        END IF;
                    END $$;
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_visitor_passes_open_departure_lookup
                    ON visitor_passes (number_plate, arrival_time DESC, created_at DESC)
                    WHERE (
                            status = 'USED'
                            OR (pass_type = 'DURATION' AND status = 'ACTIVE')
                        )
                        AND departure_time IS NULL
                        AND arrival_time IS NOT NULL
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
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_messaging_identities_provider ON messaging_identities (provider)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_messaging_identities_provider_user_id ON messaging_identities (provider_user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_messaging_identities_user_id ON messaging_identities (user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_messaging_identities_person_id ON messaging_identities (person_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_messaging_identities_last_seen_at ON messaging_identities (last_seen_at)"))
            await _ensure_alfred_semantic_schema(conn)
        logger.info("database_schema_ready")

    migration = await migrate_encrypted_payloads_for_active_auth_secret()
    if migration["settings"] or migration["icloud_accounts"]:
        logger.info("auth_secret_encrypted_payloads_migrated", extra=migration)

    await seed_dynamic_settings()
    await alfred_feedback_service.seed_default_lessons()
    await alfred_feedback_service.seed_default_eval_examples()

    if settings.seed_demo_data:
        logger.warning("seed_demo_data_ignored")


async def _ensure_alfred_semantic_schema(conn) -> None:
    """Keep Alfred semantic columns/indexes present for bootstrap deployments."""

    for table_name in (
        "alfred_memories",
        "alfred_lessons",
        "alfred_feedback",
        "alfred_eval_examples",
    ):
        await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS embedding vector(1536)"))

    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_alfred_memories_embedding_hnsw
            ON alfred_memories USING hnsw (embedding vector_cosine_ops)
            WHERE embedding IS NOT NULL
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_alfred_lessons_embedding_hnsw
            ON alfred_lessons USING hnsw (embedding vector_cosine_ops)
            WHERE embedding IS NOT NULL
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_alfred_feedback_embedding_hnsw
            ON alfred_feedback USING hnsw (embedding vector_cosine_ops)
            WHERE embedding IS NOT NULL
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_alfred_eval_examples_embedding_hnsw
            ON alfred_eval_examples USING hnsw (embedding vector_cosine_ops)
            WHERE embedding IS NOT NULL
            """
        )
    )
