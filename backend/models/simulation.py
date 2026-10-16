"""Run lifecycle projections and complete contract payloads."""

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class SimulationRecord(Base):
    __tablename__ = "simulations"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_simulations_revision"),
        CheckConstraint("status IN ('queued','running','completed','failed','cancelled')", name="ck_simulations_status"),
        CheckConstraint("mode IN ('carla','test')", name="ck_simulations_mode"),
        CheckConstraint("status NOT IN ('queued','running') OR result IS NULL", name="ck_simulations_terminal_result"),
        Index("ix_simulations_created_id", "created_at", "id"),
        Index("ix_simulations_status_mode_created", "status", "mode", "created_at", "id"),
        Index("ix_simulations_scenario_id", "scenario_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scenario_id: Mapped[str | None] = mapped_column(ForeignKey("scenarios.id", ondelete="RESTRICT"))
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
