"""Adapter Postgres de `DocumentRepositoryPort` (RAG-021).

Mesma filosofia de `adapters/knowledge_base_repository/postgres.py`
(RAG-012): sem uma abstração de unit-of-work compartilhada entre
repositórios, cada método comita sua própria transação. `create_document`
insere `Document` + `DocumentVersion` + `IndexJob` (+ o registro de
idempotência, se houver `Idempotency-Key`) num único `commit()` — os
quatro nascem juntos ou nenhum nasce (seção 11, passo 5 do plano).

**Corrida sob `Idempotency-Key` (limitação conhecida)**: o caminho comum
— um cliente repete a mesma chave depois de um timeout, em série — é
tratado corretamente: `find_idempotent_upload` é consultado antes de
qualquer escrita, então a segunda chamada nunca cria um documento novo.
Sob concorrência genuína (duas requisições *simultâneas* com a mesma
chave, ambas veem "chave não usada" antes de qualquer uma commitar), a
unique constraint do banco garante que só uma das duas linhas de
`document_idempotency_keys` sobrevive — mas como não há unit-of-work
entre as duas tentativas de `create_document`, a requisição perdedora já
terá persistido (e comitado) seu próprio Document/DocumentVersion/
IndexJob antes de descobrir a corrida ao tentar inserir o registro de
idempotência. O resultado: dois documentos podem existir nesse cenário
raro, mas a resposta HTTP da requisição perdedora ainda é a mesma da
vencedora (`replayed=True`, mesmos IDs) — o cliente nunca vê dois
resultados diferentes para a mesma chave, só o banco acumula uma linha
órfã. Corrigir isso de verdade exige uma reserva atômica da chave antes
de criar o documento (ou um unit-of-work real), o que nenhuma atividade
anterior introduziu — fica registrado aqui para uma revisão futura se
isso vier a importar (o volume de requisições concorrentes idênticas em
uma janela de milissegundos é o cenário necessário para se manifestar).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.postgres.models.document import DocumentModel
from adapters.postgres.models.document_idempotency_key import DocumentIdempotencyKeyModel
from adapters.postgres.models.document_version import DocumentVersionModel
from adapters.postgres.models.index_job import IndexJobModel
from packages.application.ports.document_repository import (
    DocumentChecksumConflictError,
    DocumentRepositoryPort,
    DocumentUpload,
)
from packages.domain.entities.document import Document
from packages.domain.entities.document_version import DocumentVersion
from packages.domain.entities.index_job import IndexJob
from packages.domain.enums.document_status import DocumentStatus
from packages.domain.enums.index_job_type import IndexJobType
from packages.domain.enums.processing_status import ProcessingStatus


def _document_to_entity(model: DocumentModel) -> Document:
    return Document(
        id=model.id,
        knowledge_base_id=model.knowledge_base_id,
        name=model.name,
        mime_type=model.mime_type,
        checksum=model.checksum,
        status=model.status,
        active_version_id=model.active_version_id,
        created_at=model.created_at,
    )


def _version_to_entity(model: DocumentVersionModel) -> DocumentVersion:
    return DocumentVersion(
        id=model.id,
        document_id=model.document_id,
        version=model.version,
        object_key=model.object_key,
        extracted_object_key=model.extracted_object_key,
        created_at=model.created_at,
    )


def _job_to_entity(model: IndexJobModel) -> IndexJob:
    return IndexJob(
        id=model.id,
        document_id=model.document_id,
        type=model.type,
        status=model.status,
        attempts=model.attempts,
        error_code=model.error_code,
        error_message=model.error_message,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class PostgresDocumentRepository(DocumentRepositoryPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_checksum(
        self, *, tenant_id: UUID, knowledge_base_id: UUID, checksum: str
    ) -> Document | None:
        del tenant_id  # isolamento é transitivo via knowledge_base_id (ver docstring da porta).
        stmt = select(DocumentModel).where(
            DocumentModel.knowledge_base_id == knowledge_base_id,
            DocumentModel.checksum == checksum,
            DocumentModel.status != DocumentStatus.DELETED,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return _document_to_entity(model) if model is not None else None

    async def find_idempotent_upload(
        self, *, tenant_id: UUID, knowledge_base_id: UUID, idempotency_key: str
    ) -> DocumentUpload | None:
        stmt = select(DocumentIdempotencyKeyModel).where(
            DocumentIdempotencyKeyModel.tenant_id == tenant_id,
            DocumentIdempotencyKeyModel.knowledge_base_id == knowledge_base_id,
            DocumentIdempotencyKeyModel.idempotency_key == idempotency_key,
        )
        result = await self._session.execute(stmt)
        key_model = result.scalar_one_or_none()
        if key_model is None:
            return None
        return await self._load_upload(
            document_id=key_model.document_id,
            document_version_id=key_model.document_version_id,
            index_job_id=key_model.index_job_id,
            replayed=True,
        )

    async def _load_upload(
        self, *, document_id: UUID, document_version_id: UUID, index_job_id: UUID, replayed: bool
    ) -> DocumentUpload:
        document_model = await self._session.get(DocumentModel, document_id)
        version_model = await self._session.get(DocumentVersionModel, document_version_id)
        job_model = await self._session.get(IndexJobModel, index_job_id)
        # FK garante a existência das três linhas referenciadas.
        assert document_model is not None
        assert version_model is not None
        assert job_model is not None
        return DocumentUpload(
            document=_document_to_entity(document_model),
            version=_version_to_entity(version_model),
            index_job=_job_to_entity(job_model),
            replayed=replayed,
        )

    async def create_document(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        name: str,
        mime_type: str,
        checksum: str,
        object_key: str,
        idempotency_key: str | None,
    ) -> DocumentUpload:
        now = datetime.now(UTC)
        document_model = DocumentModel(
            id=uuid4(),
            knowledge_base_id=knowledge_base_id,
            name=name,
            mime_type=mime_type,
            checksum=checksum,
            status=DocumentStatus.PENDING,
            created_at=now,
        )
        version_model = DocumentVersionModel(
            id=uuid4(),
            document_id=document_model.id,
            version=1,
            object_key=object_key,
            created_at=now,
        )
        job_model = IndexJobModel(
            id=uuid4(),
            document_id=document_model.id,
            type=IndexJobType.INDEX,
            status=ProcessingStatus.PENDING,
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        self._session.add_all([document_model, version_model, job_model])

        if idempotency_key is not None:
            self._session.add(
                DocumentIdempotencyKeyModel(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    knowledge_base_id=knowledge_base_id,
                    idempotency_key=idempotency_key,
                    document_id=document_model.id,
                    document_version_id=version_model.id,
                    index_job_id=job_model.id,
                    created_at=now,
                )
            )

        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if idempotency_key is not None:
                replay = await self.find_idempotent_upload(
                    tenant_id=tenant_id,
                    knowledge_base_id=knowledge_base_id,
                    idempotency_key=idempotency_key,
                )
                if replay is not None:
                    return replay
            existing = await self.find_by_checksum(
                tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, checksum=checksum
            )
            if existing is not None:
                raise DocumentChecksumConflictError(
                    knowledge_base_id=knowledge_base_id, existing_document_id=existing.id
                ) from exc
            # Nem idempotência nem checksum explicam a falha (constraint
            # inesperada) — não mascarar, deixar a causa original subir.
            raise

        return DocumentUpload(
            document=_document_to_entity(document_model),
            version=_version_to_entity(version_model),
            index_job=_job_to_entity(job_model),
            replayed=False,
        )
