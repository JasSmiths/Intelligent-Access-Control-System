"""Vehicle information snapshots and bounded, leased arrival refresh work."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VehicleInformationSnapshot(Base):
    __tablename__ = "vehicle_information_snapshots"
    vehicle_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"), primary_key=True)
    registration_number: Mapped[str] = mapped_column(String(32), nullable=False)
    dvla: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    dvsa: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class VehicleInformationJob(Base):
    __tablename__ = "vehicle_information_jobs"
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("access_events.id", ondelete="CASCADE"), primary_key=True)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vehicles.id", ondelete="SET NULL"))
    registration_number: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    lease_token: Mapped[uuid.UUID | None] = mapped_column()
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(40))
    __table_args__ = (Index("ix_vehicle_information_jobs_ready", "status", "created_at", "event_id"),)
