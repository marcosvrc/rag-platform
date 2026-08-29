"""Endpoints de bases de conhecimento (RAG-012, seção 10.1 do plano).

Isolamento por tenant: `tenant_id` vem de `get_current_tenant_id`
(resolvido a partir de um JWT autenticado — `Authorization: Bearer
<token>`, RAG-050/RAG-051) e é repassado explicitamente a toda chamada
de repositório — nunca lido do corpo da requisição, então um tenant
nunca pode operar sobre uma base de outro só porque o forjou no
payload. Um recurso de outro tenant retorna 404, nunca 403 (ver
`KnowledgeBaseRepositoryPort`).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.knowledge_base_repository import PostgresKnowledgeBaseRepository
from adapters.postgres.engine import get_session
from apps.api.dependencies import get_audit_log, get_current_identity, get_current_tenant_id
from packages.application.commands import knowledge_base as kb_commands
from packages.application.ports.audit_log import AuditLogPort, record_audit_event_safely
from packages.application.ports.knowledge_base_repository import KnowledgeBaseRepositoryPort
from packages.application.ports.token_verifier import TokenClaims
from packages.application.queries import knowledge_base as kb_queries
from packages.contracts.knowledge_base import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseListResponse,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdateRequest,
)
from packages.domain.entities.knowledge_base import KnowledgeBase

router = APIRouter(prefix="/v1/knowledge-bases", tags=["knowledge-bases"])


async def get_knowledge_base_repository(
    session: AsyncSession = Depends(get_session),
) -> KnowledgeBaseRepositoryPort:
    """Depends() próprio (não em `apps/api/dependencies.py`): mantém o
    adapter concreto (Postgres) perto de onde é o único lugar usado —
    os testes sobrescrevem via `app.dependency_overrides`."""
    return PostgresKnowledgeBaseRepository(session)


def _to_response(knowledge_base: KnowledgeBase) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(
        id=knowledge_base.id,
        tenant_id=knowledge_base.tenant_id,
        name=knowledge_base.name,
        description=knowledge_base.description,
        status=knowledge_base.status,
        config=knowledge_base.config,
        created_at=knowledge_base.created_at,
        updated_at=knowledge_base.updated_at,
    )


@router.post("", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    payload: KnowledgeBaseCreateRequest,
    tenant_id: UUID = Depends(get_current_tenant_id),
    identity: TokenClaims = Depends(get_current_identity),
    repository: KnowledgeBaseRepositoryPort = Depends(get_knowledge_base_repository),
    audit_log: AuditLogPort = Depends(get_audit_log),
) -> KnowledgeBaseResponse:
    knowledge_base = await kb_commands.create_knowledge_base(
        repository,
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        config=payload.config,
    )
    await record_audit_event_safely(
        audit_log,
        tenant_id=tenant_id,
        actor=identity.subject,
        action="knowledge_base.create",
        resource_type="knowledge_base",
        resource_id=knowledge_base.id,
    )
    return _to_response(knowledge_base)


@router.get("", response_model=KnowledgeBaseListResponse)
async def list_knowledge_bases(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=kb_queries.DEFAULT_PAGE_SIZE, ge=1, le=kb_queries.MAX_PAGE_SIZE),
    tenant_id: UUID = Depends(get_current_tenant_id),
    repository: KnowledgeBaseRepositoryPort = Depends(get_knowledge_base_repository),
) -> KnowledgeBaseListResponse:
    page = await kb_queries.list_knowledge_bases(
        repository, tenant_id=tenant_id, cursor=cursor, limit=limit
    )
    return KnowledgeBaseListResponse(
        items=[_to_response(kb) for kb in page.items], next_cursor=page.next_cursor
    )


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(
    knowledge_base_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant_id),
    repository: KnowledgeBaseRepositoryPort = Depends(get_knowledge_base_repository),
) -> KnowledgeBaseResponse:
    knowledge_base = await kb_queries.get_knowledge_base(
        repository, tenant_id=tenant_id, knowledge_base_id=knowledge_base_id
    )
    return _to_response(knowledge_base)


@router.patch("/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    knowledge_base_id: UUID,
    payload: KnowledgeBaseUpdateRequest,
    tenant_id: UUID = Depends(get_current_tenant_id),
    identity: TokenClaims = Depends(get_current_identity),
    repository: KnowledgeBaseRepositoryPort = Depends(get_knowledge_base_repository),
    audit_log: AuditLogPort = Depends(get_audit_log),
) -> KnowledgeBaseResponse:
    fields = payload.model_dump(exclude_unset=True)
    knowledge_base = await kb_commands.update_knowledge_base(
        repository, tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, fields=fields
    )
    await record_audit_event_safely(
        audit_log,
        tenant_id=tenant_id,
        actor=identity.subject,
        action="knowledge_base.update",
        resource_type="knowledge_base",
        resource_id=knowledge_base_id,
    )
    return _to_response(knowledge_base)


@router.delete("/{knowledge_base_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    knowledge_base_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant_id),
    identity: TokenClaims = Depends(get_current_identity),
    repository: KnowledgeBaseRepositoryPort = Depends(get_knowledge_base_repository),
    audit_log: AuditLogPort = Depends(get_audit_log),
) -> Response:
    await kb_commands.delete_knowledge_base(
        repository, tenant_id=tenant_id, knowledge_base_id=knowledge_base_id
    )
    await record_audit_event_safely(
        audit_log,
        tenant_id=tenant_id,
        actor=identity.subject,
        action="knowledge_base.delete",
        resource_type="knowledge_base",
        resource_id=knowledge_base_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
