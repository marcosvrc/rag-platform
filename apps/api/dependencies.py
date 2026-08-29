"""Dependências (FastAPI `Depends`) compartilhadas pelos routers da API."""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.audit_log.postgres import PostgresAuditLogRepository
from adapters.postgres.engine import get_session
from adapters.token_verifier.pyjwt_verifier import PyJWTTokenVerifier
from packages.application.errors import AuthenticationError
from packages.application.ports.audit_log import AuditLogPort
from packages.application.ports.token_verifier import TokenClaims, TokenVerifierPort
from packages.config.settings import Settings, get_settings

_BEARER_PREFIX = "Bearer "


def get_settings_dependency() -> Settings:
    """Wrapper fino sobre `get_settings()`.

    Existe como função separada (em vez de usar `get_settings` diretamente
    em `Depends(...)`) para que os testes possam sobrescrevê-la via
    `app.dependency_overrides` sem mexer no cache de `get_settings()`.
    """
    return get_settings()


def get_token_verifier(
    settings: Settings = Depends(get_settings_dependency),
) -> TokenVerifierPort:
    """Fábrica do verificador de token usado por `get_current_identity`
    (RAG-050/RAG-051).

    Depende de `Settings` via `Depends(get_settings_dependency)` — nunca
    de `get_settings()` direto — pelo mesmo motivo de
    `apps.api.routers.documents.get_object_storage`: assim os testes
    conseguem trocar a configuração (issuer/audience/segredo) via
    `app.dependency_overrides[get_settings_dependency]`, sem precisar de
    variáveis de ambiente reais.
    """
    return PyJWTTokenVerifier(settings)


async def get_current_identity(
    authorization: str | None = Header(default=None),
    token_verifier: TokenVerifierPort = Depends(get_token_verifier),
) -> TokenClaims:
    """Resolve a identidade autenticada da requisição atual (RAG-051).

    Exige um cabeçalho `Authorization: Bearer <token>` válido — sem
    nenhum mecanismo alternativo (o cabeçalho provisório `X-Tenant-Id`
    do RAG-012, que não autenticava nada, não existe mais). O token é
    verificado via `TokenVerifierPort` (RAG-050: assinatura, issuer,
    audience e expiração); qualquer falha — cabeçalho ausente, esquema
    diferente de `Bearer`, ou token que `verify()` rejeite — vira 401
    (`AuthenticationError`, RAG-013), nunca uma exceção interna vazando
    para o cliente.
    """
    if authorization is None or not authorization.startswith(_BEARER_PREFIX):
        raise AuthenticationError(detail="Cabeçalho 'Authorization: Bearer <token>' é obrigatório.")
    token = authorization[len(_BEARER_PREFIX) :]
    if not token:
        raise AuthenticationError(detail="Cabeçalho 'Authorization: Bearer <token>' é obrigatório.")
    return token_verifier.verify(token)


async def get_current_tenant_id(
    identity: TokenClaims = Depends(get_current_identity),
) -> UUID:
    """Resolve o tenant da requisição atual, a partir da identidade já
    autenticada (RAG-012/RAG-051).

    `TokenClaims.tenant_id` é opcional na porta (nem todo token de
    acesso precisa identificar um tenant — ver
    `packages/application/ports/token_verifier.py`); é esta função que
    torna a claim obrigatória, porque todo endpoint de negócio desta API
    opera em nome de exatamente um tenant. Um token válido mas sem
    `tenant_id` é rejeitado com 401, não com 403: não é uma questão de
    permissão, é o token não carregar a informação mínima que qualquer
    endpoint de negócio exige.
    """
    if identity.tenant_id is None:
        raise AuthenticationError(detail="Token válido, mas sem a claim 'tenant_id' obrigatória.")
    return identity.tenant_id


async def get_audit_log(session: AsyncSession = Depends(get_session)) -> AuditLogPort:
    """Fábrica do trilho de auditoria (RAG-054), usado por
    `apps.api.routers.knowledge_bases` e `apps.api.routers.documents`.

    Mesmo padrão de `get_token_verifier`/`get_object_storage`: os
    testes trocam por `InMemoryAuditLog` via
    `app.dependency_overrides[get_audit_log]`, nunca falam com um
    Postgres real.
    """
    return PostgresAuditLogRepository(session)
