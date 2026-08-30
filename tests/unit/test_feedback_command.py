"""Testes de RAG-045: caso de uso `submit_feedback`
(`packages/application/commands/feedback.py`).

Cobre os critérios de aceite: "respeita tenant; não permite feedback
para query alheia" (query inexistente e query de outro tenant levam
ao mesmo `NotFoundError`, 404 nunca 403); criação bem-sucedida devolve
o `Feedback` persistido com os campos corretos."""

from __future__ import annotations

from uuid import uuid4

import pytest

from adapters.query_repository.in_memory import InMemoryQueryRepository
from packages.application.commands.feedback import submit_feedback
from packages.application.errors import NotFoundError
from packages.domain.entities.query_log import QueryLog, TokenUsage
from packages.domain.enums.feedback_rating import FeedbackRating

TENANT_A = uuid4()
TENANT_B = uuid4()
KNOWLEDGE_BASE_ID = uuid4()


@pytest.fixture
def query_repository() -> InMemoryQueryRepository:
    return InMemoryQueryRepository()


async def _seed_query_log(repository: InMemoryQueryRepository, *, tenant_id: object) -> QueryLog:
    return await repository.persist_query(
        tenant_id=tenant_id,  # type: ignore[arg-type]
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        question_hash="a" * 64,
        model="m",
        latency_ms=1,
        token_usage=TokenUsage(input_tokens=0, output_tokens=0),
        trace_id=uuid4(),
        evidence=[],
    )


async def test_submit_feedback_for_unknown_query_id_raises_not_found(
    query_repository: InMemoryQueryRepository,
) -> None:
    with pytest.raises(NotFoundError):
        await submit_feedback(
            query_repository=query_repository,
            tenant_id=TENANT_A,
            query_id=uuid4(),
            rating=FeedbackRating.POSITIVE,
            reason=None,
            expected_answer=None,
        )


async def test_submit_feedback_for_a_query_of_another_tenant_raises_not_found(
    query_repository: InMemoryQueryRepository,
) -> None:
    query_log = await _seed_query_log(query_repository, tenant_id=TENANT_A)

    with pytest.raises(NotFoundError):
        await submit_feedback(
            query_repository=query_repository,
            tenant_id=TENANT_B,
            query_id=query_log.id,
            rating=FeedbackRating.POSITIVE,
            reason=None,
            expected_answer=None,
        )


async def test_submit_feedback_does_not_persist_anything_when_query_is_not_found(
    query_repository: InMemoryQueryRepository,
) -> None:
    query_log = await _seed_query_log(query_repository, tenant_id=TENANT_A)

    with pytest.raises(NotFoundError):
        await submit_feedback(
            query_repository=query_repository,
            tenant_id=TENANT_B,
            query_id=query_log.id,
            rating=FeedbackRating.NEGATIVE,
            reason="motivo",
            expected_answer=None,
        )

    assert query_repository.feedbacks == []


async def test_submit_feedback_creates_and_returns_the_persisted_feedback(
    query_repository: InMemoryQueryRepository,
) -> None:
    query_log = await _seed_query_log(query_repository, tenant_id=TENANT_A)

    feedback = await submit_feedback(
        query_repository=query_repository,
        tenant_id=TENANT_A,
        query_id=query_log.id,
        rating=FeedbackRating.POSITIVE,
        reason=None,
        expected_answer=None,
    )

    assert feedback.query_id == query_log.id
    assert feedback.rating == FeedbackRating.POSITIVE
    assert feedback in query_repository.feedbacks


async def test_submit_feedback_with_negative_rating_stores_reason_and_expected_answer(
    query_repository: InMemoryQueryRepository,
) -> None:
    query_log = await _seed_query_log(query_repository, tenant_id=TENANT_A)

    feedback = await submit_feedback(
        query_repository=query_repository,
        tenant_id=TENANT_A,
        query_id=query_log.id,
        rating=FeedbackRating.NEGATIVE,
        reason="não citou a fonte certa",
        expected_answer="deveria citar o manual v2",
    )

    assert feedback.rating == FeedbackRating.NEGATIVE
    assert feedback.reason == "não citou a fonte certa"
    assert feedback.expected_answer == "deveria citar o manual v2"
