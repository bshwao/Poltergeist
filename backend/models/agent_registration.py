"""Immutable execution snapshots; deactivation only controls new launches."""

from sqlalchemy import Boolean, CheckConstraint, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class AgentRegistrationRecord(Base):
    __tablename__ = "agent_registrations"
    __table_args__ = (
        CheckConstraint("active IN (0,1)", name="ck_agent_registrations_active"),
        Index("ix_agent_registrations_created_id", "created_at", "id"),
        Index("ix_agent_registrations_active_created_id", "active", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
