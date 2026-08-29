"""Fake configurável de `DocumentProcessorPort`, para testes (RAG-022).

Permite simular determinística e sincronamente os três desfechos que o
critério de aceite do RAG-022 exige provar: sucesso, falha com
tentativas restantes (retry), e falha definitiva (todas as tentativas
esgotadas) — sem precisar de nenhum pipeline de indexação real."""

from __future__ import annotations

from uuid import UUID

from packages.application.ports.document_processor import DocumentProcessorPort


class FakeDocumentProcessor(DocumentProcessorPort):
    """Falha nas primeiras `fail_times` chamadas (levantando `exception`),
    depois sempre sucede. `fail_times=0` (padrão) sempre sucede;
    `fail_times` maior que o número de tentativas configuradas no
    worker simula uma falha definitiva."""

    def __init__(self, *, fail_times: int = 0, exception: Exception | None = None) -> None:
        self._remaining_failures = fail_times
        self._exception = exception if exception is not None else RuntimeError("falha simulada")
        self.processed_index_job_ids: list[UUID] = []

    async def process(self, *, index_job_id: UUID) -> None:
        self.processed_index_job_ids.append(index_job_id)
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise self._exception
