"""Dependências (FastAPI `Depends`) compartilhadas pelos routers da API."""

from __future__ import annotations

from uuid import UUID

from fastapi import Header

from packages.application.errors import AuthenticationError
from packages.config.settings import Settings, get_settings

TENANT_ID_HEADER = "X-Tenant-Id"


def get_settings_dependency() -> Settings:
    """Wrapper fino sobre `get_settings()`.

    Existe como função separada (em vez de usar `get_settings` diretamente
    em `Depends(...)`) para que os testes possam sobrescrevê-la via
    `app.dependency_overrides` sem mexer no cache de `get_settings()`.
    """
    return get_settings()


async def get_current_tenant_id(
    x_tenant_id: str | None = Header(default=None, alias=TENANT_ID_HEADER),
) -> UUID:
    """Resolve o tenant da requisição atual (RAG-012).

    **Provisório**: lê o cabeçalho `X-Tenant-Id` diretamente, sem validar
    sessão nem token algum — aceitável só em desenvolvimento local. A
    autenticação JWT real (RAG-050) substitui o corpo desta função por
    uma que resolve o tenant a partir de um token assinado e validado
    (issuer/audience/expiração), sem mudar a assinatura usada pelos
    routers (`Depends(get_current_tenant_id)` continua igual).
    """
    if not x_tenant_id:
        raise AuthenticationError(
            detail=f"Cabeçalho '{TENANT_ID_HEADER}' é obrigatório (RAG-050 trará JWT real)."
        )
    try:
        return UUID(x_tenant_id)
    except ValueError as exc:
        raise AuthenticationError(
            detail=f"Cabeçalho '{TENANT_ID_HEADER}' inválido: não é um UUID."
        ) from exc
