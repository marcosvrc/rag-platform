"""Testes de RAG-022: `packages.application.commands.index_job.process_index_job_attempt`.

Cobre os três critérios de aceite da atividade sem nenhum Celery ou
broker real: "job é consumido" (reivindicação/lock idempotente),
"retry exponencial funciona" (a função sinaliza corretamente quando
ainda há tentativas restantes — quem decide o *tempo* do backoff é a
task Celery, fora do escopo deste teste) e "falha definitiva é
registrada" (estado final persistido em `IndexJob`).
"""

from uuid import UUID, uuid4

import pytest

from adapters.document_processor.in_memory import FakeDocumentProcessor
from adapters.document_repository.in_memory import InMemoryDocumentRepository
from packages.application.commands.index_job import (
    RetryableIndexJobError,
    process_index_job_attempt,
)
from packages.domain.enums.processing_status import ProcessingStatus

TENANT_ID = uuid4()
KNOWLEDGE_BASE_ID = uuid4()


@pytest.fixture
def document_repository() -> InMemoryDocumentRepository:
    return InMemoryDocumentRepository()


async def _create_index_job(repository: InMemoryDocumentRepository) -> UUID:
    upload = await repository.create_document(
        tenant_id=TENANT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        name="guia.pdf",
        mime_type="application/pdf",
        checksum=str(uuid4()) + "a" * 28,
        object_key="kb/checksum/guia.pdf",
        idempotency_key=None,
    )
    return upload.index_job.id


async def test_first_attempt_claims_the_job_and_succeeds(
    document_repository: InMemoryDocumentRepository,
) -> None:
    index_job_id = await _create_index_job(document_repository)
    processor = FakeDocumentProcessor()

    await process_index_job_attempt(
        document_repository,
        processor,
        index_job_id=index_job_id,
        attempt_number=1,
        max_attempts=5,
    )

    job = document_repository._jobs[index_job_id]
    assert job.status == ProcessingStatus.SUCCEEDED
    assert processor.processed_index_job_ids == [index_job_id]


async def test_first_attempt_skips_processing_when_job_already_claimed(
    document_repository: InMemoryDocumentRepository,
) -> None:
    index_job_id = await _create_index_job(document_repository)
    # Outro worker "chegou primeiro" e já reivindicou o job.
    await document_repository.claim_index_job(index_job_id=index_job_id)
    processor = FakeDocumentProcessor()

    await process_index_job_attempt(
        document_repository,
        processor,
        index_job_id=index_job_id,
        attempt_number=1,
        max_attempts=5,
    )

    assert processor.processed_index_job_ids == []


async def test_failure_with_attempts_remaining_raises_retryable_error(
    document_repository: InMemoryDocumentRepository,
) -> None:
    index_job_id = await _create_index_job(document_repository)
    processor = FakeDocumentProcessor(fail_times=1)

    with pytest.raises(RetryableIndexJobError):
        await process_index_job_attempt(
            document_repository,
            processor,
            index_job_id=index_job_id,
            attempt_number=1,
            max_attempts=5,
        )

    job = document_repository._jobs[index_job_id]
    assert job.status == ProcessingStatus.RUNNING
    assert job.attempts == 1
    assert job.error_code == "RuntimeError"


async def test_subsequent_attempt_does_not_reclaim_and_can_succeed(
    document_repository: InMemoryDocumentRepository,
) -> None:
    """Uma segunda tentativa (reagendada pelo Celery após a primeira
    falha) já é dona do job — não tenta reivindicar de novo — e pode
    suceder normalmente."""
    index_job_id = await _create_index_job(document_repository)
    processor = FakeDocumentProcessor(fail_times=1)

    with pytest.raises(RetryableIndexJobError):
        await process_index_job_attempt(
            document_repository,
            processor,
            index_job_id=index_job_id,
            attempt_number=1,
            max_attempts=5,
        )

    await process_index_job_attempt(
        document_repository,
        processor,
        index_job_id=index_job_id,
        attempt_number=2,
        max_attempts=5,
    )

    job = document_repository._jobs[index_job_id]
    assert job.status == ProcessingStatus.SUCCEEDED
    assert processor.processed_index_job_ids == [index_job_id, index_job_id]


async def test_failure_on_the_last_attempt_is_final_and_does_not_raise(
    document_repository: InMemoryDocumentRepository,
) -> None:
    index_job_id = await _create_index_job(document_repository)
    processor = FakeDocumentProcessor(fail_times=999)  # sempre falha

    # Não levanta: a última tentativa registra falha definitiva e retorna.
    await process_index_job_attempt(
        document_repository,
        processor,
        index_job_id=index_job_id,
        attempt_number=5,
        max_attempts=5,
    )

    job = document_repository._jobs[index_job_id]
    assert job.status == ProcessingStatus.FAILED
    assert job.attempts == 5
    assert job.error_code == "RuntimeError"


async def test_error_message_is_truncated_defensively(
    document_repository: InMemoryDocumentRepository,
) -> None:
    index_job_id = await _create_index_job(document_repository)
    huge_message = "x" * 10_000
    processor = FakeDocumentProcessor(fail_times=1, exception=RuntimeError(huge_message))

    with pytest.raises(RetryableIndexJobError):
        await process_index_job_attempt(
            document_repository,
            processor,
            index_job_id=index_job_id,
            attempt_number=1,
            max_attempts=5,
        )

    job = document_repository._jobs[index_job_id]
    assert job.error_message is not None
    assert len(job.error_message) <= 500
