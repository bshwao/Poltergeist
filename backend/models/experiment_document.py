"""Versioned local experiment definitions, approvals and deliberate feedback."""

from sqlalchemy import CheckConstraint, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class ExperimentDocumentRecord(Base):
    __tablename__ = "experiment_documents"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_experiment_documents_revision"),
        CheckConstraint("kind IN ('suite','discovery','feedback','receipt')", name="ck_experiment_documents_kind"),
        Index("ix_experiment_documents_kind_created_id", "kind", "created_at", "id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    owner_scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
