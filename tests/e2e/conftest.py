"""Fixtures do teste E2E principal (RAG-080).

Ao contrário de TODO o resto da suíte de testes (`tests/unit`), nada
aqui é sobrescrito via `app.dependency_overrides` — `apps.api.main.app`
roda exatamente como em produção, contra Postgres/pgvector, MinIO e o
gateway LiteLLM de verdade (RAG-003: `docker compose up -d`). Por isso
este diretório nunca faz parte de `make test`/da suíte comum de CI
(RAG-070) — só de `make e2e`, com a infraestrutura de pé e um `.env`
real configurado (ver `README.md`/`.env.example`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from apps.api.main import app
from packages.config.settings import get_settings
from scripts.mint_local_dev_token import mint_token

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
async def api_client() -> AsyncIterator[httpx.AsyncClient]:
    """Cliente HTTP contra a app real, via transporte ASGI in-process
    (sem subir um servidor de verdade) — mesmo app que
    `uvicorn apps.api.main:app` serviria."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://e2e-test") as client:
        yield client


def _bearer_token(*, tenant_id: str) -> str:
    """JWT real, assinado com o `JWT_SECRET` de verdade lido do
    ambiente (`get_settings()`) — mesma função usada por
    `scripts/mint_local_dev_token.py`. Nunca um segredo de teste
    próprio: a app não tem nenhum override, então só um token válido
    contra a configuração real passa por `PyJWTTokenVerifier`."""
    return mint_token(subject=f"e2e-{tenant_id}", tenant_id=tenant_id, ttl_seconds=300)


@pytest.fixture
def tenant_a_id() -> str:
    return str(uuid4())


@pytest.fixture
def tenant_b_id() -> str:
    return str(uuid4())


@pytest.fixture
def tenant_a_headers(tenant_a_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_bearer_token(tenant_id=tenant_a_id)}"}


@pytest.fixture
def tenant_b_headers(tenant_b_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_bearer_token(tenant_id=tenant_b_id)}"}


@pytest.fixture(scope="session", autouse=True)
def _require_real_settings() -> None:
    """Falha cedo, com uma mensagem clara, se `get_settings()` não
    conseguir montar (variáveis obrigatórias ausentes) — em vez de cada
    teste falhar de um jeito confuso no meio da primeira chamada HTTP.
    Não substitui `docker compose up -d`: só garante que a
    CONFIGURAÇÃO mínima está presente."""
    get_settings()


@pytest.fixture
def known_fixture_document() -> tuple[str, bytes, str]:
    """Documento conhecido usado por todo o cenário E2E (critério de
    aceite "usa fixture conhecida"): `fixtures/nimbus-rewards.md`
    descreve um programa de fidelidade inteiramente fictício, com um
    código secreto ("GIRASSOL-7") que não existe em nenhum lugar além
    deste arquivo — se a resposta gerada mencionar esse código, é
    prova de que veio do chunk recuperado, nunca do conhecimento prévio
    do modelo. Devolve (filename, content, mime_type) prontos para
    `httpx`."""
    path = _FIXTURES_DIR / "nimbus-rewards.md"
    return path.name, path.read_bytes(), "text/markdown"
