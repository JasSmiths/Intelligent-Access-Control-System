import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.enums import (
    AccessDecision,
    AccessDirection,
    AnomalySeverity,
    AnomalyType,
    GroupCategory,
    PresenceState,
    TimingClassification,
    UserRole,
)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Group(Base, TimestampMixin):
    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    category: Mapped[GroupCategory] = mapped_column(Enum(GroupCategory), nullable=False)
    subtype: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)

    people: Mapped[list["Person"]] = relationship(back_populates="group")


class Person(Base, TimestampMixin):
    __tablename__ = "people"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    first_name: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    profile_photo_data_url: Mapped[str | None] = mapped_column(Text)
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"))
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"), index=True
    )
    garage_door_entity_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    home_assistant_mobile_app_notify_service: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    group: Mapped[Group | None] = relationship(back_populates="people")
    schedule: Mapped["Schedule | None"] = relationship(back_populates="people")
    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="owner")
    presence: Mapped["Presence | None"] = relationship(back_populates="person")


class Vehicle(Base, TimestampMixin):
    __tablename__ = "vehicles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"))
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"), index=True
    )
    registration_number: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    vehicle_photo_data_url: Mapped[str | None] = mapped_column(Text)
    make: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(120))
    color: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    owner: Mapped[Person | None] = relationship(back_populates="vehicles")
    schedule: Mapped["Schedule | None"] = relationship(back_populates="vehicles")
    events: Mapped[list["AccessEvent"]] = relationship(back_populates="vehicle")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    first_name: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    profile_photo_data_url: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.STANDARD, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preferences: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class SystemSetting(Base, TimestampMixin):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    target_entity: Mapped[str | None] = mapped_column(String(120), index=True)
    target_id: Mapped[str | None] = mapped_column(String(160), index=True)
    target_label: Mapped[str | None] = mapped_column(String(240))
    diff: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    outcome: Mapped[str] = mapped_column(String(80), default="success", nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(40), default="info", nullable=False, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), index=True)
    request_id: Mapped[str | None] = mapped_column(String(80), index=True)


class TelemetryTrace(Base):
    __tablename__ = "telemetry_traces"

    trace_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), default="ok", nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(40), default="info", nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, index=True)
    actor: Mapped[str | None] = mapped_column(String(160), index=True)
    source: Mapped[str | None] = mapped_column(String(120), index=True)
    registration_number: Mapped[str | None] = mapped_column(String(32), index=True)
    access_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("access_events.id", ondelete="SET NULL"), index=True
    )
    summary: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)


class TelemetrySpan(Base):
    __tablename__ = "telemetry_spans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    span_id: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    step_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, index=True)
    status: Mapped[str] = mapped_column(String(40), default="ok", nullable=False, index=True)
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    input_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)


class NotificationRule(Base, TimestampMixin):
    __tablename__ = "notification_rules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    trigger_event: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)


class Schedule(Base, TimestampMixin):
    __tablename__ = "schedules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    time_blocks: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    people: Mapped[list["Person"]] = relationship(back_populates="schedule")
    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="schedule")


class Presence(Base, TimestampMixin):
    __tablename__ = "presence"

    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[PresenceState] = mapped_column(
        Enum(PresenceState), default=PresenceState.UNKNOWN, nullable=False
    )
    last_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("access_events.id"))
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    person: Mapped[Person] = relationship(back_populates="presence")


class AccessEvent(Base, TimestampMixin):
    __tablename__ = "access_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vehicles.id", ondelete="SET NULL"))
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"))
    registration_number: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    direction: Mapped[AccessDirection] = mapped_column(Enum(AccessDirection), nullable=False)
    decision: Mapped[AccessDecision] = mapped_column(Enum(AccessDecision), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    timing_classification: Mapped[TimingClassification] = mapped_column(
        Enum(TimingClassification), default=TimingClassification.UNKNOWN, nullable=False
    )
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    vehicle: Mapped[Vehicle | None] = relationship(back_populates="events")
    anomalies: Mapped[list["Anomaly"]] = relationship(back_populates="event")


class Anomaly(Base, TimestampMixin):
    __tablename__ = "anomalies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("access_events.id", ondelete="CASCADE"))
    anomaly_type: Mapped[AnomalyType] = mapped_column(Enum(AnomalyType), nullable=False)
    severity: Mapped[AnomalySeverity] = mapped_column(Enum(AnomalySeverity), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    event: Mapped[AccessEvent | None] = relationship(back_populates="anomalies")


class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str | None] = mapped_column(String(160))
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(120))
    tool_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    session: Mapped[ChatSession] = relationship(back_populates="messages")
