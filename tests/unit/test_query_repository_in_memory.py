"""Testes de RAG-044: `InMemoryQueryRepository` — mesmo contrato da
porta (`QueryRepositoryPort`) que o adapter Postgres real."""

from __future__ import annotations

from uuid import uuid4

import pytest

from adapters.query_repository.in_memory import InMemoryQueryRepository
from packages.application.ports.query_repository import QueryEvidenceInput
from packages.domain.entities.query_log import TokenUsage
from packages.domain.enums.feedback_rating import FeedbackRating

TENANT_ID = uuid4()
KNOWLEDGE_BASE_ID = uuid4()


@pytest.fixture
def repository() -> InMemoryQueryRepository:
    return InMemoryQueryRepository()


async def test_persist_query_creates_a_query_log_with_a_new_id(
    repository: InMemoryQueryRepository,
) -> None:
    query_log = await repository.persist_query(
        tenant_id=TENANT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        question_hash="a" * 64,
        model="generation-model-alias",
        latency_ms=120,
        token_usage=TokenUsage(input_tokens=100, output_tokens=20),
        trace_id=uuid4(),
        evidence=[],
    )

    assert query_log.tenant_id == TENANT_ID
    assert query_log.knowledge_base_id == KNOWLEDGE_BASE_ID
    assert query_log.model == "generation-model-alias"
    assert query_log.latency_ms == 120
    assert query_log.token_usage == TokenUsage(input_tokens=100, output_tokens=20)
    assert query_log.id in repository.query_logs


async def test_persist_query_with_no_evidence_stores_no_query_evidence(
    repository: InMemoryQueryRepository,
) -> None:
    await repository.persist_query(
        tenant_id=TENANT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        question_hash="a" * 64,
        model="m",
        latency_ms=1,
        token_usage=TokenUsage(input_tokens=0, output_tokens=0),
        trace_id=uuid4(),
        evidence=[],
    )

    assert repository.query_evidences == []


async def test_persist_query_stores_one_query_evidence_per_input_linked_to_the_query_id(
    repository: InMemoryQueryRepository,
) -> None:
    chunk_id_a, chunk_id_b = uuid4(), uuid4()

    query_log = await repository.persist_query(
        tenant_id=TENANT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        question_hash="a" * 64,
        model="m",
        latency_ms=1,
        token_usage=TokenUsage(input_tokens=0, output_tokens=0),
        trace_id=uuid4(),
        evidence=[
            QueryEvidenceInput(
                chunk_id=chunk_id_a, retrieval_score=0.9, rerank_score=0.8, position=0
            ),
            QueryEvidenceInput(
                chunk_id=chunk_id_b, retrieval_score=0.5, rerank_score=None, position=1
            ),
        ],
    )

    assert len(repository.query_evidences) == 2
    assert {e.chunk_id for e in repository.query_evidences} == {chunk_id_a, chunk_id_b}
    assert all(e.query_id == query_log.id for e in repository.query_evidences)
    by_chunk = {e.chunk_id: e for e in repository.query_evidences}
    assert by_chunk[chunk_id_a].retrieval_score == 0.9
    assert by_chunk[chunk_id_a].rerank_score == 0.8
    assert by_chunk[chunk_id_a].position == 0
    assert by_chunk[chunk_id_b].rerank_score is None


async def test_persist_query_twice_creates_two_independent_query_logs(
    repository: InMemoryQueryRepository,
) -> None:
    first = await repository.persist_query(
        tenant_id=TENANT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        question_hash="a" * 64,
        model="m",
        latency_ms=1,
        token_usage=TokenUsage(input_tokens=0, output_tokens=0),
        trace_id=uuid4(),
        evidence=[],
    )
    second = await repository.persist_query(
        tenant_id=TENANT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        question_hash="b" * 64,
        model="m",
        latency_ms=1,
        token_usage=TokenUsage(input_tokens=0, output_tokens=0),
        trace_id=uuid4(),
        evidence=[],
    )

    assert first.id != second.id
    assert len(repository.query_logs) == 2


# --- RAG-045: get_query_log / persist_feedback ---


async def test_get_query_log_returns_none_for_unknown_id(
    repository: InMemoryQueryRepository,
) -> None:
    assert await repository.get_query_log(query_id=uuid4()) is None


async def test_get_query_log_returns_the_persisted_query_log(
    repository: InMemoryQueryRepository,
) -> None:
    query_log = await repository.persist_query(
        tenant_id=TENANT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        question_hash="a" * 64,
        model="m",
        latency_ms=1,
        token_usage=TokenUsage(input_tokens=0, output_tokens=0),
        trace_id=uuid4(),
        evidence=[],
    )

    found = await repository.get_query_log(query_id=query_log.id)

    assert found == query_log


async def test_persist_feedback_creates_a_feedback_with_a_new_id(
    repository: InMemoryQueryRepository,
) -> None:
    query_log = await repository.persist_query(
        tenant_id=TENANT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        question_hash="a" * 64,
        model="m",
        latency_ms=1,
        token_usage=TokenUsage(input_tokens=0, output_tokens=0),
        trace_id=uuid4(),
        evidence=[],
    )

    feedback = await repository.persist_feedback(
        query_id=query_log.id,
        rating=FeedbackRating.POSITIVE,
        reason=None,
        expected_answer=None,
    )

    assert feedback.query_id == query_log.id
    assert feedback.rating == FeedbackRating.POSITIVE
    assert feedback.reason is None
    assert feedback.expected_answer is None
    assert feedback in repository.feedbacks


async def test_persist_feedback_stores_reason_and_expected_answer(
    repository: InMemoryQueryRepository,
) -> None:
    query_log = await repository.persist_query(
        tenant_id=TENANT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        question_hash="a" * 64,
        model="m",
        latency_ms=1,
        token_usage=TokenUsage(input_tokens=0, output_tokens=0),
        trace_id=uuid4(),
        evidence=[],
    )

    feedback = await repository.persist_feedback(
        query_id=query_log.id,
        rating=FeedbackRating.NEGATIVE,
        reason="resposta incompleta",
        expected_answer="deveria citar a seção 3",
    )

    assert feedback.rating == FeedbackRating.NEGATIVE
    assert feedback.reason == "resposta incompleta"
    assert feedback.expected_answer == "deveria citar a seção 3"


async def test_persist_feedback_twice_creates_two_independent_feedbacks(
    repository: InMemoryQueryRepository,
) -> None:
    query_log = await repository.persist_query(
        tenant_id=TENANT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        question_hash="a" * 64,
        model="m",
        latency_ms=1,
        token_usage=TokenUsage(input_tokens=0, output_tokens=0),
        trace_id=uuid4(),
        evidence=[],
    )

    first = await repository.persist_feedback(
        query_id=query_log.id, rating=FeedbackRating.POSITIVE, reason=None, expected_answer=None
    )
    second = await repository.persist_feedback(
        query_id=query_log.id, rating=FeedbackRating.NEGATIVE, reason="x", expected_answer=None
    )

    assert first.id != second.id
    assert len(repository.feedbacks) == 2
