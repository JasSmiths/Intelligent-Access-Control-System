"""Durable access pipeline and lossless historical/HA completion ownership.

Revision ID: 20261007_0013
Revises: 20261007_0012
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261007_0013"
down_revision = "20261007_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE lpr_ready_ordinal_seq AS bigint START 1")
    op.create_table(
        "lpr_debounce_windows",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("open_ordinal", sa.BigInteger(), unique=True, nullable=False),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("min_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("member_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "first_ingest_id",
            sa.Uuid(),
            sa.ForeignKey("lpr_ingest_events.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "best_ingest_id",
            sa.Uuid(),
            sa.ForeignKey("lpr_ingest_events.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "anchor_ingest_id",
            sa.Uuid(),
            sa.ForeignKey("lpr_ingest_events.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("first_ordinal", sa.BigInteger(), nullable=True),
        sa.Column("best_ordinal", sa.BigInteger(), nullable=True),
        sa.Column("anchor_ordinal", sa.BigInteger(), nullable=True),
        sa.Column("ranking_context", postgresql.JSONB(), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coordinator_epoch", sa.BigInteger(), nullable=True),
        sa.Column(
            "merged_into",
            sa.Uuid(),
            sa.ForeignKey("lpr_debounce_windows.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "access_event_id",
            sa.Uuid(),
            sa.ForeignKey("access_events.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "movement_saga_id",
            sa.Uuid(),
            sa.ForeignKey("movement_sagas.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(120), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('open','finalizing','completed','suppressed','held','merged')",
            name="ck_lpr_window_state",
        ),
        sa.CheckConstraint("member_count >= 0", name="ck_lpr_window_count"),
    )
    op.create_index(
        "ix_lpr_window_open_order", "lpr_debounce_windows", ["state", "source", "open_ordinal"]
    )
    op.create_index(
        "ix_lpr_window_lease", "lpr_debounce_windows", ["state", "lease_until", "open_ordinal"]
    )
    op.create_table(
        "lpr_window_members",
        sa.Column("ingest_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "window_id",
            sa.Uuid(),
            sa.ForeignKey("lpr_debounce_windows.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column("prepared_context", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["ingest_id"], ["lpr_ingest_events.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "window_id",
            "ordinal",
            name="uq_lpr_window_member_order",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    op.create_table(
        "lpr_observation_tokens",
        sa.Column("ingest_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("kind", sa.String(24), primary_key=True, nullable=False),
        sa.Column("value", sa.String(512), primary_key=True, nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["ingest_id"], ["lpr_ingest_events.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("kind IN ('ocr','protect_event')", name="ck_lpr_observation_token_kind"),
    )
    op.create_index(
        "ix_lpr_observation_token_match", "lpr_observation_tokens", ["kind", "value", "ingest_id"]
    )
    op.create_table(
        "movement_session_observations",
        sa.Column("ingest_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("movement_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column("suppression_reason", sa.String(120), nullable=True),
        sa.Column("context", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["ingest_id"], ["lpr_ingest_events.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("role IN ('window','suppressed')", name="ck_movement_observation_role"),
        sa.UniqueConstraint("session_id", "ordinal", name="uq_movement_observation_order"),
    )
    op.create_table(
        "lpr_coordinator_state",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_open_ordinal", sa.BigInteger(), nullable=False),
        sa.Column("handled_ready_ordinal", sa.BigInteger(), nullable=False),
        sa.Column("boundary_ingest_id", sa.Uuid(), nullable=True),
        sa.Column("boundary_phase", sa.String(32), nullable=True),
        sa.Column("flush_clock", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flush_upper_ordinal", sa.BigInteger(), nullable=True),
        sa.Column("flush_completed_ordinal", sa.BigInteger(), nullable=True),
        sa.Column("finalizing_window_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_lpr_coordinator_singleton"),
    )
    op.create_table(
        "access_completion_work",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "event_id",
            sa.Uuid(),
            sa.ForeignKey("access_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("window_id", sa.Uuid(), nullable=True),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("origin", postgresql.JSONB(), nullable=False),
        sa.Column("origin_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("readiness", sa.String(40), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("expected_revision", sa.BigInteger(), nullable=True),
        sa.Column("outcome", sa.String(120), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.UniqueConstraint("event_id", "stage", name="uq_access_completion_stage"),
        sa.CheckConstraint(
            "stage IN ('visual','snapshot','visitor_link','zone_shadow','publication')",
            name="ck_access_completion_stage",
        ),
        sa.CheckConstraint(
            "state IN ('waiting','queued','leased','succeeded','skipped','expired','held')",
            name="ck_access_completion_state",
        ),
    )
    op.create_index(
        "ix_access_completion_ready", "access_completion_work", ["stage", "state", "due_at", "id"]
    )
    op.create_index(
        "ix_access_completion_deadline", "access_completion_work", ["state", "deadline"]
    )
    op.create_table(
        "ha_presence_desired",
        sa.Column("person_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("presence_state", sa.String(24), nullable=True),
        sa.Column("presence_event_id", sa.Uuid(), nullable=True),
        sa.Column("binding_fingerprint", sa.String(64), nullable=False),
        sa.Column("config_fingerprint", sa.String(64), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("origin_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(120), nullable=False),
    )
    op.create_table(
        "ha_presence_targets",
        sa.Column("target_key", sa.String(64), primary_key=True, nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("entity_id", sa.String(255), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(20), nullable=True),
        sa.Column("binding_fingerprint", sa.String(64), nullable=False),
        sa.Column("config_fingerprint", sa.String(64), nullable=False),
        sa.Column("origin_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("barrier_version", sa.BigInteger(), nullable=False),
        sa.Column("barrier_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ha_presence_target_ready", "ha_presence_targets", ["state", "generation", "target_key"]
    )
    op.create_table(
        "ha_presence_effects",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "target_key",
            sa.String(64),
            sa.ForeignKey("ha_presence_targets.target_key", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("entity_id", sa.String(255), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("binding_fingerprint", sa.String(64), nullable=False),
        sa.Column("config_fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("owner_token", sa.Uuid(), nullable=False),
        sa.Column("owner_epoch", sa.BigInteger(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(120), nullable=True),
        sa.Column("detail", sa.String(500), nullable=True),
        sa.UniqueConstraint("target_key", "generation", name="uq_ha_presence_effect_generation"),
        sa.CheckConstraint(
            "state IN ('attempting','accepted','rejected','unknown')",
            name="ck_ha_presence_effect_state",
        ),
    )
    op.create_index(
        "ix_ha_presence_effect_lease", "ha_presence_effects", ["state", "lease_until", "id"]
    )
    op.create_table(
        "ha_presence_effect_resolutions",
        sa.Column("attempt_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("confirmation_id", sa.Uuid(), unique=True, nullable=False),
        sa.Column("target_key", sa.String(64), nullable=False),
        sa.Column("target_generation", sa.BigInteger(), nullable=False),
        sa.Column("barrier_version", sa.BigInteger(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("actor_auth_version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["ha_presence_effects.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "ha_presence_coordinator",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("next_generation", sa.BigInteger(), nullable=False),
        sa.Column("config_revision", sa.BigInteger(), nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_ha_presence_coordinator_singleton"),
    )
    op.create_table(
        "access_chart_dirty_state",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("requested_generation", sa.BigInteger(), nullable=False),
        sa.Column("acknowledged_generation", sa.BigInteger(), nullable=False),
        sa.Column("first_dirty_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_dirty_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_generation", sa.BigInteger(), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_origin_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_event_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_access_chart_dirty_singleton"),
    )
    op.create_table(
        "historical_access_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("protect_event_id", sa.String(180), nullable=False),
        sa.Column("candidate", postgresql.JSONB(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_id", sa.Uuid(), unique=True, nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(120), nullable=True),
        sa.Column("media_state", sa.String(24), nullable=False),
        sa.Column("media_token", sa.Uuid(), nullable=True),
        sa.Column("media_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("media_result", postgresql.JSONB(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.UniqueConstraint("source", "protect_event_id", name="uq_historical_candidate_origin"),
        sa.CheckConstraint(
            "state IN ('pending','leased','completed','skipped','held')",
            name="ck_historical_candidate_state",
        ),
    )
    op.create_index(
        "ix_historical_candidate_ready", "historical_access_candidates", ["state", "due_at", "id"]
    )
    op.add_column(
        "access_events",
        sa.Column("resource_revision", sa.BigInteger(), server_default="0", nullable=False),
    )
    for column in (
        sa.Column(
            "observation_state", sa.String(32), server_default="legacy_terminal", nullable=False
        ),
        sa.Column("original_observation", postgresql.JSONB(), nullable=True),
        sa.Column("observation_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capture_token", sa.Uuid(), nullable=True),
        sa.Column("capture_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_ordinal", sa.BigInteger(), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_id", sa.Uuid(), nullable=True),
        sa.Column("terminal_reason", sa.String(120), nullable=True),
    ):
        op.add_column("lpr_ingest_events", column)
    op.create_unique_constraint(
        "uq_lpr_ingest_ready_ordinal", "lpr_ingest_events", ["ready_ordinal"]
    )
    op.create_foreign_key(
        "fk_lpr_ingest_window",
        "lpr_ingest_events",
        "lpr_debounce_windows",
        ["window_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_lpr_ingest_observing", "lpr_ingest_events", ["observation_state", "capture_until", "id"]
    )
    op.create_index("ix_lpr_ingest_ready_order", "lpr_ingest_events", ["status", "ready_ordinal"])
    op.create_index("ix_lpr_ingest_window", "lpr_ingest_events", ["window_id", "id"])
    op.add_column(
        "vehicle_information_jobs",
        sa.Column("origin_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "vehicle_information_jobs",
        sa.Column("readiness", sa.String(40), server_default="legacy", nullable=False),
    )
    # Original payload/timestamps remain byte-identical. Lost RAM order and missing
    # first observation cannot be reconstructed or silently refreshed.
    op.execute(
        sa.text(
            "UPDATE lpr_ingest_events SET status='held', observation_state='held', terminal_reason='legacy_unordered' WHERE status IN ('pending','processing')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE vehicle_information_jobs SET status='held', readiness='held', outcome='legacy_origin_unavailable' WHERE status IN ('queued','processing')"
        )
    )


def downgrade() -> None:
    # Refuse before any DDL: a no-loss downgrade is only possible for empty new
    # state plus original terminal legacy rows. Restore verified backup otherwise.
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM lpr_debounce_windows) OR EXISTS (SELECT 1 FROM lpr_window_members) OR EXISTS (SELECT 1 FROM lpr_observation_tokens) OR EXISTS (SELECT 1 FROM movement_session_observations) OR EXISTS (SELECT 1 FROM lpr_coordinator_state) OR EXISTS (SELECT 1 FROM access_completion_work) OR EXISTS (SELECT 1 FROM ha_presence_desired) OR EXISTS (SELECT 1 FROM ha_presence_targets) OR EXISTS (SELECT 1 FROM ha_presence_effects) OR EXISTS (SELECT 1 FROM ha_presence_effect_resolutions) OR EXISTS (SELECT 1 FROM ha_presence_coordinator) OR EXISTS (SELECT 1 FROM access_chart_dirty_state) OR EXISTS (SELECT 1 FROM historical_access_candidates) OR EXISTS (SELECT 1 FROM lpr_ingest_events WHERE observation_state <> 'legacy_terminal' OR original_observation IS NOT NULL OR ready_ordinal IS NOT NULL OR capture_token IS NOT NULL OR capture_until IS NOT NULL OR observation_completed_at IS NOT NULL OR lease_token IS NOT NULL OR lease_until IS NOT NULL OR window_id IS NOT NULL OR terminal_reason IS NOT NULL) OR EXISTS (SELECT 1 FROM vehicle_information_jobs WHERE origin_at IS NOT NULL OR readiness <> 'legacy') OR EXISTS (SELECT 1 FROM access_events WHERE resource_revision <> 0)"
        )
    ):
        raise RuntimeError(
            "Populated access pipeline requires a verified matching backup/source restore"
        )
    op.execute("DROP SEQUENCE lpr_ready_ordinal_seq")
    op.drop_column("vehicle_information_jobs", "readiness")
    op.drop_column("vehicle_information_jobs", "origin_at")
    op.drop_constraint("fk_lpr_ingest_window", "lpr_ingest_events", type_="foreignkey")
    op.drop_constraint("uq_lpr_ingest_ready_ordinal", "lpr_ingest_events", type_="unique")
    for name in ("ix_lpr_ingest_observing", "ix_lpr_ingest_ready_order", "ix_lpr_ingest_window"):
        op.drop_index(name, table_name="lpr_ingest_events")
    for name in (
        "terminal_reason",
        "window_id",
        "lease_until",
        "lease_token",
        "ready_ordinal",
        "capture_until",
        "capture_token",
        "observation_completed_at",
        "original_observation",
        "observation_state",
    ):
        op.drop_column("lpr_ingest_events", name)
    op.drop_column("access_events", "resource_revision")
    op.drop_table("historical_access_candidates")
    op.drop_table("access_chart_dirty_state")
    op.drop_table("ha_presence_coordinator")
    op.drop_table("ha_presence_effect_resolutions")
    op.drop_table("ha_presence_effects")
    op.drop_table("ha_presence_targets")
    op.drop_table("ha_presence_desired")
    op.drop_table("access_completion_work")
    op.drop_table("lpr_coordinator_state")
    op.drop_table("movement_session_observations")
    op.drop_table("lpr_observation_tokens")
    op.drop_table("lpr_window_members")
    op.drop_table("lpr_debounce_windows")
