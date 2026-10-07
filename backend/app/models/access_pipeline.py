"""Durable access evidence, completion and ordered HA effects; no provider I/O."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LprDebounceWindow(Base):
    __tablename__ = "lpr_debounce_windows"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, nullable=False)
    open_ordinal: Mapped[int] = mapped_column(BigInteger(), unique=True, nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    min_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    member_count: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    first_ingest_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("lpr_ingest_events.id", ondelete="RESTRICT"), nullable=True
    )
    best_ingest_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("lpr_ingest_events.id", ondelete="RESTRICT"), nullable=True
    )
    anchor_ingest_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("lpr_ingest_events.id", ondelete="RESTRICT"), nullable=True
    )
    first_ordinal: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    best_ordinal: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    anchor_ordinal: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    ranking_context: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    coordinator_epoch: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    merged_into: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("lpr_debounce_windows.id", ondelete="RESTRICT"), nullable=True
    )
    access_event_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("access_events.id", ondelete="RESTRICT"), nullable=True
    )
    movement_saga_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("movement_sagas.id", ondelete="RESTRICT"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )
    __table_args__ = (
        CheckConstraint(
            "state IN ('open','finalizing','completed','suppressed','held','merged')",
            name="ck_lpr_window_state",
        ),
        CheckConstraint("member_count >= 0", name="ck_lpr_window_count"),
        Index("ix_lpr_window_open_order", "state", "source", "open_ordinal"),
        Index("ix_lpr_window_lease", "state", "lease_until", "open_ordinal"),
    )


class LprWindowMember(Base):
    __tablename__ = "lpr_window_members"
    ingest_id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, nullable=False)
    window_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("lpr_debounce_windows.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    prepared_context: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["ingest_id"], ["lpr_ingest_events.id"], ondelete="RESTRICT"),
        UniqueConstraint(
            "window_id",
            "ordinal",
            name="uq_lpr_window_member_order",
            deferrable=True,
            initially="DEFERRED",
        ),
    )


class LprObservationToken(Base):
    __tablename__ = "lpr_observation_tokens"
    ingest_id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), primary_key=True, nullable=False)
    value: Mapped[str] = mapped_column(String(512), primary_key=True, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer(), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["ingest_id"], ["lpr_ingest_events.id"], ondelete="RESTRICT"),
        CheckConstraint("kind IN ('ocr','protect_event')", name="ck_lpr_observation_token_kind"),
        Index("ix_lpr_observation_token_match", "kind", "value", "ingest_id"),
    )


class MovementSessionObservation(Base):
    __tablename__ = "movement_session_observations"
    ingest_id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("movement_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    ordinal: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    suppression_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["ingest_id"], ["lpr_ingest_events.id"], ondelete="RESTRICT"),
        CheckConstraint("role IN ('window','suppressed')", name="ck_movement_observation_role"),
        UniqueConstraint("session_id", "ordinal", name="uq_movement_observation_order"),
    )


class LprCoordinatorState(Base):
    __tablename__ = "lpr_coordinator_state"
    id: Mapped[int] = mapped_column(Integer(), primary_key=True, nullable=False)
    epoch: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_ready_ordinal: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    next_open_ordinal: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    handled_ready_ordinal: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    boundary_ingest_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    boundary_phase: Mapped[str | None] = mapped_column(String(32), nullable=True)
    flush_clock: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    flush_upper_ordinal: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    flush_completed_ordinal: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    finalizing_window_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    __table_args__ = (CheckConstraint("id = 1", name="ck_lpr_coordinator_singleton"),)


class AccessCompletionWork(Base):
    __tablename__ = "access_completion_work"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("access_events.id", ondelete="RESTRICT"), nullable=False
    )
    window_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    origin: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
    origin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    readiness: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    expected_revision: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(120), nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )
    __table_args__ = (
        UniqueConstraint("event_id", "stage", name="uq_access_completion_stage"),
        CheckConstraint(
            "stage IN ('visual','snapshot','visitor_link','zone_shadow','publication')",
            name="ck_access_completion_stage",
        ),
        CheckConstraint(
            "state IN ('waiting','queued','leased','succeeded','skipped','expired','held')",
            name="ck_access_completion_state",
        ),
        Index("ix_access_completion_ready", "stage", "state", "due_at", "id"),
        Index("ix_access_completion_deadline", "state", "deadline"),
    )


class HaPresenceDesired(Base):
    __tablename__ = "ha_presence_desired"
    person_id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    presence_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    presence_event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    binding_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    origin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String(120), nullable=False)


class HaPresenceTarget(Base):
    __tablename__ = "ha_presence_targets"
    target_key: Mapped[str] = mapped_column(String(64), primary_key=True, nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    person_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    action: Mapped[str | None] = mapped_column(String(20), nullable=True)
    binding_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    origin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    barrier_version: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    barrier_attempt_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index("ix_ha_presence_target_ready", "state", "generation", "target_key"),)


class HaPresenceEffect(Base):
    __tablename__ = "ha_presence_effects"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, nullable=False)
    target_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ha_presence_targets.target_key", ondelete="RESTRICT"),
        nullable=False,
    )
    generation: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    person_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    binding_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    owner_token: Mapped[uuid.UUID] = mapped_column(Uuid(), nullable=False)
    owner_epoch: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(120), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    __table_args__ = (
        UniqueConstraint("target_key", "generation", name="uq_ha_presence_effect_generation"),
        CheckConstraint(
            "state IN ('attempting','accepted','rejected','unknown')",
            name="ck_ha_presence_effect_state",
        ),
        Index("ix_ha_presence_effect_lease", "state", "lease_until", "id"),
    )


class HaPresenceResolution(Base):
    __tablename__ = "ha_presence_effect_resolutions"
    attempt_id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, nullable=False)
    confirmation_id: Mapped[uuid.UUID] = mapped_column(Uuid(), unique=True, nullable=False)
    target_key: Mapped[str] = mapped_column(String(64), nullable=False)
    target_generation: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    barrier_version: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid(), nullable=False)
    actor_auth_version: Mapped[int] = mapped_column(Integer(), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["attempt_id"], ["ha_presence_effects.id"], ondelete="RESTRICT"),
    )


class HaPresenceCoordinator(Base):
    __tablename__ = "ha_presence_coordinator"
    id: Mapped[int] = mapped_column(Integer(), primary_key=True, nullable=False)
    next_generation: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    config_revision: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    epoch: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (CheckConstraint("id = 1", name="ck_ha_presence_coordinator_singleton"),)


class AccessChartDirtyState(Base):
    __tablename__ = "access_chart_dirty_state"
    id: Mapped[int] = mapped_column(Integer(), primary_key=True, nullable=False)
    requested_generation: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    acknowledged_generation: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    first_dirty_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_dirty_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_generation: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    live_origin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    live_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    live_event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    __table_args__ = (CheckConstraint("id = 1", name="ck_access_chart_dirty_singleton"),)


class HistoricalAccessCandidate(Base):
    __tablename__ = "historical_access_candidates"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    protect_event_id: Mapped[str] = mapped_column(String(180), nullable=False)
    candidate: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid(), unique=True, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    media_state: Mapped[str] = mapped_column(String(24), nullable=False)
    media_token: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    media_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    media_result: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )
    __table_args__ = (
        UniqueConstraint("source", "protect_event_id", name="uq_historical_candidate_origin"),
        CheckConstraint(
            "state IN ('pending','leased','completed','skipped','held')",
            name="ck_historical_candidate_state",
        ),
        Index("ix_historical_candidate_ready", "state", "due_at", "id"),
    )
