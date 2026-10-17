"""Durable parent operations and their atomically linked simulation trials."""

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class OptimizationOperationRecord(Base):
    __tablename__ = "optimization_operations"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_optimization_operations_revision"),
        CheckConstraint("status IN ('queued','running','completed','failed','cancelled','interrupted')", name="ck_optimization_operations_status"),
        CheckConstraint("cancel_requested IN (0,1)", name="ck_optimization_operations_cancel_requested"),
        Index("ix_optimization_operations_created_id", "created_at", "id"),
        Index("ix_optimization_operations_status_created", "status", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)


class OptimizationTrialRecord(Base):
    __tablename__ = "optimization_trials"
    __table_args__ = (
        CheckConstraint("number >= 0", name="ck_optimization_trials_number"),
        CheckConstraint("assessed IN (0,1)", name="ck_optimization_trials_assessed"),
        Index("ix_optimization_trials_simulation_id", "simulation_id", unique=True),
        Index("ix_optimization_trials_failure_id", "failure_id"),
    )

    operation_id: Mapped[str] = mapped_column(String(36), ForeignKey("optimization_operations.id", ondelete="RESTRICT"), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    simulation_id: Mapped[str] = mapped_column(String(36), ForeignKey("simulations.id", ondelete="RESTRICT"), nullable=False)
    failure_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("failures.id", ondelete="RESTRICT"))
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    assessed: Mapped[bool] = mapped_column(Boolean, nullable=False)
