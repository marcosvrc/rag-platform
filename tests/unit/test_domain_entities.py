"""Testes de invariantes das entidades de domínio (RAG-010).

Cobre as convenções compartilhadas (EntityId = UUID v4, UtcDateTime =
timezone-aware em UTC, imutabilidade) e validações específicas de
alguns campos das entidades da seção 9 do plano.
"""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from packages.domain.entities.chunk import Chunk
from packages.domain.entities.evaluation_run import EvaluationRun
from packages.domain.entities.feedback import Feedback
from packages.domain.entities.index_job import IndexJob
from packages.domain.entities.knowledge_base import KnowledgeBase
from packages.domain.entities.query_evidence import QueryEvidence
from packages.domain.entities.query_log import QueryLog, TokenUsage
from packages.domain.entities.tenant import Tenant
from packages.domain.enums.document_status import DocumentStatus
from packages.domain.enums.feedback_rating import FeedbackRating
from packages.domain.enums.index_job_type import IndexJobType
from packages.domain.enums.knowledge_base_status import KnowledgeBaseStatus
from packages.domain.enums.processing_status import ProcessingStatus
from packages.domain.enums.tenant_status import TenantStatus

NOW = datetime.now(UTC)

# UUID conhecida por ser versão 5 (baseada em namespace), usada para provar
# que EntityId aceita apenas UUID v4.
_UUID_V5 = UUID("886313e1-3b8a-5372-9b90-0c9aee199e5d")


def test_entity_id_rejects_non_v4_uuid() -> None:
    with pytest.raises(ValidationError):
        Tenant(id=_UUID_V5, name="acme", status=TenantStatus.ACTIVE, created_at=NOW)


def test_entity_id_accepts_v4_uuid() -> None:
    tenant = Tenant(id=uuid4(), name="acme", status=TenantStatus.ACTIVE, created_at=NOW)

    assert tenant.id.version == 4


def test_utc_datetime_rejects_naive_datetime() -> None:
    naive = datetime(2026, 1, 1, 12, 0, 0)  # noqa: DTZ001 - propositalmente naive

    with pytest.raises(ValidationError):
        Tenant(id=uuid4(), name="acme", status=TenantStatus.ACTIVE, created_at=naive)


def test_utc_datetime_rejects_non_utc_offset() -> None:
    non_utc = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=-3)))

    with pytest.raises(ValidationError):
        Tenant(id=uuid4(), name="acme", status=TenantStatus.ACTIVE, created_at=non_utc)


def test_utc_datetime_accepts_utc_datetime() -> None:
    tenant = Tenant(id=uuid4(), name="acme", status=TenantStatus.ACTIVE, created_at=NOW)

    assert tenant.created_at == NOW


def test_tenant_name_cannot_be_empty() -> None:
    with pytest.raises(ValidationError):
        Tenant(id=uuid4(), name="", status=TenantStatus.ACTIVE, created_at=NOW)


def test_tenant_is_frozen() -> None:
    tenant = Tenant(id=uuid4(), name="acme", status=TenantStatus.ACTIVE, created_at=NOW)

    with pytest.raises(ValidationError):
        tenant.status = TenantStatus.SUSPENDED


def test_tenant_rejects_unknown_extra_field() -> None:
    with pytest.raises(ValidationError):
        Tenant(
            id=uuid4(),
            name="acme",
            status=TenantStatus.ACTIVE,
            created_at=NOW,
            unexpected="nope",  # type: ignore[call-arg]
        )


def test_knowledge_base_defaults_config_to_empty_dict() -> None:
    kb = KnowledgeBase(
        id=uuid4(),
        tenant_id=uuid4(),
        name="Base RH",
        status=KnowledgeBaseStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )

    assert kb.config == {}
    assert kb.description is None


def test_chunk_requires_non_empty_content_and_positive_token_count() -> None:
    base_kwargs: dict[str, Any] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "knowledge_base_id": uuid4(),
        "version_id": uuid4(),
    }

    with pytest.raises(ValidationError):
        Chunk(content="", token_count=1, **base_kwargs)

    with pytest.raises(ValidationError):
        Chunk(content="texto", token_count=0, **base_kwargs)

    chunk = Chunk(content="texto", token_count=10, **base_kwargs)
    assert chunk.token_count == 10
    assert chunk.metadata == {}


def test_index_job_attempts_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        IndexJob(
            id=uuid4(),
            document_id=uuid4(),
            type=IndexJobType.INDEX,
            status=ProcessingStatus.PENDING,
            attempts=-1,
            created_at=NOW,
            updated_at=NOW,
        )

    job = IndexJob(
        id=uuid4(),
        document_id=uuid4(),
        type=IndexJobType.REINDEX,
        status=ProcessingStatus.RUNNING,
        created_at=NOW,
        updated_at=NOW,
    )
    assert job.attempts == 0


def test_query_log_token_usage_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=-1, output_tokens=0)

    query_log = QueryLog(
        id=uuid4(),
        tenant_id=uuid4(),
        knowledge_base_id=uuid4(),
        question_hash="abc123",
        model="gpt-x",
        latency_ms=120,
        token_usage=TokenUsage(input_tokens=10, output_tokens=20),
        trace_id=uuid4(),
    )
    assert query_log.token_usage.input_tokens == 10


def test_query_evidence_position_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        QueryEvidence(
            query_id=uuid4(),
            chunk_id=uuid4(),
            retrieval_score=0.9,
            position=-1,
        )

    evidence = QueryEvidence(query_id=uuid4(), chunk_id=uuid4(), retrieval_score=0.9, position=0)
    assert evidence.rerank_score is None


def test_feedback_rating_is_positive_or_negative() -> None:
    feedback = Feedback(
        id=uuid4(),
        query_id=uuid4(),
        rating=FeedbackRating.POSITIVE,
        created_at=NOW,
    )
    assert feedback.rating is FeedbackRating.POSITIVE

    with pytest.raises(ValidationError):
        Feedback(
            id=uuid4(),
            query_id=uuid4(),
            rating="MAYBE",  # type: ignore[arg-type]
            created_at=NOW,
        )


def test_evaluation_run_requires_dataset_and_config_versions() -> None:
    with pytest.raises(ValidationError):
        EvaluationRun(
            id=uuid4(),
            dataset_version="",
            config_version="v1",
            status=ProcessingStatus.PENDING,
            created_at=NOW,
        )

    run = EvaluationRun(
        id=uuid4(),
        dataset_version="v1",
        config_version="v1",
        status=ProcessingStatus.SUCCEEDED,
        created_at=NOW,
    )
    assert run.metrics == {}


def test_document_status_enum_has_the_six_states_from_the_plan() -> None:
    assert {status.value for status in DocumentStatus} == {
        "PENDING",
        "PROCESSING",
        "INDEXED",
        "FAILED",
        "QUARANTINED",
        "DELETED",
    }
