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

Desde o RAG-026, `_run_attempt` monta o pipeline real
(`PipelineDocumentProcessor`, RAG-023 a RAG-026) em vez do placeholder
`NotImplementedDocumentProcessor` do RAG-022 — `get_settings()` é lido
aqui (não em `apps/indexing_worker/worker.py`) porque só esta função
roda de fato por execução de task, e os adapters que dependem de
`Settings` (object storage, gateway de embeddings) precisam de uma
instância por chamada tanto quanto a sessão de banco.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from celery import Task

from adapters.docling.parser import DoclingDocumentParser
from adapters.document_processor.pipeline import PipelineDocumentProcessor
from adapters.document_repository.postgres import PostgresDocumentRepository
from adapters.knowledge_base_repository.postgres import PostgresKnowledgeBaseRepository
from adapters.litellm.embedding_provider import LiteLLMEmbeddingProvider
from adapters.object_storage.s3_object_storage import S3ObjectStorage
from adapters.postgres.engine import get_session_factory
from adapters.queue.celery_app import celery_app
from adapters.queue.celery_job_queue import INDEX_JOB_TASK_NAME
from packages.application.commands.index_job import (
    RetryableIndexJobError,
    process_index_job_attempt,
)
from packages.config.settings import get_settings

# (1s, 2s, 4s, 8s, ... até `_RETRY_BACKOFF_MAX_SECONDS`) cobre bem
# falhas transitórias de rede/broker sem manter um job "preso" por
# tempo indefinido nem esgotar tentativas cedo demais em picos curtos
# de indisponibilidade (ex.: um restart do Postgres/MinIO).
_MAX_ATTEMPTS = 5
_RETRY_BACKOFF_MAX_SECONDS = 600


async def _run_attempt(index_job_id: UUID, *, attempt_number: int, max_attempts: int) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    async with session_factory() as session:
        document_repository = PostgresDocumentRepository(session)
        document_processor = PipelineDocumentProcessor(
            document_repository=document_repository,
            knowledge_base_repository=PostgresKnowledgeBaseRepository(session),
            object_storage=S3ObjectStorage(settings),
            document_parser=DoclingDocumentParser(),
            embedding_provider=LiteLLMEmbeddingProvider(settings),
        )
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
