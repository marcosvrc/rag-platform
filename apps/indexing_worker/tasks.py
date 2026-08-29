"""Task Celery que processa um `IndexJob` (RAG-022).

Adapter fino: só traduz a mecânica do Celery (contagem de tentativas,
reagendamento com backoff exponencial) para uma chamada ao caso de uso
puro `packages.application.commands.index_job.process_index_job_attempt`
— toda a lógica de negócio (reivindicar, decidir retry vs. falha
definitiva) mora lá, não aqui, e é testada sem nenhum Celery real (ver
`tests/unit/test_index_job_processing.py`).

Número de tentativas e backoff são decisões de implementação: o plano
(seção 17, RAG-022) só exige "retry exponencial funciona; falha
definitiva é registrada", sem especificar valores. `_MAX_ATTEMPTS = 5`
(a primeira tentativa + 4 reagendamentos) com backoff exponencial
(`retry_backoff=True`, dobrando a cada tentativa, com jitter e teto em
`_RETRY_BACKOFF_MAX_SECONDS`) é um ponto de partida razoável — não um
requisito do plano — revisite se a operação real pedir outro perfil.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from celery import Task

from adapters.document_processor.not_implemented import NotImplementedDocumentProcessor
from adapters.document_repository.postgres import PostgresDocumentRepository
from adapters.postgres.engine import get_session_factory
from adapters.queue.celery_app import celery_app
from adapters.queue.celery_job_queue import INDEX_JOB_TASK_NAME
from packages.application.commands.index_job import (
    RetryableIndexJobError,
    process_index_job_attempt,
)

# (1s, 2s, 4s, 8s, ... até `_RETRY_BACKOFF_MAX_SECONDS`) cobre bem
# falhas transitórias de rede/broker sem manter um job "preso" por
# tempo indefinido nem esgotar tentativas cedo demais em picos curtos
# de indisponibilidade (ex.: um restart do Postgres/MinIO).
_MAX_ATTEMPTS = 5
_RETRY_BACKOFF_MAX_SECONDS = 600


async def _run_attempt(index_job_id: UUID, *, attempt_number: int, max_attempts: int) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        document_repository = PostgresDocumentRepository(session)
        # Placeholder até o RAG-023 existir — ver o adapter para detalhes.
        document_processor = NotImplementedDocumentProcessor()
        await process_index_job_attempt(
            document_repository,
            document_processor,
            index_job_id=index_job_id,
            attempt_number=attempt_number,
            max_attempts=max_attempts,
        )


@celery_app.task(
    bind=True,
    name=INDEX_JOB_TASK_NAME,
    max_retries=_MAX_ATTEMPTS - 1,
    autoretry_for=(RetryableIndexJobError,),
    retry_backoff=True,
    retry_backoff_max=_RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,
)
def process_index_job_task(self: Task, index_job_id: str) -> None:
    """`self.request.retries` é 0 na primeira execução e incrementa a
    cada reagendamento do Celery — por isso `attempt_number =
    self.request.retries + 1` (1-based, ver
    `process_index_job_attempt`)."""
    asyncio.run(
        _run_attempt(
            UUID(index_job_id),
            attempt_number=self.request.retries + 1,
            max_attempts=_MAX_ATTEMPTS,
        )
    )
