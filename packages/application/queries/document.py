"""Consultas (leitura) de status de indexação de documentos (RAG-027).

`get_index_job_status` é o que fica atrás de `GET /v1/jobs/{index_job_id}`
(prometido desde o RAG-021 — ver o docstring de `DocumentUploadResponse`
em `packages/contracts/document.py`): o cliente recebe `index_job_id`
na resposta do upload (ou da reindexação) e consulta esse id
diretamente para saber se a indexação terminou, e com que erro, se
algum (critério de aceite "estados e erros são consultáveis").

`IndexJob` não carrega `tenant_id` (só `document_id`, e `Document` só
carrega `knowledge_base_id` — ver `packages/domain/entities/index_job.py`
e `document.py`), então o isolamento por tenant aqui é sempre
transitivo: resolve `IndexJob` -> `Document` -> `KnowledgeBase` (via
`get_by_id_unscoped`, RAG-026) e só então compara `KnowledgeBase.
tenant_id` contra o tenant autenticado — nunca expõe o resultado de
`get_by_id_unscoped` a um chamador sem essa checagem explícita antes.
Um job de outro tenant (ou inexistente) sempre vira 404, nunca 403,
mesmo padrão do resto da API (nunca revela a existência de um recurso
de outro tenant)."""

from __future__ import annotations

from uuid import UUID

from packages.application.errors import NotFoundError
from packages.application.ports.document_repository import DocumentRepositoryPort
from packages.application.ports.knowledge_base_repository import KnowledgeBaseRepositoryPort
from packages.domain.entities.index_job import IndexJob


async def get_index_job_status(
    document_repository: DocumentRepositoryPort,
    knowledge_base_repository: KnowledgeBaseRepositoryPort,
    *,
    tenant_id: UUID,
    index_job_id: UUID,
) -> IndexJob:
    job = await document_repository.get_index_job(index_job_id=index_job_id)
    if job is None:
        raise NotFoundError(detail="Job de indexação não encontrado.")

    document = await document_repository.get_document(document_id=job.document_id)
    if document is None:
        # Defensivo: um IndexJob sempre referencia um Document existente
        # (nascem juntos, RAG-021/RAG-027) — não deveria acontecer.
        raise NotFoundError(detail="Job de indexação não encontrado.")

    knowledge_base = await knowledge_base_repository.get_by_id_unscoped(
        knowledge_base_id=document.knowledge_base_id
    )
    if knowledge_base is None or knowledge_base.tenant_id != tenant_id:
        raise NotFoundError(detail="Job de indexação não encontrado.")

    return job
