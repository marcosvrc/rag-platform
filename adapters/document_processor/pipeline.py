"""Adapter real de `DocumentProcessorPort`: o pipeline de indexação
completo (RAG-026, seção 11 do plano, passos 8-14).

Orquestra os adapters entregues pelas atividades anteriores — nenhuma
lógica de extração/chunking/embeddings mora aqui, só a sequência e o
tratamento de erros esperado pelo contrato da porta (`process` levanta
para sinalizar falha; quem chama decide retry vs. falha definitiva,
ver `packages/application/ports/document_processor.py`):

1. Resolve `IndexJob` -> `Document` -> `DocumentVersion` mais recente
   -> `KnowledgeBase` (só para ler `tenant_id`/`config`; nenhum destes
   passos conhece um tenant autenticado ainda, mesma justificativa de
   `DocumentRepositoryPort.get_document`/`KnowledgeBaseRepositoryPort.
   get_by_id_unscoped`).
2. Marca o documento como `PROCESSING` (idempotente).
3. Baixa o conteúdo original do object storage e extrai Markdown
   (RAG-023).
4. Chunka o Markdown (RAG-024), usando a config de chunking da base de
   conhecimento.
5. Gera embeddings em lote para todos os chunks (RAG-025).
6. Sobe o Markdown extraído para o object storage (auditoria/debug —
   `extracted_object_key` na versão).
7. Persiste os chunks e ativa a versão, tudo numa única transação
   (RAG-026, `persist_chunks_and_activate_version`) — índice parcial
   nunca fica ativo.

**Escopo deliberadamente fora desta atividade**: se qualquer passo
acima levantar uma exceção, ela sobe para
`packages.application.commands.index_job.process_index_job_attempt`
(já implementado no RAG-022), que decide entre reagendar (retry) ou
marcar o `IndexJob` como `FAILED` definitivamente. O que esse código já
faz HOJE é registrar a falha no `IndexJob` — ele não transiciona
`Document.status` para `FAILED` quando as tentativas se esgotam,
porque `process_index_job_attempt` só tem `index_job_id` em escopo no
ponto de falha, não `document_id` (RAG-022, código já mesclado). Fechar
essa lacuna exigiria alterar `process_index_job_attempt` (código já
revisado e mesclado antes desta atividade) só para propagar
`document_id` até lá — decidido deliberadamente que isso é um
follow-up (ver README, seção RAG-026), não algo para mudar sem revisão
enquanto o autor está indisponível. Efeito prático: um documento cuja
indexação falha definitivamente hoje fica preso em `PROCESSING` (ou
`PENDING`, se a falha ocorrer antes do passo 2) em vez de ir para
`FAILED` — o `IndexJob.status=FAILED` correspondente já é suficiente
para um operador humano perceber e investigar.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from packages.application.ports.document_parser import DocumentParserPort
from packages.application.ports.document_processor import DocumentProcessorPort
from packages.application.ports.document_repository import DocumentRepositoryPort
from packages.application.ports.embedding_provider import EmbeddingProviderPort
from packages.application.ports.knowledge_base_repository import KnowledgeBaseRepositoryPort
from packages.application.ports.object_storage import ObjectStoragePort, sanitize_object_key
from packages.domain.entities.chunk import Chunk
from packages.ingestion.chunking import ChunkingConfig, chunk_document


class DocumentNotFoundForIndexJobError(RuntimeError):
    """`IndexJob.document_id` não corresponde a nenhum `Document`
    existente. Nunca deveria acontecer em operação normal (o documento
    é criado antes do job, na mesma transação — ver `create_document`);
    indica um estado inconsistente no banco, não um erro de usuário."""

    def __init__(self, *, document_id: UUID) -> None:
        self.document_id = document_id
        super().__init__(f"documento {document_id} não encontrado para o job de indexação.")


class DocumentVersionNotFoundError(RuntimeError):
    """`Document` existe mas não tem nenhuma `DocumentVersion` — mesma
    categoria de inconsistência que `DocumentNotFoundForIndexJobError`
    (documento e versão 1 nascem juntos)."""

    def __init__(self, *, document_id: UUID) -> None:
        self.document_id = document_id
        super().__init__(f"nenhuma versão encontrada para o documento {document_id}.")


class MissingKnowledgeBaseForDocumentError(RuntimeError):
    """`Document.knowledge_base_id` não corresponde a nenhuma
    `KnowledgeBase` existente (nem excluída — `get_by_id_unscoped`
    nunca filtra por status). Mesma categoria de inconsistência das
    duas exceções acima."""

    def __init__(self, *, knowledge_base_id: UUID) -> None:
        self.knowledge_base_id = knowledge_base_id
        super().__init__(f"base de conhecimento {knowledge_base_id} não encontrada.")


class PipelineDocumentProcessor(DocumentProcessorPort):
    """Implementação real do pipeline de indexação, orquestrando os
    ports/adapters de RAG-020 (object storage), RAG-023 (parsing),
    RAG-024 (chunking, função pura — não é um port), RAG-025
    (embeddings) e RAG-026 (persistência)."""

    def __init__(
        self,
        *,
        document_repository: DocumentRepositoryPort,
        knowledge_base_repository: KnowledgeBaseRepositoryPort,
        object_storage: ObjectStoragePort,
        document_parser: DocumentParserPort,
        embedding_provider: EmbeddingProviderPort,
    ) -> None:
        self._document_repository = document_repository
        self._knowledge_base_repository = knowledge_base_repository
        self._object_storage = object_storage
        self._document_parser = document_parser
        self._embedding_provider = embedding_provider

    async def process(self, *, index_job_id: UUID) -> None:
        job = await self._document_repository.get_index_job(index_job_id=index_job_id)
        if job is None:
            # Job sumiu entre o worker reivindicá-lo e este ponto — nada
            # a fazer (defensivo; não deveria acontecer em operação normal).
            return

        document = await self._document_repository.get_document(document_id=job.document_id)
        if document is None:
            raise DocumentNotFoundForIndexJobError(document_id=job.document_id)

        version = await self._document_repository.get_latest_version(document_id=document.id)
        if version is None:
            raise DocumentVersionNotFoundError(document_id=document.id)

        knowledge_base = await self._knowledge_base_repository.get_by_id_unscoped(
            knowledge_base_id=document.knowledge_base_id
        )
        if knowledge_base is None:
            raise MissingKnowledgeBaseForDocumentError(knowledge_base_id=document.knowledge_base_id)

        await self._document_repository.mark_document_processing(document_id=document.id)

        raw_content = await self._object_storage.download(key=version.object_key)
        parsed = await self._document_parser.parse(
            filename=document.name, content=raw_content, content_type=document.mime_type
        )

        chunking_config = ChunkingConfig.from_knowledge_base_config(knowledge_base.config)
        chunk_drafts = chunk_document(
            parsed.markdown,
            title=document.name,
            origin=document.name,
            config=chunking_config,
        )

        embeddings = await self._embedding_provider.embed(
            texts=[draft.content for draft in chunk_drafts]
        )

        extracted_key = sanitize_object_key(
            f"{knowledge_base.id}/{document.id}/v{version.version}/extracted.md"
        )
        await self._object_storage.upload(
            key=extracted_key,
            content=parsed.markdown.encode("utf-8"),
            content_type="text/markdown",
        )

        chunks = [
            Chunk(
                id=uuid4(),
                tenant_id=knowledge_base.tenant_id,
                knowledge_base_id=knowledge_base.id,
                version_id=version.id,
                content=draft.content,
                token_count=draft.token_count,
                page=draft.page,
                section=draft.section,
                metadata=draft.metadata,
                embedding=embedding,
            )
            for draft, embedding in zip(chunk_drafts, embeddings, strict=True)
        ]

        await self._document_repository.persist_chunks_and_activate_version(
            document_id=document.id,
            version_id=version.id,
            extracted_object_key=extracted_key,
            chunks=chunks,
        )
