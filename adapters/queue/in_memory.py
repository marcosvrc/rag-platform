"""Fake em memória de `JobQueuePort`, para testes (RAG-022).

Só registra os ids publicados — nenhum processamento acontece (isso é
testado separadamente em `packages.application.commands.index_job`,
que independe de qualquer fila real ou fake)."""

from __future__ import annotations

from uuid import UUID

from packages.application.ports.job_queue import JobQueuePort


class InMemoryJobQueue(JobQueuePort):
    def __init__(self) -> None:
        self.enqueued_index_job_ids: list[UUID] = []

    def enqueue_index_job(self, *, index_job_id: UUID) -> None:
        self.enqueued_index_job_ids.append(index_job_id)
