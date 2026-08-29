"""Endpoints de operação: liveness e readiness (RAG-005, seção 10.4).

Nenhum dos dois é versionado (`/v1`) — são endpoints de operação, não de
domínio (seção 8 das convenções).
"""

import asyncio
from typing import Literal

import asyncpg
import httpx
import redis.asyncio as redis_asyncio
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from apps.api.dependencies import get_settings_dependency
from packages.config.settings import Settings

router = APIRouter(tags=["health"])

CheckStatus = Literal["ok", "error"]

_CHECK_TIMEOUT_SECONDS = 2.0


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    status: CheckStatus
    checks: dict[str, CheckStatus]


@router.get("/health/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    """Confirma apenas que o processo da API está no ar.

    Nunca depende de recursos externos (banco, fila, object storage): se
    dependesse, uma indisponibilidade temporária de qualquer um deles
    derrubaria o liveness probe e causaria reinícios desnecessários do
    processo pelo orquestrador (o sintoma certo para isso é o readiness
    ficar "not ready", não o processo ser reiniciado).
    """
    return LivenessResponse()


async def _check_postgres(settings: Settings) -> CheckStatus:
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(
                host=settings.postgres_host,
                port=settings.postgres_port,
                user=settings.postgres_user,
                password=settings.postgres_password.get_secret_value(),
                database=settings.postgres_db,
            ),
            timeout=_CHECK_TIMEOUT_SECONDS,
        )
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()
        return "ok"
    except Exception:
        # Qualquer falha (timeout, autenticação, DNS, etc.) vira "error" sem
        # detalhes na resposta HTTP (seção 13: nunca expor stack trace ou
        # credenciais). Diagnóstico detalhado é responsabilidade dos logs
        # estruturados (RAG-052), não deste endpoint.
        return "error"


async def _check_redis(settings: Settings) -> CheckStatus:
    client = redis_asyncio.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        socket_timeout=_CHECK_TIMEOUT_SECONDS,
    )
    try:
        await asyncio.wait_for(client.ping(), timeout=_CHECK_TIMEOUT_SECONDS)
        return "ok"
    except Exception:
        return "error"
    finally:
        await client.aclose()


async def _check_minio(settings: Settings) -> CheckStatus:
    scheme = "https" if settings.minio_use_ssl else "http"
    url = f"{scheme}://{settings.minio_endpoint}/minio/health/live"
    try:
        async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
        return "ok" if response.status_code == 200 else "error"
    except Exception:
        return "error"


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness(
    response: Response,
    settings: Settings = Depends(get_settings_dependency),
) -> ReadinessResponse:
    """Valida a conectividade com as dependências críticas (Postgres, Redis
    e MinIO), em paralelo. Retorna 503 se qualquer uma estiver indisponível.

    `return_exceptions=True` é uma segunda camada de defesa: cada
    `_check_*` já captura suas próprias exceções, mas mesmo que uma delas
    deixasse algo escapar, nenhuma exceção chegaria à resposta HTTP —
    vira "error" aqui também (seção 13: nunca expor stack trace).
    """
    names = ("postgres", "redis", "minio")
    results = await asyncio.gather(
        _check_postgres(settings),
        _check_redis(settings),
        _check_minio(settings),
        return_exceptions=True,
    )
    checks: dict[str, CheckStatus] = {
        name: result if result in ("ok", "error") else "error"
        for name, result in zip(names, results, strict=True)
    }
    overall: CheckStatus = "ok" if all(value == "ok" for value in checks.values()) else "error"
    if overall == "error":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status=overall, checks=checks)
