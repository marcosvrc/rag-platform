"""Comando de upload e reindexação de documentos (RAG-021/RAG-022/RAG-027,
seção 11 do plano).

`upload_document` implementa os passos 1-6 do fluxo de indexação:
validar, calcular checksum, detectar duplicidade, armazenar arquivo,
criar documento + versão + job (RAG-021), e publicar o job na fila
(RAG-022) — só quando algo novo foi de fato criado (`not
upload.replayed`); uma repetição idempotente não publica de novo, já
que o job original já está (ou já foi) na fila.

`reindex_document` (RAG-027) reprocessa o conteúdo original de um
documento já `INDEXED`: cria uma nova `DocumentVersion` (mesma
`object_key`, versão incrementada) + um novo `IndexJob` (tipo
`REINDEX`) e publica na fila — nunca troca o arquivo original, só pede
um novo processamento dele (útil após mudar a config de chunking da
base, ou o modelo de embeddings por trás do alias). O documento
continua consultável pela versão ativa atual (`consultas continuam
disponíveis`, critério de aceite do RAG-027) até o worker terminar de
processar a nova versão e ativá-la (RAG-026,
`persist_chunks_and_activate_version`)."""

from __future__ import annotations

import hashlib
from uuid import UUID

from packages.application.errors import ConflictError, NotFoundError, UnprocessableEntityError
from packages.application.ports.document_repository import (
    DocumentChecksumConflictError,
    DocumentRepositoryPort,
    DocumentUpload,
    DocumentVersionConflictError,
    IdempotencyKeyConflictError,
    ReindexJob,
)
from packages.application.ports.job_queue import JobQueuePort
from packages.application.ports.knowledge_base_repository import KnowledgeBaseRepositoryPort
from packages.application.ports.object_storage import ObjectStoragePort, sanitize_object_key
from packages.domain.enums.document_status import DocumentStatus

