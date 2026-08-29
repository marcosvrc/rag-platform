"""Adapter de `JobQueuePort` via Celery/Redis (RAG-022)."""

from __future__ import annotations

from uuid import UUID

from adapters.queue.celery_app import celery_app, configure_celery_app
from packages.application.ports.job_queue import JobQueuePort
from packages.config.settings import Settings

# Nome lógico da task (não o caminho do módulo Python) — permite ao
# produtor (API) publicar sem importar `apps.indexing_worker.tasks`
# (que traria dependências do worker, como o repositório Postgres, para
# dentro do processo da API). O worker registra uma task com este
# mesmo nome via `@celery_app.task(name=INDEX_JOB_TASK_NAME)`.
INDEX_JOB_TASK_NAME = "process_index_job"


class CeleryJobQueue(JobQueuePort):
    def __init__(self, settings: Settings) -> None:
        configure_celery_app(settings)
        self._app = celery_app

    def enqueue_index_job(self, *, index_job_id: UUID) -> None:
        self._app.send_task(INDEX_JOB_TASK_NAME, args=[str(index_job_id)])
