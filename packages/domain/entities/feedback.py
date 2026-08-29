"""Entidade Feedback (seção 9 do plano)."""

from packages.domain.entities.base import DomainModel, EntityId, UtcDateTime
from packages.domain.enums.feedback_rating import FeedbackRating


class Feedback(DomainModel):
    id: EntityId
    query_id: EntityId
    rating: FeedbackRating
    reason: str | None = None
    expected_answer: str | None = None
    created_at: UtcDateTime
