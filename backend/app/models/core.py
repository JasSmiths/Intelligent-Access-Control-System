import uuid
from datetime import datetime, time
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, Time
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
    ScheduleKind,
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
    schedules: Mapped[list["ScheduleAssignment"]] = relationship(back_populates="group")


class Person(Base, TimestampMixin):
    __tablename__ = "people"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    first_name: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    profile_photo_data_url: Mapped[str | None] = mapped_column(Text)
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"))
    garage_door_entity_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    group: Mapped[Group | None] = relationship(back_populates="people")
    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="owner")
    presence: Mapped["Presence | None"] = relationship(back_populates="person")
    schedules: Mapped[list["ScheduleAssignment"]] = relationship(back_populates="person")


class Vehicle(Base, TimestampMixin):
    __tablename__ = "vehicles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"))
    registration_number: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    vehicle_photo_data_url: Mapped[str | None] = mapped_column(Text)
    make: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(120))
    color: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    owner: Mapped[Person | None] = relationship(back_populates="vehicles")
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


class TimeSlot(Base, TimestampMixin):
    __tablename__ = "time_slots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    kind: Mapped[ScheduleKind] = mapped_column(Enum(ScheduleKind), nullable=False)
    days_of_week: Mapped[list[int] | None] = mapped_column(JSONB)
    start_time: Mapped[time | None] = mapped_column(Time(timezone=False))
    end_time: Mapped[time | None] = mapped_column(Time(timezone=False))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    assignments: Mapped[list["ScheduleAssignment"]] = relationship(back_populates="time_slot")


class ScheduleAssignment(Base, TimestampMixin):
    __tablename__ = "schedule_assignments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    time_slot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("time_slots.id", ondelete="CASCADE"))
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    time_slot: Mapped[TimeSlot] = relationship(back_populates="assignments")
    person: Mapped[Person | None] = relationship(back_populates="schedules")
    group: Mapped[Group | None] = relationship(back_populates="schedules")


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
