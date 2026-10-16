"""Mutable saved scenarios; each run retains its own immutable snapshot."""

from sqlalchemy import CheckConstraint, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class ScenarioRecord(Base):
    __tablename__ = "scenarios"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_scenarios_revision"),
        Index("ix_scenarios_created_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
