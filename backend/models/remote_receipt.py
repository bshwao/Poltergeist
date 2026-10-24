"""PC-local accepted remote commands; account IDs have no cross-DB relation."""

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class RemoteCommandReceiptRecord(Base):
    __tablename__ = "remote_command_receipts"
    __table_args__ = (
        CheckConstraint("generation >= 1", name="ck_remote_command_receipts_generation"),
        CheckConstraint("operation IN ('execute','cancel')", name="ck_remote_command_receipts_operation"),
        CheckConstraint("revision >= 1", name="ck_remote_command_receipts_revision"),
        CheckConstraint("(operation = 'execute' AND scenario_sha256 IS NOT NULL) OR (operation = 'cancel' AND scenario_sha256 IS NULL)",
                        name="ck_remote_command_receipts_scenario"),
        Index("ix_remote_command_receipts_request", "origin", "device_id", "generation", "request_id", unique=True),
        Index("ix_remote_command_receipts_binding_run", "origin", "device_id", "generation", "simulation_id", "operation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    origin: Mapped[str] = mapped_column(String(256), nullable=False)
    device_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation: Mapped[str] = mapped_column(String(8), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_sha256: Mapped[str | None] = mapped_column(String(64))
    simulation_id: Mapped[str] = mapped_column(String(36), ForeignKey("simulations.id", ondelete="RESTRICT"), nullable=False)
    accepted_at: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)


class RemoteProjectionRecord(Base):
    __tablename__ = "remote_run_projections"
    __table_args__ = (
        CheckConstraint("generation >= 1", name="ck_remote_run_projections_generation"),
        CheckConstraint("revision >= 1", name="ck_remote_run_projections_revision"),
    )

    origin: Mapped[str] = mapped_column(String(256), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, primary_key=True)
    simulation_id: Mapped[str] = mapped_column(String(36), ForeignKey("simulations.id", ondelete="RESTRICT"), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