# Formatos aceitos (seção 2 do plano: "receber documentos PDF, Markdown,
# TXT e DOCX"; RAG-023 os extrai). Fixo — não é configuração de
# ambiente, é uma decisão de produto (ver packages/config/settings.py).
_ALLOWED_EXTENSIONS_BY_MIME_TYPE: dict[str, str] = {
    "application/pdf": ".pdf",
    "text/markdown": ".md",
    "text/plain": ".txt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


def _validate_upload(
    *, filename: str, content_type: str, size_bytes: int, max_size_bytes: int
) -> None:
    """Seção 11, passo 1 do plano: "validar autorização, extensão, MIME
    type e tamanho" (autorização já aconteceu em `get_current_tenant_id`
    antes deste comando ser chamado)."""
    if not filename or not filename.strip():
        raise UnprocessableEntityError(detail="Nome de arquivo é obrigatório.")

    expected_extension = _ALLOWED_EXTENSIONS_BY_MIME_TYPE.get(content_type)
    if expected_extension is None:
        allowed = ", ".join(sorted(_ALLOWED_EXTENSIONS_BY_MIME_TYPE))
        raise UnprocessableEntityError(
            detail=f"Tipo de arquivo '{content_type}' não suportado. Tipos aceitos: {allowed}."
        )

    if not filename.lower().endswith(expected_extension):
        raise UnprocessableEntityError(
            detail=(
                f"Extensão do arquivo não corresponde ao tipo '{content_type}' "
                f"(esperado: '{expected_extension}')."
            )
        )

    if size_bytes <= 0:
        raise UnprocessableEntityError(detail="Arquivo vazio não é permitido.")
    if size_bytes > max_size_bytes:
        raise UnprocessableEntityError(
            detail=f"Arquivo excede o tamanho máximo permitido ({max_size_bytes} bytes)."
        )


def _is_matching_replay(
    upload: DocumentUpload, *, filename: str, content_type: str, checksum: str
) -> bool:
    return (
        upload.document.name == filename
        and upload.document.mime_type == content_type
        and upload.document.checksum == checksum
    )


async def upload_document(
    document_repository: DocumentRepositoryPort,
    knowledge_base_repository: KnowledgeBaseRepositoryPort,
    object_storage: ObjectStoragePort,
    job_queue: JobQueuePort,
    *,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    filename: str,
    content_type: str,
    content: bytes,
    max_size_bytes: int,
    idempotency_key: str | None,
) -> DocumentUpload:
    knowledge_base = await knowledge_base_repository.get_by_id(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id
    )
    if knowledge_base is None:
        raise NotFoundError(detail="Base de conhecimento não encontrada.")

    _validate_upload(
        filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        max_size_bytes=max_size_bytes,
    )

    checksum = hashlib.sha256(content).hexdigest()

    if idempotency_key is not None:
        previous = await document_repository.find_idempotent_upload(
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            idempotency_key=idempotency_key,
        )
        if previous is not None:
            if _is_matching_replay(
                previous, filename=filename, content_type=content_type, checksum=checksum
            ):
                return previous
            raise ConflictError(
                detail=(
                    f"Idempotency-Key {idempotency_key!r} já foi usada para uma "
                    "requisição diferente nesta base."
                )
            )

    duplicate = await document_repository.find_by_checksum(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, checksum=checksum
    )
    if duplicate is not None:
        raise ConflictError(
            detail=(
                f"Já existe um documento ({duplicate.id}) com este conteúdo nesta base "
                "(mesmo checksum)."
            )
        )

    object_key = sanitize_object_key(f"{knowledge_base_id}/{checksum}/{filename}")
    stored = await object_storage.upload(key=object_key, content=content, content_type=content_type)

    try:
        upload = await document_repository.create_document(
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            name=filename,
            mime_type=content_type,
            checksum=stored.checksum_sha256,
            object_key=stored.key,
            idempotency_key=idempotency_key,
        )
    except DocumentChecksumConflictError as exc:
        raise ConflictError(detail=str(exc)) from exc
    except IdempotencyKeyConflictError as exc:
        raise ConflictError(detail=str(exc)) from exc

    if not upload.replayed:
        job_queue.enqueue_index_job(index_job_id=upload.index_job.id)
    return upload


async def reindex_document(
    document_repository: DocumentRepositoryPort,
    knowledge_base_repository: KnowledgeBaseRepositoryPort,
    job_queue: JobQueuePort,
    *,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
) -> ReindexJob:
    """RAG-027: dispara uma reindexação do conteúdo original já
    armazenado (nunca aceita um novo arquivo — isso continua sendo
    `upload_document`, cujo checksum duplicado já é rejeitado)."""
    knowledge_base = await knowledge_base_repository.get_by_id(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id
    )
    if knowledge_base is None:
        raise NotFoundError(detail="Base de conhecimento não encontrada.")

    document = await document_repository.get_document(document_id=document_id)
    # `Document` não carrega tenant_id próprio: a checagem de
    # `knowledge_base_id` é o que isola por tenant aqui (mesmo padrão
    # transitivo de `find_by_checksum`) — um documento de outra base
    # (ou de outro tenant, que nunca teria passado no `get_by_id`
    # acima) nunca é distinguível de um documento inexistente.
    if document is None or document.knowledge_base_id != knowledge_base_id:
        raise NotFoundError(detail="Documento não encontrado.")

    if document.status != DocumentStatus.INDEXED:
        raise ConflictError(
            detail=(
                "Documento precisa estar indexado para poder ser reindexado "
                f"(status atual: {document.status.value})."
            )
        )

    latest_version = await document_repository.get_latest_version(document_id=document_id)
    if latest_version is None:
        # Defensivo: um documento INDEXED sempre tem ao menos a versão 1
        # (criada junto com o documento, RAG-021) — não deveria acontecer.
        raise NotFoundError(detail="Nenhuma versão encontrada para este documento.")

    try:
        result = await document_repository.create_reindex_job(
            document_id=document_id,
            object_key=latest_version.object_key,
            version=latest_version.version + 1,
        )
    except DocumentVersionConflictError as exc:
        raise ConflictError(
            detail="Já existe uma reindexação em andamento para este documento."
        ) from exc

    job_queue.enqueue_index_job(index_job_id=result.index_job.id)
    return result
