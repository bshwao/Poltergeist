"""Reproducible failure reports linked to finalized run results."""

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class FailureRecord(Base):
    __tablename__ = "failures"
    __table_args__ = (
        CheckConstraint("reproduced IS NULL OR reproduced IN (0,1)", name="ck_failures_reproduced"),
        Index("ix_failures_created_id", "created_at", "id"),
        Index("ix_failures_simulation_id", "simulation_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    simulation_id: Mapped[str] = mapped_column(ForeignKey("simulations.id", ondelete="RESTRICT"), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    reproduced: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
