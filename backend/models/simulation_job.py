"""Durable launch settings owned by the local simulation worker."""

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class SimulationJobRecord(Base):
    __tablename__ = "simulation_jobs"
    __table_args__ = (
        CheckConstraint("cancel_requested IN (0,1)", name="ck_simulation_jobs_cancel_requested"),
        CheckConstraint("claimed IN (0,1)", name="ck_simulation_jobs_claimed"),
        Index("ix_simulation_jobs_simulation_id", "simulation_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    simulation_id: Mapped[str] = mapped_column(String(36), ForeignKey("simulations.id", ondelete="RESTRICT"), nullable=False)
    options: Mapped[str] = mapped_column(Text, nullable=False)
    evaluation_settings: Mapped[str] = mapped_column(Text, nullable=False)
    agent_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False)
    claimed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
