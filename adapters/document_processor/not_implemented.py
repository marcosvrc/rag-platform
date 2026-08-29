"""Adapter placeholder de `DocumentProcessorPort` (RAG-022).

Usado em produção até o RAG-023 (extração de conteúdo) existir: todo
`IndexJob` reivindicado hoje falha definitivamente de propósito, com
uma mensagem de erro clara — nunca um "sucesso" silencioso sem nenhum
processamento real ter acontecido. Troque
`apps/indexing_worker/worker.py` por um adapter real assim que o
RAG-023 (e a cadeia RAG-024 a RAG-027) estiver pronta.
"""

from __future__ import annotations

from uuid import UUID

from packages.application.ports.document_processor import DocumentProcessorPort


class NotImplementedDocumentProcessor(DocumentProcessorPort):
    async def process(self, *, index_job_id: UUID) -> None:
        raise NotImplementedError(
            "Pipeline de indexação (RAG-023 em diante) ainda não implementado; "
            f"job {index_job_id} não pode ser processado."
        )
