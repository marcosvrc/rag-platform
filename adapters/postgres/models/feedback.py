"""Tabela `feedbacks` (RAG-011, entidade `Feedback` de RAG-010)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Enum, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from adapters.postgres.base import Base
from packages.domain.enums.feedback_rating import FeedbackRating


class FeedbackModel(Base):
    __tablename__ = "feedbacks"
    __table_args__ = (Index("ix_feedbacks_query_id", "query_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    query_id: Mapped[UUID] = mapped_column(
        ForeignKey("query_logs.id", name="fk_feedbacks_query_id_query_logs", ondelete="CASCADE"),
        nullable=False,
    )
    rating: Mapped[FeedbackRating] = mapped_column(
        Enum(FeedbackRating, name="feedback_rating", native_enum=False, length=16),
        nullable=False,
    )
    reason: Mapped[str | None] = mapped_column(nullable=True)
    expected_answer: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
